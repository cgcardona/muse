"""Rebase engine for ``muse rebase``.

A Muse rebase replays a sequence of commits onto a new base.  Because commits
are content-addressed, replaying a commit produces a *new* commit with a new
ID — the original commits are untouched in the store.

Algorithm
---------
Given::

    A ─── B ─── C ─── D  (current branch HEAD = D)
     \\
      E ─── F             (upstream = F)

After ``muse rebase F`` (or ``muse rebase --onto F A`` where A is the merge
base)::

    E ─── F ─── B' ─── C' ─── D'  (current branch HEAD = D')

Each replayed commit ``X'`` is produced by:

1. Taking the delta between ``X`` and its parent ``X-1`` (what changed).
2. Applying that delta on top of the current tip via the domain plugin's
   three-way merge (same logic as cherry-pick).
3. Writing a new ``CommitRecord`` with the new parent pointer.

State
-----
When a conflict occurs mid-replay, the rebase pauses and writes
``.muse/REBASE_STATE.json``.  The user resolves the conflict and runs
``muse rebase --continue`` to resume, or ``muse rebase --abort`` to undo.

Squash mode
-----------
When ``squash=True``, all commits are replayed without writing intermediate
commits — only the final merged state is committed.
"""

from __future__ import annotations

import datetime
import json
import logging
import pathlib
from typing import TypedDict

from muse.core.snapshot import compute_commit_id, compute_snapshot_id
from muse.core.store import (
    CommitRecord,
    SnapshotRecord,
    read_commit,
    read_snapshot,
    write_commit,
    write_snapshot,
)
from muse.core.validation import validate_branch_name
from muse.core.workdir import apply_manifest
from muse.domain import MergeResult, MuseDomainPlugin, SnapshotManifest

logger = logging.getLogger(__name__)

_REBASE_STATE_FILE = ".muse/REBASE_STATE.json"


# ---------------------------------------------------------------------------
# State TypedDict
# ---------------------------------------------------------------------------


class RebaseState(TypedDict):
    """Serialisable state for an in-progress rebase session."""

    original_branch: str
    original_head: str
    onto: str
    remaining: list[str]
    completed: list[str]
    squash: bool


# ---------------------------------------------------------------------------
# State file I/O
# ---------------------------------------------------------------------------


def load_rebase_state(root: pathlib.Path) -> RebaseState | None:
    """Return the current rebase state, or ``None`` if none is active."""
    path = root / _REBASE_STATE_FILE
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(raw, dict):
        return None
    remaining = raw.get("remaining")
    completed = raw.get("completed")
    if not isinstance(remaining, list) or not isinstance(completed, list):
        return None
    return RebaseState(
        original_branch=str(raw.get("original_branch", "")),
        original_head=str(raw.get("original_head", "")),
        onto=str(raw.get("onto", "")),
        remaining=[str(x) for x in remaining if isinstance(x, str)],
        completed=[str(x) for x in completed if isinstance(x, str)],
        squash=bool(raw.get("squash", False)),
    )


def save_rebase_state(root: pathlib.Path, state: RebaseState) -> None:
    """Write rebase state to ``.muse/REBASE_STATE.json``."""
    (root / ".muse").mkdir(parents=True, exist_ok=True)
    (root / _REBASE_STATE_FILE).write_text(
        json.dumps(dict(state), indent=2), encoding="utf-8"
    )


def clear_rebase_state(root: pathlib.Path) -> None:
    """Remove ``.muse/REBASE_STATE.json``."""
    path = root / _REBASE_STATE_FILE
    if path.exists():
        path.unlink()
        logger.debug("✅ Cleared REBASE_STATE.json")


# ---------------------------------------------------------------------------
# Commit collection
# ---------------------------------------------------------------------------


def collect_commits_to_replay(
    root: pathlib.Path,
    stop_at: str,
    tip: str,
    max_commits: int = 10_000,
) -> list[CommitRecord]:
    """Return commits from *tip* back to (but not including) *stop_at*.

    The result is in chronological order (oldest first) so the replay loop
    can iterate forward.

    Args:
        root:        Repository root.
        stop_at:     Commit ID to stop at (exclusive — the merge base).
        tip:         Starting commit ID (the current branch HEAD).
        max_commits: Safety cap.

    Returns:
        List of ``CommitRecord`` objects, oldest first (ready to replay).
    """
    commits: list[CommitRecord] = []
    seen: set[str] = set()
    current: str | None = tip

    while current and current not in seen and len(commits) < max_commits:
        seen.add(current)
        if current == stop_at:
            break
        commit = read_commit(root, current)
        if commit is None:
            break
        commits.append(commit)
        current = commit.parent_commit_id

    # Reverse so oldest is first.
    commits.reverse()
    return commits


# ---------------------------------------------------------------------------
# Single-commit replay
# ---------------------------------------------------------------------------


def replay_one(
    root: pathlib.Path,
    commit: CommitRecord,
    parent_id: str,
    plugin: "MuseDomainPlugin",
    domain: str,
    repo_id: str,
    branch: str,
) -> CommitRecord | list[str]:
    """Replay *commit* on top of *parent_id* using the domain plugin.

    Performs a three-way merge where:
    - ``base``   = commit's original parent snapshot (what existed before)
    - ``ours``   = the current rebased tip snapshot (what we've built so far)
    - ``theirs`` = commit's snapshot (what we want to apply)

    When the merge is clean, writes the new commit and snapshot and returns
    the new ``CommitRecord``.  When conflicts exist, returns the list of
    conflicting paths — the caller is responsible for writing ``MERGE_STATE.json``
    and stopping the rebase.

    Args:
        root:      Repository root.
        commit:    The original commit being replayed.
        parent_id: The new parent commit ID (last replayed commit or onto base).
        plugin:    The active domain plugin instance.
        domain:    Domain name string.
        repo_id:   Repository UUID.
        branch:    Current branch name.

    Returns:
        New ``CommitRecord`` on clean merge, ``list[str]`` of conflict paths on conflict.
    """
    if not isinstance(plugin, MuseDomainPlugin):
        raise TypeError(f"replay_one: plugin {type(plugin).__name__!r} is not a MuseDomainPlugin")

    # Resolve original parent snapshot (the "base" for the merge).
    base_manifest: dict[str, str] = {}
    if commit.parent_commit_id:
        parent_commit = read_commit(root, commit.parent_commit_id)
        if parent_commit:
            parent_snap = read_snapshot(root, parent_commit.snapshot_id)
            if parent_snap:
                base_manifest = parent_snap.manifest

    # "Theirs" = the original commit's snapshot.
    theirs_snap = read_snapshot(root, commit.snapshot_id)
    theirs_manifest = theirs_snap.manifest if theirs_snap else {}

    # "Ours" = the current rebased tip.
    ours_manifest: dict[str, str] = {}
    if parent_id:
        parent_rec = read_commit(root, parent_id)
        if parent_rec:
            ours_snap = read_snapshot(root, parent_rec.snapshot_id)
            if ours_snap:
                ours_manifest = ours_snap.manifest

    base_snap_obj = SnapshotManifest(files=base_manifest, domain=domain)
    ours_snap_obj = SnapshotManifest(files=ours_manifest, domain=domain)
    theirs_snap_obj = SnapshotManifest(files=theirs_manifest, domain=domain)

    result: MergeResult = plugin.merge(base_snap_obj, ours_snap_obj, theirs_snap_obj, repo_root=root)

    if not result.is_clean:
        return result.conflicts

    merged_manifest = result.merged["files"]

    # Apply the merged state to the working tree.
    apply_manifest(root, merged_manifest)

    snapshot_id = compute_snapshot_id(merged_manifest)
    committed_at = datetime.datetime.now(datetime.timezone.utc)
    new_commit_id = compute_commit_id(
        parent_ids=[parent_id] if parent_id else [],
        snapshot_id=snapshot_id,
        message=commit.message,
        committed_at_iso=committed_at.isoformat(),
    )

    write_snapshot(root, SnapshotRecord(snapshot_id=snapshot_id, manifest=merged_manifest))
    new_commit = CommitRecord(
        commit_id=new_commit_id,
        repo_id=repo_id,
        branch=branch,
        snapshot_id=snapshot_id,
        message=commit.message,
        committed_at=committed_at,
        parent_commit_id=parent_id if parent_id else None,
        author=commit.author,
        agent_id=commit.agent_id,
        model_id=commit.model_id,
    )
    write_commit(root, new_commit)
    return new_commit


def _write_branch_ref(root: pathlib.Path, branch: str, commit_id: str) -> None:
    """Write commit_id to the branch ref file atomically."""
    validate_branch_name(branch)
    ref_path = root / ".muse" / "refs" / "heads" / branch
    ref_path.parent.mkdir(parents=True, exist_ok=True)
    ref_path.write_text(commit_id, encoding="utf-8")

"""muse pull — fetch from a remote and merge into the current branch.

Combines ``muse fetch`` and ``muse merge`` in a single command:

1. Downloads commits, snapshots, and objects from the remote.
2. Updates the remote tracking pointer.
3. Performs a three-way merge of the remote branch HEAD into the current branch.

If the remote branch is already an ancestor of the local HEAD (fast-forward),
the local branch ref and working tree are advanced without a merge commit.

Pass ``--no-merge`` to stop after the fetch step (equivalent to ``muse fetch``).
"""

from __future__ import annotations

import argparse
import datetime
import json
import logging
import pathlib
import sys

from muse.cli.config import get_auth_token, get_remote, get_remote_head, get_upstream, set_remote_head
from muse.core.errors import ExitCode
from muse.core.merge_engine import find_merge_base, write_merge_state
from muse.core.pack import apply_pack
from muse.core.repo import require_repo
from muse.core.snapshot import compute_commit_id, compute_snapshot_id
from muse.core.store import (
    CommitRecord,
    SnapshotRecord,
    get_all_commits,
    get_head_commit_id,
    get_head_snapshot_manifest,
    read_commit,
    read_current_branch,
    read_snapshot,
    write_commit,
    write_snapshot,
)
from muse.core.transport import TransportError, make_transport
from muse.core.workdir import apply_manifest
from muse.domain import SnapshotManifest, StructuredMergePlugin
from muse.plugins.registry import read_domain, resolve_plugin

logger = logging.getLogger(__name__)


def _current_branch(root: pathlib.Path) -> str:
    """Return the current branch name from ``.muse/HEAD``."""
    return read_current_branch(root)


def _read_repo_id(root: pathlib.Path) -> str:
    """Return the repository UUID from ``.muse/repo.json``."""
    return str(json.loads((root / ".muse" / "repo.json").read_text())["repo_id"])


def _restore_from_manifest(root: pathlib.Path, manifest: dict[str, str]) -> None:
    """Apply *manifest* to the working tree at *root*."""
    apply_manifest(root, manifest)


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the pull subcommand."""
    parser = subparsers.add_parser(
        "pull",
        help="Fetch from a remote and merge into the current branch.",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("remote", nargs="?", default="origin", help="Remote name to pull from (default: origin).")
    parser.add_argument("--branch", "-b", default=None,
                        help="Remote branch to pull (default: tracked branch or current branch).")
    parser.add_argument("--no-merge", action="store_true", dest="no_merge",
                        help="Only fetch; do not merge into the current branch.")
    parser.add_argument("-m", "--message", default=None, help="Override the merge commit message.")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> None:
    """Fetch from a remote and merge into the current branch.

    Equivalent to running ``muse fetch`` followed by ``muse merge``.
    Pass ``--no-merge`` to stop after the fetch step.
    """
    remote: str = args.remote
    branch: str | None = args.branch
    no_merge: bool = args.no_merge
    message: str | None = args.message

    root = require_repo()

    url = get_remote(remote, root)
    if url is None:
        print(f"❌ Remote '{remote}' is not configured.")
        print(f"  Add it with: muse remote add {remote} <url>")
        raise SystemExit(ExitCode.USER_ERROR)

    token = get_auth_token(root, remote_url=url)
    current_branch = _current_branch(root)
    target_branch = branch or get_upstream(current_branch, root) or current_branch

    transport = make_transport(url)

    # ── Fetch ────────────────────────────────────────────────────────────────
    try:
        info = transport.fetch_remote_info(url, token)
    except TransportError as exc:
        print(f"❌ Cannot reach remote '{remote}': {exc}")
        raise SystemExit(ExitCode.INTERNAL_ERROR)

    remote_commit_id = info["branch_heads"].get(target_branch)
    if remote_commit_id is None:
        print(f"❌ Branch '{target_branch}' does not exist on remote '{remote}'.")
        raise SystemExit(ExitCode.USER_ERROR)

    local_commit_ids = [c.commit_id for c in get_all_commits(root)]
    print(f"Fetching {remote}/{target_branch} …")

    try:
        bundle = transport.fetch_pack(
            url, token, want=[remote_commit_id], have=local_commit_ids
        )
    except TransportError as exc:
        print(f"❌ Fetch failed: {exc}")
        raise SystemExit(ExitCode.INTERNAL_ERROR)

    apply_result = apply_pack(root, bundle)
    set_remote_head(remote, target_branch, remote_commit_id, root)
    commits_received = len(bundle.get("commits") or [])
    print(
        f"✅ Fetched {commits_received} commit(s), {apply_result['objects_written']} new object(s) "
        f"from {remote}/{target_branch} ({remote_commit_id[:8]})"
    )

    if no_merge:
        return

    # ── Merge ────────────────────────────────────────────────────────────────
    repo_id = _read_repo_id(root)
    ours_commit_id = get_head_commit_id(root, current_branch)
    theirs_commit_id = remote_commit_id

    if ours_commit_id is None:
        # No local commits yet — just advance HEAD to the remote commit.
        (root / ".muse" / "refs" / "heads" / current_branch).write_text(
            theirs_commit_id
        )
        theirs_commit = read_commit(root, theirs_commit_id)
        if theirs_commit:
            snap = read_snapshot(root, theirs_commit.snapshot_id)
            if snap:
                _restore_from_manifest(root, snap.manifest)
        print(f"✅ Initialised {current_branch} at {theirs_commit_id[:8]}")
        return

    if ours_commit_id == theirs_commit_id:
        print("Already up to date.")
        return

    base_commit_id = find_merge_base(root, ours_commit_id, theirs_commit_id)

    if base_commit_id == theirs_commit_id:
        print("Already up to date.")
        return

    # Fast-forward: remote is a direct descendant of local HEAD.
    if base_commit_id == ours_commit_id:
        theirs_commit = read_commit(root, theirs_commit_id)
        if theirs_commit:
            snap = read_snapshot(root, theirs_commit.snapshot_id)
            if snap:
                _restore_from_manifest(root, snap.manifest)
        (root / ".muse" / "refs" / "heads" / current_branch).write_text(
            theirs_commit_id
        )
        print(
            f"Fast-forward {current_branch} to {theirs_commit_id[:8]} "
            f"({remote}/{target_branch})"
        )
        return

    # Three-way merge.
    domain = read_domain(root)
    plugin = resolve_plugin(root)

    ours_manifest = get_head_snapshot_manifest(root, repo_id, current_branch) or {}
    theirs_commit = read_commit(root, theirs_commit_id)
    theirs_manifest: dict[str, str] = {}
    if theirs_commit:
        theirs_snap = read_snapshot(root, theirs_commit.snapshot_id)
        if theirs_snap:
            theirs_manifest = dict(theirs_snap.manifest)

    base_manifest: dict[str, str] = {}
    if base_commit_id:
        base_commit = read_commit(root, base_commit_id)
        if base_commit:
            base_snap = read_snapshot(root, base_commit.snapshot_id)
            if base_snap:
                base_manifest = dict(base_snap.manifest)

    base_snap_obj = SnapshotManifest(files=base_manifest, domain=domain)
    ours_snap_obj = SnapshotManifest(files=ours_manifest, domain=domain)
    theirs_snap_obj = SnapshotManifest(files=theirs_manifest, domain=domain)

    if isinstance(plugin, StructuredMergePlugin):
        ours_delta = plugin.diff(base_snap_obj, ours_snap_obj, repo_root=root)
        theirs_delta = plugin.diff(base_snap_obj, theirs_snap_obj, repo_root=root)
        result = plugin.merge_ops(
            base_snap_obj,
            ours_snap_obj,
            theirs_snap_obj,
            ours_delta["ops"],
            theirs_delta["ops"],
            repo_root=root,
        )
    else:
        result = plugin.merge(base_snap_obj, ours_snap_obj, theirs_snap_obj, repo_root=root)

    if result.applied_strategies:
        for p, strategy in sorted(result.applied_strategies.items()):
            if strategy != "manual":
                print(f"  ✔ [{strategy}] {p}")

    if not result.is_clean:
        write_merge_state(
            root,
            base_commit=base_commit_id or "",
            ours_commit=ours_commit_id,
            theirs_commit=theirs_commit_id,
            conflict_paths=result.conflicts,
            other_branch=f"{remote}/{target_branch}",
        )
        print(f"❌ Merge conflict in {len(result.conflicts)} file(s):")
        for p in sorted(result.conflicts):
            print(f"  CONFLICT (both modified): {p}")
        print('\nFix conflicts and run "muse commit" to complete the merge.')
        raise SystemExit(ExitCode.USER_ERROR)

    merged_manifest = result.merged["files"]
    _restore_from_manifest(root, merged_manifest)

    snapshot_id = compute_snapshot_id(merged_manifest)
    committed_at = datetime.datetime.now(datetime.timezone.utc)
    merge_message = (
        message
        or f"Merge {remote}/{target_branch} into {current_branch}"
    )
    commit_id = compute_commit_id(
        parent_ids=[ours_commit_id, theirs_commit_id],
        snapshot_id=snapshot_id,
        message=merge_message,
        committed_at_iso=committed_at.isoformat(),
    )
    write_snapshot(root, SnapshotRecord(snapshot_id=snapshot_id, manifest=merged_manifest))
    write_commit(
        root,
        CommitRecord(
            commit_id=commit_id,
            repo_id=repo_id,
            branch=current_branch,
            snapshot_id=snapshot_id,
            message=merge_message,
            committed_at=committed_at,
            parent_commit_id=ours_commit_id,
            parent2_commit_id=theirs_commit_id,
        ),
    )
    (root / ".muse" / "refs" / "heads" / current_branch).write_text(commit_id)
    print(
        f"✅ Merged {remote}/{target_branch} into {current_branch} ({commit_id[:8]})"
    )

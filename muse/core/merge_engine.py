"""Muse VCS merge engine — fast-forward, 3-way, op-level, and CRDT merge.

Public API
----------
Pure functions (no I/O):

- :func:`diff_snapshots` — paths that changed between two snapshot manifests.
- :func:`detect_conflicts` — paths changed on *both* branches since the base.
- :func:`apply_merge` — build merged manifest for a conflict-free 3-way merge.
- :func:`crdt_join_snapshots` — convergent CRDT join; always succeeds.

Operational Transformation (operation-level) merge:

- :mod:`muse.core.op_transform` — ``ops_commute``, ``transform``, ``merge_op_lists``,
  ``merge_structured``, and :class:`~muse.core.op_transform.MergeOpsResult`.
  Plugins that implement :class:`~muse.domain.StructuredMergePlugin` use these
  functions to auto-merge non-conflicting ``DomainOp`` lists.

CRDT convergent merge:

- :func:`crdt_join_snapshots` — detects :class:`~muse.domain.CRDTPlugin` at
  runtime and delegates to ``plugin.join(a, b)``.  Returns a
  :class:`~muse.domain.MergeResult` with an empty ``conflicts`` list; CRDT
  joins never fail.

File-based helpers:

- :func:`find_merge_base` — lowest common ancestor (LCA) of two commits.
- :func:`read_merge_state` — detect and load an in-progress merge.
- :func:`write_merge_state` — persist conflict state before exiting.
- :func:`clear_merge_state` — remove MERGE_STATE.json after resolution.
- :func:`apply_resolution` — restore a specific object version to state/.

``MERGE_STATE.json`` schema
---------------------------

.. code-block:: json

    {
        "base_commit": "abc123...",
        "ours_commit": "def456...",
        "theirs_commit": "789abc...",
        "conflict_paths": ["beat.mid", "lead.mp3"],
        "other_branch": "feature/experiment"
    }

``other_branch`` is optional; all other fields are required when conflicts exist.
"""

from __future__ import annotations

import json
import logging
import pathlib
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, TypedDict

from muse.core.validation import contain_path, validate_object_id, validate_ref_id

if TYPE_CHECKING:
    from muse.domain import MergeResult, MuseDomainPlugin

logger = logging.getLogger(__name__)

_MERGE_STATE_FILENAME = "MERGE_STATE.json"


# ---------------------------------------------------------------------------
# Wire-format TypedDict
# ---------------------------------------------------------------------------


class MergeStatePayload(TypedDict, total=False):
    """JSON-serialisable form of an in-progress merge state."""

    base_commit: str
    ours_commit: str
    theirs_commit: str
    conflict_paths: list[str]
    other_branch: str


# ---------------------------------------------------------------------------
# MergeState dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MergeState:
    """Describes an in-progress merge with unresolved conflicts."""

    conflict_paths: list[str] = field(default_factory=list)
    base_commit: str | None = None
    ours_commit: str | None = None
    theirs_commit: str | None = None
    other_branch: str | None = None


# ---------------------------------------------------------------------------
# Filesystem helpers
# ---------------------------------------------------------------------------


def read_merge_state(root: pathlib.Path) -> MergeState | None:
    """Return :class:`MergeState` if a merge is in progress, otherwise ``None``."""
    merge_state_path = root / ".muse" / _MERGE_STATE_FILENAME
    if not merge_state_path.exists():
        return None
    try:
        data = json.loads(merge_state_path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("⚠️ Failed to read %s: %s", _MERGE_STATE_FILENAME, exc)
        return None

    raw_conflicts = data.get("conflict_paths", [])
    safe_conflict_paths: list[str] = []
    if isinstance(raw_conflicts, list):
        for c in raw_conflicts:
            try:
                contained = contain_path(root, str(c))
                # Store as relative POSIX string for display; contain_path already validated it.
                safe_conflict_paths.append(contained.relative_to(root.resolve()).as_posix())
            except ValueError:
                logger.warning(
                    "⚠️ Skipping unsafe conflict path %r from MERGE_STATE.json", c
                )

    def _validated_ref(key: str) -> str | None:
        val = data.get(key)
        if val is None:
            return None
        s = str(val)
        try:
            validate_ref_id(s)
            return s
        except ValueError:
            logger.warning(
                "⚠️ Invalid %s %r in MERGE_STATE.json — ignoring", key, s
            )
            return None

    def _str_or_none(key: str) -> str | None:
        val = data.get(key)
        return str(val) if val is not None else None

    return MergeState(
        conflict_paths=safe_conflict_paths,
        base_commit=_validated_ref("base_commit"),
        ours_commit=_validated_ref("ours_commit"),
        theirs_commit=_validated_ref("theirs_commit"),
        other_branch=_str_or_none("other_branch"),
    )


def write_merge_state(
    root: pathlib.Path,
    *,
    base_commit: str,
    ours_commit: str,
    theirs_commit: str,
    conflict_paths: list[str],
    other_branch: str | None = None,
) -> None:
    """Write ``.muse/MERGE_STATE.json`` to signal an in-progress conflicted merge.

    Called by the ``muse merge`` command when the merge produces at least one
    conflict that cannot be auto-resolved.  The file is read back by
    :func:`read_merge_state` on subsequent ``muse status`` and ``muse commit``
    invocations to surface conflict state to the user.

    Args:
        root:           Repository root (parent of ``.muse/``).
        base_commit:    Commit ID of the merge base (common ancestor).
        ours_commit:    Commit ID of the current branch (HEAD) at merge time.
        theirs_commit:  Commit ID of the branch being merged in.
        conflict_paths: Sorted list of workspace-relative POSIX paths with
                        unresolvable conflicts.
        other_branch:   Name of the branch being merged in; stored for
                        informational display but not required for resolution.
    """
    merge_state_path = root / ".muse" / _MERGE_STATE_FILENAME
    payload: MergeStatePayload = {
        "base_commit": base_commit,
        "ours_commit": ours_commit,
        "theirs_commit": theirs_commit,
        "conflict_paths": sorted(conflict_paths),
    }
    if other_branch is not None:
        payload["other_branch"] = other_branch
    merge_state_path.write_text(json.dumps(payload, indent=2))
    logger.info("✅ Wrote MERGE_STATE.json with %d conflict(s)", len(conflict_paths))


def clear_merge_state(root: pathlib.Path) -> None:
    """Remove ``.muse/MERGE_STATE.json`` after a successful merge or resolution."""
    merge_state_path = root / ".muse" / _MERGE_STATE_FILENAME
    if merge_state_path.exists():
        merge_state_path.unlink()
        logger.debug("✅ Cleared MERGE_STATE.json")


def apply_resolution(
    root: pathlib.Path,
    rel_path: str,
    object_id: str,
) -> None:
    """Restore a specific object version to the working tree at ``<rel_path>``.

    Used by the ``muse merge --resolve`` workflow: after a user has chosen
    which version of a conflicting file to keep, this function writes that
    version into the working tree so ``muse commit`` can snapshot it.

    Args:
        root:      Repository root (parent of ``.muse/``).
        rel_path:  Workspace-relative POSIX path of the conflicting file.
        object_id: SHA-256 of the chosen resolution content in the object store.

    Raises:
        FileNotFoundError: When *object_id* is not present in the local store.
    """
    from muse.core.object_store import read_object

    validate_object_id(object_id)
    dest = contain_path(root, rel_path)

    content = read_object(root, object_id)
    if content is None:
        raise FileNotFoundError(
            f"Object {object_id[:8]} for '{rel_path}' not found in local store."
        )
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(content)
    logger.debug("✅ Restored '%s' from object %s", rel_path, object_id[:8])


def is_conflict_resolved(merge_state: MergeState, rel_path: str) -> bool:
    """Return ``True`` if *rel_path* is NOT listed as a conflict in *merge_state*."""
    return rel_path not in merge_state.conflict_paths


# ---------------------------------------------------------------------------
# Pure merge functions (no I/O)
# ---------------------------------------------------------------------------


def diff_snapshots(
    base_manifest: dict[str, str],
    other_manifest: dict[str, str],
) -> set[str]:
    """Return the set of paths that differ between *base_manifest* and *other_manifest*.

    A path is "different" if it was added (in *other* but not *base*), deleted
    (in *base* but not *other*), or modified (present in both with different
    content hashes).

    Args:
        base_manifest:  Path → content-hash map for the ancestor snapshot.
        other_manifest: Path → content-hash map for the other snapshot.

    Returns:
        Set of workspace-relative POSIX paths that differ.
    """
    base_paths = set(base_manifest.keys())
    other_paths = set(other_manifest.keys())
    added = other_paths - base_paths
    deleted = base_paths - other_paths
    common = base_paths & other_paths
    modified = {p for p in common if base_manifest[p] != other_manifest[p]}
    return added | deleted | modified


def detect_conflicts(
    ours_changed: set[str],
    theirs_changed: set[str],
) -> set[str]:
    """Return paths changed on *both* branches since the merge base."""
    return ours_changed & theirs_changed


def apply_merge(
    base_manifest: dict[str, str],
    ours_manifest: dict[str, str],
    theirs_manifest: dict[str, str],
    ours_changed: set[str],
    theirs_changed: set[str],
    conflict_paths: set[str],
) -> dict[str, str]:
    """Build the merged snapshot manifest for a conflict-free 3-way merge.

    Starts from *base_manifest* and applies non-conflicting changes from both
    branches:

    - Ours-only changes (in *ours_changed* but not *conflict_paths*) are taken
      from *ours_manifest*.  Deletions are handled by the absence of the path
      in *ours_manifest*.
    - Theirs-only changes (in *theirs_changed* but not *conflict_paths*) are
      taken from *theirs_manifest* by the same logic.
    - Paths in *conflict_paths* are excluded — callers must resolve them
      separately before producing a final merged snapshot.

    Args:
        base_manifest:  Path → content-hash for the common ancestor.
        ours_manifest:  Path → content-hash for our branch.
        theirs_manifest: Path → content-hash for their branch.
        ours_changed:   Paths changed by our branch (from :func:`diff_snapshots`).
        theirs_changed: Paths changed by their branch.
        conflict_paths: Paths with concurrent changes — excluded from output.

    Returns:
        Merged path → content-hash mapping; conflict paths are absent.
    """
    merged: dict[str, str] = dict(base_manifest)
    for path in ours_changed - conflict_paths:
        if path in ours_manifest:
            merged[path] = ours_manifest[path]
        else:
            merged.pop(path, None)
    for path in theirs_changed - conflict_paths:
        if path in theirs_manifest:
            merged[path] = theirs_manifest[path]
        else:
            merged.pop(path, None)
    return merged


# ---------------------------------------------------------------------------
# CRDT convergent join
# ---------------------------------------------------------------------------


def crdt_join_snapshots(
    plugin: MuseDomainPlugin,
    a_snapshot: dict[str, str],
    b_snapshot: dict[str, str],
    a_vclock: dict[str, int],
    b_vclock: dict[str, int],
    a_crdt_state: dict[str, str],
    b_crdt_state: dict[str, str],
    domain: str,
) -> MergeResult:
    """Convergent CRDT merge — always succeeds, no conflicts possible.

    Detects :class:`~muse.domain.CRDTPlugin` support via ``isinstance`` and
    delegates to ``plugin.join(a, b)``.  The returned :class:`~muse.domain.MergeResult`
    always has an empty ``conflicts`` list — the defining property of CRDT joins.

    This function is the CRDT entry point for the ``muse merge`` command.
    It is only called when ``DomainSchema.merge_mode == "crdt"`` AND the plugin
    passes the ``isinstance(plugin, CRDTPlugin)`` check.

    Args:
        plugin:       The loaded domain plugin instance.
        a_snapshot:   ``files`` mapping (path → content hash) for replica A.
        b_snapshot:   ``files`` mapping (path → content hash) for replica B.
        a_vclock:     Vector clock ``{agent_id: count}`` for replica A.
        b_vclock:     Vector clock ``{agent_id: count}`` for replica B.
        a_crdt_state: CRDT metadata hashes (path → blob hash) for replica A.
        b_crdt_state: CRDT metadata hashes (path → blob hash) for replica B.
        domain:       Domain name string (e.g. ``"midi"``).

    Returns:
        A :class:`~muse.domain.MergeResult` with the joined snapshot and an
        empty ``conflicts`` list.

    Raises:
        TypeError: When *plugin* does not implement the
                   :class:`~muse.domain.CRDTPlugin` protocol.
    """
    from muse.domain import CRDTPlugin, CRDTSnapshotManifest, MergeResult, StateSnapshot

    if not isinstance(plugin, CRDTPlugin):
        raise TypeError(
            f"crdt_join_snapshots: plugin {type(plugin).__name__!r} does not "
            "implement CRDTPlugin — cannot use CRDT join path."
        )

    a_crdt: CRDTSnapshotManifest = {
        "files": a_snapshot,
        "domain": domain,
        "vclock": a_vclock,
        "crdt_state": a_crdt_state,
        "schema_version": 1,
    }
    b_crdt: CRDTSnapshotManifest = {
        "files": b_snapshot,
        "domain": domain,
        "vclock": b_vclock,
        "crdt_state": b_crdt_state,
        "schema_version": 1,
    }

    result_crdt = plugin.join(a_crdt, b_crdt)
    plain_snapshot: StateSnapshot = plugin.from_crdt_state(result_crdt)

    return MergeResult(
        merged=plain_snapshot,
        conflicts=[],
        applied_strategies={},
    )


# ---------------------------------------------------------------------------
# File-based merge base finder
# ---------------------------------------------------------------------------


def find_merge_base(
    repo_root: pathlib.Path,
    commit_id_a: str,
    commit_id_b: str,
) -> str | None:
    """Find the Lowest Common Ancestor (LCA) of two commits.

    Uses BFS to collect all ancestors of *commit_id_a* (inclusive), then
    walks *commit_id_b*'s ancestor graph (BFS) until the first node found
    in *a*'s ancestor set is reached.

    Args:
        repo_root: The repository root directory.
        commit_id_a: First commit ID (e.g., current branch HEAD).
        commit_id_b: Second commit ID (e.g., target branch HEAD).

    Returns:
        The LCA commit ID, or ``None`` if the commits share no common ancestor.
    """
    from muse.core.errors import MuseCLIError
    from muse.core.store import read_commit

    _MAX_ANCESTORS = 50_000

    def _all_ancestors(start: str) -> set[str]:
        visited: set[str] = set()
        queue: deque[str] = deque([start])
        while queue:
            if len(visited) >= _MAX_ANCESTORS:
                raise MuseCLIError(
                    f"Ancestor graph exceeds {_MAX_ANCESTORS} commits during "
                    "merge-base search. The repository DAG may be malformed."
                )
            cid = queue.popleft()
            if cid in visited:
                continue
            visited.add(cid)
            commit = read_commit(repo_root, cid)
            if commit is None:
                continue
            if commit.parent_commit_id:
                queue.append(commit.parent_commit_id)
            if commit.parent2_commit_id:
                queue.append(commit.parent2_commit_id)
        return visited

    a_ancestors = _all_ancestors(commit_id_a)

    visited_b: set[str] = set()
    queue_b: deque[str] = deque([commit_id_b])
    while queue_b:
        if len(visited_b) >= _MAX_ANCESTORS:
            logger.warning(
                "⚠️ Ancestor graph exceeds %d commits during merge-base search — stopping",
                _MAX_ANCESTORS,
            )
            return None
        cid = queue_b.popleft()
        if cid in visited_b:
            continue
        visited_b.add(cid)
        if cid in a_ancestors:
            return cid
        commit = read_commit(repo_root, cid)
        if commit is None:
            continue
        if commit.parent_commit_id:
            queue_b.append(commit.parent_commit_id)
        if commit.parent2_commit_id:
            queue_b.append(commit.parent2_commit_id)

    return None

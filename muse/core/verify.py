"""Whole-repository integrity verification for ``muse verify``.

Walks every reachable commit from every branch ref and performs three tiers
of integrity checking:

1. **Ref integrity** — every branch ref file points to an existing commit.
2. **Commit → snapshot integrity** — every commit's ``snapshot_id`` resolves.
3. **Snapshot → object integrity** — every object path in every manifest exists.
4. **Object hash integrity** — re-hashes each object file and compares against
   its declared ID (the SHA-256 content address).  Optional; controlled by the
   ``check_objects`` flag.  Skipped when ``check_objects=False`` for speed.

The walk is BFS across all reachable commits from all branches; each commit is
checked exactly once even when reachable from multiple branches.

This module is domain-agnostic: it only reads from ``.muse/commits/``,
``.muse/snapshots/``, ``.muse/objects/``, and ``.muse/refs/``.  No plugin
loading is required.
"""

from __future__ import annotations

import hashlib
import logging
import pathlib
from collections import deque
from typing import Literal, TypedDict

from muse.core.object_store import object_path
from muse.core.store import get_head_commit_id, read_commit, read_snapshot

logger = logging.getLogger(__name__)

_CHUNK_SIZE = 65_536
_MAX_COMMITS = 500_000


class VerifyFailure(TypedDict):
    """A single integrity failure detected during ``muse verify``."""

    kind: Literal["ref", "commit", "snapshot", "object"]
    id: str
    error: str


class VerifyResult(TypedDict):
    """Aggregate result of a full repository integrity walk."""

    refs_checked: int
    commits_checked: int
    snapshots_checked: int
    objects_checked: int
    all_ok: bool
    failures: list[VerifyFailure]


def _branch_refs(root: pathlib.Path) -> list[tuple[str, str]]:
    """Return ``[(branch_name, commit_id)]`` for all branch ref files."""
    heads_dir = root / ".muse" / "refs" / "heads"
    if not heads_dir.exists():
        return []
    refs: list[tuple[str, str]] = []
    for ref_file in sorted(heads_dir.rglob("*")):
        if ref_file.is_file():
            branch = str(ref_file.relative_to(heads_dir).as_posix())
            raw = ref_file.read_text(encoding="utf-8").strip()
            if raw:
                refs.append((branch, raw))
    return refs


def _rehash_object(path: pathlib.Path) -> str:
    """Re-compute SHA-256 of the object file at *path*."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK_SIZE), b""):
            h.update(chunk)
    return h.hexdigest()


def run_verify(
    root: pathlib.Path,
    *,
    check_objects: bool = True,
) -> VerifyResult:
    """Perform a full repository integrity walk.

    Args:
        root:          Repository root (the directory containing ``.muse/``).
        check_objects: When ``True`` (the default), every object file is
                       re-hashed and its digest is compared against its
                       declared content address.  Pass ``False`` for a faster
                       existence-only check.

    Returns:
        A :class:`VerifyResult` summarising what was checked and any failures.
    """
    failures: list[VerifyFailure] = []
    refs_checked = 0
    commits_checked = 0
    snapshots_checked = 0
    objects_checked = 0

    # Track which objects and snapshots we've already verified to avoid
    # re-checking the same content multiple times.
    verified_snapshots: set[str] = set()
    verified_objects: set[str] = set()

    # Phase 1: enumerate all branch refs and collect tip commit IDs.
    branch_refs = _branch_refs(root)
    tip_commit_ids: list[str] = []

    for branch, commit_id in branch_refs:
        refs_checked += 1
        # Validate the ref format (64 hex chars).
        if len(commit_id) != 64 or not all(c in "0123456789abcdef" for c in commit_id):
            failures.append(VerifyFailure(
                kind="ref",
                id=branch,
                error=f"invalid commit ID in ref: {commit_id!r}",
            ))
            continue
        tip_commit_ids.append(commit_id)

    # Phase 2: BFS walk of the commit DAG.
    visited: set[str] = set()
    queue: deque[str] = deque(tip_commit_ids)

    while queue:
        cid = queue.popleft()
        if cid in visited:
            continue
        visited.add(cid)

        if len(visited) > _MAX_COMMITS:
            logger.warning("⚠️ verify: reached %d-commit limit — stopping early", _MAX_COMMITS)
            break

        commit = read_commit(root, cid)
        if commit is None:
            failures.append(VerifyFailure(
                kind="commit",
                id=cid,
                error="commit file missing or unreadable",
            ))
            continue

        commits_checked += 1

        # Phase 3: check snapshot.
        snap_id = commit.snapshot_id
        if snap_id not in verified_snapshots:
            verified_snapshots.add(snap_id)
            snap = read_snapshot(root, snap_id)
            if snap is None:
                failures.append(VerifyFailure(
                    kind="snapshot",
                    id=snap_id,
                    error=f"snapshot missing (referenced by commit {cid[:12]})",
                ))
            else:
                snapshots_checked += 1

                # Phase 4: check objects referenced by this snapshot.
                for rel_path, obj_id in snap.manifest.items():
                    if obj_id in verified_objects:
                        continue
                    verified_objects.add(obj_id)

                    obj_file = object_path(root, obj_id)
                    if not obj_file.exists():
                        failures.append(VerifyFailure(
                            kind="object",
                            id=obj_id,
                            error=f"object file missing (path={rel_path})",
                        ))
                        continue

                    objects_checked += 1

                    if check_objects:
                        actual = _rehash_object(obj_file)
                        if actual != obj_id:
                            failures.append(VerifyFailure(
                                kind="object",
                                id=obj_id,
                                error=(
                                    f"hash mismatch: expected {obj_id[:12]} "
                                    f"got {actual[:12]} — data corruption detected"
                                ),
                            ))

        # Enqueue parents for the BFS walk.
        if commit.parent_commit_id:
            queue.append(commit.parent_commit_id)
        if commit.parent2_commit_id:
            queue.append(commit.parent2_commit_id)

    return VerifyResult(
        refs_checked=refs_checked,
        commits_checked=commits_checked,
        snapshots_checked=snapshots_checked,
        objects_checked=objects_checked,
        all_ok=len(failures) == 0,
        failures=failures,
    )

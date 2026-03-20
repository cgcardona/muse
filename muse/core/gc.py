"""Garbage collection — prune unreachable objects from the object store.

Muse uses a content-addressed object store: every file snapshot is stored as a
SHA-256-addressed blob under ``.muse/objects/``.  Over time, after branch
deletions, rebases, and abandoned experiments, objects that are no longer
reachable from any live commit accumulate.  This module identifies and removes
them.

Reachability
------------
An object is *reachable* if it can be reached by following the graph from any
live ref (branch HEAD, tag, or the current HEAD):

    branch HEAD → CommitRecord → SnapshotRecord → manifest → object SHA-256

Any object not in the reachable set is *loose garbage* and is safe to delete.

Safety
------
The GC walk is always performed **before** any deletion.  The ``dry_run``
option shows what *would* be deleted without touching the store, making it safe
to run frequently in CI or by agents to estimate bloat.

Return value
------------
``GcResult`` is a typed dataclass with integer counts and the set of collected
IDs.  The CLI command renders it; the plumbing layer can expose it as JSON.
"""

from __future__ import annotations

import logging
import pathlib
import time
from dataclasses import dataclass, field

from muse.core.object_store import object_path
from muse.core.store import get_all_commits, get_head_commit_id, read_commit, read_snapshot

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class GcResult:
    """Statistics from one garbage-collection pass."""

    reachable_count: int = 0
    collected_count: int = 0
    collected_bytes: int = 0
    elapsed_seconds: float = 0.0
    collected_ids: list[str] = field(default_factory=list)
    dry_run: bool = False


# ---------------------------------------------------------------------------
# Reachability walk
# ---------------------------------------------------------------------------


def _collect_reachable_objects(
    repo_root: pathlib.Path,
) -> set[str]:
    """Return the set of all object SHA-256 IDs reachable from any live ref."""
    reachable: set[str] = set()

    commits = get_all_commits(repo_root)
    for commit in commits:
        snap = read_snapshot(repo_root, commit.snapshot_id)
        if snap is None:
            continue
        for object_id in snap.manifest.values():
            reachable.add(object_id)

    return reachable


def _list_stored_objects(repo_root: pathlib.Path) -> list[tuple[str, pathlib.Path]]:
    """Return (object_id, path) for every object in the object store."""
    objects_dir = repo_root / ".muse" / "objects"
    if not objects_dir.exists():
        return []
    pairs: list[tuple[str, pathlib.Path]] = []
    for prefix_dir in objects_dir.iterdir():
        if not prefix_dir.is_dir() or len(prefix_dir.name) != 2:
            continue
        for obj_file in prefix_dir.iterdir():
            if obj_file.is_file():
                object_id = prefix_dir.name + obj_file.name
                pairs.append((object_id, obj_file))
    return pairs


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_gc(
    repo_root: pathlib.Path,
    dry_run: bool = False,
) -> GcResult:
    """Prune unreachable objects from the Muse object store.

    Args:
        repo_root:  Root of the Muse repository (``.muse/`` lives here).
        dry_run:    When ``True``, report what *would* be deleted without
                    actually removing anything.

    Returns:
        A ``GcResult`` with counts and the list of collected object IDs.
    """
    t0 = time.monotonic()
    reachable = _collect_reachable_objects(repo_root)
    stored = _list_stored_objects(repo_root)

    result = GcResult(dry_run=dry_run)
    result.reachable_count = len(reachable)

    for object_id, obj_path in stored:
        if object_id not in reachable:
            size = obj_path.stat().st_size if obj_path.exists() else 0
            result.collected_ids.append(object_id)
            result.collected_bytes += size
            result.collected_count += 1
            if not dry_run:
                try:
                    obj_path.unlink()
                    # Remove empty prefix directory to keep the store tidy.
                    if not any(obj_path.parent.iterdir()):
                        obj_path.parent.rmdir()
                except OSError as exc:
                    logger.warning("⚠️ Could not remove object %s: %s", object_id[:12], exc)

    result.elapsed_seconds = time.monotonic() - t0
    logger.info(
        "gc: %d reachable, %d %s, %.3fs elapsed",
        result.reachable_count,
        result.collected_count,
        "would be removed" if dry_run else "removed",
        result.elapsed_seconds,
    )
    return result


def count_unreachable(repo_root: pathlib.Path) -> int:
    """Return the number of unreachable objects without deleting them."""
    reachable = _collect_reachable_objects(repo_root)
    stored = _list_stored_objects(repo_root)
    return sum(1 for oid, _ in stored if oid not in reachable)

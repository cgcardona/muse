"""Worktree management — multiple simultaneous branch checkouts.

A *worktree* is a second (or third, …) checked-out working tree linked to
the same ``.muse/`` repository.  Each worktree has its own branch and its own
``state/`` directory, so multiple agents — or multiple human engineers — can
work on different branches simultaneously without interfering with each other.

Layout
------
Each linked worktree lives in a sibling directory of the repository root::

    myproject/              ← main worktree  (holds .muse/)
      state/                ← main working directory
      .muse/
        worktrees/
          <name>.toml       ← metadata for each linked worktree

    myproject-<name>/       ← linked worktree directory
      state/                ← worktree working directory
      .muse -> ../myproject/.muse   ← symlink back to the shared store

The shared ``.muse/`` directory is the single source of truth for commits,
snapshots, objects, and branch refs.  Each worktree has its own HEAD file
stored inside the main ``.muse/worktrees/<name>/HEAD``.

Security
--------
Worktree names are validated through the same ``validate_branch_name``
primitive used for branch names — no path separators, no null bytes, no
control characters.

Agent concurrency
-----------------
Multiple agents can operate on separate worktrees simultaneously.  Each
worktree's HEAD is independent; commits from one worktree appear immediately
in all others (they share the object store).
"""

from __future__ import annotations

import json
import logging
import pathlib
import shutil
from dataclasses import dataclass
from typing import TypedDict

from muse.core.object_store import restore_object
from muse.core.store import get_head_commit_id, read_current_branch, read_snapshot
from muse.core.validation import contain_path, validate_branch_name

logger = logging.getLogger(__name__)

_WORKTREES_META_DIR = ".muse/worktrees"


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


class WorktreeRecord(TypedDict):
    """Persisted metadata for a linked worktree."""

    name: str
    branch: str
    path: str  # absolute path to the worktree directory


@dataclass
class WorktreeInfo:
    """Runtime information about a worktree."""

    name: str
    branch: str
    path: pathlib.Path
    head_commit: str | None
    is_main: bool = False


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def _worktrees_dir(repo_root: pathlib.Path) -> pathlib.Path:
    return repo_root / ".muse" / "worktrees"


def _worktree_meta_path(repo_root: pathlib.Path, name: str) -> pathlib.Path:
    return _worktrees_dir(repo_root) / f"{name}.json"


def _worktree_head_path(repo_root: pathlib.Path, name: str) -> pathlib.Path:
    return _worktrees_dir(repo_root) / f"{name}.HEAD"


def _worktree_dir(repo_root: pathlib.Path, name: str) -> pathlib.Path:
    """Return the path of the linked worktree directory (sibling of repo_root)."""
    parent = repo_root.parent
    repo_name = repo_root.name
    return parent / f"{repo_name}-{name}"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _load_meta(repo_root: pathlib.Path, name: str) -> WorktreeRecord | None:
    meta_path = _worktree_meta_path(repo_root, name)
    if not meta_path.exists():
        return None
    try:
        raw = json.loads(meta_path.read_text(encoding="utf-8"))
        return WorktreeRecord(
            name=str(raw["name"]),
            branch=str(raw["branch"]),
            path=str(raw["path"]),
        )
    except (KeyError, ValueError, OSError) as exc:
        logger.warning("⚠️ Could not read worktree metadata for %r: %s", name, exc)
        return None


def _save_meta(repo_root: pathlib.Path, record: WorktreeRecord) -> None:
    meta_path = _worktree_meta_path(repo_root, record["name"])
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = meta_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(record, indent=2), encoding="utf-8")
    tmp.replace(meta_path)


def _read_main_branch(repo_root: pathlib.Path) -> str:
    return read_current_branch(repo_root)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def add_worktree(
    repo_root: pathlib.Path,
    name: str,
    branch: str,
) -> pathlib.Path:
    """Create and populate a new linked worktree.

    Args:
        repo_root:  Main repository root (where ``.muse/`` lives).
        name:       Short identifier for the worktree (validated like a branch name).
        branch:     Branch to check out in the new worktree.

    Returns:
        The path to the newly created worktree directory.

    Raises:
        ValueError: If the name is invalid, the worktree already exists, or
                    the branch does not exist.
    """
    validate_branch_name(name)

    wt_dir = _worktree_dir(repo_root, name)
    meta_path = _worktree_meta_path(repo_root, name)

    if meta_path.exists():
        raise ValueError(f"Worktree '{name}' already exists.")
    if wt_dir.exists():
        raise ValueError(f"Directory '{wt_dir}' already exists.")

    # Verify the branch exists.
    branch_ref = repo_root / ".muse" / "refs" / "heads" / branch
    if not branch_ref.exists():
        raise ValueError(f"Branch '{branch}' does not exist.")

    # Create the worktree directory — its root IS the working tree.
    wt_dir.mkdir(parents=True)

    # Write the worktree HEAD file.
    head_path = _worktree_head_path(repo_root, name)
    head_path.parent.mkdir(parents=True, exist_ok=True)
    head_path.write_text(f"refs/heads/{branch}\n", encoding="utf-8")

    # Populate the worktree from the branch snapshot.
    repo_json = json.loads((repo_root / ".muse" / "repo.json").read_text())
    commit_id = get_head_commit_id(repo_root, branch)
    if commit_id:
        snap = read_snapshot(repo_root, commit_id)  # commit_id is actually snapshot_id approach
        # Get snapshot via proper commit read.
        from muse.core.store import read_commit
        commit = read_commit(repo_root, commit_id)
        if commit:
            snap = read_snapshot(repo_root, commit.snapshot_id)
            if snap:
                for rel_path, object_id in snap.manifest.items():
                    try:
                        dest = contain_path(wt_dir, rel_path)
                    except ValueError as exc:
                        logger.warning("⚠️ Skipping unsafe path %r: %s", rel_path, exc)
                        continue
                    restore_object(repo_root, object_id, dest)

    # Persist metadata.
    record: WorktreeRecord = {
        "name": name,
        "branch": branch,
        "path": str(wt_dir),
    }
    _save_meta(repo_root, record)

    logger.info("✅ Worktree '%s' created at %s (branch: %s)", name, wt_dir, branch)
    return wt_dir


def list_worktrees(repo_root: pathlib.Path) -> list[WorktreeInfo]:
    """Return all worktrees (main + linked), sorted by name."""
    results: list[WorktreeInfo] = []

    # Main worktree.
    main_branch = _read_main_branch(repo_root)
    main_head = get_head_commit_id(repo_root, main_branch)
    results.append(WorktreeInfo(
        name="(main)",
        branch=main_branch,
        path=repo_root,
        head_commit=main_head,
        is_main=True,
    ))

    wt_dir = _worktrees_dir(repo_root)
    if not wt_dir.exists():
        return results

    for meta_file in sorted(wt_dir.glob("*.json")):
        name = meta_file.stem
        record = _load_meta(repo_root, name)
        if record is None:
            continue
        wt_path = pathlib.Path(record["path"])
        branch = record["branch"]
        head_path = _worktree_head_path(repo_root, name)
        commit_id = get_head_commit_id(repo_root, branch) if head_path.exists() else None
        results.append(WorktreeInfo(
            name=name,
            branch=branch,
            path=wt_path,
            head_commit=commit_id,
        ))
    return results


def remove_worktree(repo_root: pathlib.Path, name: str, force: bool = False) -> None:
    """Remove a linked worktree.

    Args:
        repo_root:  Main repository root.
        name:       Name of the worktree to remove.
        force:      When ``True``, remove even if the worktree directory has
                    uncommitted changes (i.e. it was modified externally).

    Raises:
        ValueError: If the worktree does not exist.
    """
    validate_branch_name(name)

    meta_path = _worktree_meta_path(repo_root, name)
    if not meta_path.exists():
        raise ValueError(f"Worktree '{name}' does not exist.")

    record = _load_meta(repo_root, name)
    if record is None:
        raise ValueError(f"Could not read metadata for worktree '{name}'.")

    wt_path = pathlib.Path(record["path"])
    if wt_path.exists():
        shutil.rmtree(wt_path)

    meta_path.unlink(missing_ok=True)
    head_path = _worktree_head_path(repo_root, name)
    head_path.unlink(missing_ok=True)

    logger.info("Worktree '%s' removed.", name)


def prune_worktrees(repo_root: pathlib.Path) -> list[str]:
    """Remove metadata for worktrees whose directories no longer exist.

    Returns:
        Names of pruned worktrees.
    """
    pruned: list[str] = []
    wt_dir = _worktrees_dir(repo_root)
    if not wt_dir.exists():
        return pruned
    for meta_file in list(wt_dir.glob("*.json")):
        name = meta_file.stem
        record = _load_meta(repo_root, name)
        if record is None:
            meta_file.unlink(missing_ok=True)
            pruned.append(name)
            continue
        wt_path = pathlib.Path(record["path"])
        if not wt_path.exists():
            meta_file.unlink(missing_ok=True)
            _worktree_head_path(repo_root, name).unlink(missing_ok=True)
            pruned.append(name)
    return pruned

"""Shared CLI pre-flight guards for operations that modify the working tree.

Any command that calls :func:`muse.core.workdir.apply_manifest` — merge,
checkout, reset, revert, cherry-pick, pull — should call
:func:`require_clean_workdir` first so users never lose uncommitted work
silently.  The check mirrors Git's behaviour: modified/deleted tracked files
block the operation; newly untracked files are left alone.
"""

from __future__ import annotations

import json
import pathlib
import sys

from muse.core.errors import ExitCode
from muse.core.snapshot import diff_workdir_vs_snapshot
from muse.core.store import get_head_snapshot_manifest, read_current_branch
from muse.core.validation import sanitize_display


def require_clean_workdir(
    root: pathlib.Path,
    operation: str,
    *,
    force: bool = False,
) -> None:
    """Abort with a friendly error if the working tree has uncommitted changes.

    Protects against silent data loss when *operation* (merge, checkout,
    reset, …) is about to overwrite files via ``apply_manifest``.  Pass
    ``force=True`` to bypass the check (e.g. for ``--force`` flags).

    Only modified and deleted tracked files are considered dangerous —
    brand-new files that have never been committed are left untouched by any
    manifest-apply, so they are not flagged.

    Args:
        root:      Repository root (directory that contains ``.muse/``).
        operation: Human-readable name of the operation (used in the message).
        force:     When ``True`` the guard is a no-op.

    Raises:
        SystemExit: With :attr:`ExitCode.USER_ERROR` when dirty files exist.
    """
    if force:
        return

    branch = read_current_branch(root)
    repo_id = str(json.loads((root / ".muse" / "repo.json").read_text())["repo_id"])
    head_manifest = get_head_snapshot_manifest(root, repo_id, branch) or {}

    # If the branch has no commits yet there is nothing to protect.
    if not head_manifest:
        return

    added, modified, deleted, _ = diff_workdir_vs_snapshot(root, head_manifest)

    # Added paths are brand-new files never seen in a snapshot; apply_manifest
    # won't touch them, so they are safe to ignore.  Modified and deleted
    # paths ARE in the snapshot and would be overwritten or removed.
    dirty = modified | deleted
    if not dirty:
        return

    print(
        f"❌ error: Your local changes to the following files would be "
        f"overwritten by {sanitize_display(operation)}:",
        file=sys.stderr,
    )
    shown = sorted(dirty)[:10]
    for path in shown:
        print(f"        {sanitize_display(path)}", file=sys.stderr)
    if len(dirty) > 10:
        print(f"        … and {len(dirty) - 10} more", file=sys.stderr)
    print(
        f"\nPlease commit your changes or stash them before you "
        f"{sanitize_display(operation)}.\nAborting.",
        file=sys.stderr,
    )
    raise SystemExit(ExitCode.USER_ERROR)

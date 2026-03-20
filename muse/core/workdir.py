"""workdir.py — Working-tree restoration utilities.

The Muse working tree is the repository root (minus ``.muse/`` and other
hidden/ignored paths tracked by ``.museignore``).  These helpers surgically
apply a snapshot manifest to the working tree without ever destroying the
root directory itself.
"""

from __future__ import annotations

import logging
import pathlib

from muse.core.object_store import restore_object
from muse.core.snapshot import walk_workdir
from muse.core.validation import contain_path

logger = logging.getLogger(__name__)


def apply_manifest(root: pathlib.Path, target_manifest: dict[str, str]) -> None:
    """Surgically apply *target_manifest* to the working tree at *root*.

    Unlike a wipe-and-restore approach this function:

    1. Removes files currently visible to Muse that are absent from
       *target_manifest*.
    2. Restores every file listed in *target_manifest* from the object store,
       overwriting any existing content.

    The repository root and ``.muse/`` are never deleted.  Only files that
    were previously tracked (visible to ``walk_workdir``) are candidates for
    removal, so untracked files (hidden files, ignored paths) are left alone.

    Args:
        root:            Repository root — the directory that contains ``.muse/``.
        target_manifest: Mapping of POSIX-relative paths to SHA-256 object IDs
                         that the working tree should contain after this call.
    """
    current_files = set(walk_workdir(root).keys())
    target_files = set(target_manifest.keys())

    for rel_posix in current_files - target_files:
        fp = root / pathlib.Path(rel_posix)
        if fp.exists():
            fp.unlink()

    for rel_path, object_id in target_manifest.items():
        try:
            safe_dest = contain_path(root, rel_path)
        except ValueError as exc:
            logger.warning("⚠️ Skipping unsafe manifest path %r: %s", rel_path, exc)
            continue
        restore_object(root, object_id, safe_dest)

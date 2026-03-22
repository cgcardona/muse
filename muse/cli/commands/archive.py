"""``muse archive`` — export a snapshot as a portable archive.

Creates a ``tar.gz`` or ``zip`` archive from any historical snapshot —
HEAD by default.  The archive contains only the tracked files (the contents
of ``state/`` at that point in time), making it the canonical way to share
a specific version without exposing the ``.muse/`` internals.

Usage::

    muse archive                             # HEAD snapshot → archive.tar.gz
    muse archive --ref feat/audio            # branch tip
    muse archive --ref a1b2c3d4             # specific commit SHA prefix
    muse archive --format zip               # zip instead of tar.gz
    muse archive --output release-v1.0.zip  # custom output path
    muse archive --prefix myproject/        # add a directory prefix inside the archive

The archive is purely content — no Muse metadata (``.muse/``) is included.
This is intentional: archives are for *distribution*, not collaboration.
Use ``muse push`` / ``muse clone`` for distribution with full history.
"""

from __future__ import annotations

import argparse
import json
import logging
import pathlib
import sys
import tarfile
import zipfile


from muse.core.errors import ExitCode
from muse.core.object_store import object_path
from muse.core.repo import require_repo
from muse.core.store import get_head_commit_id, read_commit, read_current_branch, read_snapshot, resolve_commit_ref
from muse.core.validation import contain_path, sanitize_display

logger = logging.getLogger(__name__)

_FORMAT_CHOICES = {"tar.gz", "zip"}


def _safe_arcname(prefix: str, rel_path: str) -> str | None:
    """Build a safe archive entry name, guarding against zip-slip path traversal.

    Returns ``None`` if either *prefix* or *rel_path* contains ``..`` segments
    or absolute paths; the caller must skip those entries.
    """
    clean_prefix = prefix.rstrip("/").strip()
    if clean_prefix and ".." in clean_prefix.split("/"):
        return None
    resolved = pathlib.PurePosixPath(rel_path)
    if resolved.is_absolute() or ".." in resolved.parts:
        return None
    safe_rel = str(resolved)
    return (clean_prefix + "/" + safe_rel) if clean_prefix else safe_rel


def _read_repo_id(root: pathlib.Path) -> str:
    return str(json.loads((root / ".muse" / "repo.json").read_text())["repo_id"])


def _read_branch(root: pathlib.Path) -> str:
    return read_current_branch(root)


def _build_tar(
    root: pathlib.Path,
    manifest: dict[str, str],
    output_path: pathlib.Path,
    prefix: str,
) -> int:
    """Write a tar.gz archive from *manifest*; return file count.

    Each entry's archive name is validated to prevent zip-slip / tar-slip
    path traversal attacks.  Entries that would escape the archive root are
    silently skipped with a warning.
    """
    count = 0
    with tarfile.open(output_path, "w:gz") as tar:
        for rel_path, object_id in sorted(manifest.items()):
            arcname = _safe_arcname(prefix, rel_path)
            if arcname is None:
                logger.warning("⚠️ Skipping unsafe archive path: %s", rel_path)
                continue
            obj = object_path(root, object_id)
            if not obj.exists():
                logger.warning("⚠️ Missing object %s for %s — skipping", object_id[:12], rel_path)
                continue
            tar.add(str(obj), arcname=arcname, recursive=False)
            count += 1
    return count


def _build_zip(
    root: pathlib.Path,
    manifest: dict[str, str],
    output_path: pathlib.Path,
    prefix: str,
) -> int:
    """Write a zip archive from *manifest*; return file count.

    Each entry's archive name is validated to prevent zip-slip path traversal
    attacks.  Entries that would escape the archive root are skipped.
    """
    count = 0
    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for rel_path, object_id in sorted(manifest.items()):
            arcname = _safe_arcname(prefix, rel_path)
            if arcname is None:
                logger.warning("⚠️ Skipping unsafe archive path: %s", rel_path)
                continue
            obj = object_path(root, object_id)
            if not obj.exists():
                logger.warning("⚠️ Missing object %s for %s — skipping", object_id[:12], rel_path)
                continue
            zf.write(str(obj), arcname=arcname)
            count += 1
    return count


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the archive subcommand."""
    parser = subparsers.add_parser(
        "archive",
        help="Export any historical snapshot as a portable archive.",
        description=__doc__,
    )
    parser.add_argument(
        "--ref", default=None,
        help="Branch, tag, or commit SHA to archive (default: HEAD).",
    )
    parser.add_argument(
        "--format", default="tar.gz", dest="fmt",
        help="Archive format: tar.gz or zip (default: tar.gz).",
    )
    parser.add_argument(
        "--output", default=None,
        help="Output file path (default: <sha12>.<format>).",
    )
    parser.add_argument(
        "--prefix", default="",
        help="Directory prefix inside the archive (e.g. myproject/).",
    )
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> None:
    """Export any historical snapshot as a portable archive.

    The archive contains only tracked files — no ``.muse/`` metadata.  It is
    the canonical distribution format for a specific version.

    Examples::

        muse archive                            # HEAD → <sha12>.tar.gz
        muse archive --ref v1.0.0               # tag → v1.0.0.tar.gz
        muse archive --format zip --output dist/release.zip
        muse archive --prefix myproject/        # all files under myproject/
    """
    ref: str | None = args.ref
    fmt: str = args.fmt
    output: str | None = args.output
    prefix: str = args.prefix

    if fmt not in _FORMAT_CHOICES:
        print(f"❌ Unknown format '{sanitize_display(fmt)}'. Choose from: {', '.join(sorted(_FORMAT_CHOICES))}")
        raise SystemExit(ExitCode.USER_ERROR)

    # Validate prefix against traversal — _safe_arcname will also catch it per-entry,
    # but an early check gives the user a clear error message.
    clean_prefix = prefix.rstrip("/").strip()
    if clean_prefix and ".." in clean_prefix.split("/"):
        print(f"❌ --prefix must not contain '..' segments: {sanitize_display(prefix)}", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)

    root = require_repo()
    repo_id = _read_repo_id(root)
    branch = _read_branch(root)

    if ref is None:
        commit_id = get_head_commit_id(root, branch)
        if not commit_id:
            print("❌ No commits yet on this branch.")
            raise SystemExit(ExitCode.USER_ERROR)
        commit = read_commit(root, commit_id)
    else:
        commit = resolve_commit_ref(root, repo_id, branch, ref)

    if commit is None:
        print(f"❌ Ref '{sanitize_display(ref or 'HEAD')}' not found.")
        raise SystemExit(ExitCode.USER_ERROR)

    snapshot = read_snapshot(root, commit.snapshot_id)
    if snapshot is None:
        print(f"❌ Snapshot {commit.snapshot_id[:8]} not found.")
        raise SystemExit(ExitCode.INTERNAL_ERROR)

    short = commit.commit_id[:12]
    out_name = output or f"{short}.{fmt}"
    out_path = pathlib.Path(out_name)

    if fmt == "tar.gz":
        count = _build_tar(root, snapshot.manifest, out_path, prefix)
    else:
        count = _build_zip(root, snapshot.manifest, out_path, prefix)

    size_kb = out_path.stat().st_size / 1024 if out_path.exists() else 0
    print(
        f"✅ Archive: {out_path}  ({count} file(s), {size_kb:.1f} KiB)\n"
        f"   Commit:  {short}  {sanitize_display(commit.message)}"
    )

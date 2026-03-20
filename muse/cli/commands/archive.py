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

import io
import logging
import pathlib
import tarfile
import zipfile
from typing import Annotated, Literal

import typer

from muse.core.errors import ExitCode
from muse.core.object_store import object_path
from muse.core.repo import require_repo
from muse.core.store import get_head_commit_id, read_commit, read_snapshot, resolve_commit_ref
from muse.core.validation import contain_path, sanitize_display

logger = logging.getLogger(__name__)
app = typer.Typer(help="Export a snapshot as a portable tar.gz or zip archive.")

_FORMAT_CHOICES = {"tar.gz", "zip"}


def _read_repo_id(root: pathlib.Path) -> str:
    import json
    return str(json.loads((root / ".muse" / "repo.json").read_text())["repo_id"])


def _read_branch(root: pathlib.Path) -> str:
    head = (root / ".muse" / "HEAD").read_text().strip()
    return head.removeprefix("refs/heads/").strip()


def _build_tar(
    root: pathlib.Path,
    manifest: dict[str, str],
    output_path: pathlib.Path,
    prefix: str,
) -> int:
    """Write a tar.gz archive; return file count."""
    count = 0
    with tarfile.open(output_path, "w:gz") as tar:
        for rel_path, object_id in sorted(manifest.items()):
            obj = object_path(root, object_id)
            if not obj.exists():
                logger.warning("⚠️ Missing object %s for %s — skipping", object_id[:12], rel_path)
                continue
            arcname = (prefix.rstrip("/") + "/" + rel_path) if prefix else rel_path
            tar.add(str(obj), arcname=arcname, recursive=False)
            count += 1
    return count


def _build_zip(
    root: pathlib.Path,
    manifest: dict[str, str],
    output_path: pathlib.Path,
    prefix: str,
) -> int:
    """Write a zip archive; return file count."""
    count = 0
    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for rel_path, object_id in sorted(manifest.items()):
            obj = object_path(root, object_id)
            if not obj.exists():
                logger.warning("⚠️ Missing object %s for %s — skipping", object_id[:12], rel_path)
                continue
            arcname = (prefix.rstrip("/") + "/" + rel_path) if prefix else rel_path
            zf.write(str(obj), arcname=arcname)
            count += 1
    return count


@app.callback(invoke_without_command=True)
def archive(
    ref: Annotated[
        str | None,
        typer.Option("--ref", "-r", help="Branch name or commit SHA (default: HEAD)."),
    ] = None,
    fmt: Annotated[
        str,
        typer.Option("--format", "-f", help="Archive format: tar.gz or zip."),
    ] = "tar.gz",
    output: Annotated[
        str | None,
        typer.Option("--output", "-o", help="Output file path (default: <commit12>.<format>)."),
    ] = None,
    prefix: Annotated[
        str,
        typer.Option("--prefix", help="Add a directory prefix to all paths inside the archive."),
    ] = "",
) -> None:
    """Export any historical snapshot as a portable archive.

    The archive contains only tracked files — no ``.muse/`` metadata.  It is
    the canonical distribution format for a specific version.

    Examples::

        muse archive                            # HEAD → <sha12>.tar.gz
        muse archive --ref v1.0.0               # tag → v1.0.0.tar.gz
        muse archive --format zip --output dist/release.zip
        muse archive --prefix myproject/        # all files under myproject/
    """
    if fmt not in _FORMAT_CHOICES:
        typer.echo(f"❌ Unknown format '{sanitize_display(fmt)}'. Choose from: {', '.join(sorted(_FORMAT_CHOICES))}")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    root = require_repo()
    repo_id = _read_repo_id(root)
    branch = _read_branch(root)

    if ref is None:
        commit_id = get_head_commit_id(root, branch)
        if not commit_id:
            typer.echo("❌ No commits yet on this branch.")
            raise typer.Exit(code=ExitCode.USER_ERROR)
        commit = read_commit(root, commit_id)
    else:
        commit = resolve_commit_ref(root, repo_id, branch, ref)

    if commit is None:
        typer.echo(f"❌ Ref '{sanitize_display(ref or 'HEAD')}' not found.")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    snapshot = read_snapshot(root, commit.snapshot_id)
    if snapshot is None:
        typer.echo(f"❌ Snapshot {commit.snapshot_id[:8]} not found.")
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)

    short = commit.commit_id[:12]
    out_name = output or f"{short}.{fmt}"
    out_path = pathlib.Path(out_name)

    if fmt == "tar.gz":
        count = _build_tar(root, snapshot.manifest, out_path, prefix)
    else:
        count = _build_zip(root, snapshot.manifest, out_path, prefix)

    size_kb = out_path.stat().st_size / 1024 if out_path.exists() else 0
    typer.echo(
        f"✅ Archive: {out_path}  ({count} file(s), {size_kb:.1f} KiB)\n"
        f"   Commit:  {short}  {sanitize_display(commit.message)}"
    )

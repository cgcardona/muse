"""``muse snapshot`` — explicit snapshot management.

A snapshot is Muse's fundamental unit of state: a content-addressed,
immutable record mapping workspace-relative paths to their SHA-256 object IDs.
Every commit points to exactly one snapshot.

``muse snapshot`` makes snapshots a first-class operation — you can capture,
list, inspect, and export them independently of the commit workflow.  This is
especially useful for agents that want to checkpoint mid-work without creating
a formal commit.

Subcommands::

    muse snapshot create [-m <note>]              — capture current state
    muse snapshot list   [--limit N] [-f json]    — list all stored snapshots
    muse snapshot show   <id> [-f json|text]       — print snapshot manifest
    muse snapshot export <id> [-f tar.gz|zip] [-o file]  — export to archive

Exit codes::

    0 — success
    1 — snapshot not found, bad arguments
    3 — I/O error
"""

from __future__ import annotations

import argparse
import sys

import io
import json
import logging
import pathlib
import tarfile
import zipfile


from muse.core.errors import ExitCode
from muse.core.object_store import object_path, write_object_from_path
from muse.core.repo import require_repo
from muse.core.snapshot import compute_snapshot_id
from muse.core.store import SnapshotRecord, read_snapshot, write_snapshot
from muse.core.validation import sanitize_display
from muse.plugins.registry import resolve_plugin

logger = logging.getLogger(__name__)


_HEX_CHARS = frozenset("0123456789abcdef")


def _safe_arcname(prefix: str, rel_path: str) -> str | None:
    """Build an archive entry name that cannot escape the archive root (zip-slip guard).

    Returns ``None`` when *rel_path* resolves outside the intended prefix, in
    which case the caller should skip that entry.  ``prefix`` must not contain
    ``..`` segments; the caller is responsible for validating it.
    """
    # Normalise the prefix: strip trailing slashes, reject traversal.
    clean_prefix = prefix.rstrip("/").strip() if prefix else ""
    if ".." in clean_prefix.split("/"):
        return None

    # Normalise rel_path: must be purely relative, no .. segments.
    resolved = pathlib.PurePosixPath(rel_path)
    if resolved.is_absolute():
        return None
    parts = resolved.parts
    if ".." in parts:
        return None
    safe_rel = str(resolved)

    return (clean_prefix + "/" + safe_rel) if clean_prefix else safe_rel


def _validate_snapshot_id_prefix(snapshot_id: str) -> str:
    """Return a glob-safe prefix from *snapshot_id* (hex chars only, max 64)."""
    # Strip any non-hex characters so the prefix cannot inject glob metacharacters.
    clean = "".join(c for c in snapshot_id[:64] if c in _HEX_CHARS)
    return clean


def _list_all_snapshots(root: pathlib.Path) -> list[SnapshotRecord]:
    """Return all stored snapshots sorted newest-first."""
    snaps_dir = root / ".muse" / "snapshots"
    if not snaps_dir.exists():
        return []
    results: list[SnapshotRecord] = []
    for path in snaps_dir.glob("*.json"):
        try:
            record = SnapshotRecord.from_dict(
                json.loads(path.read_text(encoding="utf-8"))
            )
            results.append(record)
        except (json.JSONDecodeError, KeyError, TypeError):
            continue
    return sorted(results, key=lambda s: s.created_at, reverse=True)


def _build_tar(
    root: pathlib.Path,
    manifest: dict[str, str],
    output_path: pathlib.Path,
    prefix: str,
) -> int:
    """Write a tar.gz archive from *manifest*; return the number of files written."""
    count = 0
    with tarfile.open(output_path, "w:gz") as tar:
        for rel_path, object_id in sorted(manifest.items()):
            arcname = _safe_arcname(prefix, rel_path)
            if arcname is None:
                logger.warning("⚠️ Skipping unsafe path in manifest: %s", rel_path)
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
    """Write a zip archive from *manifest*; return the number of files written."""
    count = 0
    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for rel_path, object_id in sorted(manifest.items()):
            arcname = _safe_arcname(prefix, rel_path)
            if arcname is None:
                logger.warning("⚠️ Skipping unsafe path in manifest: %s", rel_path)
                continue
            obj = object_path(root, object_id)
            if not obj.exists():
                logger.warning("⚠️ Missing object %s for %s — skipping", object_id[:12], rel_path)
                continue
            zf.write(str(obj), arcname=arcname)
            count += 1
    return count


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the snapshot subcommand."""
    parser = subparsers.add_parser(
        "snapshot",
        help="Explicit snapshot management.",
        description=__doc__,
    )
    subs = parser.add_subparsers(dest="subcommand", metavar="SUBCOMMAND")
    subs.required = True

    create_p = subs.add_parser("create", help="Capture the current working tree as a snapshot without committing.")
    create_p.add_argument("-m", "--note", default="", metavar="NOTE", help="Optional note for the snapshot.")
    create_p.add_argument("--format", "-f", dest="fmt", default="text", choices=["text", "json"], help="Output format.")
    create_p.set_defaults(func=run_snapshot_create)

    list_p = subs.add_parser("list", help="List all stored snapshots, newest first.")
    list_p.add_argument("--limit", type=int, default=20, metavar="N", help="Maximum snapshots to show (default: 20).")
    list_p.add_argument("--format", "-f", dest="fmt", default="text", choices=["text", "json"], help="Output format.")
    list_p.set_defaults(func=run_snapshot_list)

    show_p = subs.add_parser("show", help="Print the full manifest of a snapshot.")
    show_p.add_argument("snapshot_id", metavar="ID", help="Snapshot ID (full or prefix).")
    show_p.add_argument("--format", "-f", dest="fmt", default="json", choices=["text", "json"], help="Output format.")
    show_p.set_defaults(func=run_snapshot_show)

    export_p = subs.add_parser("export", help="Export a snapshot as a portable archive.")
    export_p.add_argument("snapshot_id", metavar="ID", help="Snapshot ID (full or prefix).")
    export_p.add_argument("--format", "-f", dest="fmt", default="tar.gz", choices=["tar.gz", "zip"], help="Archive format.")
    export_p.add_argument("--output", "-o", default=None, metavar="FILE", help="Output file path.")
    export_p.add_argument("--prefix", default="", metavar="PREFIX", help="Path prefix inside the archive.")
    export_p.set_defaults(func=run_snapshot_export)


def run_snapshot_create(args: argparse.Namespace) -> None:
    """Capture the current working tree as a snapshot without committing.

    Hashes every tracked file, stores their content in the object store, and
    writes a ``SnapshotRecord`` to ``.muse/snapshots/``.  No commit is created
    — the snapshot is a standalone checkpoint.

    The snapshot ID is printed to ``stdout`` so it can be captured::

        SNAP=$(muse snapshot create -m "before refactor" -f json | jq -r .snapshot_id)
        muse snapshot export "$SNAP" --output before.tar.gz

    Examples::

        muse snapshot create
        muse snapshot create -m "WIP: verse melody"
        muse snapshot create --format json
    """
    note: str = args.note
    fmt: str = args.fmt

    if fmt not in {"text", "json"}:
        print(f"❌ Unknown --format '{sanitize_display(fmt)}'. Choose text or json.", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)

    root = require_repo()
    plugin = resolve_plugin(root)

    snap_result = plugin.snapshot(root)
    manifest: dict[str, str] = snap_result["files"]

    # Store every object file.
    for rel_path, object_id in manifest.items():
        src = root / rel_path
        if src.exists():
            try:
                write_object_from_path(root, object_id, src)
            except (ValueError, OSError) as exc:
                logger.warning("⚠️ Could not store %s: %s", rel_path, exc)

    snapshot_id = compute_snapshot_id(manifest)
    record = SnapshotRecord(snapshot_id=snapshot_id, manifest=manifest)
    write_snapshot(root, record)

    if fmt == "json":
        print(json.dumps({
            "snapshot_id": snapshot_id,
            "file_count": len(manifest),
            "note": note,
            "created_at": record.created_at.isoformat(),
        }))
    else:
        print(f"Snapshot {snapshot_id[:12]}  ({len(manifest)} file(s))")
        if note:
            print(f"Note: {sanitize_display(note)}")


def run_snapshot_list(args: argparse.Namespace) -> None:
    """List all stored snapshots, newest first.

    Examples::

        muse snapshot list
        muse snapshot list --limit 5
        muse snapshot list --format json
    """
    limit: int = args.limit
    fmt: str = args.fmt

    if fmt not in {"text", "json"}:
        print(f"❌ Unknown --format '{sanitize_display(fmt)}'. Choose text or json.", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)

    root = require_repo()
    snapshots = _list_all_snapshots(root)

    if limit:
        snapshots = snapshots[:limit]

    if not snapshots:
        if fmt == "json":
            print("[]")
        else:
            print("No snapshots found.")
        return

    if fmt == "json":
        print(json.dumps([
            {
                "snapshot_id": s.snapshot_id,
                "file_count": len(s.manifest),
                "created_at": s.created_at.isoformat(),
            }
            for s in snapshots
        ], indent=2))
    else:
        for s in snapshots:
            when = s.created_at.strftime("%Y-%m-%d %H:%M:%S UTC")
            print(f"{s.snapshot_id[:12]}  {when}  {len(s.manifest)} file(s)")


def run_snapshot_show(args: argparse.Namespace) -> None:
    """Print the full manifest of a snapshot.

    Examples::

        muse snapshot show abc123
        muse snapshot show abc123 --format text
    """
    snapshot_id: str = args.snapshot_id
    fmt: str = args.fmt

    if fmt not in {"text", "json"}:
        print(f"❌ Unknown --format '{sanitize_display(fmt)}'. Choose text or json.", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)

    root = require_repo()

    # Try full ID first, then prefix scan with a glob-safe prefix.
    snap = read_snapshot(root, snapshot_id)
    if snap is None:
        snaps_dir = root / ".muse" / "snapshots"
        safe_prefix = _validate_snapshot_id_prefix(snapshot_id)
        for p in snaps_dir.glob(f"{safe_prefix}*.json"):
            try:
                snap = SnapshotRecord.from_dict(
                    json.loads(p.read_text(encoding="utf-8"))
                )
                break
            except (json.JSONDecodeError, KeyError, TypeError):
                continue

    if snap is None:
        print(f"❌ Snapshot '{sanitize_display(snapshot_id)}' not found.", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)

    if fmt == "json":
        print(json.dumps({
            "snapshot_id": snap.snapshot_id,
            "created_at": snap.created_at.isoformat(),
            "file_count": len(snap.manifest),
            "manifest": dict(sorted(snap.manifest.items())),
        }, indent=2))
    else:
        print(f"snapshot_id: {snap.snapshot_id}")
        print(f"created_at:  {snap.created_at.isoformat()}")
        print(f"files ({len(snap.manifest)}):")
        for rel_path, obj_id in sorted(snap.manifest.items()):
            print(f"  {obj_id[:12]}  {sanitize_display(rel_path)}")


def run_snapshot_export(args: argparse.Namespace) -> None:
    """Export a snapshot as a portable tar.gz or zip archive.

    The archive contains only tracked files — no ``.muse/`` metadata.

    Examples::

        muse snapshot export abc123
        muse snapshot export abc123 --format zip --output release.zip
        muse snapshot export abc123 --prefix myproject/
    """
    snapshot_id: str = args.snapshot_id
    fmt: str = args.fmt
    output: str | None = args.output
    prefix: str = args.prefix

    if fmt not in {"tar.gz", "zip"}:
        print(f"❌ Unknown --format '{sanitize_display(fmt)}'. Choose tar.gz or zip.", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)

    root = require_repo()

    snap = read_snapshot(root, snapshot_id)
    if snap is None:
        snaps_dir = root / ".muse" / "snapshots"
        safe_prefix = _validate_snapshot_id_prefix(snapshot_id)
        for p in snaps_dir.glob(f"{safe_prefix}*.json"):
            try:
                snap = SnapshotRecord.from_dict(
                    json.loads(p.read_text(encoding="utf-8"))
                )
                break
            except (json.JSONDecodeError, KeyError, TypeError):
                continue

    if snap is None:
        print(f"❌ Snapshot '{sanitize_display(snapshot_id)}' not found.", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)

    short = snap.snapshot_id[:12]
    out_name = output or f"{short}.{fmt}"
    out_path = pathlib.Path(out_name)

    if fmt == "tar.gz":
        count = _build_tar(root, snap.manifest, out_path, prefix)
    else:
        count = _build_zip(root, snap.manifest, out_path, prefix)

    size_kb = out_path.stat().st_size / 1024 if out_path.exists() else 0
    print(
        f"✅ Archive: {out_path}  ({count} file(s), {size_kb:.1f} KiB)\n"
        f"   Snapshot: {short}"
    )

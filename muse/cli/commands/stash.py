"""muse stash — temporarily shelve uncommitted changes.

Saves the current working tree to ``.muse/stash.json`` and restores
the HEAD snapshot to the working tree.

Usage::

    muse stash           — save current changes and restore HEAD
    muse stash pop       — restore the most recent stash
    muse stash list      — list all stash entries
    muse stash drop      — discard the most recent stash
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import pathlib
import tempfile
from typing import TypedDict

import typer

from muse.core.errors import ExitCode
from muse.core.object_store import write_object_from_path
from muse.core.repo import require_repo
from muse.core.snapshot import compute_snapshot_id
from muse.core.store import get_head_snapshot_manifest, read_current_branch, read_snapshot
from muse.core.validation import sanitize_display
from muse.core.workdir import apply_manifest
from muse.plugins.registry import resolve_plugin

_STASH_MAX_BYTES = 64 * 1024 * 1024  # 64 MiB guard against huge stash files

logger = logging.getLogger(__name__)

app = typer.Typer()

_STASH_FILE = ".muse/stash.json"



class StashEntry(TypedDict):
    """A single entry in the stash stack."""

    snapshot_id: str
    manifest: dict[str, str]
    branch: str
    stashed_at: str


def _read_branch(root: pathlib.Path) -> str:
    return read_current_branch(root)


def _read_repo_id(root: pathlib.Path) -> str:
    return str(json.loads((root / ".muse" / "repo.json").read_text())["repo_id"])


def _load_stash(root: pathlib.Path) -> list[StashEntry]:
    stash_file = root / _STASH_FILE
    if not stash_file.exists():
        return []
    # Guard against unreasonably large stash files to prevent memory exhaustion.
    stat = stash_file.stat()
    if stat.st_size > _STASH_MAX_BYTES:
        logger.warning("⚠️ stash.json exceeds size limit (%d bytes) — ignoring", stat.st_size)
        return []
    raw = json.loads(stash_file.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        logger.warning("⚠️ stash.json has unexpected structure — ignoring")
        return []
    entries: list[StashEntry] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        manifest = item.get("manifest")
        if not isinstance(manifest, dict):
            continue
        # Validate that manifest values are strings (object IDs).
        safe_manifest: dict[str, str] = {
            k: v for k, v in manifest.items()
            if isinstance(k, str) and isinstance(v, str)
        }
        entries.append(StashEntry(
            snapshot_id=str(item.get("snapshot_id", "")),
            manifest=safe_manifest,
            branch=str(item.get("branch", "")),
            stashed_at=str(item.get("stashed_at", "")),
        ))
    return entries


def _save_stash(root: pathlib.Path, stash: list[StashEntry]) -> None:
    """Write stash atomically via a temp file + rename to survive crashes."""
    target = root / _STASH_FILE
    payload = json.dumps(stash, indent=2, ensure_ascii=False)
    fd, tmp_path = tempfile.mkstemp(dir=target.parent, prefix=".stash_tmp_", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
        os.replace(tmp_path, target)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


@app.callback(invoke_without_command=True)
def stash(
    ctx: typer.Context,
    fmt: str = typer.Option("text", "--format", "-f", help="Output format: text or json."),
) -> None:
    """Save current state/ changes and restore HEAD.

    Agents should pass ``--format json`` to receive ``{snapshot_id, branch,
    stashed_at, stash_size}`` rather than human-readable text.
    """
    if ctx.invoked_subcommand is not None:
        return
    if fmt not in ("text", "json"):
        typer.echo(f"❌ Unknown --format '{sanitize_display(fmt)}'. Choose text or json.", err=True)
        raise typer.Exit(code=ExitCode.USER_ERROR)
    root = require_repo()
    repo_id = _read_repo_id(root)
    branch = _read_branch(root)
    plugin = resolve_plugin(root)
    manifest = plugin.snapshot(root)["files"]
    if not manifest:
        if fmt == "json":
            typer.echo(json.dumps({"status": "nothing_to_stash"}))
        else:
            typer.echo("Nothing to stash.")
        return

    snapshot_id = compute_snapshot_id(manifest)
    for rel_path, object_id in manifest.items():
        write_object_from_path(root, object_id, root / rel_path)

    stashed_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    stash_entry = StashEntry(
        snapshot_id=snapshot_id,
        manifest=manifest,
        branch=branch,
        stashed_at=stashed_at,
    )
    entries = _load_stash(root)
    entries.insert(0, stash_entry)
    _save_stash(root, entries)

    head_manifest = get_head_snapshot_manifest(root, repo_id, branch) or {}
    apply_manifest(root, head_manifest)

    if fmt == "json":
        typer.echo(json.dumps({
            "snapshot_id": snapshot_id,
            "branch": branch,
            "stashed_at": stashed_at,
            "stash_size": len(entries),
        }))
    else:
        typer.echo(f"Saved working directory (stash@{{0}})")


@app.command("pop")
def stash_pop(
    fmt: str = typer.Option("text", "--format", "-f", help="Output format: text or json."),
) -> None:
    """Restore the most recent stash.

    Agents should pass ``--format json`` to receive ``{snapshot_id, branch,
    stashed_at}`` rather than human-readable text.
    """
    if fmt not in ("text", "json"):
        typer.echo(f"❌ Unknown --format '{sanitize_display(fmt)}'. Choose text or json.", err=True)
        raise typer.Exit(code=ExitCode.USER_ERROR)
    root = require_repo()
    entries = _load_stash(root)
    if not entries:
        if fmt == "json":
            typer.echo(json.dumps({"error": "no_stash_entries"}))
        else:
            typer.echo("No stash entries.")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    entry = entries.pop(0)
    _save_stash(root, entries)

    apply_manifest(root, entry["manifest"])
    if fmt == "json":
        typer.echo(json.dumps({
            "snapshot_id": entry["snapshot_id"],
            "branch": entry["branch"],
            "stashed_at": entry["stashed_at"],
        }))
    else:
        typer.echo(f"Restored stash@{{0}} (branch: {sanitize_display(entry['branch'])})")


@app.command("list")
def stash_list(
    fmt: str = typer.Option("text", "--format", "-f", help="Output format: text or json."),
) -> None:
    """List all stash entries.

    Agents should pass ``--format json`` to receive a JSON array of
    ``{index, snapshot_id, branch, stashed_at}`` objects.
    """
    if fmt not in ("text", "json"):
        typer.echo(f"❌ Unknown --format '{sanitize_display(fmt)}'. Choose text or json.", err=True)
        raise typer.Exit(code=ExitCode.USER_ERROR)
    root = require_repo()
    entries = _load_stash(root)
    if fmt == "json":
        typer.echo(json.dumps([{
            "index": i,
            "snapshot_id": e["snapshot_id"],
            "branch": e["branch"],
            "stashed_at": e["stashed_at"],
        } for i, e in enumerate(entries)]))
        return
    if not entries:
        typer.echo("No stash entries.")
        return
    for i, entry in enumerate(entries):
        typer.echo(f"stash@{{{i}}}: WIP on {sanitize_display(entry['branch'])} — {sanitize_display(entry['stashed_at'])}")


@app.command("drop")
def stash_drop(
    fmt: str = typer.Option("text", "--format", "-f", help="Output format: text or json."),
) -> None:
    """Discard the most recent stash entry.

    Agents should pass ``--format json`` to receive ``{status, stash_size}``
    rather than human-readable text.
    """
    if fmt not in ("text", "json"):
        typer.echo(f"❌ Unknown --format '{sanitize_display(fmt)}'. Choose text or json.", err=True)
        raise typer.Exit(code=ExitCode.USER_ERROR)
    root = require_repo()
    entries = _load_stash(root)
    if not entries:
        if fmt == "json":
            typer.echo(json.dumps({"error": "no_stash_entries"}))
        else:
            typer.echo("No stash entries.")
        raise typer.Exit(code=ExitCode.USER_ERROR)
    entries.pop(0)
    _save_stash(root, entries)
    if fmt == "json":
        typer.echo(json.dumps({"status": "dropped", "stash_size": len(entries)}))
    else:
        typer.echo("Dropped stash@{0}")

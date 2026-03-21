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
import pathlib
from typing import TypedDict

import typer

from muse.core.errors import ExitCode
from muse.core.object_store import write_object_from_path
from muse.core.repo import require_repo
from muse.core.snapshot import compute_snapshot_id
from muse.core.store import get_head_snapshot_manifest, read_current_branch, read_snapshot
from muse.core.workdir import apply_manifest
from muse.plugins.registry import resolve_plugin

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
    raw = json.loads(stash_file.read_text())
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
    (root / _STASH_FILE).write_text(json.dumps(stash, indent=2))


@app.callback(invoke_without_command=True)
def stash(ctx: typer.Context) -> None:
    """Save current state/ changes and restore HEAD."""
    if ctx.invoked_subcommand is not None:
        return
    root = require_repo()
    repo_id = _read_repo_id(root)
    branch = _read_branch(root)
    plugin = resolve_plugin(root)
    manifest = plugin.snapshot(root)["files"]
    if not manifest:
        typer.echo("Nothing to stash.")
        return

    snapshot_id = compute_snapshot_id(manifest)
    for rel_path, object_id in manifest.items():
        write_object_from_path(root, object_id, root / rel_path)

    stash_entry = StashEntry(
        snapshot_id=snapshot_id,
        manifest=manifest,
        branch=branch,
        stashed_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
    )
    entries = _load_stash(root)
    entries.insert(0, stash_entry)
    _save_stash(root, entries)

    head_manifest = get_head_snapshot_manifest(root, repo_id, branch) or {}
    apply_manifest(root, head_manifest)

    typer.echo(f"Saved working directory (stash@{{0}})")


@app.command("pop")
def stash_pop() -> None:
    """Restore the most recent stash."""
    root = require_repo()
    entries = _load_stash(root)
    if not entries:
        typer.echo("No stash entries.")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    entry = entries.pop(0)
    _save_stash(root, entries)

    apply_manifest(root, entry["manifest"])
    typer.echo(f"Restored stash@{{0}} (branch: {entry['branch']})")


@app.command("list")
def stash_list() -> None:
    """List all stash entries."""
    root = require_repo()
    entries = _load_stash(root)
    if not entries:
        typer.echo("No stash entries.")
        return
    for i, entry in enumerate(entries):
        typer.echo(f"stash@{{{i}}}: WIP on {entry['branch']} — {entry['stashed_at']}")


@app.command("drop")
def stash_drop() -> None:
    """Discard the most recent stash entry."""
    root = require_repo()
    entries = _load_stash(root)
    if not entries:
        typer.echo("No stash entries.")
        raise typer.Exit(code=ExitCode.USER_ERROR)
    entries.pop(0)
    _save_stash(root, entries)
    typer.echo("Dropped stash@{0}")

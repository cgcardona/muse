"""muse stash — temporarily shelve uncommitted changes.

Saves the current muse-work/ state to ``.muse/stash.json`` and restores
the HEAD snapshot to muse-work/.

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
import shutil
from typing import TypedDict

import typer

from muse.core.errors import ExitCode
from muse.core.object_store import restore_object, write_object_from_path
from muse.core.repo import require_repo
from muse.core.snapshot import compute_snapshot_id
from muse.core.store import get_head_snapshot_manifest, read_snapshot
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
    head_ref = (root / ".muse" / "HEAD").read_text().strip()
    return head_ref.removeprefix("refs/heads/").strip()


def _read_repo_id(root: pathlib.Path) -> str:
    return str(json.loads((root / ".muse" / "repo.json").read_text())["repo_id"])


def _load_stash(root: pathlib.Path) -> list[StashEntry]:
    stash_file = root / _STASH_FILE
    if not stash_file.exists():
        return []
    result: list[StashEntry] = json.loads(stash_file.read_text())
    return result


def _save_stash(root: pathlib.Path, stash: list[StashEntry]) -> None:
    (root / _STASH_FILE).write_text(json.dumps(stash, indent=2))


@app.callback(invoke_without_command=True)
def stash(ctx: typer.Context) -> None:
    """Save current muse-work/ changes and restore HEAD."""
    if ctx.invoked_subcommand is not None:
        return
    root = require_repo()
    repo_id = _read_repo_id(root)
    branch = _read_branch(root)
    workdir = root / "muse-work"

    plugin = resolve_plugin(root)
    manifest = plugin.snapshot(workdir)["files"]
    if not manifest:
        typer.echo("Nothing to stash.")
        return

    snapshot_id = compute_snapshot_id(manifest)
    for rel_path, object_id in manifest.items():
        write_object_from_path(root, object_id, workdir / rel_path)

    stash_entry = StashEntry(
        snapshot_id=snapshot_id,
        manifest=manifest,
        branch=branch,
        stashed_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
    )
    entries = _load_stash(root)
    entries.insert(0, stash_entry)
    _save_stash(root, entries)

    # Restore HEAD
    head_manifest = get_head_snapshot_manifest(root, repo_id, branch) or {}
    if workdir.exists():
        shutil.rmtree(workdir)
    workdir.mkdir()
    for rel_path, object_id in head_manifest.items():
        restore_object(root, object_id, workdir / rel_path)

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

    workdir = root / "muse-work"
    if workdir.exists():
        shutil.rmtree(workdir)
    workdir.mkdir()
    for rel_path, object_id in entry["manifest"].items():
        restore_object(root, object_id, workdir / rel_path)

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

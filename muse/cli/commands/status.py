"""muse status — show working-tree drift against HEAD.

Output modes
------------

Default::

    On branch main

    Changes since last commit:
      (use "muse commit -m <msg>" to record changes)

            modified: tracks/drums.mid
            new file: tracks/lead.mp3
            deleted:  tracks/scratch.mid

--short::

    M tracks/drums.mid
    A tracks/lead.mp3
    D tracks/scratch.mid

--porcelain (machine-readable, stable for scripting)::

    ## main
     M tracks/drums.mid
     A tracks/lead.mp3
     D tracks/scratch.mid
"""
from __future__ import annotations

import json
import logging
import pathlib

import typer

from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.store import get_head_snapshot_manifest
from muse.domain import SnapshotManifest
from muse.plugins.registry import read_domain, resolve_plugin

logger = logging.getLogger(__name__)

app = typer.Typer()


def _read_branch(root: pathlib.Path) -> str:
    head_ref = (root / ".muse" / "HEAD").read_text().strip()
    return head_ref.removeprefix("refs/heads/").strip()


def _read_repo_id(root: pathlib.Path) -> str:
    return str(json.loads((root / ".muse" / "repo.json").read_text())["repo_id"])


@app.callback(invoke_without_command=True)
def status(
    ctx: typer.Context,
    short: bool = typer.Option(False, "--short", "-s", help="Condensed output."),
    porcelain: bool = typer.Option(False, "--porcelain", help="Machine-readable output."),
    branch_only: bool = typer.Option(False, "--branch", "-b", help="Show branch info only."),
) -> None:
    """Show working-tree drift against HEAD."""
    root = require_repo()
    branch = _read_branch(root)
    repo_id = _read_repo_id(root)

    if porcelain:
        typer.echo(f"## {branch}")
        if branch_only:
            return

    elif not short:
        typer.echo(f"On branch {branch}")
        if branch_only:
            return

    head_manifest = get_head_snapshot_manifest(root, repo_id, branch) or {}
    workdir = root / "muse-work"

    plugin = resolve_plugin(root)
    committed_snap = SnapshotManifest(files=head_manifest, domain=read_domain(root))
    report = plugin.drift(committed_snap, workdir)
    delta = report.delta

    added: set[str] = {op["address"] for op in delta["ops"] if op["op"] == "insert"}
    modified: set[str] = {op["address"] for op in delta["ops"] if op["op"] in ("replace", "patch")}
    deleted: set[str] = {op["address"] for op in delta["ops"] if op["op"] == "delete"}

    if not any([added, modified, deleted]):
        if not short and not porcelain:
            typer.echo("\nNothing to commit, working tree clean")
        return

    if short or porcelain:
        for p in sorted(modified):
            typer.echo(f" M {p}")
        for p in sorted(added):
            typer.echo(f" A {p}")
        for p in sorted(deleted):
            typer.echo(f" D {p}")
        return

    typer.echo("\nChanges since last commit:")
    typer.echo('  (use "muse commit -m <msg>" to record changes)\n')
    for p in sorted(modified):
        typer.echo(f"\t    modified: {p}")
    for p in sorted(added):
        typer.echo(f"\t    new file: {p}")
    for p in sorted(deleted):
        typer.echo(f"\t     deleted: {p}")

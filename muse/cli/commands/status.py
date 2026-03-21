"""muse status — show working-tree drift against HEAD.

Output modes
------------

Default (color when stdout is a TTY)::

    On branch main

    Changes since last commit:
      (use "muse commit -m <msg>" to record changes)

            modified: tracks/drums.mid
            new file: tracks/lead.mp3
            deleted:  tracks/scratch.mid

--short (color letter prefix when stdout is a TTY)::

    M tracks/drums.mid
    A tracks/lead.mp3
    D tracks/scratch.mid

--porcelain (machine-readable, stable for scripting — no color ever)::

    ## main
     M tracks/drums.mid
     A tracks/lead.mp3
     D tracks/scratch.mid

Color convention
----------------
- yellow  modified  — file exists in both old and new snapshot, content changed
- green   new file  — file is new, not present in last commit
- red     deleted   — file was removed since last commit
"""

from __future__ import annotations

import json
import logging
import pathlib
import sys

import typer

from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.store import get_head_snapshot_manifest, read_current_branch
from muse.domain import SnapshotManifest
from muse.plugins.registry import read_domain, resolve_plugin

logger = logging.getLogger(__name__)

app = typer.Typer()

# Change-type colors. Applied only when stdout is a TTY so piped output stays
# clean without needing --porcelain.
_YELLOW = typer.colors.YELLOW
_GREEN  = typer.colors.GREEN
_RED    = typer.colors.RED


def _c(text: str, color: str) -> str:
    """Apply *color* to *text* when stdout is a terminal; pass through otherwise."""
    if sys.stdout.isatty():
        return typer.style(text, fg=color, bold=True)
    return text


def _read_repo_id(root: pathlib.Path) -> str:
    return str(json.loads((root / ".muse" / "repo.json").read_text())["repo_id"])


@app.callback(invoke_without_command=True)
def status(
    ctx: typer.Context,
    short: bool = typer.Option(False, "--short", "-s", help="Condensed output."),
    porcelain: bool = typer.Option(False, "--porcelain", help="Machine-readable output (no color)."),
    branch_only: bool = typer.Option(False, "--branch", "-b", help="Show branch info only."),
) -> None:
    """Show working-tree drift against HEAD."""
    root = require_repo()
    try:
        branch = read_current_branch(root)
    except ValueError as exc:
        typer.echo(f"fatal: {exc}", err=True)
        raise typer.Exit(code=ExitCode.USER_ERROR)
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
    plugin = resolve_plugin(root)
    committed_snap = SnapshotManifest(files=head_manifest, domain=read_domain(root))
    report = plugin.drift(committed_snap, root)
    delta = report.delta

    added: set[str] = {op["address"] for op in delta["ops"] if op["op"] == "insert"}
    modified: set[str] = {op["address"] for op in delta["ops"] if op["op"] in ("replace", "patch")}
    deleted: set[str] = {op["address"] for op in delta["ops"] if op["op"] == "delete"}

    if not any([added, modified, deleted]):
        if not short and not porcelain:
            typer.echo("\nNothing to commit, working tree clean")
        return

    # --porcelain: stable machine-readable output, no color, ever.
    if porcelain:
        for p in sorted(modified):
            typer.echo(f" M {p}")
        for p in sorted(added):
            typer.echo(f" A {p}")
        for p in sorted(deleted):
            typer.echo(f" D {p}")
        return

    # --short: compact one-line-per-file, colored letter prefix.
    if short:
        for p in sorted(modified):
            typer.echo(f" {_c('M', _YELLOW)} {p}")
        for p in sorted(added):
            typer.echo(f" {_c('A', _GREEN)} {p}")
        for p in sorted(deleted):
            typer.echo(f" {_c('D', _RED)} {p}")
        return

    # Default: human-readable, colored label.
    typer.echo("\nChanges since last commit:")
    typer.echo('  (use "muse commit -m <msg>" to record changes)\n')
    for p in sorted(modified):
        typer.echo(f"\t{_c('    modified:', _YELLOW)} {p}")
    for p in sorted(added):
        typer.echo(f"\t{_c('    new file:', _GREEN)} {p}")
    for p in sorted(deleted):
        typer.echo(f"\t{_c('     deleted:', _RED)} {p}")

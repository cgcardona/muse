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
from muse.plugins.registry import resolve_plugin_by_domain

logger = logging.getLogger(__name__)

app = typer.Typer()

# Change-type colors. Applied only when stdout is a TTY so piped output stays
# clean without needing --porcelain.
_YELLOW = typer.colors.YELLOW
_GREEN  = typer.colors.GREEN
_RED    = typer.colors.RED


def _read_repo_meta(root: pathlib.Path) -> tuple[str, str]:
    """Read ``.muse/repo.json`` once and return ``(repo_id, domain)``.

    Returns sensible defaults on any read or parse failure rather than
    propagating an unhandled exception to the user.  The caller never needs
    to guard against a missing or corrupt ``repo.json`` — status degrades
    gracefully to an empty diff in the worst case.
    """
    repo_json = root / ".muse" / "repo.json"
    try:
        data = json.loads(repo_json.read_text(encoding="utf-8"))
        repo_id_raw = data.get("repo_id", "")
        repo_id = str(repo_id_raw) if isinstance(repo_id_raw, str) and repo_id_raw else ""
        domain_raw = data.get("domain", "")
        domain = str(domain_raw) if isinstance(domain_raw, str) and domain_raw else "midi"
        return repo_id, domain
    except (OSError, json.JSONDecodeError):
        return "", "midi"


@app.callback(invoke_without_command=True)
def status(
    ctx: typer.Context,
    short: bool = typer.Option(False, "--short", "-s", help="Condensed output."),
    porcelain: bool = typer.Option(False, "--porcelain", help="Machine-readable output (no color)."),
    branch_only: bool = typer.Option(False, "--branch", "-b", help="Show branch info only."),
    fmt: str = typer.Option("text", "--format", "-f", help="Output format: text or json."),
) -> None:
    """Show working-tree drift against HEAD.

    Agents should pass ``--format json`` to receive structured output with
    ``branch``, ``clean`` (bool), and ``added``, ``modified``, ``deleted``
    file lists.
    """
    if fmt not in ("text", "json"):
        from muse.core.validation import sanitize_display as _sd
        typer.echo(f"❌ Unknown --format '{_sd(fmt)}'. Choose text or json.", err=True)
        raise typer.Exit(code=ExitCode.USER_ERROR)

    root = require_repo()
    try:
        branch = read_current_branch(root)
    except ValueError as exc:
        typer.echo(f"fatal: {exc}", err=True)
        raise typer.Exit(code=ExitCode.USER_ERROR)

    # Read repo.json exactly once — repo_id and domain both come from here.
    # resolve_plugin_by_domain() uses the pre-read domain string, eliminating
    # the two additional repo.json reads that resolve_plugin() and read_domain()
    # would otherwise each trigger independently.
    repo_id, domain = _read_repo_meta(root)

    # JSON mode prints everything at once at the end — skip all partial prints.
    if fmt != "json":
        if porcelain:
            typer.echo(f"## {branch}")
        elif not short:
            typer.echo(f"On branch {branch}")

    # --branch: print only the branch header then exit, regardless of mode.
    if branch_only:
        if fmt == "json":
            typer.echo(json.dumps({"branch": branch}))
        return

    # Compute isatty once; it is a syscall and must not be repeated per line.
    # Porcelain output is never colored, even on a TTY.
    is_tty = sys.stdout.isatty() and not porcelain and fmt != "json"

    def _color(text: str, color: str) -> str:
        return typer.style(text, fg=color, bold=True) if is_tty else text

    head_manifest = get_head_snapshot_manifest(root, repo_id, branch) or {}
    plugin = resolve_plugin_by_domain(domain)
    committed_snap = SnapshotManifest(files=head_manifest, domain=domain)
    report = plugin.drift(committed_snap, root)
    delta = report.delta

    added: set[str] = {op["address"] for op in delta["ops"] if op["op"] == "insert"}
    modified: set[str] = {op["address"] for op in delta["ops"] if op["op"] in ("replace", "patch")}
    deleted: set[str] = {op["address"] for op in delta["ops"] if op["op"] == "delete"}

    clean = not (added or modified or deleted)

    # --format json: always wins, no color, fully structured.
    if fmt == "json":
        typer.echo(json.dumps({
            "branch": branch,
            "clean": clean,
            "added": sorted(added),
            "modified": sorted(modified),
            "deleted": sorted(deleted),
        }))
        return

    if clean:
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
            typer.echo(f" {_color('M', _YELLOW)} {p}")
        for p in sorted(added):
            typer.echo(f" {_color('A', _GREEN)} {p}")
        for p in sorted(deleted):
            typer.echo(f" {_color('D', _RED)} {p}")
        return

    # Default: human-readable, colored label.
    typer.echo("\nChanges since last commit:")
    typer.echo('  (use "muse commit -m <msg>" to record changes)\n')
    for p in sorted(modified):
        typer.echo(f"\t{_color('    modified:', _YELLOW)} {p}")
    for p in sorted(added):
        typer.echo(f"\t{_color('    new file:', _GREEN)} {p}")
    for p in sorted(deleted):
        typer.echo(f"\t{_color('     deleted:', _RED)} {p}")

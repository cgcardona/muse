"""``muse check`` — generic domain invariant enforcement.

Dispatches to the domain plugin's registered :class:`~muse.core.invariants.InvariantChecker`
and reports all violations.  Works for any domain that has registered a checker.

Currently supported domains:

- ``midi``  — polyphony, pitch range, key consistency, parallel fifths.
- ``code``  — complexity, circular imports, dead exports, test coverage.

Usage::

    muse check                     # check HEAD with auto-detected domain
    muse check abc1234             # check specific commit
    muse check --strict            # exit 1 on any error-severity violation
    muse check --json              # machine-readable JSON output
    muse check --rules my.toml    # custom rules file
"""

from __future__ import annotations

import json
import logging
import pathlib

import typer

from muse.core.invariants import InvariantChecker, format_report
from muse.core.repo import require_repo
from muse.core.store import get_head_commit_id
from muse.plugins.registry import read_domain

logger = logging.getLogger(__name__)

app = typer.Typer()


def _resolve_head(root: pathlib.Path) -> str | None:
    head_ref = (root / ".muse" / "HEAD").read_text().strip()
    branch = head_ref.removeprefix("refs/heads/").strip()
    return get_head_commit_id(root, branch)


def _get_checker(domain: str) -> InvariantChecker | None:
    """Return the domain's InvariantChecker instance, or None."""
    if domain == "code":
        from muse.plugins.code._invariants import CodeChecker
        return CodeChecker()
    if domain == "midi":
        from muse.plugins.midi._invariants import MidiChecker
        return MidiChecker()
    return None


@app.callback(invoke_without_command=True)
def check(
    ctx: typer.Context,
    commit_arg: str | None = typer.Argument(None, help="Commit ID to check (default: HEAD)."),
    strict: bool = typer.Option(False, "--strict", help="Exit 1 when any error-severity violation is found."),
    output_json: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
    rules_file: str | None = typer.Option(None, "--rules", help="Path to a TOML invariants file."),
) -> None:
    """Run invariant checks for the current domain against a commit.

    Auto-detects the repository domain (code or midi) and dispatches to the
    appropriate checker.  Use ``muse code-check`` or ``muse midi-check`` for
    domain-specific options.
    """
    root = require_repo()
    domain = read_domain(root)

    commit_id = commit_arg or _resolve_head(root)
    if commit_id is None:
        typer.echo("❌ No commit found.")
        raise typer.Exit(code=1)

    checker = _get_checker(domain)
    if checker is None:
        typer.echo(f"⚠️  No invariant checker registered for domain {domain!r}.")
        typer.echo("  Supported domains: code, midi")
        raise typer.Exit(code=0)

    rules_path = pathlib.Path(rules_file) if rules_file else None
    report = checker.check(root, commit_id, rules_file=rules_path)

    if output_json:
        typer.echo(json.dumps(report))
        if strict and report["has_errors"]:
            raise typer.Exit(code=1)
        return

    typer.echo(f"\ncheck [{domain}] {commit_id[:8]} — {report['rules_checked']} rules")
    typer.echo(format_report(report))

    if strict and report["has_errors"]:
        raise typer.Exit(code=1)

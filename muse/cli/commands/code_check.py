"""``muse code-check`` — code invariant enforcement.

Evaluates semantic rules declared in ``.muse/code_invariants.toml`` against
the code snapshot of the specified commit and reports violations.

Built-in rule types (declared in TOML)::

    [[rule]]
    name = "complexity_gate"
    severity = "error"
    rule_type = "max_complexity"
    [rule.params]
    threshold = 10

    [[rule]]
    name = "no_cycles"
    severity = "error"
    rule_type = "no_circular_imports"

    [[rule]]
    name = "dead_exports"
    severity = "warning"
    rule_type = "no_dead_exports"

    [[rule]]
    name = "coverage_floor"
    severity = "warning"
    rule_type = "test_coverage_floor"
    [rule.params]
    min_ratio = 0.30

Usage::

    muse code-check                      # check HEAD
    muse code-check abc1234              # check specific commit
    muse code-check --strict             # exit 1 on any error-severity violation
    muse code-check --json               # machine-readable JSON output
    muse code-check --rules my_rules.toml
"""

from __future__ import annotations

import json
import logging
import pathlib
import sys

import typer

from muse.core.invariants import format_report
from muse.core.repo import require_repo
from muse.core.store import get_head_commit_id
from muse.plugins.code._invariants import CodeChecker, load_invariant_rules, run_invariants

logger = logging.getLogger(__name__)

app = typer.Typer()


def _resolve_head(root: pathlib.Path) -> str | None:
    head_ref = (root / ".muse" / "HEAD").read_text().strip()
    branch = head_ref.removeprefix("refs/heads/").strip()
    return get_head_commit_id(root, branch)


@app.callback(invoke_without_command=True)
def code_check(
    ctx: typer.Context,
    commit_arg: str | None = typer.Argument(None, help="Commit ID to check (default: HEAD)."),
    strict: bool = typer.Option(False, "--strict", help="Exit 1 when any error-severity violation is found."),
    output_json: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
    rules_file: str | None = typer.Option(None, "--rules", help="Path to a TOML invariants file (default: .muse/code_invariants.toml)."),
) -> None:
    """Enforce code invariant rules against a commit snapshot.

    Reports cyclomatic complexity violations, import cycles, dead exports,
    and test coverage shortfalls based on the rules in
    ``.muse/code_invariants.toml`` (or built-in defaults when the file is
    absent).
    """
    root = require_repo()

    commit_id = commit_arg or _resolve_head(root)
    if commit_id is None:
        typer.echo("❌ No commit found.")
        raise typer.Exit(code=1)

    rules_path = pathlib.Path(rules_file) if rules_file else None
    rules = load_invariant_rules(rules_path)
    report = run_invariants(root, commit_id, rules)

    if output_json:
        typer.echo(json.dumps(report))
        if strict and report["has_errors"]:
            raise typer.Exit(code=1)
        return

    typer.echo(f"\ncode-check {commit_id[:8]} — {report['rules_checked']} rules")
    typer.echo(format_report(report))

    if strict and report["has_errors"]:
        raise typer.Exit(code=1)

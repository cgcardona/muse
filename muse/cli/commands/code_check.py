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

import argparse
import json
import logging
import pathlib
import sys

from muse.core.invariants import format_report
from muse.core.repo import require_repo
from muse.core.store import get_head_commit_id, read_current_branch
from muse.core.validation import contain_path
from muse.plugins.code._invariants import CodeChecker, load_invariant_rules, run_invariants

logger = logging.getLogger(__name__)


def _resolve_head(root: pathlib.Path) -> str | None:
    branch = read_current_branch(root)
    return get_head_commit_id(root, branch)


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the code-check subcommand."""
    parser = subparsers.add_parser(
        "code-check",
        help="Enforce code invariant rules against a commit snapshot.",
        description=__doc__,
    )
    parser.add_argument(
        "commit_arg", nargs="?", default=None,
        help="Commit ID to check (default: HEAD).",
    )
    parser.add_argument(
        "--strict", action="store_true",
        help="Exit 1 when any error-severity violation is found.",
    )
    parser.add_argument(
        "--json", action="store_true", dest="as_json",
        help="Emit machine-readable JSON.",
    )
    parser.add_argument(
        "--rules", default=None, dest="rules_file",
        help="Path to a TOML invariants file inside the repo (default: .muse/code_invariants.toml).",
    )
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> None:
    """Enforce code invariant rules against a commit snapshot.

    Reports cyclomatic complexity violations, import cycles, dead exports,
    and test coverage shortfalls based on the rules in
    ``.muse/code_invariants.toml`` (or built-in defaults when the file is
    absent).

    The ``--rules`` path is validated against the repo root — paths that
    escape the repository (e.g. ``../../shared/rules.toml``) are rejected.
    """
    commit_arg: str | None = args.commit_arg
    strict: bool = args.strict
    as_json: bool = args.as_json
    rules_file: str | None = args.rules_file

    root = require_repo()

    commit_id = commit_arg or _resolve_head(root)
    if commit_id is None:
        print("❌ No commit found.")
        raise SystemExit(1)

    rules_path: pathlib.Path | None = None
    if rules_file:
        try:
            rules_path = contain_path(root, rules_file)
        except ValueError as exc:
            print(f"❌ {exc}", file=sys.stderr)
            raise SystemExit(1)

    rules = load_invariant_rules(rules_path)
    report = run_invariants(root, commit_id, rules)

    if as_json:
        print(json.dumps(report))
        if strict and report["has_errors"]:
            raise SystemExit(1)
        return

    print(f"\ncode-check {commit_id[:8]} — {report['rules_checked']} rules")
    print(format_report(report))

    if strict and report["has_errors"]:
        raise SystemExit(1)

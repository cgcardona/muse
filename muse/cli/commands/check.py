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

import argparse
import json
import logging
import pathlib
import sys

from muse.core.invariants import InvariantChecker, format_report
from muse.core.repo import require_repo
from muse.core.store import get_head_commit_id, read_current_branch
from muse.plugins.registry import read_domain

logger = logging.getLogger(__name__)


def _resolve_head(root: pathlib.Path) -> str | None:
    branch = read_current_branch(root)
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


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the check subcommand."""
    parser = subparsers.add_parser(
        "check",
        help="Run invariant checks for the current domain against a commit.",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("commit_arg", nargs="?", default=None,
                        help="Commit ID to check (default: HEAD).")
    parser.add_argument("--strict", action="store_true",
                        help="Exit 1 when any error-severity violation is found.")
    parser.add_argument("--json", action="store_true", dest="output_json",
                        help="Emit machine-readable JSON.")
    parser.add_argument("--rules", default=None, dest="rules_file",
                        help="Path to a TOML invariants file.")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> None:
    """Run invariant checks for the current domain against a commit.

    Auto-detects the repository domain (code or midi) and dispatches to the
    appropriate checker.  Use ``muse code-check`` or ``muse midi-check`` for
    domain-specific options.
    """
    commit_arg: str | None = args.commit_arg
    strict: bool = args.strict
    output_json: bool = args.output_json
    rules_file: str | None = args.rules_file

    root = require_repo()
    domain = read_domain(root)

    commit_id = commit_arg or _resolve_head(root)
    if commit_id is None:
        print("❌ No commit found.")
        raise SystemExit(1)

    checker = _get_checker(domain)
    if checker is None:
        print(f"⚠️  No invariant checker registered for domain {domain!r}.")
        print("  Supported domains: code, midi")
        raise SystemExit(0)

    rules_path = pathlib.Path(rules_file) if rules_file else None
    report = checker.check(root, commit_id, rules_file=rules_path)

    if output_json:
        print(json.dumps(report))
        if strict and report["has_errors"]:
            raise SystemExit(1)
        return

    print(f"\ncheck [{domain}] {commit_id[:8]} — {report['rules_checked']} rules")
    print(format_report(report))

    if strict and report["has_errors"]:
        raise SystemExit(1)

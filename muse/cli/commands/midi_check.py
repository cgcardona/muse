"""``muse midi-check`` — MIDI invariant enforcement.

Evaluates the invariant rules declared in ``.muse/midi_invariants.toml``
against every MIDI track in the specified commit and reports violations with
severity, location, and description.

Built-in rule types (declared in TOML)::

    [[rule]]
    name = "max_polyphony"
    severity = "error"
    rule_type = "max_polyphony"
    [rule.params]
    max_simultaneous = 6

    [[rule]]
    name = "pitch_range"
    severity = "warning"
    rule_type = "pitch_range"
    [rule.params]
    min_pitch = 24
    max_pitch = 108

    [[rule]]
    name = "key_consistency"
    severity = "info"
    rule_type = "key_consistency"
    [rule.params]
    threshold = 0.15

    [[rule]]
    name = "no_parallel_fifths"
    severity = "warning"
    rule_type = "no_parallel_fifths"

Usage::

    muse midi-check                      # check HEAD
    muse midi-check abc1234              # check specific commit
    muse midi-check --track piano.mid    # check one track
    muse midi-check --strict             # exit 1 on any error-severity violation
    muse midi-check --json               # machine-readable output
"""

from __future__ import annotations

import argparse
import json
import logging
import pathlib
import sys

from muse.core.repo import require_repo
from muse.core.store import get_head_commit_id, read_current_branch
from muse.plugins.midi._invariants import (
    InvariantReport,
    load_invariant_rules,
    run_invariants,
)

logger = logging.getLogger(__name__)


def _read_branch(root: pathlib.Path) -> str:
    return read_current_branch(root)


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the midi-check subcommand."""
    parser = subparsers.add_parser("check", help="Enforce MIDI invariant rules against a commit's MIDI tracks.", description=__doc__)
    parser.add_argument("commit", nargs="?", metavar="COMMIT", default=None, help="Commit ID to check (default: HEAD).")
    parser.add_argument("--track", "-t", metavar="PATH", default=None, help="Restrict check to a single MIDI file path.")
    parser.add_argument("--rules", "-r", metavar="FILE", default=None, dest="rules_file", help="Path to a TOML invariant rules file (default: .muse/midi_invariants.toml).")
    parser.add_argument("--strict", action="store_true", help="Exit with code 1 when any error-severity violations are found.")
    parser.add_argument("--json", action="store_true", dest="as_json", help="Output machine-readable JSON instead of formatted text.")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> None:
    """Enforce MIDI invariant rules against a commit's MIDI tracks."""
    commit: str | None = args.commit
    track: str | None = args.track
    rules_file: str | None = args.rules_file
    strict: bool = args.strict
    as_json: bool = args.as_json

    root = require_repo()

    commit_id = commit
    if commit_id is None:
        branch = _read_branch(root)
        commit_id = get_head_commit_id(root, branch)
        if commit_id is None:
            print("❌ No commits in this repository.", file=sys.stderr)
            raise SystemExit(1)

    # Load rules.
    rules_path: pathlib.Path | None = None
    if rules_file:
        rules_path = pathlib.Path(rules_file)
    else:
        default = root / ".muse" / "midi_invariants.toml"
        if default.exists():
            rules_path = default

    rules = load_invariant_rules(rules_path)
    report = run_invariants(root, commit_id, rules, track_filter=track)

    if as_json:
        sys.stdout.write(json.dumps(report, indent=2) + "\n")
    else:
        _print_report(report)

    if strict and report["has_errors"]:
        raise SystemExit(1)


_SEVERITY_ICON = {
    "error":   "❌",
    "warning": "⚠️",
    "info":    "ℹ️",
}


def _print_report(report: InvariantReport) -> None:
    """Format and print an invariant report to stdout."""
    violations = report["violations"]

    if not violations:
        print(
            f"✅ No violations found ({report['rules_checked']} rule-track checks)"
        )
        return

    current_track: str | None = None
    for v in violations:
        if v["track"] != current_track:
            current_track = v["track"]
            print(f"\n  {current_track}")
        icon = _SEVERITY_ICON.get(v["severity"], "•")
        bar_label = f"bar {v['bar']}" if v["bar"] > 0 else "track"
        print(
            f"    {icon} [{v['rule_name']}] {bar_label}: {v['description']}"
        )

    error_count = sum(1 for v in violations if v["severity"] == "error")
    warn_count = sum(1 for v in violations if v["severity"] == "warning")
    info_count = sum(1 for v in violations if v["severity"] == "info")

    parts: list[str] = []
    if error_count:
        parts.append(f"{error_count} error{'s' if error_count != 1 else ''}")
    if warn_count:
        parts.append(f"{warn_count} warning{'s' if warn_count != 1 else ''}")
    if info_count:
        parts.append(f"{info_count} info")

    summary = ", ".join(parts)
    icon = "❌" if error_count else "⚠️" if warn_count else "ℹ️"
    print(
        f"\n{icon} {summary} in commit {report['commit_id'][:8]} "
        f"({report['rules_checked']} rule-track checks)"
    )

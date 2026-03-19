"""Domain-agnostic invariants engine for Muse.

An *invariant* is a semantic rule that a domain's state must satisfy.  Rules
are declared in TOML, evaluated against commit snapshots, and reported with
structured violations.  Any domain plugin can implement invariant checking
by satisfying the :class:`InvariantChecker` protocol and wiring a CLI command.

This module defines the **shared vocabulary** — TypedDicts and protocols that
are domain-agnostic.  Domain-specific implementations (MIDI, code, genomics…)
import these types and add their own rule types and evaluators.

Architecture
------------
::

    muse/core/invariants.py          ← this file: shared protocol
    muse/plugins/midi/_invariants.py ← MIDI-specific rules + evaluator
    muse/plugins/code/_invariants.py ← code-specific rules + evaluator
    muse/cli/commands/midi_check.py  ← CLI wiring for MIDI
    muse/cli/commands/code_check.py  ← CLI wiring for code

TOML rule file format (shared across all domains)::

    [[rule]]
    name     = "my_rule"         # unique human-readable identifier
    severity = "error"           # "info" | "warning" | "error"
    scope    = "file"            # domain-specific scope tag
    rule_type = "max_complexity" # domain-specific rule type string

    [rule.params]
    threshold = 10               # rule-specific numeric / string params

Severity levels
---------------
- ``"error"``   — must be resolved before committing (when ``--strict`` is set).
- ``"warning"`` — reported but does not block commits.
- ``"info"``    — informational; surfaced in ``muse check`` output only.

Public API
----------
- :data:`InvariantSeverity`     — severity literal type alias.
- :class:`BaseViolation`        — domain-agnostic violation record.
- :class:`BaseReport`           — full check report for one commit.
- :class:`InvariantChecker`     — Protocol every domain checker must satisfy.
- :func:`make_report`           — build a ``BaseReport`` from a violation list.
- :func:`load_rules_toml`       — parse any ``[[rule]]`` TOML file.
- :func:`format_report`         — human-readable report text.
"""
from __future__ import annotations

import logging
import pathlib
from typing import Literal, Protocol, TypedDict, runtime_checkable

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shared severity literal
# ---------------------------------------------------------------------------

InvariantSeverity = Literal["info", "warning", "error"]


# ---------------------------------------------------------------------------
# Domain-agnostic violation + report TypedDicts
# ---------------------------------------------------------------------------


class BaseViolation(TypedDict):
    """A single invariant violation, domain-agnostic.

    Domain implementations extend this with additional fields (e.g. ``track``
    for MIDI, ``file`` and ``symbol`` for code).

    ``rule_name``   The name of the rule that fired.
    ``severity``    Violation severity inherited from the rule declaration.
    ``address``     Dotted path to the violating element
                    (e.g. ``"src/utils.py::my_fn"`` or ``"piano.mid/bar:4"``).
    ``description`` Human-readable explanation of the violation.
    """

    rule_name: str
    severity: InvariantSeverity
    address: str
    description: str


class BaseReport(TypedDict):
    """Full invariant check report for one commit, domain-agnostic.

    ``commit_id``     The commit that was checked.
    ``domain``        Domain tag (e.g. ``"midi"``, ``"code"``).
    ``violations``    All violations found, sorted by address.
    ``rules_checked`` Number of rules evaluated.
    ``has_errors``    ``True`` when any violation has severity ``"error"``.
    ``has_warnings``  ``True`` when any violation has severity ``"warning"``.
    """

    commit_id: str
    domain: str
    violations: list[BaseViolation]
    rules_checked: int
    has_errors: bool
    has_warnings: bool


# ---------------------------------------------------------------------------
# InvariantChecker protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class InvariantChecker(Protocol):
    """Protocol every domain invariant checker must satisfy.

    Domain plugins implement this by providing :meth:`check` — a function that
    loads and evaluates the domain's invariant rules against a commit, returning
    a :class:`BaseReport`.  The CLI ``muse check`` command dispatches to the
    domain's registered checker via this protocol.

    Example implementation::

        class MyDomainChecker:
            def check(
                self,
                repo_root: pathlib.Path,
                commit_id: str,
                *,
                rules_file: pathlib.Path | None = None,
            ) -> BaseReport:
                rules = load_rules_toml(rules_file or default_path)
                violations = _evaluate(repo_root, commit_id, rules)
                return make_report(commit_id, "mydomain", violations, len(rules))
    """

    def check(
        self,
        repo_root: pathlib.Path,
        commit_id: str,
        *,
        rules_file: pathlib.Path | None = None,
    ) -> BaseReport:
        """Evaluate invariant rules and return a structured report.

        Args:
            repo_root:  Repository root (contains ``.muse/``).
            commit_id:  Commit to check.
            rules_file: Path to a TOML rule file.  ``None`` → use the
                        domain's default location.

        Returns:
            A :class:`BaseReport` with all violations and summary flags.
        """
        ...


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_report(
    commit_id: str,
    domain: str,
    violations: list[BaseViolation],
    rules_checked: int,
) -> BaseReport:
    """Build a :class:`BaseReport` from a flat violation list.

    Sorts violations by address then rule name for deterministic output.

    Args:
        commit_id:     Commit that was checked.
        domain:        Domain tag.
        violations:    All violations found.
        rules_checked: Number of rules that were evaluated.

    Returns:
        A fully populated :class:`BaseReport`.
    """
    sorted_violations = sorted(violations, key=lambda v: (v["address"], v["rule_name"]))
    return BaseReport(
        commit_id=commit_id,
        domain=domain,
        violations=sorted_violations,
        rules_checked=rules_checked,
        has_errors=any(v["severity"] == "error" for v in violations),
        has_warnings=any(v["severity"] == "warning" for v in violations),
    )


def load_rules_toml(path: pathlib.Path) -> list[dict[str, str | int | float | dict[str, str | int | float]]]:
    """Parse a ``[[rule]]`` TOML file and return the raw rule dicts.

    Returns an empty list when the file does not exist (domain then uses
    built-in defaults).

    Args:
        path: Path to the TOML file.

    Returns:
        List of raw rule dicts (``{"name": ..., "severity": ..., ...}``).
    """
    if not path.exists():
        logger.debug("Invariants rules file not found at %s — using defaults", path)
        return []
    import tomllib  # stdlib on Python ≥ 3.11; Muse requires 3.12
    try:
        data = tomllib.loads(path.read_text())
        rules: list[dict[str, str | int | float | dict[str, str | int | float]]] = data.get("rule", [])
        return rules
    except Exception as exc:
        logger.warning("Failed to parse invariants file %s: %s", path, exc)
        return []


def format_report(report: BaseReport, *, color: bool = True) -> str:
    """Return a human-readable multi-line report string.

    Args:
        report: The report to format.
        color:  If ``True``, prefix error/warning/info lines with emoji.

    Returns:
        Formatted string ready for ``typer.echo()``.
    """
    lines: list[str] = []
    prefix = {
        "error":   "❌" if color else "[error]",
        "warning": "⚠️ " if color else "[warn] ",
        "info":    "ℹ️ " if color else "[info] ",
    }
    for v in report["violations"]:
        p = prefix.get(v["severity"], "   ")
        lines.append(f"  {p} [{v['rule_name']}] {v['address']}: {v['description']}")

    checked = report["rules_checked"]
    total = len(report["violations"])
    errors = sum(1 for v in report["violations"] if v["severity"] == "error")
    warnings = sum(1 for v in report["violations"] if v["severity"] == "warning")

    summary = f"\n{checked} rules checked — {total} violation(s)"
    if errors:
        summary += f", {errors} error(s)"
    if warnings:
        summary += f", {warnings} warning(s)"
    if not total:
        summary = f"\n✅ {checked} rules checked — no violations"

    return "\n".join(lines) + summary

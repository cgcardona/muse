"""muse plumbing check-attr — query merge-strategy attributes for paths.

Reads ``.museattributes``, resolves the applicable rules for each supplied
path, and reports the strategy that would be applied per dimension.  Useful
for verifying that attribute rules are wired up correctly before a merge, and
for scripting domain-aware merge drivers.

Output (JSON, default)::

    {
      "domain":       "midi",
      "rules_loaded": 3,
      "results": [
        {
          "path":      "tracks/drums.mid",
          "dimension": "*",
          "strategy":  "ours",
          "rule": {
            "path_pattern":  "drums/*",
            "dimension":     "*",
            "strategy":      "ours",
            "comment":       "Drums always prefer ours.",
            "priority":      10,
            "source_index":  0
          }
        },
        {
          "path":      "tracks/melody.mid",
          "dimension": "*",
          "strategy":  "auto",
          "rule":      null
        }
      ]
    }

Text output (``--format text``)::

    tracks/drums.mid   dimension=*   strategy=ours    (rule 0: drums/*)
    tracks/melody.mid  dimension=*   strategy=auto    (no matching rule)

Plumbing contract
-----------------

- Exit 0: attributes resolved and emitted (even when no rules match).
- Exit 1: bad ``--format`` value; missing path arguments.
- Exit 3: I/O or TOML parse error reading ``.museattributes``.
"""

from __future__ import annotations

import json
import logging
from typing import TypedDict

import typer

from muse.core.attributes import AttributeRule, load_attributes, resolve_strategy
from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.plugins.registry import read_domain

logger = logging.getLogger(__name__)

app = typer.Typer()

_FORMAT_CHOICES = ("json", "text")


class _RuleDict(TypedDict):
    path_pattern: str
    dimension: str
    strategy: str
    comment: str
    priority: int
    source_index: int


class _PathResult(TypedDict):
    path: str
    dimension: str
    strategy: str
    rule: _RuleDict | None


def _find_matching_rule(
    rules: list[AttributeRule], path: str, dimension: str
) -> AttributeRule | None:
    """Return the first rule that matches *path* and *dimension*, or ``None``."""
    import fnmatch

    for rule in rules:
        path_match = fnmatch.fnmatch(path, rule.path_pattern)
        dim_match = (
            rule.dimension == "*"
            or rule.dimension == dimension
            or dimension == "*"
        )
        if path_match and dim_match:
            return rule
    return None


def _rule_to_dict(rule: AttributeRule) -> _RuleDict:
    return {
        "path_pattern": rule.path_pattern,
        "dimension": rule.dimension,
        "strategy": rule.strategy,
        "comment": rule.comment,
        "priority": rule.priority,
        "source_index": rule.source_index,
    }


@app.callback(invoke_without_command=True)
def check_attr(
    ctx: typer.Context,
    paths: list[str] = typer.Argument(..., help="Workspace-relative paths to check."),
    dimension: str = typer.Option(
        "*",
        "--dimension",
        "-d",
        help="Domain dimension to query (e.g. 'notes', 'pitch_bend'). "
        "Use '*' to match any dimension.",
    ),
    fmt: str = typer.Option(
        "json", "--format", "-f", help="Output format: json or text."
    ),
    all_rules: bool = typer.Option(
        False,
        "--all-rules",
        "-A",
        help="For each path, list all matching rules (not just the first).",
    ),
) -> None:
    """Query merge-strategy attributes for one or more paths.

    Reads ``.museattributes`` from the repository root and reports the
    strategy that would be applied to each path for the given dimension.
    Domain context is read automatically from ``.muse/repo.json``.

    Paths should be workspace-relative POSIX paths.  Use ``--dimension`` to
    narrow the query to a specific domain axis (e.g. ``notes``, ``pitch_bend``,
    ``symbols``); the default ``*`` matches any dimension.

    Use ``--all-rules`` to see every rule that would apply to a path (in
    priority order), not just the first-match winner.
    """
    if fmt not in _FORMAT_CHOICES:
        typer.echo(
            json.dumps(
                {"error": f"Unknown format {fmt!r}. Valid: {', '.join(_FORMAT_CHOICES)}"}
            )
        )
        raise typer.Exit(code=ExitCode.USER_ERROR)

    if not paths:
        typer.echo(json.dumps({"error": "At least one path argument is required."}))
        raise typer.Exit(code=ExitCode.USER_ERROR)

    root = require_repo()
    domain = read_domain(root)

    try:
        rules = load_attributes(root, domain=domain)
    except ValueError as exc:
        typer.echo(json.dumps({"error": str(exc)}))
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)

    if all_rules:
        # Return every matching rule per path.
        import fnmatch

        per_path: dict[str, list[_RuleDict]] = {}
        for path in paths:
            matching: list[_RuleDict] = []
            for rule in rules:
                path_match = fnmatch.fnmatch(path, rule.path_pattern)
                dim_match = (
                    rule.dimension == "*"
                    or rule.dimension == dimension
                    or dimension == "*"
                )
                if path_match and dim_match:
                    matching.append(_rule_to_dict(rule))
            per_path[path] = matching

        if fmt == "text":
            for path, matched_rules in per_path.items():
                if not matched_rules:
                    typer.echo(f"{path}  (no matching rules)")
                else:
                    for rd in matched_rules:
                        typer.echo(
                            f"{path}  dimension={rd['dimension']}  "
                            f"strategy={rd['strategy']}  (rule {rd['source_index']}: "
                            f"{rd['path_pattern']})"
                        )
            return

        typer.echo(
            json.dumps(
                {
                    "domain": domain,
                    "rules_loaded": len(rules),
                    "dimension": dimension,
                    "results": [
                        {"path": path, "matching_rules": per_path[path]}
                        for path in paths
                    ],
                }
            )
        )
        return

    # Default: first-match winner per path.
    results: list[_PathResult] = []
    for path in paths:
        strategy = resolve_strategy(rules, path, dimension)
        matched_rule = _find_matching_rule(rules, path, dimension)
        results.append(
            {
                "path": path,
                "dimension": dimension,
                "strategy": strategy,
                "rule": _rule_to_dict(matched_rule) if matched_rule else None,
            }
        )

    if fmt == "text":
        for res in results:
            rule_entry: _RuleDict | None = res["rule"]
            if rule_entry is not None:
                rule_info = f"(rule {rule_entry['source_index']}: {rule_entry['path_pattern']})"
            else:
                rule_info = "(no matching rule)"
            typer.echo(
                f"{res['path']}  dimension={res['dimension']}  "
                f"strategy={res['strategy']}  {rule_info}"
            )
        return

    typer.echo(
        json.dumps(
            {
                "domain": domain,
                "rules_loaded": len(rules),
                "dimension": dimension,
                "results": [dict(r) for r in results],
            }
        )
    )

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

import argparse
import fnmatch
import json
import logging
import sys
from typing import TypedDict

from muse.core.attributes import AttributeRule, load_attributes, resolve_strategy
from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.plugins.registry import read_domain

logger = logging.getLogger(__name__)

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


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the check-attr subcommand."""
    parser = subparsers.add_parser(
        "check-attr",
        help="Query merge-strategy attributes for workspace paths.",
        description=__doc__,
    )
    parser.add_argument(
        "paths",
        nargs="+",
        help="Workspace-relative paths to check.",
    )
    parser.add_argument(
        "--dimension", "-d",
        default="*",
        dest="dimension",
        metavar="DIMENSION",
        help="Domain dimension to query (e.g. 'notes', 'pitch_bend'). "
             "Use '*' to match any dimension. (default: *)",
    )
    parser.add_argument(
        "--format", "-f",
        dest="fmt",
        default="json",
        metavar="FORMAT",
        help="Output format: json or text. (default: json)",
    )
    parser.add_argument(
        "--all-rules", "-A",
        action="store_true",
        dest="all_rules",
        help="For each path, list all matching rules (not just the first).",
    )
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> None:
    """Query merge-strategy attributes for one or more paths.

    Reads ``.museattributes`` from the repository root and reports the
    strategy that would be applied to each path for the given dimension.
    """
    fmt: str = args.fmt
    paths: list[str] = args.paths
    dimension: str = args.dimension
    all_rules: bool = args.all_rules

    if fmt not in _FORMAT_CHOICES:
        print(
            json.dumps(
                {"error": f"Unknown format {fmt!r}. Valid: {', '.join(_FORMAT_CHOICES)}"}
            )
        )
        raise SystemExit(ExitCode.USER_ERROR)

    if not paths:
        print(json.dumps({"error": "At least one path argument is required."}))
        raise SystemExit(ExitCode.USER_ERROR)

    root = require_repo()
    domain = read_domain(root)

    try:
        rules = load_attributes(root, domain=domain)
    except ValueError as exc:
        print(json.dumps({"error": str(exc)}))
        raise SystemExit(ExitCode.INTERNAL_ERROR)

    if all_rules:
        # Return every matching rule per path.
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
                    print(f"{path}  (no matching rules)")
                else:
                    for rd in matched_rules:
                        print(
                            f"{path}  dimension={rd['dimension']}  "
                            f"strategy={rd['strategy']}  (rule {rd['source_index']}: "
                            f"{rd['path_pattern']})"
                        )
            return

        print(
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
            print(
                f"{res['path']}  dimension={res['dimension']}  "
                f"strategy={res['strategy']}  {rule_info}"
            )
        return

    print(
        json.dumps(
            {
                "domain": domain,
                "rules_loaded": len(rules),
                "dimension": dimension,
                "results": [dict(r) for r in results],
            }
        )
    )

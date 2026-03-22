"""muse attributes — display the ``.museattributes`` merge-strategy rules.

Reads and pretty-prints the ``.museattributes`` file from the current
repository, showing the ``[meta]`` domain (if set) and each rule's path
pattern, dimension, and strategy.

Usage::

    muse attributes            # tabular display
    muse attributes --json     # JSON object with meta + rules array
"""

from __future__ import annotations

import argparse
import json

from muse.core.attributes import load_attributes, read_attributes_meta
from muse.core.repo import require_repo


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the attributes subcommand."""
    parser = subparsers.add_parser(
        "attributes",
        help="Display the .museattributes merge-strategy rules.",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--json", action="store_true", dest="output_json",
                        help="Output rules as JSON.")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> None:
    """Display the ``.museattributes`` merge-strategy rules."""
    output_json: bool = args.output_json

    root = require_repo()
    meta = read_attributes_meta(root)
    rules = load_attributes(root)

    if output_json:
        payload: dict[str, str | list[dict[str, str | int]]] = {}
        domain_val = meta.get("domain")
        if domain_val is not None:
            payload["domain"] = domain_val
        payload["rules"] = [
            {
                "path_pattern": r.path_pattern,
                "dimension": r.dimension,
                "strategy": r.strategy,
                "source_index": r.source_index,
            }
            for r in rules
        ]
        print(json.dumps(payload, indent=2))
        return

    if not rules:
        print("No .museattributes file found (or file is empty).")
        print(
            "Create one at the repository root to declare per-path merge strategies."
        )
        return

    # Header: domain from [meta] if present
    domain_val = meta.get("domain")
    if domain_val is not None:
        print(f"Domain: {domain_val}")
        print("")

    # Compute column widths for aligned output.
    pat_w = max(len(r.path_pattern) for r in rules)
    dim_w = max(len(r.dimension) for r in rules)

    print(f"{'Path pattern':<{pat_w}}  {'Dimension':<{dim_w}}  Strategy")
    print(f"{'-' * pat_w}  {'-' * dim_w}  --------")
    for rule in rules:
        print(
            f"{rule.path_pattern:<{pat_w}}  {rule.dimension:<{dim_w}}  {rule.strategy}"
        )

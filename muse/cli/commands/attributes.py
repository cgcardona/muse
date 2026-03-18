"""muse attributes — display the ``.museattributes`` merge-strategy rules.

Reads and pretty-prints the ``.museattributes`` file from the current
repository, showing the ``[meta]`` domain (if set) and each rule's path
pattern, dimension, and strategy.

Usage::

    muse attributes            # tabular display
    muse attributes --json     # JSON object with meta + rules array
"""
from __future__ import annotations

import json

import typer

from muse.core.attributes import load_attributes, read_attributes_meta
from muse.core.repo import require_repo

app = typer.Typer()


@app.callback(invoke_without_command=True)
def attributes(
    ctx: typer.Context,
    output_json: bool = typer.Option(False, "--json", help="Output rules as JSON."),
) -> None:
    """Display the ``.museattributes`` merge-strategy rules."""
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
        typer.echo(json.dumps(payload, indent=2))
        return

    if not rules:
        typer.echo("No .museattributes file found (or file is empty).")
        typer.echo(
            "Create one at the repository root to declare per-path merge strategies."
        )
        return

    # Header: domain from [meta] if present
    domain_val = meta.get("domain")
    if domain_val is not None:
        typer.echo(f"Domain: {domain_val}")
        typer.echo("")

    # Compute column widths for aligned output.
    pat_w = max(len(r.path_pattern) for r in rules)
    dim_w = max(len(r.dimension) for r in rules)

    typer.echo(f"{'Path pattern':<{pat_w}}  {'Dimension':<{dim_w}}  Strategy")
    typer.echo(f"{'-' * pat_w}  {'-' * dim_w}  --------")
    for rule in rules:
        typer.echo(
            f"{rule.path_pattern:<{pat_w}}  {rule.dimension:<{dim_w}}  {rule.strategy}"
        )

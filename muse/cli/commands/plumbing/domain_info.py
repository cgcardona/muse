"""muse plumbing domain-info — inspect the active domain plugin.

Reports which domain is active for this repository, which plugin class
implements it, what optional capabilities it exposes, and the full structural
schema it declares (merge mode, top-level element shape, dimensions).

Output (JSON, default)::

    {
      "domain":       "midi",
      "plugin_class": "MidiPlugin",
      "capabilities": {
        "structured_merge": true,
        "crdt":             false,
        "rerere":           false
      },
      "schema": {
        "domain":         "midi",
        "description":    "...",
        "merge_mode":     "three_way",
        "schema_version": "0.x.y",
        "top_level":      { ... },
        "dimensions":     [ ... ]
      },
      "registered_domains": ["bitcoin", "code", "midi", "scaffold"]
    }

Text output (``--format text``)::

    Domain:       midi
    Plugin:       MidiPlugin
    Merge mode:   three_way
    Capabilities: structured_merge

Plumbing contract
-----------------

- Exit 0: domain resolved and schema emitted.
- Exit 1: no repository found; domain not registered; bad ``--format`` value.
- Exit 3: plugin raised an unexpected error when computing its schema.
"""

from __future__ import annotations

import json
import logging
from typing import TypedDict

import typer

from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.domain import CRDTPlugin, RererePlugin, StructuredMergePlugin
from muse.plugins.registry import read_domain, registered_domains, resolve_plugin

logger = logging.getLogger(__name__)

app = typer.Typer()

_FORMAT_CHOICES = ("json", "text")


class _CapabilitiesDict(TypedDict):
    structured_merge: bool
    crdt: bool
    rerere: bool


@app.callback(invoke_without_command=True)
def domain_info(
    ctx: typer.Context,
    fmt: str = typer.Option(
        "json", "--format", "-f", help="Output format: json or text."
    ),
    all_domains: bool = typer.Option(
        False,
        "--all-domains",
        "-a",
        help="List every registered domain instead of querying the active repo.",
    ),
) -> None:
    """Inspect the domain plugin active for this repository.

    Reports the domain name, plugin class, optional protocol capabilities
    (``StructuredMergePlugin``, ``CRDTPlugin``, ``RererePlugin``), and the
    full structural schema declared by the plugin.

    Use ``--all-domains`` to enumerate every domain registered in this Muse
    installation without requiring an active repository.
    """
    if fmt not in _FORMAT_CHOICES:
        typer.echo(
            json.dumps(
                {"error": f"Unknown format {fmt!r}. Valid: {', '.join(_FORMAT_CHOICES)}"}
            )
        )
        raise typer.Exit(code=ExitCode.USER_ERROR)

    if all_domains:
        domains = registered_domains()
        if fmt == "text":
            for d in domains:
                typer.echo(d)
        else:
            typer.echo(json.dumps({"registered_domains": domains}))
        return

    root = require_repo()
    domain = read_domain(root)

    try:
        plugin = resolve_plugin(root)
    except Exception as exc:
        typer.echo(json.dumps({"error": str(exc)}))
        raise typer.Exit(code=ExitCode.USER_ERROR)

    plugin_class = type(plugin).__name__

    capabilities: _CapabilitiesDict = {
        "structured_merge": isinstance(plugin, StructuredMergePlugin),
        "crdt": isinstance(plugin, CRDTPlugin),
        "rerere": isinstance(plugin, RererePlugin),
    }

    try:
        schema = plugin.schema()
    except Exception as exc:
        logger.debug("domain-info: plugin.schema() failed: %s", exc)
        typer.echo(json.dumps({"error": f"Plugin schema error: {exc}"}))
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)

    all_domains_list = registered_domains()

    if fmt == "text":
        typer.echo(f"Domain:       {domain}")
        typer.echo(f"Plugin:       {plugin_class}")
        typer.echo(f"Merge mode:   {schema.get('merge_mode', 'unknown')}")
        active_caps = [k for k, v in capabilities.items() if v]
        cap_str = ", ".join(active_caps) if active_caps else "none"
        typer.echo(f"Capabilities: {cap_str}")
        typer.echo(f"Registered:   {', '.join(all_domains_list)}")
        return

    typer.echo(
        json.dumps(
            {
                "domain": domain,
                "plugin_class": plugin_class,
                "capabilities": dict(capabilities),
                "schema": dict(schema),
                "registered_domains": all_domains_list,
            }
        )
    )

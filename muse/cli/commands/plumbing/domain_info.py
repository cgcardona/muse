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

import argparse
import json
import logging
import sys
from typing import TypedDict

from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.domain import CRDTPlugin, RererePlugin, StructuredMergePlugin
from muse.plugins.registry import read_domain, registered_domains, resolve_plugin

logger = logging.getLogger(__name__)

_FORMAT_CHOICES = ("json", "text")


class _CapabilitiesDict(TypedDict):
    structured_merge: bool
    crdt: bool
    rerere: bool


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the domain-info subcommand."""
    parser = subparsers.add_parser(
        "domain-info",
        help="Inspect active domain plugin capabilities and schema.",
        description=__doc__,
    )
    parser.add_argument(
        "--format", "-f",
        dest="fmt",
        default="json",
        metavar="FORMAT",
        help="Output format: json or text. (default: json)",
    )
    parser.add_argument(
        "--all-domains", "-a",
        action="store_true",
        dest="all_domains",
        help="List every registered domain instead of querying the active repo.",
    )
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> None:
    """Inspect the domain plugin active for this repository.

    Reports the domain name, plugin class, optional protocol capabilities
    (``StructuredMergePlugin``, ``CRDTPlugin``, ``RererePlugin``), and the
    full structural schema declared by the plugin.

    Use ``--all-domains`` to enumerate every domain registered in this Muse
    installation without requiring an active repository.
    """
    fmt: str = args.fmt
    all_domains: bool = args.all_domains

    if fmt not in _FORMAT_CHOICES:
        print(
            json.dumps(
                {"error": f"Unknown format {fmt!r}. Valid: {', '.join(_FORMAT_CHOICES)}"}
            )
        )
        raise SystemExit(ExitCode.USER_ERROR)

    if all_domains:
        domains = registered_domains()
        if fmt == "text":
            for d in domains:
                print(d)
        else:
            print(json.dumps({"registered_domains": domains}))
        return

    root = require_repo()
    domain = read_domain(root)

    try:
        plugin = resolve_plugin(root)
    except Exception as exc:
        print(json.dumps({"error": str(exc)}))
        raise SystemExit(ExitCode.USER_ERROR)

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
        print(json.dumps({"error": f"Plugin schema error: {exc}"}))
        raise SystemExit(ExitCode.INTERNAL_ERROR)

    all_domains_list = registered_domains()

    if fmt == "text":
        print(f"Domain:       {domain}")
        print(f"Plugin:       {plugin_class}")
        print(f"Merge mode:   {schema.get('merge_mode', 'unknown')}")
        active_caps = [k for k, v in capabilities.items() if v]
        cap_str = ", ".join(active_caps) if active_caps else "none"
        print(f"Capabilities: {cap_str}")
        print(f"Registered:   {', '.join(all_domains_list)}")
        return

    print(
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

"""``muse whoami`` — show the current identity.

A top-level convenience shortcut for ``muse auth whoami``.  Returns the
identity stored in ``~/.muse/identity.toml`` for the currently configured
hub, or exits non-zero when no identity is stored.

Usage::

    muse whoami           # human-readable
    muse whoami --json    # JSON for agent consumers
    muse whoami --all     # all hubs

Exit codes::

    0 — identity found and printed
    1 — no identity stored (not authenticated)
"""

from __future__ import annotations

import argparse
import sys

import logging


from muse.core.errors import ExitCode
from muse.core.identity import IdentityEntry, list_all_identities, load_identity
from muse.core.validation import sanitize_display
from muse.cli.config import get_hub_url

logger = logging.getLogger(__name__)


def _display(hub: str, entry: IdentityEntry, *, json_output: bool) -> None:
    import json as _json
    if json_output:
        out: dict[str, str | list[str]] = {"hub": hub}
        for key in ("type", "name", "id"):
            val = entry.get(key, "")
            if isinstance(val, str) and val:
                out[key] = val
        token = entry.get("token", "")
        out["token_set"] = "true" if (isinstance(token, str) and token) else "false"
        caps: list[str] = entry.get("capabilities") or []
        if caps:
            out["capabilities"] = caps
        print(_json.dumps(out, indent=2))
    else:
        itype = entry.get("type") or "unknown"
        name = entry.get("name") or "—"
        uid = entry.get("id") or "—"
        token = entry.get("token", "")
        token_status = "set" if (isinstance(token, str) and token) else "not set"
        print(f"  hub:    {sanitize_display(hub)}")
        print(f"  type:   {sanitize_display(str(itype))}")
        print(f"  name:   {sanitize_display(str(name))}")
        print(f"  id:     {sanitize_display(str(uid))}")
        print(f"  token:  {token_status}")


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the whoami subcommand."""
    parser = subparsers.add_parser(
        "whoami",
        help='Show the current identity stored in ~/.muse/identity.toml.',
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--json", dest="json_output", action="store_true", help="Emit identity as JSON.")
    parser.add_argument("--all", dest="all_hubs", action="store_true", help="Show all hubs.")
    parser.add_argument(
        "--format",
        dest="fmt",
        default="text",
        choices=["text", "json"],
        help="Output format: text (default) or json.",
    )
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> None:
    """Show the current identity stored in ~/.muse/identity.toml.

    Exits non-zero when no identity is stored so agents can branch on
    authentication status::

        muse whoami --json || muse auth login --agent ...
        muse whoami --format json   # same as --json

    Agents should pass ``--format json`` (or ``--json``) to receive a
    machine-readable result::

        {
          "hub":          "https://musehub.ai",
          "type":         "agent",
          "name":         "muse-agent-001",
          "id":           "<uuid>",
          "token_set":    true,
          "capabilities": ["read", "write"]
        }

    Examples::

        muse whoami
        muse whoami --json
        muse whoami --format json
        muse whoami --all
    """
    json_output: bool = args.json_output
    all_hubs: bool = args.all_hubs
    fmt: str = args.fmt

    # --format json is an alias for --json for CLI consistency across all commands.
    if fmt == "json":
        json_output = True
    if all_hubs:
        identities = list_all_identities()
        if not identities:
            print("No identities stored. Run `muse auth login` to authenticate.")
            raise SystemExit(ExitCode.USER_ERROR)
        for hostname, entry in sorted(identities.items()):
            _display(hostname, entry, json_output=json_output)
        return

    hub_url = get_hub_url(None)
    if hub_url is None:
        print(
            "No hub configured. Run `muse hub connect <url>` or `muse auth login --hub <url>`."
        )
        raise SystemExit(ExitCode.USER_ERROR)

    loaded = load_identity(hub_url)
    if loaded is None:
        print(
            f"No identity stored for {hub_url}.\n"
            f"Run: muse auth login --hub {hub_url}"
        )
        raise SystemExit(ExitCode.USER_ERROR)

    hub_display = hub_url.rstrip("/").split("://")[-1].split("/")[0]
    _display(hub_display, loaded, json_output=json_output)

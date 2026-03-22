"""muse auth — identity management.

Muse has two primary user types: **humans** and **agents**.  Both are
first-class identities.  This command manages them symmetrically.

Why not ``muse config set auth.token``?
----------------------------------------
Credentials belong to the machine, not the repository.  Storing a token
inside ``.muse/config.toml`` means it could be committed to version
control, shared across repos accidentally, or tied to a single repo when
the identity is global.  Instead:

- Credentials live in ``~/.muse/identity.toml`` (mode 0o600, never
  read by the snapshot engine).
- ``config.toml`` records *where* the hub is (``[hub] url``), not *who
  you are*.
- This command owns the identity lifecycle: login, introspection, logout.

Authentication flows
---------------------
Human::

    muse auth login --hub https://musehub.ai
    # Prompts for a personal access token.

Agent (non-interactive)::

    muse auth login --hub https://musehub.ai --token $MUSE_TOKEN

Both flows accept ``--name`` and ``--id`` to store display metadata
alongside the credential.

Subcommands
-----------
::

    muse auth login    [--token TOKEN] [--hub HUB] [--name NAME]
                       [--id ID] [--agent]
    muse auth whoami   [--hub HUB] [--json]
    muse auth logout   [--hub HUB]
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import pathlib
import sys

from muse.cli.config import get_hub_url
from muse.core.errors import ExitCode
from muse.core.identity import (
    IdentityEntry,
    clear_identity,
    get_identity_path,
    list_all_identities,
    load_identity,
    save_identity,
)

logger = logging.getLogger(__name__)

_DEFAULT_HUMAN_TYPE = "human"
_DEFAULT_AGENT_TYPE = "agent"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_hub(hub_opt: str | None, repo_root: pathlib.Path | None = None) -> str | None:
    """Return the hub URL: explicit option → repo config → None."""
    if hub_opt:
        return hub_opt
    return get_hub_url(repo_root)


def _prompt_token(hub_url: str) -> str:
    """Interactively prompt for a bearer token."""
    print(f"\nAuthenticating with {hub_url}")
    print("Paste your personal access token below.")
    print("(Obtain one at your hub's settings page → Access Tokens)\n")
    import getpass
    token = getpass.getpass("Token: ")
    return token.strip()


def _display_entry(hostname: str, entry: IdentityEntry, *, json_output: bool) -> None:
    """Print an identity entry in human-readable or JSON format."""
    if json_output:
        out: dict[str, str | list[str]] = {"hub": hostname}
        itype = entry.get("type", "")
        if itype:
            out["type"] = itype
        name = entry.get("name", "")
        if name:
            out["name"] = name
        identity_id = entry.get("id", "")
        if identity_id:
            out["id"] = identity_id
        token = entry.get("token", "")
        out["token_set"] = "true" if token else "false"
        caps = entry.get("capabilities") or []
        if caps:
            out["capabilities"] = caps
        print(json.dumps(out, indent=2))
    else:
        itype = entry.get("type") or "unknown"
        name = entry.get("name") or "—"
        identity_id = entry.get("id") or "—"
        token = entry.get("token", "")
        token_status = "set (Bearer ***)" if token else "not set"
        caps = entry.get("capabilities") or []

        print("")
        print(f"  Identity")
        print(f"    Hub:    {hostname}")
        print(f"    Type:   {itype}")
        print(f"    Name:   {name}")
        print(f"    ID:     {identity_id}")
        print(f"    Token:  {token_status}")
        if caps:
            print(f"    Caps:   {' '.join(caps)}")
        print("")


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the auth subcommand."""
    parser = subparsers.add_parser(
        "auth",
        help="Identity management.",
        description=__doc__,
    )
    subs = parser.add_subparsers(dest="subcommand", metavar="SUBCOMMAND")
    subs.required = True

    login_p = subs.add_parser("login", help="Authenticate with a MuseHub instance and store credentials locally.")
    login_p.add_argument("--token", default=None, metavar="TOKEN",
                         help="Bearer token. Reads MUSE_TOKEN env var if not passed explicitly.")
    login_p.add_argument("--hub", default=None, metavar="URL",
                         help="Hub URL (e.g. https://musehub.ai). Falls back to [hub] url in config.toml.")
    login_p.add_argument("--name", default=None, metavar="NAME",
                         help="Display name for this identity (human name or agent handle).")
    login_p.add_argument("--id", default=None, metavar="ID", dest="identity_id",
                         help="Hub-assigned identity ID (optional — stored for reference).")
    login_p.add_argument("--agent", action="store_true",
                         help="Mark this identity as an agent (default: human).")
    login_p.set_defaults(func=run_login)

    whoami_p = subs.add_parser("whoami", help="Show the current identity for a hub.")
    whoami_p.add_argument("--hub", default=None, metavar="URL",
                          help="Hub URL to inspect. Defaults to the repo's configured hub.")
    whoami_p.add_argument("--all", action="store_true", dest="all_hubs",
                          help="Show identities for all configured hubs.")
    whoami_p.add_argument("--json", action="store_true", dest="json_output",
                          help="Emit JSON instead of human-readable output.")
    whoami_p.set_defaults(func=run_whoami)

    logout_p = subs.add_parser("logout", help="Remove stored credentials for a hub.")
    logout_p.add_argument("--hub", default=None, metavar="URL",
                          help="Hub URL to log out from. Defaults to the repo's configured hub.")
    logout_p.add_argument("--all", action="store_true", dest="all_hubs",
                          help="Remove credentials for ALL configured hubs.")
    logout_p.set_defaults(func=run_logout)


def run_login(args: argparse.Namespace) -> None:
    """Authenticate with a MuseHub instance and store credentials locally.

    Credentials are written to ``~/.muse/identity.toml`` (mode 0o600).
    They are never stored inside the repository.

    Human flow (interactive)::

        muse auth login --hub https://musehub.ai

    Agent flow (non-interactive)::

        muse auth login --hub https://musehub.ai --token $MUSE_TOKEN --agent
    """
    token: str | None = args.token or os.environ.get("MUSE_TOKEN")
    hub: str | None = args.hub
    name: str | None = args.name
    identity_id: str | None = args.identity_id
    agent: bool = args.agent

    hub_url = _resolve_hub(hub)
    if hub_url is None:
        print(
            "❌ No hub URL provided.\n"
            "   Pass --hub <url>, or first run: muse hub connect <url>",
        )
        raise SystemExit(ExitCode.USER_ERROR)

    # Detect whether the token came from the --token CLI flag (not from the
    # MUSE_TOKEN env var or the interactive prompt).  Tokens passed on the
    # command line appear in shell history (~/.zsh_history), process listings
    # (ps aux), and /proc/PID/cmdline on Linux.
    token_from_cli_flag = args.token is not None and os.environ.get("MUSE_TOKEN") is None

    # Resolve token: explicit --token / MUSE_TOKEN → interactive prompt.
    raw_token = token
    if not raw_token:
        raw_token = _prompt_token(hub_url)
    if not raw_token:
        print("❌ No token provided.")
        raise SystemExit(ExitCode.USER_ERROR)

    if token_from_cli_flag:
        print(
            "⚠️  Token passed via --token flag.\n"
            "   It may appear in your shell history and process listings.\n"
            "   For automation, prefer: MUSE_TOKEN=<token> muse auth login ...",
            file=sys.stderr,
        )

    identity_type = _DEFAULT_AGENT_TYPE if agent else _DEFAULT_HUMAN_TYPE

    entry: IdentityEntry = {
        "type": identity_type,
        "token": raw_token,
    }
    if name:
        entry["name"] = name
    if identity_id:
        entry["id"] = identity_id

    try:
        save_identity(hub_url, entry)
    except OSError as exc:
        print(f"❌ Could not write credentials: {exc}")
        raise SystemExit(ExitCode.INTERNAL_ERROR) from exc

    display_name = name or "<unnamed>"
    print(
        f"✅ Authenticated as {identity_type} '{display_name}' on {hub_url}\n"
        f"   Credentials stored in {get_identity_path()}"
    )


def run_whoami(args: argparse.Namespace) -> None:
    """Show the current identity for a hub.

    Reads from ``~/.muse/identity.toml``.  When no identity is stored,
    exits non-zero so agents can branch on authentication status::

        muse auth whoami --hub musehub.ai --json || muse auth login --agent ...
    """
    hub: str | None = args.hub
    all_hubs: bool = args.all_hubs
    json_output: bool = args.json_output

    if all_hubs:
        identities = list_all_identities()
        if not identities:
            print("No identities stored. Run `muse auth login` to authenticate.")
            raise SystemExit(ExitCode.USER_ERROR)
        for hostname, stored_entry in sorted(identities.items()):
            _display_entry(hostname, stored_entry, json_output=json_output)
        return

    hub_url = _resolve_hub(hub)
    if hub_url is None:
        print(
            "❌ No hub URL provided.\n"
            "   Pass --hub <url>, or first run: muse hub connect <url>",
        )
        raise SystemExit(ExitCode.USER_ERROR)

    single_entry = load_identity(hub_url)
    if single_entry is None:
        print(
            f"No identity stored for {hub_url}.\n"
            f"Run: muse auth login --hub {hub_url}",
        )
        raise SystemExit(ExitCode.USER_ERROR)

    # Normalise hostname for display
    hub_display = hub_url.rstrip("/").split("://")[-1].split("/")[0]
    _display_entry(hub_display, single_entry, json_output=json_output)


def run_logout(args: argparse.Namespace) -> None:
    """Remove stored credentials for a hub.

    The token is deleted from ``~/.muse/identity.toml``.  The hub URL in
    ``.muse/config.toml`` is preserved — use ``muse hub disconnect`` to
    remove the hub association from the repository as well.
    """
    hub: str | None = args.hub
    all_hubs: bool = args.all_hubs

    if all_hubs:
        all_identities = list_all_identities()
        if not all_identities:
            print("No identities stored.")
            return
        for hostname in list(all_identities):
            clear_identity(hostname)
        print(f"✅ Logged out from {len(all_identities)} hub(s).")
        return

    hub_url = _resolve_hub(hub)
    if hub_url is None:
        print(
            "❌ No hub URL provided.\n"
            "   Pass --hub <url>, or first run: muse hub connect <url>",
        )
        raise SystemExit(ExitCode.USER_ERROR)

    removed = clear_identity(hub_url)
    hub_display = hub_url.rstrip("/").split("://")[-1].split("/")[0]
    if removed:
        print(f"✅ Logged out from {hub_display}.")
    else:
        print(f"No identity stored for {hub_display} — nothing to do.")

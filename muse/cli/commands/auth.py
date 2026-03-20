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

import json
import logging
import os
import pathlib

import typer

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

app = typer.Typer(no_args_is_help=True)

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
    typer.echo(f"\nAuthenticating with {hub_url}")
    typer.echo("Paste your personal access token below.")
    typer.echo("(Obtain one at your hub's settings page → Access Tokens)\n")
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
        typer.echo(json.dumps(out, indent=2))
    else:
        itype = entry.get("type") or "unknown"
        name = entry.get("name") or "—"
        identity_id = entry.get("id") or "—"
        token = entry.get("token", "")
        token_status = "set (Bearer ***)" if token else "not set"
        caps = entry.get("capabilities") or []

        typer.echo("")
        typer.echo(f"  Identity")
        typer.echo(f"    Hub:    {hostname}")
        typer.echo(f"    Type:   {itype}")
        typer.echo(f"    Name:   {name}")
        typer.echo(f"    ID:     {identity_id}")
        typer.echo(f"    Token:  {token_status}")
        if caps:
            typer.echo(f"    Caps:   {' '.join(caps)}")
        typer.echo("")


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@app.command()
def login(
    token: str | None = typer.Option(
        None,
        "--token",
        metavar="TOKEN",
        envvar="MUSE_TOKEN",
        help="Bearer token. Reads MUSE_TOKEN env var if not passed explicitly.",
    ),
    hub: str | None = typer.Option(
        None,
        "--hub",
        metavar="URL",
        help="Hub URL (e.g. https://musehub.ai). Falls back to [hub] url in config.toml.",
    ),
    name: str | None = typer.Option(
        None,
        "--name",
        metavar="NAME",
        help="Display name for this identity (human name or agent handle).",
    ),
    identity_id: str | None = typer.Option(
        None,
        "--id",
        metavar="ID",
        help="Hub-assigned identity ID (optional — stored for reference).",
    ),
    agent: bool = typer.Option(
        False,
        "--agent",
        help="Mark this identity as an agent (default: human).",
    ),
) -> None:
    """Authenticate with a MuseHub instance and store credentials locally.

    Credentials are written to ``~/.muse/identity.toml`` (mode 0o600).
    They are never stored inside the repository.

    Human flow (interactive)::

        muse auth login --hub https://musehub.ai

    Agent flow (non-interactive)::

        muse auth login --hub https://musehub.ai --token $MUSE_TOKEN --agent
    """
    hub_url = _resolve_hub(hub)
    if hub_url is None:
        typer.echo(
            "❌ No hub URL provided.\n"
            "   Pass --hub <url>, or first run: muse hub connect <url>",
        )
        raise typer.Exit(code=ExitCode.USER_ERROR)

    # Resolve token
    raw_token = token
    if not raw_token:
        raw_token = _prompt_token(hub_url)
    if not raw_token:
        typer.echo("❌ No token provided.")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    identity_type = _DEFAULT_AGENT_TYPE if agent else _DEFAULT_HUMAN_TYPE

    entry: IdentityEntry = {
        "type": identity_type,
        "token": raw_token,
    }
    if name:
        entry["name"] = name
    if identity_id:
        entry["id"] = identity_id

    save_identity(hub_url, entry)

    display_name = name or "<unnamed>"
    typer.echo(
        f"✅ Authenticated as {identity_type} '{display_name}' on {hub_url}\n"
        f"   Credentials stored in {get_identity_path()}"
    )


@app.command()
def whoami(
    hub: str | None = typer.Option(
        None,
        "--hub",
        metavar="URL",
        help="Hub URL to inspect. Defaults to the repo's configured hub.",
    ),
    all_hubs: bool = typer.Option(
        False,
        "--all",
        help="Show identities for all configured hubs.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit JSON instead of human-readable output.",
    ),
) -> None:
    """Show the current identity for a hub.

    Reads from ``~/.muse/identity.toml``.  When no identity is stored,
    exits non-zero so agents can branch on authentication status::

        muse auth whoami --hub musehub.ai --json || muse auth login --agent ...
    """
    if all_hubs:
        identities = list_all_identities()
        if not identities:
            typer.echo("No identities stored. Run `muse auth login` to authenticate.")
            raise typer.Exit(code=ExitCode.USER_ERROR)
        for hostname, stored_entry in sorted(identities.items()):
            _display_entry(hostname, stored_entry, json_output=json_output)
        return

    hub_url = _resolve_hub(hub)
    if hub_url is None:
        typer.echo(
            "❌ No hub URL provided.\n"
            "   Pass --hub <url>, or first run: muse hub connect <url>",
        )
        raise typer.Exit(code=ExitCode.USER_ERROR)

    single_entry = load_identity(hub_url)
    if single_entry is None:
        typer.echo(
            f"No identity stored for {hub_url}.\n"
            f"Run: muse auth login --hub {hub_url}",
        )
        raise typer.Exit(code=ExitCode.USER_ERROR)

    # Normalise hostname for display
    hub_display = hub_url.rstrip("/").split("://")[-1].split("/")[0]
    _display_entry(hub_display, single_entry, json_output=json_output)


@app.command()
def logout(
    hub: str | None = typer.Option(
        None,
        "--hub",
        metavar="URL",
        help="Hub URL to log out from. Defaults to the repo's configured hub.",
    ),
    all_hubs: bool = typer.Option(
        False,
        "--all",
        help="Remove credentials for ALL configured hubs.",
    ),
) -> None:
    """Remove stored credentials for a hub.

    The token is deleted from ``~/.muse/identity.toml``.  The hub URL in
    ``.muse/config.toml`` is preserved — use ``muse hub disconnect`` to
    remove the hub association from the repository as well.
    """
    if all_hubs:
        all_identities = list_all_identities()
        if not all_identities:
            typer.echo("No identities stored.")
            return
        for hostname in list(all_identities):
            clear_identity(hostname)
        typer.echo(f"✅ Logged out from {len(all_identities)} hub(s).")
        return

    hub_url = _resolve_hub(hub)
    if hub_url is None:
        typer.echo(
            "❌ No hub URL provided.\n"
            "   Pass --hub <url>, or first run: muse hub connect <url>",
        )
        raise typer.Exit(code=ExitCode.USER_ERROR)

    removed = clear_identity(hub_url)
    hub_display = hub_url.rstrip("/").split("://")[-1].split("/")[0]
    if removed:
        typer.echo(f"✅ Logged out from {hub_display}.")
    else:
        typer.echo(f"No identity stored for {hub_display} — nothing to do.")

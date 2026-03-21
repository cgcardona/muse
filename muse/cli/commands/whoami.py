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

import logging
from typing import Annotated

import typer

from muse.core.errors import ExitCode
from muse.core.identity import IdentityEntry, list_all_identities, load_identity
from muse.cli.config import get_hub_url

logger = logging.getLogger(__name__)

app = typer.Typer(help="Show the current identity (shortcut for muse auth whoami).")


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
        typer.echo(_json.dumps(out, indent=2))
    else:
        itype = entry.get("type") or "unknown"
        name = entry.get("name") or "—"
        uid = entry.get("id") or "—"
        token = entry.get("token", "")
        token_status = "set" if (isinstance(token, str) and token) else "not set"
        typer.echo(f"  hub:    {hub}")
        typer.echo(f"  type:   {itype}")
        typer.echo(f"  name:   {name}")
        typer.echo(f"  id:     {uid}")
        typer.echo(f"  token:  {token_status}")


@app.callback(invoke_without_command=True)
def whoami(
    json_output: Annotated[
        bool,
        typer.Option("--json", "-j", help="Emit JSON instead of human-readable output."),
    ] = False,
    all_hubs: Annotated[
        bool,
        typer.Option("--all", "-a", help="Show identities for all configured hubs."),
    ] = False,
) -> None:
    """Show the current identity stored in ~/.muse/identity.toml.

    Exits non-zero when no identity is stored so agents can branch on
    authentication status::

        muse whoami --json || muse auth login --agent ...

    Examples::

        muse whoami
        muse whoami --json
        muse whoami --all
    """
    if all_hubs:
        identities = list_all_identities()
        if not identities:
            typer.echo("No identities stored. Run `muse auth login` to authenticate.")
            raise typer.Exit(code=ExitCode.USER_ERROR)
        for hostname, entry in sorted(identities.items()):
            _display(hostname, entry, json_output=json_output)
        return

    hub_url = get_hub_url(None)
    if hub_url is None:
        typer.echo(
            "No hub configured. Run `muse hub connect <url>` or `muse auth login --hub <url>`."
        )
        raise typer.Exit(code=ExitCode.USER_ERROR)

    loaded = load_identity(hub_url)
    if loaded is None:
        typer.echo(
            f"No identity stored for {hub_url}.\n"
            f"Run: muse auth login --hub {hub_url}"
        )
        raise typer.Exit(code=ExitCode.USER_ERROR)

    hub_display = hub_url.rstrip("/").split("://")[-1].split("/")[0]
    _display(hub_display, loaded, json_output=json_output)

"""muse config — local repository configuration.

Provides structured, typed read/write access to ``.muse/config.toml``.
For hub credentials, use ``muse auth``.  For remote connections, use
``muse remote``.

Settable namespaces
--------------------
- ``user.name``   — display name (human or agent handle)
- ``user.email``  — contact email
- ``user.type``   — ``"human"`` or ``"agent"``
- ``hub.url``     — hub fabric URL (alias for ``muse hub connect``)
- ``domain.*``    — domain-specific keys; read by the active plugin

Blocked via ``muse config set``
---------------------------------
- ``auth.*``    — use ``muse auth login``
- ``remotes.*`` — use ``muse remote add/remove``

Output format
-------------
``muse config show`` emits TOML by default (human-readable).  Pass
``--json`` for machine-readable output — no credentials are ever included.

Examples
--------
::

    muse config show
    muse config show --json
    muse config get user.name
    muse config set user.name "Alice"
    muse config set user.type agent
    muse config set domain.ticks_per_beat 480
    muse config edit
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys

import typer

from muse.cli.config import (
    config_as_dict,
    config_path_for_editor,
    get_config_value,
    set_config_value,
)
from muse.core.errors import ExitCode
from muse.core.repo import find_repo_root

logger = logging.getLogger(__name__)

app = typer.Typer(no_args_is_help=True)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@app.command()
def show(
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit JSON instead of TOML.",
    ),
) -> None:
    """Display the current repository configuration.

    Output is TOML by default.  Use ``--json`` for agent-friendly output.
    Credentials are never included regardless of format.
    """
    root = find_repo_root()

    data = config_as_dict(root)

    if json_output:
        typer.echo(json.dumps(data, indent=2))
        return

    # Render as TOML-like display
    if not data:
        typer.echo("# No configuration set.")
        return

    user = data.get("user")
    if user:
        typer.echo("[user]")
        for key, val in sorted(user.items()):
            typer.echo(f'{key} = "{val}"')
        typer.echo("")

    hub = data.get("hub")
    if hub:
        typer.echo("[hub]")
        for key, val in sorted(hub.items()):
            typer.echo(f'{key} = "{val}"')
        typer.echo("")

    remotes = data.get("remotes")
    if remotes:
        for remote_name, remote_url in sorted(remotes.items()):
            typer.echo(f"[remotes.{remote_name}]")
            typer.echo(f'url = "{remote_url}"')
            typer.echo("")

    domain = data.get("domain")
    if domain:
        typer.echo("[domain]")
        for key, val in sorted(domain.items()):
            typer.echo(f'{key} = "{val}"')
        typer.echo("")


@app.command()
def get(
    key: str = typer.Argument(
        ...,
        metavar="KEY",
        help="Dotted key to read (e.g. user.name, hub.url, domain.ticks_per_beat).",
    ),
) -> None:
    """Print the value of a single config key.

    Exits non-zero when the key is not set, so agents can branch::

        VALUE=$(muse config get user.type) || echo "not set"
    """
    root = find_repo_root()
    value = get_config_value(key, root)

    if value is None:
        typer.echo(f"# {key} is not set", err=True)
        raise typer.Exit(code=ExitCode.USER_ERROR)

    typer.echo(value)


@app.command()
def set(  # noqa: A001
    key: str = typer.Argument(
        ...,
        metavar="KEY",
        help="Dotted key to set (e.g. user.name, domain.ticks_per_beat).",
    ),
    value: str = typer.Argument(
        ...,
        metavar="VALUE",
        help="New value (always stored as a string).",
    ),
) -> None:
    """Set a config value by dotted key.

    Examples::

        muse config set user.name "Alice"
        muse config set user.type agent
        muse config set hub.url https://musehub.ai
        muse config set domain.ticks_per_beat 480

    For credentials, use ``muse auth login``.
    For remotes, use ``muse remote add``.
    """
    root = find_repo_root()
    try:
        set_config_value(key, value, root)
    except ValueError as exc:
        typer.echo(f"❌ {exc}")
        raise typer.Exit(code=ExitCode.USER_ERROR) from exc

    typer.echo(f"✅ {key} = {value!r}")


@app.command()
def edit() -> None:
    """Open ``.muse/config.toml`` in ``$EDITOR`` or ``$VISUAL``.

    Falls back to ``vi`` when neither environment variable is set.
    """
    root = find_repo_root()
    if root is None:
        typer.echo("❌ Not inside a Muse repository.")
        raise typer.Exit(code=ExitCode.REPO_NOT_FOUND)

    config_path = config_path_for_editor(root)
    if not config_path.is_file():
        typer.echo(f"❌ Config file not found: {config_path}")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "vi"
    try:
        subprocess.run([editor, str(config_path)], check=True)
    except FileNotFoundError:
        typer.echo(f"❌ Editor not found: {editor!r}")
        raise typer.Exit(code=ExitCode.USER_ERROR)
    except subprocess.CalledProcessError as exc:
        typer.echo(f"❌ Editor exited with code {exc.returncode}")
        raise typer.Exit(code=ExitCode.USER_ERROR)

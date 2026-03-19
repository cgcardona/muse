"""muse remote — manage remote repository connections.

Subcommands
-----------

    muse remote add <name> <url>         Register a new remote
    muse remote remove <name>            Remove a remote and its tracking refs
    muse remote rename <old> <new>       Rename a remote
    muse remote list [-v]                List configured remotes
    muse remote get-url <name>           Print a remote's URL
    muse remote set-url <name> <url>     Update a remote's URL

All remote URLs and tracking data are stored in ``.muse/config.toml`` and
``.muse/remotes/<name>/<branch>`` — no network calls are made by this command.
"""

from __future__ import annotations

import logging

import typer

from muse.cli.config import (
    get_remote,
    get_remote_head,
    get_upstream,
    list_remotes,
    remove_remote,
    rename_remote,
    set_remote,
)
from muse.core.errors import ExitCode
from muse.core.repo import require_repo

logger = logging.getLogger(__name__)

app = typer.Typer(no_args_is_help=True)


@app.command("add")
def remote_add(
    name: str = typer.Argument(..., help="Name for the new remote (e.g. origin)."),
    url: str = typer.Argument(..., help="URL of the remote repository."),
) -> None:
    """Register a new remote repository connection."""
    root = require_repo()
    existing = get_remote(name, root)
    if existing is not None:
        typer.echo(f"❌ Remote '{name}' already exists: {existing}")
        typer.echo(f"  Use 'muse remote set-url {name} <url>' to update it.")
        raise typer.Exit(code=ExitCode.USER_ERROR)
    set_remote(name, url, root)
    typer.echo(f"✅ Remote '{name}' added: {url}")


@app.command("remove")
def remote_remove(
    name: str = typer.Argument(..., help="Name of the remote to remove."),
) -> None:
    """Remove a remote and all its tracking refs."""
    root = require_repo()
    try:
        remove_remote(name, root)
    except KeyError:
        typer.echo(f"❌ Remote '{name}' does not exist.")
        raise typer.Exit(code=ExitCode.USER_ERROR)
    typer.echo(f"✅ Remote '{name}' removed.")


@app.command("rename")
def remote_rename(
    old_name: str = typer.Argument(..., help="Current remote name."),
    new_name: str = typer.Argument(..., help="New remote name."),
) -> None:
    """Rename a remote and move its tracking refs."""
    root = require_repo()
    try:
        rename_remote(old_name, new_name, root)
    except KeyError:
        typer.echo(f"❌ Remote '{old_name}' does not exist.")
        raise typer.Exit(code=ExitCode.USER_ERROR)
    except ValueError:
        typer.echo(f"❌ Remote '{new_name}' already exists.")
        raise typer.Exit(code=ExitCode.USER_ERROR)
    typer.echo(f"✅ Remote '{old_name}' renamed to '{new_name}'.")


@app.command("list")
def remote_list(
    verbose: bool = typer.Option(
        False, "-v", "--verbose", help="Show URL and upstream tracking branch."
    ),
) -> None:
    """List configured remote repositories."""
    root = require_repo()
    remotes = list_remotes(root)
    if not remotes:
        typer.echo("No remotes configured. Use 'muse remote add <name> <url>'.")
        return
    for r in remotes:
        if verbose:
            upstream = get_upstream(r["name"], root)
            head = get_remote_head(r["name"], upstream or "main", root)
            head_str = f" @ {head[:8]}" if head else ""
            tracking = f" -> {r['name']}/{upstream}" if upstream else ""
            typer.echo(f"{r['name']}\t{r['url']}{tracking}{head_str}")
        else:
            typer.echo(r["name"])


@app.command("get-url")
def remote_get_url(
    name: str = typer.Argument(..., help="Remote name."),
) -> None:
    """Print the URL of a remote."""
    root = require_repo()
    url = get_remote(name, root)
    if url is None:
        typer.echo(f"❌ Remote '{name}' does not exist.")
        raise typer.Exit(code=ExitCode.USER_ERROR)
    typer.echo(url)


@app.command("set-url")
def remote_set_url(
    name: str = typer.Argument(..., help="Remote name."),
    url: str = typer.Argument(..., help="New URL for the remote."),
) -> None:
    """Update the URL of an existing remote."""
    root = require_repo()
    existing = get_remote(name, root)
    if existing is None:
        typer.echo(f"❌ Remote '{name}' does not exist.")
        typer.echo(f"  Use 'muse remote add {name} <url>' to create it.")
        raise typer.Exit(code=ExitCode.USER_ERROR)
    set_remote(name, url, root)
    typer.echo(f"✅ Remote '{name}' URL updated: {url}")

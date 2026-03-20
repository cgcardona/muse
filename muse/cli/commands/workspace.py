"""``muse workspace`` — compose multiple Muse repositories.

A workspace links several independent Muse repos together under a single
manifest, giving you a unified status view, one-shot sync, and a clear model
for multi-repo projects.

Subcommands::

    muse workspace add <name> <url> [--path repos/<name>] [--branch main]
    muse workspace list
    muse workspace remove <name>
    muse workspace status
    muse workspace sync [<name>]

Example workflow::

    # Create a workspace manifest in the current repo
    muse workspace add core   https://musehub.ai/acme/core
    muse workspace add sounds https://musehub.ai/acme/sounds --branch v2

    # Clone or pull all members
    muse workspace sync

    # Show status of all members
    muse workspace status
"""

from __future__ import annotations

import logging
from typing import Annotated

import typer

from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.validation import sanitize_display
from muse.core.workspace import (
    add_workspace_member,
    list_workspace_members,
    remove_workspace_member,
    sync_workspace,
)

logger = logging.getLogger(__name__)
app = typer.Typer(
    help="Compose and manage multi-repository workspaces.",
    no_args_is_help=True,
)


@app.command("add")
def workspace_add(
    name: str = typer.Argument(..., help="Short name for the member repo."),
    url: str = typer.Argument(..., help="URL or local path to the member Muse repo."),
    path: Annotated[
        str,
        typer.Option("--path", help="Relative checkout path inside the workspace (default: repos/<name>)."),
    ] = "",
    branch: Annotated[
        str,
        typer.Option("--branch", "-b", help="Branch to track (default: main)."),
    ] = "main",
) -> None:
    """Add a member repository to the workspace manifest.

    The member is *registered* in ``.muse/workspace.toml``.  Run
    ``muse workspace sync`` to clone it.

    Examples::

        muse workspace add core https://musehub.ai/acme/core
        muse workspace add dataset /path/to/local/dataset --branch v2
    """
    root = require_repo()
    try:
        add_workspace_member(root, name, url, path=path, branch=branch)
    except ValueError as exc:
        typer.echo(f"❌ {exc}")
        raise typer.Exit(code=ExitCode.USER_ERROR)
    typer.echo(f"✅ Added workspace member '{sanitize_display(name)}'  ({sanitize_display(url)})")
    typer.echo("   Run 'muse workspace sync' to clone it.")


@app.command("remove")
def workspace_remove(
    name: str = typer.Argument(..., help="Name of the member to remove."),
) -> None:
    """Remove a member from the workspace manifest.

    This does **not** delete the member's directory — only its registration
    in the workspace manifest is removed.
    """
    root = require_repo()
    try:
        remove_workspace_member(root, name)
    except ValueError as exc:
        typer.echo(f"❌ {exc}")
        raise typer.Exit(code=ExitCode.USER_ERROR)
    typer.echo(f"✅ Removed workspace member '{sanitize_display(name)}'.")


@app.command("list")
def workspace_list() -> None:
    """List all workspace members from the manifest."""
    root = require_repo()
    members = list_workspace_members(root)
    if not members:
        typer.echo("No workspace members.  Add one with 'muse workspace add'.")
        return
    header = f"{'name':<20} {'branch':<16} {'present':<8} {'HEAD':12}  url"
    typer.echo(header)
    typer.echo("-" * len(header))
    for m in members:
        present_str = "yes" if m.present else "no"
        head_str = m.head_commit[:12] if m.head_commit else "(not cloned)"
        url_short = sanitize_display(m.url[:50])
        typer.echo(
            f"{sanitize_display(m.name):<20} "
            f"{sanitize_display(m.branch):<16} "
            f"{present_str:<8} "
            f"{head_str:<12}  {url_short}"
        )


@app.command("status")
def workspace_status() -> None:
    """Show status of all workspace members (clone state, HEAD, branch)."""
    root = require_repo()
    members = list_workspace_members(root)
    if not members:
        typer.echo("No workspace members.  Add one with 'muse workspace add'.")
        return
    typer.echo(f"Workspace: {root}\n")
    for m in members:
        icon = "✅" if m.present else "❌"
        head = m.head_commit[:12] if m.head_commit else "not cloned"
        typer.echo(f"{icon}  {sanitize_display(m.name):<20}  branch={sanitize_display(m.branch)}  head={head}")
        typer.echo(f"     path: {m.path}")
        typer.echo(f"     url:  {sanitize_display(m.url)}")


@app.command("sync")
def workspace_sync(
    name: Annotated[
        str | None,
        typer.Argument(help="Sync only this member (default: sync all)."),
    ] = None,
) -> None:
    """Clone or pull the latest state for workspace members.

    Run without arguments to sync all members.  Provide a member name to
    sync only that one.

    Examples::

        muse workspace sync         # sync everything
        muse workspace sync core    # sync only 'core'
    """
    root = require_repo()
    results = sync_workspace(root, member_name=name)
    if not results:
        typer.echo("No members to sync.  Add one with 'muse workspace add'.")
        return
    for member_name, status in results:
        icon = "✅" if not status.startswith("error") else "❌"
        typer.echo(f"{icon}  {sanitize_display(member_name)}: {sanitize_display(status)}")

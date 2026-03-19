"""muse ls-remote — list references on a remote repository.

Plumbing command that contacts the remote and prints every branch and its
current commit ID without modifying any local state.  Useful for scripting,
agent coordination, and pre-flight checks before push/pull.

Output format (default)::

    <commit_id>\\t<branch>

Output format (--json)::

    {"branches": {"main": "<commit_id>", ...}, "repo_id": "...", "domain": "..."}
"""

from __future__ import annotations

import json
import logging

import typer

from muse.cli.config import get_auth_token, get_remote
from muse.core.errors import ExitCode
from muse.core.repo import find_repo_root
from muse.core.transport import HttpTransport, TransportError

logger = logging.getLogger(__name__)

app = typer.Typer()


@app.callback(invoke_without_command=True)
def ls_remote(
    ctx: typer.Context,
    remote_or_url: str = typer.Argument(
        "origin",
        help="Remote name (e.g. 'origin') or a full URL. Defaults to 'origin'.",
    ),
    output_json: bool = typer.Option(
        False, "--json", help="Emit JSON for agent consumption."
    ),
) -> None:
    """List branches and commit IDs on a remote.

    Contacts the remote and prints each branch HEAD without altering any local
    state.  Pass a remote name (configured via ``muse remote add``) or a full
    URL.  Use ``--json`` for structured output.
    """
    import pathlib

    root = find_repo_root(pathlib.Path.cwd())
    token: str | None = None

    # Resolve URL: accept either a named remote or a bare URL.
    url: str | None = None
    if root is not None:
        token = get_auth_token(root)
        url = get_remote(remote_or_url, root)

    if url is None:
        # Treat the argument as a bare URL if it looks like one.
        if remote_or_url.startswith("http://") or remote_or_url.startswith("https://"):
            url = remote_or_url
        else:
            typer.echo(
                f"❌ '{remote_or_url}' is not a configured remote and does not "
                "look like a URL."
            )
            typer.echo("  Configure it with: muse remote add <name> <url>")
            raise typer.Exit(code=ExitCode.USER_ERROR)

    transport = HttpTransport()
    try:
        info = transport.fetch_remote_info(url, token)
    except TransportError as exc:
        typer.echo(f"❌ Cannot reach remote: {exc}")
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)

    if output_json:
        typer.echo(
            json.dumps(
                {
                    "repo_id": info["repo_id"],
                    "domain": info["domain"],
                    "default_branch": info["default_branch"],
                    "branches": info["branch_heads"],
                },
                indent=2,
            )
        )
        return

    if not info["branch_heads"]:
        typer.echo("(no branches)")
        return

    for branch, commit_id in sorted(info["branch_heads"].items()):
        marker = " *" if branch == info["default_branch"] else ""
        typer.echo(f"{commit_id}\t{branch}{marker}")

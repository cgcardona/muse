"""muse push — upload local commits, snapshots, and objects to a remote.

Computes the set of commits the remote lacks (local branch HEAD vs the last
known remote tracking pointer), bundles them with all referenced snapshots and
objects, and uploads the bundle to MuseHub.

Fast-forward check
------------------

By default, ``muse push`` requires the remote branch to be an ancestor of the
local branch (a fast-forward update).  If the remote has diverged, the push is
rejected with exit code 1.  Pass ``--force`` to bypass this check.

Upstream tracking
-----------------

Pass ``-u`` / ``--set-upstream`` on first push to record the tracking
relationship between the local branch and the remote branch so that future
``muse pull`` and ``muse push`` invocations can resolve the remote automatically.
"""

from __future__ import annotations

import logging
import pathlib

import typer

from muse.cli.config import (
    get_auth_token,
    get_remote,
    get_remote_head,
    get_upstream,
    set_remote_head,
    set_upstream,
)
from muse.core.errors import ExitCode
from muse.core.pack import build_pack
from muse.core.repo import require_repo
from muse.core.store import get_head_commit_id, read_current_branch
from muse.core.transport import TransportError, make_transport

logger = logging.getLogger(__name__)

app = typer.Typer()


def _current_branch(root: pathlib.Path) -> str:
    """Return the current branch name from ``.muse/HEAD``."""
    return read_current_branch(root)


@app.callback(invoke_without_command=True)
def push(
    ctx: typer.Context,
    remote: str = typer.Argument(
        "origin", help="Remote name to push to (default: origin)."
    ),
    branch: str | None = typer.Option(
        None, "--branch", "-b", help="Local branch to push (default: current branch)."
    ),
    set_upstream_flag: bool = typer.Option(
        False, "-u", "--set-upstream", help="Record upstream tracking for this branch."
    ),
    force: bool = typer.Option(
        False, "--force", help="Force push even if the remote has diverged."
    ),
) -> None:
    """Upload local commits, snapshots, and objects to a remote.

    Requires the remote to be a fast-forward of the local branch unless
    ``--force`` is specified.
    """
    root = require_repo()

    url = get_remote(remote, root)
    if url is None:
        typer.echo(f"❌ Remote '{remote}' is not configured.")
        typer.echo(f"  Add it with: muse remote add {remote} <url>")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    token = get_auth_token(root, remote_url=url)
    current_branch = _current_branch(root)
    push_branch = branch or get_upstream(current_branch, root) or current_branch

    local_head = get_head_commit_id(root, push_branch)
    if local_head is None:
        typer.echo(f"❌ Branch '{push_branch}' has no commits to push.")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    # Determine what the remote already has (via tracking pointer).
    remote_head = get_remote_head(remote, push_branch, root)
    have: list[str] = [remote_head] if remote_head else []

    if remote_head == local_head:
        typer.echo(f"Everything up to date. Remote {remote}/{push_branch} is already at {local_head[:8]}.")
        return

    typer.echo(f"Pushing {push_branch} → {remote}/{push_branch} …")

    bundle = build_pack(root, [local_head], have=have)

    transport = make_transport(url)
    try:
        result = transport.push_pack(url, token, bundle, push_branch, force)
    except TransportError as exc:
        if exc.status_code == 409:
            typer.echo(
                f"❌ Push rejected — remote '{remote}/{push_branch}' has diverged.\n"
                "  Pull first (muse pull) or use --force to override."
            )
        else:
            typer.echo(f"❌ Push failed: {exc}")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    if not result["ok"]:
        typer.echo(f"❌ Push rejected by remote: {result['message']}")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    # Update local tracking pointer to reflect the new remote state.
    updated_head = result["branch_heads"].get(push_branch, local_head)
    set_remote_head(remote, push_branch, updated_head, root)

    if set_upstream_flag:
        set_upstream(push_branch, remote, root)
        typer.echo(f"  Upstream set: {push_branch} → {remote}/{push_branch}")

    commits_sent = len(bundle.get("commits") or [])
    objects_sent = len(bundle.get("objects") or [])
    typer.echo(
        f"✅ Pushed {commits_sent} commit(s), {objects_sent} object(s) "
        f"to {remote}/{push_branch} ({updated_head[:8]})"
    )
    if result["message"]:
        typer.echo(f"   {result['message']}")

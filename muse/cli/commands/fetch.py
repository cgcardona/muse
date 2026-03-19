"""muse fetch — download commits, snapshots, and objects from a remote.

Fetches the latest state of a remote branch without touching the local branch
HEAD or working tree.  After a successful fetch:

- All new commits, snapshots, and objects from the remote are stored locally.
- The remote tracking pointer ``.muse/remotes/<remote>/<branch>`` is updated.

Use ``muse pull`` to fetch *and* merge into the current branch, or run
``muse merge`` after fetching to integrate on your own schedule.
"""

from __future__ import annotations

import logging
import pathlib

import typer

from muse.cli.config import get_auth_token, get_remote, get_upstream, set_remote_head
from muse.core.errors import ExitCode
from muse.core.pack import apply_pack
from muse.core.repo import require_repo
from muse.core.store import get_all_commits
from muse.core.transport import HttpTransport, TransportError

logger = logging.getLogger(__name__)

app = typer.Typer()


def _current_branch(root: pathlib.Path) -> str:
    """Return the current branch name from ``.muse/HEAD``."""
    head_ref = (root / ".muse" / "HEAD").read_text().strip()
    return head_ref.removeprefix("refs/heads/").strip()


@app.callback(invoke_without_command=True)
def fetch(
    ctx: typer.Context,
    remote: str = typer.Argument(
        "origin", help="Remote name to fetch from (default: origin)."
    ),
    branch: str | None = typer.Option(
        None, "--branch", "-b", help="Remote branch to fetch (default: tracked branch or current branch)."
    ),
) -> None:
    """Download commits, snapshots, and objects from a remote.

    Updates the remote tracking pointer but does NOT change the local branch
    HEAD or working tree.  Run ``muse pull`` to fetch and merge in one step.
    """
    root = require_repo()

    url = get_remote(remote, root)
    if url is None:
        typer.echo(f"❌ Remote '{remote}' is not configured.")
        typer.echo(f"  Add it with: muse remote add {remote} <url>")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    token = get_auth_token(root)
    current_branch = _current_branch(root)
    target_branch = branch or get_upstream(current_branch, root) or current_branch

    transport = HttpTransport()

    try:
        info = transport.fetch_remote_info(url, token)
    except TransportError as exc:
        typer.echo(f"❌ Cannot reach remote '{remote}': {exc}")
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)

    remote_commit_id = info["branch_heads"].get(target_branch)
    if remote_commit_id is None:
        typer.echo(
            f"❌ Branch '{target_branch}' does not exist on remote '{remote}'."
        )
        raise typer.Exit(code=ExitCode.USER_ERROR)

    # Collect local commit IDs so the server can send only the delta.
    local_commit_ids = [c.commit_id for c in get_all_commits(root)]

    typer.echo(f"Fetching {remote}/{target_branch} …")

    try:
        bundle = transport.fetch_pack(
            url, token, want=[remote_commit_id], have=local_commit_ids
        )
    except TransportError as exc:
        typer.echo(f"❌ Fetch failed: {exc}")
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)

    new_objects = apply_pack(root, bundle)
    set_remote_head(remote, target_branch, remote_commit_id, root)

    commits_received = len(bundle.get("commits") or [])
    typer.echo(
        f"✅ Fetched {commits_received} commit(s), {new_objects} new object(s) "
        f"from {remote}/{target_branch} ({remote_commit_id[:8]})"
    )

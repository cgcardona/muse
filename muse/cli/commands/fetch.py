"""muse fetch — download commits, snapshots, and objects from a remote.

Fetches the latest state of a remote branch without touching the local branch
HEAD or working tree.  After a successful fetch:

- All new commits, snapshots, and objects from the remote are stored locally.
- The remote tracking pointer ``.muse/remotes/<remote>/<branch>`` is updated.

Use ``muse pull`` to fetch *and* merge into the current branch, or run
``muse merge`` after fetching to integrate on your own schedule.
"""

from __future__ import annotations

import argparse
import logging
import pathlib
import sys

from muse.cli.config import get_auth_token, get_remote, get_upstream, set_remote_head
from muse.core.errors import ExitCode
from muse.core.pack import apply_pack
from muse.core.repo import require_repo
from muse.core.store import get_all_commits, read_current_branch
from muse.core.transport import TransportError, make_transport

logger = logging.getLogger(__name__)


def _current_branch(root: pathlib.Path) -> str:
    """Return the current branch name from ``.muse/HEAD``."""
    return read_current_branch(root)


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the fetch subcommand."""
    parser = subparsers.add_parser(
        "fetch",
        help="Download commits, snapshots, and objects from a remote.",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("remote", nargs="?", default="origin", help="Remote name to fetch from (default: origin).")
    parser.add_argument("--branch", "-b", default=None,
                        help="Remote branch to fetch (default: tracked branch or current branch).")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> None:
    """Download commits, snapshots, and objects from a remote.

    Updates the remote tracking pointer but does NOT change the local branch
    HEAD or working tree.  Run ``muse pull`` to fetch and merge in one step.
    """
    remote: str = args.remote
    branch: str | None = args.branch

    root = require_repo()

    url = get_remote(remote, root)
    if url is None:
        print(f"❌ Remote '{remote}' is not configured.")
        print(f"  Add it with: muse remote add {remote} <url>")
        raise SystemExit(ExitCode.USER_ERROR)

    token = get_auth_token(root)
    current_branch = _current_branch(root)
    target_branch = branch or get_upstream(current_branch, root) or current_branch

    transport = make_transport(url)

    try:
        info = transport.fetch_remote_info(url, token)
    except TransportError as exc:
        print(f"❌ Cannot reach remote '{remote}': {exc}")
        raise SystemExit(ExitCode.INTERNAL_ERROR)

    remote_commit_id = info["branch_heads"].get(target_branch)
    if remote_commit_id is None:
        print(
            f"❌ Branch '{target_branch}' does not exist on remote '{remote}'."
        )
        raise SystemExit(ExitCode.USER_ERROR)

    # Collect local commit IDs so the server can send only the delta.
    local_commit_ids = [c.commit_id for c in get_all_commits(root)]

    print(f"Fetching {remote}/{target_branch} …")

    try:
        bundle = transport.fetch_pack(
            url, token, want=[remote_commit_id], have=local_commit_ids
        )
    except TransportError as exc:
        print(f"❌ Fetch failed: {exc}")
        raise SystemExit(ExitCode.INTERNAL_ERROR)

    apply_result = apply_pack(root, bundle)
    set_remote_head(remote, target_branch, remote_commit_id, root)

    commits_received = len(bundle.get("commits") or [])
    print(
        f"✅ Fetched {commits_received} commit(s), {apply_result['objects_written']} new object(s) "
        f"from {remote}/{target_branch} ({remote_commit_id[:8]})"
    )

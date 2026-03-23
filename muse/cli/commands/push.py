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

import argparse
import logging
import pathlib
import sys

from muse.cli.config import (
    get_auth_token,
    get_remote,
    get_remote_head,
    set_remote_head,
    set_upstream,
)
from muse.core.errors import ExitCode
from muse.core.pack import PackBundle, RemoteInfo, build_pack
from muse.core.repo import require_repo
from muse.core.store import get_head_commit_id, read_current_branch
from muse.core.transport import MuseTransport, TransportError, make_transport

logger = logging.getLogger(__name__)


def _current_branch(root: pathlib.Path) -> str:
    """Return the current branch name from ``.muse/HEAD``."""
    return read_current_branch(root)


def _fetch_remote_info_safe(
    transport: MuseTransport,
    url: str,
    token: str | None,
) -> RemoteInfo | None:
    """Call GET /refs on the remote and return its current branch heads.

    Returns ``None`` on any transport error so callers can fall back
    gracefully instead of aborting the whole push.
    """
    try:
        return transport.fetch_remote_info(url, token)
    except TransportError:
        return None


def _all_known_have_anchors(root: pathlib.Path) -> list[str]:
    """Return every commit ID cached in any remote's tracking refs.

    When pushing a new branch (or to a remote with no local tracking cache),
    these commits are our best guess at what the remote already holds.  Any
    remote the user has previously pushed to shares commit ancestry with other
    remotes — using all cached heads as ``have`` anchors ensures ``build_pack``
    only transmits the delta since the nearest shared ancestor.
    """
    remotes_dir = root / ".muse" / "remotes"
    if not remotes_dir.is_dir():
        return []
    heads: list[str] = []
    for ref_file in remotes_dir.rglob("*"):
        if ref_file.is_file():
            commit_id = ref_file.read_text().strip()
            if commit_id:
                heads.append(commit_id)
    return heads


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the push subcommand."""
    parser = subparsers.add_parser(
        "push",
        help="Upload local commits, snapshots, and objects to a remote.",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("remote", nargs="?", default="origin",
                        help="Remote name to push to (default: origin).")
    parser.add_argument("branch_pos", nargs="?", default=None, metavar="BRANCH",
                        help="Branch to push (default: current branch). Same as --branch.")
    parser.add_argument("--branch", "-b", default=None, dest="branch_flag",
                        help="Branch to push (default: current branch).")
    parser.add_argument("-u", "--set-upstream", action="store_true", dest="set_upstream_flag",
                        help="Record upstream tracking for this branch.")
    parser.add_argument("--force", action="store_true", help="Force push even if the remote has diverged.")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> None:
    """Upload local commits, snapshots, and objects to a remote.

    Requires the remote to be a fast-forward of the local branch unless
    ``--force`` is specified.
    """
    remote: str = args.remote
    branch: str | None = getattr(args, "branch_flag", None) or getattr(args, "branch_pos", None)
    set_upstream_flag: bool = args.set_upstream_flag
    force: bool = args.force

    root = require_repo()

    url = get_remote(remote, root)
    if url is None:
        print(f"❌ Remote '{remote}' is not configured.")
        print(f"  Add it with: muse remote add {remote} <url>")
        raise SystemExit(ExitCode.USER_ERROR)

    token = get_auth_token(root, remote_url=url)
    current_branch = _current_branch(root)
    push_branch = branch or current_branch

    local_head = get_head_commit_id(root, push_branch)
    if local_head is None:
        print(f"❌ Branch '{push_branch}' has no commits to push.")
        raise SystemExit(ExitCode.USER_ERROR)

    transport = make_transport(url)

    # Ask the remote what it already has so we never send redundant objects.
    # This single GET /refs call is cheap and gives us authoritative have-anchors
    # regardless of whether we've cached tracking refs locally.
    remote_info = _fetch_remote_info_safe(transport, url, token)
    remote_branch_heads = remote_info["branch_heads"] if remote_info else {}

    # Collect candidate have-anchors from two sources:
    #   1. Live branch heads from GET /refs (what the remote claims to have)
    #   2. All cached tracking refs across every configured remote (commits we
    #      know are shared ancestry because we've pushed them before)
    # Then filter to only commits that exist in the LOCAL object store —
    # build_pack's BFS can only stop at commits it can walk through locally.
    # Commits from the live remote often don't exist locally (e.g. GitHub
    # merge commits never fetched), so without filtering they become no-ops
    # and build_pack falls back to walking the entire history.
    candidate_have = list(remote_branch_heads.values()) + _all_known_have_anchors(root)
    commits_dir = root / ".muse" / "commits"
    # Exclude local_head itself — if it appears in `have` (e.g. because another
    # remote already has this branch), build_pack stops immediately and sends
    # nothing, even though the target remote doesn't have the branch yet.
    have: list[str] = [
        c for c in candidate_have
        if c != local_head and (commits_dir / f"{c}.json").exists()
    ]

    remote_head = remote_branch_heads.get(push_branch) or get_remote_head(remote, push_branch, root)

    if remote_head == local_head:
        print(f"Everything up to date. Remote {remote}/{push_branch} is already at {local_head[:8]}.")
        return

    print(f"Pushing {push_branch} → {remote}/{push_branch} …")

    bundle: PackBundle = build_pack(root, [local_head], have=have)

    try:
        result = transport.push_pack(url, token, bundle, push_branch, force)
    except TransportError as exc:
        if exc.status_code == 409:
            print(
                f"❌ Push rejected — remote '{remote}/{push_branch}' has diverged.\n"
                "  Pull first (muse pull) or use --force to override."
            )
        else:
            print(f"❌ Push failed: {exc}")
        raise SystemExit(ExitCode.USER_ERROR)

    if not result["ok"]:
        print(f"❌ Push rejected by remote: {result['message']}")
        raise SystemExit(ExitCode.USER_ERROR)

    # Update local tracking pointer to reflect the new remote state.
    updated_head = result["branch_heads"].get(push_branch, local_head)
    set_remote_head(remote, push_branch, updated_head, root)

    if set_upstream_flag:
        set_upstream(push_branch, remote, root)
        print(f"  Upstream set: {push_branch} → {remote}/{push_branch}")

    commits_sent = len(bundle.get("commits") or [])
    objects_sent = len(bundle.get("objects") or [])
    print(
        f"✅ Pushed {commits_sent} commit(s), {objects_sent} object(s) "
        f"to {remote}/{push_branch} ({updated_head[:8]})"
    )

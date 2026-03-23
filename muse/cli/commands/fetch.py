"""muse fetch — download commits, snapshots, and objects from a remote.

Fetches the latest state of a remote branch without touching the local branch
HEAD or working tree.  After a successful fetch:

- All new commits, snapshots, and objects from the remote are stored locally.
- The remote tracking pointer ``.muse/remotes/<remote>/<branch>`` is updated.

Use ``muse pull`` to fetch *and* merge into the current branch, or run
``muse merge`` after fetching to integrate on your own schedule.

Flags
-----
``--all``
    Fetch every configured remote instead of just one.

``--prune / -p``
    After fetching, delete local remote-tracking refs (pointers under
    ``.muse/remotes/<remote>/``) for branches that no longer exist on the
    remote.  Mirrors ``git fetch --prune``.

``--dry-run / -n``
    Show what would be fetched without writing anything.

``--tags``
    Also fetch tags from the remote (default behaviour when tags exist).

``--no-tags``
    Do not fetch tags from the remote.
"""

from __future__ import annotations

import argparse
import logging
import pathlib
import shutil
import sys

from muse.cli.config import (
    get_auth_token,
    get_remote,
    get_remote_head,
    list_remotes,
    set_remote_head,
)
from muse.core.errors import ExitCode
from muse.core.pack import apply_pack
from muse.core.repo import require_repo
from muse.core.store import get_all_commits, read_current_branch
from muse.core.transport import TransportError, make_transport

logger = logging.getLogger(__name__)

_MUSE_DIR = ".muse"


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the fetch subcommand."""
    parser = subparsers.add_parser(
        "fetch",
        help="Download commits, snapshots, and objects from a remote.",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "remote",
        nargs="?",
        default="origin",
        help="Remote name to fetch from (default: origin). Ignored when --all is set.",
    )
    parser.add_argument(
        "--branch", "-b",
        default=None,
        help="Remote branch to fetch (default: current branch). Ignored when --all is set.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        default=False,
        help="Fetch all configured remotes.",
    )
    parser.add_argument(
        "--prune", "-p",
        action="store_true",
        default=False,
        help=(
            "Remove local remote-tracking refs for branches that no longer exist "
            "on the remote (mirrors git fetch --prune)."
        ),
    )
    parser.add_argument(
        "--dry-run", "-n",
        action="store_true",
        default=False,
        dest="dry_run",
        help="Show what would be fetched without writing any objects or tracking refs.",
    )
    # Tag handling flags — reserved for future use when tag storage is added.
    tag_group = parser.add_mutually_exclusive_group()
    tag_group.add_argument(
        "--tags",
        action="store_true",
        default=None,
        dest="tags",
        help="Fetch tags from the remote (default).",
    )
    tag_group.add_argument(
        "--no-tags",
        action="store_false",
        dest="tags",
        help="Do not fetch tags from the remote.",
    )
    parser.set_defaults(func=run)


def _fetch_one(
    root: pathlib.Path,
    remote: str,
    branch: str,
    *,
    prune: bool,
    dry_run: bool,
) -> int:
    """Fetch a single remote/branch pair.

    Returns the number of new commits received, or raises SystemExit on error.
    Prunes stale remote-tracking refs when *prune* is True and the fetch
    succeeds.  Writes nothing when *dry_run* is True.
    """
    url = get_remote(remote, root)
    if url is None:
        print(f"❌ Remote '{remote}' is not configured.")
        print(f"  Add it with: muse remote add {remote} <url>")
        raise SystemExit(ExitCode.USER_ERROR)

    token = get_auth_token(root, remote_url=url)
    transport = make_transport(url)

    try:
        info = transport.fetch_remote_info(url, token)
    except TransportError as exc:
        print(f"❌ Cannot reach remote '{remote}': {exc}")
        raise SystemExit(ExitCode.INTERNAL_ERROR)

    remote_commit_id = info["branch_heads"].get(branch)
    if remote_commit_id is None:
        print(f"❌ Branch '{branch}' does not exist on remote '{remote}'.")
        print(f"  Available branches: {', '.join(sorted(info['branch_heads']))}")
        raise SystemExit(ExitCode.USER_ERROR)

    already_known = get_remote_head(remote, branch, root)
    if already_known == remote_commit_id:
        print(f"✅ {remote}/{branch} is already up to date ({remote_commit_id[:8]})")
        if prune and not dry_run:
            _prune_stale_refs(root, remote, info["branch_heads"])
        return 0

    if dry_run:
        print(f"  Would fetch {remote}/{branch} → {remote_commit_id[:8]}")
        return 0

    local_commit_ids = [c.commit_id for c in get_all_commits(root)]

    print(f"Fetching {remote}/{branch} …")

    try:
        bundle = transport.fetch_pack(
            url, token, want=[remote_commit_id], have=local_commit_ids
        )
    except TransportError as exc:
        print(f"❌ Fetch failed: {exc}")
        raise SystemExit(ExitCode.INTERNAL_ERROR)

    apply_result = apply_pack(root, bundle)
    set_remote_head(remote, branch, remote_commit_id, root)

    commits_received = len(bundle.get("commits") or [])
    print(
        f"✅ Fetched {commits_received} commit(s), "
        f"{apply_result['objects_written']} new object(s) "
        f"from {remote}/{branch} ({remote_commit_id[:8]})"
    )

    if prune:
        _prune_stale_refs(root, remote, info["branch_heads"])

    return commits_received


def _prune_stale_refs(
    root: pathlib.Path,
    remote: str,
    live_branch_heads: dict[str, str],
) -> None:
    """Remove tracking-ref files for branches that no longer exist on *remote*.

    Scans ``.muse/remotes/<remote>/`` and deletes any file whose name is not
    a key in *live_branch_heads*.
    """
    refs_dir = root / _MUSE_DIR / "remotes" / remote
    if not refs_dir.is_dir():
        return

    pruned: list[str] = []
    for ref_file in refs_dir.iterdir():
        if ref_file.is_file() and ref_file.name not in live_branch_heads:
            ref_file.unlink()
            pruned.append(ref_file.name)
            logger.debug("🗑  Pruned stale tracking ref %s/%s", remote, ref_file.name)

    if pruned:
        for branch_name in sorted(pruned):
            print(f" - [deleted]  {remote}/{branch_name}")


def run(args: argparse.Namespace) -> None:
    """Download commits, snapshots, and objects from a remote.

    Updates the remote tracking pointer but does NOT change the local branch
    HEAD or working tree.  Run ``muse pull`` to fetch and merge in one step.

    When ``--all`` is passed every configured remote is fetched; the positional
    ``remote`` argument and ``--branch`` are ignored.

    When ``--prune`` is passed, stale remote-tracking refs (branches that no
    longer exist on the remote) are deleted after a successful fetch.
    """
    root = require_repo()
    current_branch = read_current_branch(root)
    dry_run: bool = args.dry_run
    prune: bool = args.prune

    if dry_run:
        print("(dry run — no objects or refs will be written)")

    if args.all:
        remotes = list_remotes(root)
        if not remotes:
            print("❌ No remotes configured.")
            print("  Add one with: muse remote add <name> <url>")
            raise SystemExit(ExitCode.USER_ERROR)
        for remote_cfg in remotes:
            # For --all, always fetch the current branch from every remote.
            _fetch_one(
                root,
                remote_cfg["name"],
                current_branch,
                prune=prune,
                dry_run=dry_run,
            )
        return

    remote: str = args.remote
    # Use the explicitly passed branch, or fall back to the current local branch.
    # Do NOT use get_upstream() here — that returns the remote *name*, not branch.
    branch: str = args.branch or current_branch

    _fetch_one(root, remote, branch, prune=prune, dry_run=dry_run)

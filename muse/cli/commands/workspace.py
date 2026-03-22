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

import argparse
import sys

import logging


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


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the workspace subcommand."""
    parser = subparsers.add_parser(
        "workspace",
        help="Compose multiple Muse repositories.",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subs = parser.add_subparsers(dest="subcommand", metavar="SUBCOMMAND")
    subs.required = True

    add_p = subs.add_parser("add", help="Add a member repository to the workspace manifest.")
    add_p.add_argument("name", metavar="NAME", help="Member name.")
    add_p.add_argument("url", metavar="URL", help="Remote URL of the member repository.")
    add_p.add_argument("--path", default="", metavar="PATH", help="Local path for the member (default: repos/<name>).")
    add_p.add_argument("--branch", default="main", metavar="BRANCH", help="Branch to track (default: main).")
    add_p.set_defaults(func=run_workspace_add)

    list_p = subs.add_parser("list", help="List all workspace members from the manifest.")
    list_p.set_defaults(func=run_workspace_list)

    remove_p = subs.add_parser("remove", help="Remove a member from the workspace manifest.")
    remove_p.add_argument("name", metavar="NAME", help="Member name to remove.")
    remove_p.set_defaults(func=run_workspace_remove)

    status_p = subs.add_parser("status", help="Show status of all workspace members.")
    status_p.set_defaults(func=run_workspace_status)

    sync_p = subs.add_parser("sync", help="Clone or pull the latest state for workspace members.")
    sync_p.add_argument("name", nargs="?", default=None, metavar="NAME", help="Sync only this member (default: all).")
    sync_p.set_defaults(func=run_workspace_sync)


def run_workspace_add(args: argparse.Namespace) -> None:
    """Add a member repository to the workspace manifest.

    The member is *registered* in ``.muse/workspace.toml``.  Run
    ``muse workspace sync`` to clone it.

    Examples::

        muse workspace add core https://musehub.ai/acme/core
        muse workspace add dataset /path/to/local/dataset --branch v2
    """
    name: str = args.name
    url: str = args.url
    path: str = args.path
    branch: str = args.branch

    root = require_repo()
    try:
        add_workspace_member(root, name, url, path=path, branch=branch)
    except ValueError as exc:
        print(f"❌ {exc}")
        raise SystemExit(ExitCode.USER_ERROR)
    print(f"✅ Added workspace member '{sanitize_display(name)}'  ({sanitize_display(url)})")
    print("   Run 'muse workspace sync' to clone it.")


def run_workspace_remove(args: argparse.Namespace) -> None:
    """Remove a member from the workspace manifest.

    This does **not** delete the member's directory — only its registration
    in the workspace manifest is removed.
    """
    name: str = args.name

    root = require_repo()
    try:
        remove_workspace_member(root, name)
    except ValueError as exc:
        print(f"❌ {exc}")
        raise SystemExit(ExitCode.USER_ERROR)
    print(f"✅ Removed workspace member '{sanitize_display(name)}'.")


def run_workspace_list(args: argparse.Namespace) -> None:
    """List all workspace members from the manifest."""
    root = require_repo()
    members = list_workspace_members(root)
    if not members:
        print("No workspace members.  Add one with 'muse workspace add'.")
        return
    header = f"{'name':<20} {'branch':<16} {'present':<8} {'HEAD':12}  url"
    print(header)
    print("-" * len(header))
    for m in members:
        present_str = "yes" if m.present else "no"
        head_str = m.head_commit[:12] if m.head_commit else "(not cloned)"
        url_short = sanitize_display(m.url[:50])
        print(
            f"{sanitize_display(m.name):<20} "
            f"{sanitize_display(m.branch):<16} "
            f"{present_str:<8} "
            f"{head_str:<12}  {url_short}"
        )


def run_workspace_status(args: argparse.Namespace) -> None:
    """Show status of all workspace members (clone state, HEAD, branch)."""
    root = require_repo()
    members = list_workspace_members(root)
    if not members:
        print("No workspace members.  Add one with 'muse workspace add'.")
        return
    print(f"Workspace: {root}\n")
    for m in members:
        icon = "✅" if m.present else "❌"
        head = m.head_commit[:12] if m.head_commit else "not cloned"
        print(f"{icon}  {sanitize_display(m.name):<20}  branch={sanitize_display(m.branch)}  head={head}")
        print(f"     path: {m.path}")
        print(f"     url:  {sanitize_display(m.url)}")


def run_workspace_sync(args: argparse.Namespace) -> None:
    """Clone or pull the latest state for workspace members.

    Run without arguments to sync all members.  Provide a member name to
    sync only that one.

    Examples::

        muse workspace sync         # sync everything
        muse workspace sync core    # sync only 'core'
    """
    name: str | None = args.name

    root = require_repo()
    results = sync_workspace(root, member_name=name)
    if not results:
        print("No members to sync.  Add one with 'muse workspace add'.")
        return
    for member_name, status in results:
        icon = "✅" if not status.startswith("error") else "❌"
        print(f"{icon}  {sanitize_display(member_name)}: {sanitize_display(status)}")

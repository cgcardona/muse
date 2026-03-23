"""muse remote — manage remote repository connections.

Subcommands
-----------

    muse remote [-v]                     List configured remotes (default)
    muse remote add <name> <url>         Register a new remote
    muse remote remove <name>            Remove a remote and its tracking refs
    muse remote rename <old> <new>       Rename a remote
    muse remote get-url <name>           Print a remote's URL
    muse remote set-url <name> <url>     Update a remote's URL

All remote URLs and tracking data are stored in ``.muse/config.toml`` and
``.muse/remotes/<name>/<branch>`` — no network calls are made by this command.
"""

from __future__ import annotations

import argparse
import logging
import sys

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


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the remote subcommand."""
    parser = subparsers.add_parser(
        "remote",
        help="Manage remote repository connections.",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Show fetch and push URLs with commit hash (like git remote -v).")
    subs = parser.add_subparsers(dest="subcommand", metavar="SUBCOMMAND")

    add_p = subs.add_parser("add", help="Register a new remote repository connection.")
    add_p.add_argument("name", help="Name for the new remote (e.g. origin).")
    add_p.add_argument("url", help="URL of the remote repository.")
    add_p.set_defaults(func=run_add)

    remove_p = subs.add_parser("remove", help="Remove a remote and all its tracking refs.")
    remove_p.add_argument("name", help="Name of the remote to remove.")
    remove_p.set_defaults(func=run_remove)

    rename_p = subs.add_parser("rename", help="Rename a remote and move its tracking refs.")
    rename_p.add_argument("old_name", help="Current remote name.")
    rename_p.add_argument("new_name", help="New remote name.")
    rename_p.set_defaults(func=run_rename)

    get_url_p = subs.add_parser("get-url", help="Print the URL of a remote.")
    get_url_p.add_argument("name", help="Remote name.")
    get_url_p.set_defaults(func=run_get_url)

    set_url_p = subs.add_parser("set-url", help="Update the URL of an existing remote.")
    set_url_p.add_argument("name", help="Remote name.")
    set_url_p.add_argument("url", help="New URL for the remote.")
    set_url_p.set_defaults(func=run_set_url)

    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> None:
    """Manage remote repository connections. With no subcommand, lists remotes."""
    verbose: bool = args.verbose

    root = require_repo()
    remotes = list_remotes(root)
    if not remotes:
        print("No remotes configured. Use 'muse remote add <name> <url>'.")
        return
    name_width = max(len(r["name"]) for r in remotes)
    for r in remotes:
        if verbose:
            upstream = get_upstream(r["name"], root)
            head = get_remote_head(r["name"], upstream or "main", root)
            head_str = f" @ {head[:8]}" if head else ""
            tracking = f" -> {r['name']}/{upstream}" if upstream else ""
            label = f"{r['name']:<{name_width}}"
            print(f"{label}\t{r['url']}{tracking}{head_str} (fetch)")
            print(f"{label}\t{r['url']}{tracking}{head_str} (push)")
        else:
            print(r["name"])


def run_add(args: argparse.Namespace) -> None:
    """Register a new remote repository connection."""
    name: str = args.name
    url: str = args.url

    root = require_repo()
    existing = get_remote(name, root)
    if existing is not None:
        print(f"❌ Remote '{name}' already exists: {existing}")
        print(f"  Use 'muse remote set-url {name} <url>' to update it.")
        raise SystemExit(ExitCode.USER_ERROR)
    set_remote(name, url, root)
    print(f"✅ Remote '{name}' added: {url}")


def run_remove(args: argparse.Namespace) -> None:
    """Remove a remote and all its tracking refs."""
    name: str = args.name

    root = require_repo()
    try:
        remove_remote(name, root)
    except KeyError:
        print(f"❌ Remote '{name}' does not exist.")
        raise SystemExit(ExitCode.USER_ERROR)
    print(f"✅ Remote '{name}' removed.")


def run_rename(args: argparse.Namespace) -> None:
    """Rename a remote and move its tracking refs."""
    old_name: str = args.old_name
    new_name: str = args.new_name

    root = require_repo()
    try:
        rename_remote(old_name, new_name, root)
    except KeyError:
        print(f"❌ Remote '{old_name}' does not exist.")
        raise SystemExit(ExitCode.USER_ERROR)
    except ValueError:
        print(f"❌ Remote '{new_name}' already exists.")
        raise SystemExit(ExitCode.USER_ERROR)
    print(f"✅ Remote '{old_name}' renamed to '{new_name}'.")


def run_get_url(args: argparse.Namespace) -> None:
    """Print the URL of a remote."""
    name: str = args.name

    root = require_repo()
    url = get_remote(name, root)
    if url is None:
        print(f"❌ Remote '{name}' does not exist.")
        raise SystemExit(ExitCode.USER_ERROR)
    print(url)


def run_set_url(args: argparse.Namespace) -> None:
    """Update the URL of an existing remote."""
    name: str = args.name
    url: str = args.url

    root = require_repo()
    existing = get_remote(name, root)
    if existing is None:
        print(f"❌ Remote '{name}' does not exist.")
        print(f"  Use 'muse remote add {name} <url>' to create it.")
        raise SystemExit(ExitCode.USER_ERROR)
    set_remote(name, url, root)
    print(f"✅ Remote '{name}' URL updated: {url}")

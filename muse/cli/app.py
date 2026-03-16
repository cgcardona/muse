"""Muse CLI — entry point for the ``muse`` console script.

Core VCS commands::

    init        status      log         commit      diff
    show        branch      checkout    merge       reset
    revert      stash       cherry-pick tag
"""
from __future__ import annotations

import typer

from muse.cli.commands import (
    branch,
    cherry_pick,
    checkout,
    commit,
    diff,
    init,
    log,
    merge,
    reset,
    revert,
    show,
    stash,
    status,
    tag,
)

cli = typer.Typer(
    name="muse",
    help="Muse — domain-agnostic version control for multidimensional state.",
    no_args_is_help=True,
)

cli.add_typer(init.app,         name="init",        help="Initialise a new Muse repository.")
cli.add_typer(commit.app,       name="commit",      help="Record the current working tree as a new version.")
cli.add_typer(status.app,       name="status",      help="Show working-tree drift against HEAD.")
cli.add_typer(log.app,          name="log",         help="Display commit history.")
cli.add_typer(diff.app,         name="diff",        help="Compare working tree against HEAD, or two commits.")
cli.add_typer(show.app,         name="show",        help="Inspect a commit: metadata, diff, files.")
cli.add_typer(branch.app,       name="branch",      help="List, create, or delete branches.")
cli.add_typer(checkout.app,     name="checkout",    help="Switch branches or restore working tree from a commit.")
cli.add_typer(merge.app,        name="merge",       help="Three-way merge a branch into the current branch.")
cli.add_typer(reset.app,        name="reset",       help="Move HEAD to a prior commit.")
cli.add_typer(revert.app,       name="revert",      help="Create a new commit that undoes a prior commit.")
cli.add_typer(cherry_pick.app,  name="cherry-pick", help="Apply a specific commit's changes on top of HEAD.")
cli.add_typer(stash.app,        name="stash",       help="Shelve and restore uncommitted changes.")
cli.add_typer(tag.app,          name="tag",         help="Attach and query semantic tags on commits.")


if __name__ == "__main__":
    cli()

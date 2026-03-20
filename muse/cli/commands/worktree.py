"""``muse worktree`` — manage multiple simultaneous branch checkouts.

Worktrees let you work on multiple branches at once without stashing or
switching — each worktree is an independent ``state/`` directory, but they
all share the same ``.muse/`` object store.

This is especially powerful for agents: one agent per worktree, each
autonomously developing a feature on its own branch, with zero interference.

Subcommands::

    muse worktree add <name> <branch>   — create a new linked worktree
    muse worktree list                  — list all worktrees
    muse worktree remove <name>         — remove a linked worktree
    muse worktree prune                 — remove metadata for missing worktrees

Layout::

    myproject/                  ← main worktree
      state/                    ← main working files
      .muse/                    ← shared store

    myproject-feat-audio/       ← linked worktree for feat/audio
      state/
"""

from __future__ import annotations

import logging

import typer

from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.validation import sanitize_display
from muse.core.worktree import (
    WorktreeInfo,
    add_worktree,
    list_worktrees,
    prune_worktrees,
    remove_worktree,
)

logger = logging.getLogger(__name__)
app = typer.Typer(
    help="Manage multiple simultaneous branch checkouts.",
    no_args_is_help=True,
)


def _fmt_info(wt: WorktreeInfo) -> str:
    prefix = "* " if wt.is_main else "  "
    head = wt.head_commit[:12] if wt.head_commit else "(no commits)"
    return f"{prefix}{wt.name:<24} {sanitize_display(wt.branch):<30} {head}  {wt.path}"


@app.command("add")
def worktree_add(
    name: str = typer.Argument(..., help="Short identifier for the worktree (no spaces)."),
    branch: str = typer.Argument(..., help="Branch to check out in the new worktree."),
) -> None:
    """Create a new linked worktree checked out at *branch*.

    The new worktree is created as a sibling directory of the repository root,
    named ``<repo>-<name>``.  Its ``state/`` directory is pre-populated from
    the branch's latest snapshot.

    Examples::

        muse worktree add feat-audio feat/audio
        muse worktree add hotfix-001 hotfix/001
    """
    root = require_repo()
    try:
        wt_path = add_worktree(root, name, branch)
    except ValueError as exc:
        typer.echo(f"❌ {exc}")
        raise typer.Exit(code=ExitCode.USER_ERROR)
    typer.echo(f"✅ Worktree '{sanitize_display(name)}' created at {wt_path}")
    typer.echo(f"   Branch: {sanitize_display(branch)}")


@app.command("list")
def worktree_list() -> None:
    """List all worktrees (main + linked)."""
    root = require_repo()
    worktrees = list_worktrees(root)
    if not worktrees:
        typer.echo("No worktrees.")
        return
    header = f"{'  name':<26} {'branch':<30} {'HEAD':12}  path"
    typer.echo(header)
    typer.echo("-" * len(header))
    for wt in worktrees:
        typer.echo(_fmt_info(wt))


@app.command("remove")
def worktree_remove(
    name: str = typer.Argument(..., help="Name of the worktree to remove."),
    force: bool = typer.Option(False, "--force", "-f", help="Remove even if the worktree has unsaved changes."),
) -> None:
    """Remove a linked worktree and its state/ directory.

    The branch itself is not deleted — only the worktree directory and its
    metadata are removed.  Commits already pushed from the worktree remain in
    the shared store.
    """
    root = require_repo()
    try:
        remove_worktree(root, name, force=force)
    except ValueError as exc:
        typer.echo(f"❌ {exc}")
        raise typer.Exit(code=ExitCode.USER_ERROR)
    typer.echo(f"✅ Worktree '{sanitize_display(name)}' removed.")


@app.command("prune")
def worktree_prune() -> None:
    """Remove metadata entries for worktrees whose directories no longer exist."""
    root = require_repo()
    pruned = prune_worktrees(root)
    if not pruned:
        typer.echo("Nothing to prune.")
        return
    for name in pruned:
        typer.echo(f"  pruned: {sanitize_display(name)}")
    typer.echo(f"Pruned {len(pruned)} stale worktree(s).")

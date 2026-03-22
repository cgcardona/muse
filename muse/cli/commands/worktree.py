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

import argparse
import sys

import json
import logging


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


def _fmt_info(wt: WorktreeInfo) -> str:
    prefix = "* " if wt.is_main else "  "
    head = wt.head_commit[:12] if wt.head_commit else "(no commits)"
    return f"{prefix}{wt.name:<24} {sanitize_display(wt.branch):<30} {head}  {sanitize_display(str(wt.path))}"


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the worktree subcommand."""
    parser = subparsers.add_parser(
        "worktree",
        help="Manage multiple simultaneous branch checkouts.",
        description=__doc__,
    )
    subs = parser.add_subparsers(dest="subcommand", metavar="SUBCOMMAND")
    subs.required = True

    add_p = subs.add_parser("add", help="Create a new linked worktree checked out at a branch.")
    add_p.add_argument("name", metavar="NAME", help="Worktree name.")
    add_p.add_argument("branch", metavar="BRANCH", help="Branch to check out.")
    add_p.add_argument(
        "--format", dest="fmt", default="text", choices=["text", "json"],
        help="Output format: text (default) or json.",
    )
    add_p.set_defaults(func=run_worktree_add)

    list_p = subs.add_parser("list", help="List all worktrees (main + linked).")
    list_p.add_argument(
        "--format", dest="fmt", default="text", choices=["text", "json"],
        help="Output format: text (default) or json.",
    )
    list_p.set_defaults(func=run_worktree_list)

    remove_p = subs.add_parser("remove", help="Remove a linked worktree and its state/ directory.")
    remove_p.add_argument("name", metavar="NAME", help="Worktree name to remove.")
    remove_p.add_argument("--force", action="store_true", help="Force removal even with uncommitted changes.")
    remove_p.add_argument(
        "--format", dest="fmt", default="text", choices=["text", "json"],
        help="Output format: text (default) or json.",
    )
    remove_p.set_defaults(func=run_worktree_remove)

    prune_p = subs.add_parser("prune", help="Remove metadata entries for missing worktrees.")
    prune_p.set_defaults(func=run_worktree_prune)


def run_worktree_add(args: argparse.Namespace) -> None:
    """Create a new linked worktree checked out at *branch*.

    The new worktree is created as a sibling directory of the repository root,
    named ``<repo>-<name>``.  Its ``state/`` directory is pre-populated from
    the branch's latest snapshot.  Agents should pass ``--format json`` to
    receive ``{name, branch, path}`` rather than human-readable text.

    Examples::

        muse worktree add feat-audio feat/audio
        muse worktree add hotfix-001 hotfix/001
    """
    name: str = args.name
    branch: str = args.branch
    fmt: str = args.fmt

    if fmt not in ("text", "json"):
        print(f"❌ Unknown --format '{sanitize_display(fmt)}'. Choose text or json.", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)
    root = require_repo()
    try:
        wt_path = add_worktree(root, name, branch)
    except ValueError as exc:
        print(f"❌ {exc}")
        raise SystemExit(ExitCode.USER_ERROR)
    if fmt == "json":
        print(json.dumps({"name": name, "branch": branch, "path": str(wt_path)}))
    else:
        print(f"✅ Worktree '{sanitize_display(name)}' created at {wt_path}")
        print(f"   Branch: {sanitize_display(branch)}")


def run_worktree_list(args: argparse.Namespace) -> None:
    """List all worktrees (main + linked).

    Agents should pass ``--format json`` to receive a JSON array of
    ``{name, branch, path, head_commit, is_main}`` objects.
    """
    fmt: str = args.fmt

    if fmt not in ("text", "json"):
        print(f"❌ Unknown --format '{sanitize_display(fmt)}'. Choose text or json.", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)
    root = require_repo()
    worktrees = list_worktrees(root)
    if fmt == "json":
        print(json.dumps([{
            "name": wt.name,
            "branch": wt.branch,
            "path": str(wt.path),
            "head_commit": wt.head_commit,
            "is_main": wt.is_main,
        } for wt in worktrees]))
        return
    if not worktrees:
        print("No worktrees.")
        return
    header = f"{'  name':<26} {'branch':<30} {'HEAD':12}  path"
    print(header)
    print("-" * len(header))
    for wt in worktrees:
        print(_fmt_info(wt))


def run_worktree_remove(args: argparse.Namespace) -> None:
    """Remove a linked worktree and its state/ directory.

    The branch itself is not deleted — only the worktree directory and its
    metadata are removed.  Commits already pushed from the worktree remain in
    the shared store.  Agents should pass ``--format json`` to receive
    ``{name, status}`` rather than human-readable text.
    """
    name: str = args.name
    force: bool = args.force
    fmt: str = args.fmt

    if fmt not in ("text", "json"):
        print(f"❌ Unknown --format '{sanitize_display(fmt)}'. Choose text or json.", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)
    root = require_repo()
    try:
        remove_worktree(root, name, force=force)
    except ValueError as exc:
        print(f"❌ {exc}")
        raise SystemExit(ExitCode.USER_ERROR)
    if fmt == "json":
        print(json.dumps({"name": name, "status": "removed"}))
    else:
        print(f"✅ Worktree '{sanitize_display(name)}' removed.")


def run_worktree_prune(args: argparse.Namespace) -> None:
    """Remove metadata entries for worktrees whose directories no longer exist."""
    root = require_repo()
    pruned = prune_worktrees(root)
    if not pruned:
        print("Nothing to prune.")
        return
    for name in pruned:
        print(f"  pruned: {sanitize_display(name)}")
    print(f"Pruned {len(pruned)} stale worktree(s).")

"""``muse branch`` — list, create, or delete branches.

Branch rename is not yet implemented; use ``muse branch <new-name>`` followed
by ``muse branch --delete <old-name>`` as a workaround.

Usage::

    muse branch                       # list all branches
    muse branch <name>                # create a branch at HEAD
    muse branch --delete <name>       # delete a branch
    muse branch --verbose             # list with commit SHAs

Exit codes::

    0 — success
    1 — invalid branch name, branch not found, trying to delete current branch
"""

from __future__ import annotations

import argparse
import json
import logging
import pathlib
import sys

from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.store import get_head_commit_id, read_current_branch
from muse.core.validation import sanitize_display, validate_branch_name

logger = logging.getLogger(__name__)


def _read_current_branch(root: pathlib.Path) -> str:
    return read_current_branch(root)


def _list_branches(root: pathlib.Path) -> list[str]:
    heads_dir = root / ".muse" / "refs" / "heads"
    if not heads_dir.exists():
        return []
    return sorted(
        p.relative_to(heads_dir).as_posix()
        for p in heads_dir.rglob("*")
        if p.is_file()
    )


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the branch subcommand."""
    parser = subparsers.add_parser(
        "branch",
        help="List, create, or delete branches.",
        description=__doc__,
    )
    parser.add_argument("name", nargs="?", default=None, help="Branch name to create.")
    parser.add_argument("-d", "--delete", default=None, help="Delete a branch.")
    parser.add_argument("-v", "--verbose", action="store_true", help="Show commit ID for each branch.")
    parser.add_argument("-a", "--all", action="store_true", dest="all_branches", help="List all branches.")
    parser.add_argument("--format", "-f", default="text", dest="fmt", help="Output format: text or json.")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> None:
    """List, create, or delete branches.

    Agents should pass ``--format json`` when listing to receive a JSON array
    of ``{name, current, commit_id}`` objects, or a single result object when
    creating or deleting a branch.
    """
    name: str | None = args.name
    delete: str | None = args.delete
    verbose: bool = args.verbose
    all_branches: bool = args.all_branches
    fmt: str = args.fmt

    if fmt not in ("text", "json"):
        print(f"❌ Unknown --format '{sanitize_display(fmt)}'. Choose text or json.", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)
    root = require_repo()
    current = _read_current_branch(root)

    if delete:
        try:
            validate_branch_name(delete)
        except ValueError as exc:
            print(f"❌ Invalid branch name: {exc}")
            raise SystemExit(ExitCode.USER_ERROR)
        if delete == current:
            print(f"❌ Cannot delete the currently checked-out branch '{sanitize_display(delete)}'.")
            raise SystemExit(ExitCode.USER_ERROR)
        ref_file = root / ".muse" / "refs" / "heads" / delete
        if not ref_file.exists():
            print(f"❌ Branch '{sanitize_display(delete)}' not found.")
            raise SystemExit(ExitCode.USER_ERROR)
        ref_file.unlink()
        if fmt == "json":
            print(json.dumps({"action": "deleted", "branch": delete}))
        else:
            print(f"Deleted branch {sanitize_display(delete)}.")
        return

    if name:
        try:
            validate_branch_name(name)
        except ValueError as exc:
            print(f"❌ Invalid branch name: {exc}")
            raise SystemExit(ExitCode.USER_ERROR)
        ref_file = root / ".muse" / "refs" / "heads" / name
        if ref_file.exists():
            print(f"❌ Branch '{sanitize_display(name)}' already exists.")
            raise SystemExit(ExitCode.USER_ERROR)
        # Point new branch at current HEAD commit
        current_commit = get_head_commit_id(root, current) or ""
        ref_file.parent.mkdir(parents=True, exist_ok=True)
        ref_file.write_text(current_commit)
        if fmt == "json":
            print(json.dumps({"action": "created", "branch": name, "commit_id": current_commit}))
        else:
            print(f"Created branch {sanitize_display(name)}.")
        return

    # List branches
    branches = _list_branches(root)
    if fmt == "json":
        result = []
        for b in branches:
            commit_id = get_head_commit_id(root, b) or ""
            result.append({"name": b, "current": b == current, "commit_id": commit_id})
        print(json.dumps(result))
        return
    for b in branches:
        marker = "* " if b == current else "  "
        if verbose:
            commit_id = get_head_commit_id(root, b) or "(empty)"
            print(f"{marker}{b}  {commit_id[:8]}")
        else:
            print(f"{marker}{b}")

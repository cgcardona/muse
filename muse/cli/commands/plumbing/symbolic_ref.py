"""muse plumbing symbolic-ref — read or write HEAD's symbolic reference.

In Muse, HEAD is always a symbolic reference that points to a branch.
This command reads which branch HEAD currently tracks or, with ``--set``,
updates HEAD to point to a different branch.

Read mode output (JSON, default)::

    {
      "ref":             "HEAD",
      "symbolic_target": "refs/heads/main",
      "branch":          "main",
      "commit_id":       "<sha256>"
    }

When the branch has no commits yet, ``commit_id`` is ``null``.

Write mode (``--set <branch>``)::

    muse plumbing symbolic-ref HEAD main

Output after a successful write::

    {"ref": "HEAD", "symbolic_target": "refs/heads/main", "branch": "main"}

Text output (``--format text``, read mode)::

    refs/heads/main

Plumbing contract
-----------------

- Exit 0: ref read or updated successfully.
- Exit 1: ``--set`` target branch does not exist; bad ``--format``.
- Exit 3: I/O error reading or writing HEAD.
"""

from __future__ import annotations

import argparse
import json
import logging
import pathlib
import sys
from typing import TypedDict

from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.store import get_head_commit_id, read_current_branch, write_head_branch
from muse.core.validation import validate_branch_name

logger = logging.getLogger(__name__)

_FORMAT_CHOICES = ("json", "text")


class _SymbolicRefResult(TypedDict):
    ref: str
    symbolic_target: str
    branch: str
    commit_id: str | None


def _read_symbolic_ref(root: pathlib.Path) -> _SymbolicRefResult:
    """Return the current HEAD symbolic-ref data."""
    branch = read_current_branch(root)
    commit_id = get_head_commit_id(root, branch)
    return {
        "ref": "HEAD",
        "symbolic_target": f"refs/heads/{branch}",
        "branch": branch,
        "commit_id": commit_id,
    }


def _branch_exists(root: pathlib.Path, branch: str) -> bool:
    """Return True if the branch ref file exists under .muse/refs/heads/."""
    return (root / ".muse" / "refs" / "heads" / branch).exists()


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the symbolic-ref subcommand."""
    parser = subparsers.add_parser(
        "symbolic-ref",
        help="Read or write HEAD's symbolic branch reference.",
        description=__doc__,
    )
    parser.add_argument(
        "ref",
        nargs="?",
        default="HEAD",
        help="The symbolic ref to query or update. Currently only HEAD is supported.",
    )
    parser.add_argument(
        "--set", "-s",
        default="",
        dest="set_branch",
        metavar="BRANCH",
        help="Branch name to point HEAD at (write mode).",
    )
    parser.add_argument(
        "--format", "-f",
        dest="fmt",
        default="json",
        metavar="FORMAT",
        help="Output format: json or text. (default: json)",
    )
    parser.add_argument(
        "--short", "-S",
        action="store_true",
        help="In text mode, emit only the branch name rather than the full ref path.",
    )
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> None:
    """Read or write HEAD's symbolic reference.

    With no ``--set`` flag, reads the current branch HEAD points to and
    the commit ID at that branch tip.

    With ``--set <branch>``, updates HEAD to point to *branch*.  The branch
    must already exist (have at least one ref entry); this command does not
    create new branches.
    """
    fmt: str = args.fmt
    ref: str = args.ref
    set_branch: str = args.set_branch
    short: bool = args.short

    if fmt not in _FORMAT_CHOICES:
        print(
            json.dumps(
                {"error": f"Unknown format {fmt!r}. Valid: {', '.join(_FORMAT_CHOICES)}"}
            )
        )
        raise SystemExit(ExitCode.USER_ERROR)

    ref_upper = ref.upper()
    if ref_upper != "HEAD":
        print(json.dumps({"error": f"Unsupported ref {ref!r}. Only HEAD is supported."}))
        raise SystemExit(ExitCode.USER_ERROR)

    root = require_repo()

    # Write mode
    if set_branch:
        try:
            validate_branch_name(set_branch)
        except ValueError as exc:
            print(json.dumps({"error": str(exc)}))
            raise SystemExit(ExitCode.USER_ERROR)

        if not _branch_exists(root, set_branch):
            print(json.dumps({"error": f"Branch {set_branch!r} does not exist."}))
            raise SystemExit(ExitCode.USER_ERROR)

        try:
            write_head_branch(root, set_branch)
        except OSError as exc:
            logger.debug("symbolic-ref write error: %s", exc)
            print(json.dumps({"error": str(exc)}))
            raise SystemExit(ExitCode.INTERNAL_ERROR)

        result: _SymbolicRefResult = {
            "ref": "HEAD",
            "symbolic_target": f"refs/heads/{set_branch}",
            "branch": set_branch,
            "commit_id": get_head_commit_id(root, set_branch),
        }
        if fmt == "text":
            if short:
                print(set_branch)
            else:
                print(f"refs/heads/{set_branch}")
            return
        print(json.dumps(dict(result)))
        return

    # Read mode
    try:
        result = _read_symbolic_ref(root)
    except OSError as exc:
        logger.debug("symbolic-ref read error: %s", exc)
        print(json.dumps({"error": str(exc)}))
        raise SystemExit(ExitCode.INTERNAL_ERROR)

    if fmt == "text":
        if short:
            print(result["branch"])
        else:
            print(result["symbolic_target"])
        return

    print(json.dumps(dict(result)))

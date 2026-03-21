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

import json
import logging
import pathlib
from typing import TypedDict

import typer

from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.store import get_head_commit_id, read_current_branch, write_head_branch
from muse.core.validation import validate_branch_name

logger = logging.getLogger(__name__)

app = typer.Typer()

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


@app.callback(invoke_without_command=True)
def symbolic_ref(
    ctx: typer.Context,
    _ref: str = typer.Argument(
        "HEAD",
        help="The symbolic ref to query or update. Currently only HEAD is supported.",
    ),
    set_branch: str = typer.Option(
        "",
        "--set",
        "-s",
        help="Branch name to point HEAD at (write mode).",
    ),
    fmt: str = typer.Option(
        "json", "--format", "-f", help="Output format: json or text."
    ),
    short: bool = typer.Option(
        False,
        "--short",
        "-S",
        help="In text mode, emit only the branch name rather than the full ref path.",
    ),
) -> None:
    """Read or write HEAD's symbolic reference.

    With no ``--set`` flag, reads the current branch HEAD points to and
    the commit ID at that branch tip.

    With ``--set <branch>``, updates HEAD to point to *branch*.  The branch
    must already exist (have at least one ref entry); this command does not
    create new branches.

    Only ``HEAD`` is supported as the *ref* argument in this version.  Future
    Muse versions may support other symbolic refs for worktree and namespace
    operations.
    """
    if fmt not in _FORMAT_CHOICES:
        typer.echo(
            json.dumps(
                {"error": f"Unknown format {fmt!r}. Valid: {', '.join(_FORMAT_CHOICES)}"}
            )
        )
        raise typer.Exit(code=ExitCode.USER_ERROR)

    ref_upper = _ref.upper()
    if ref_upper != "HEAD":
        typer.echo(
            json.dumps({"error": f"Unsupported ref {_ref!r}. Only HEAD is supported."})
        )
        raise typer.Exit(code=ExitCode.USER_ERROR)

    root = require_repo()

    # Write mode
    if set_branch:
        try:
            validate_branch_name(set_branch)
        except ValueError as exc:
            typer.echo(json.dumps({"error": str(exc)}))
            raise typer.Exit(code=ExitCode.USER_ERROR)

        if not _branch_exists(root, set_branch):
            typer.echo(
                json.dumps({"error": f"Branch {set_branch!r} does not exist."})
            )
            raise typer.Exit(code=ExitCode.USER_ERROR)

        try:
            write_head_branch(root, set_branch)
        except OSError as exc:
            logger.debug("symbolic-ref write error: %s", exc)
            typer.echo(json.dumps({"error": str(exc)}))
            raise typer.Exit(code=ExitCode.INTERNAL_ERROR)

        result: _SymbolicRefResult = {
            "ref": "HEAD",
            "symbolic_target": f"refs/heads/{set_branch}",
            "branch": set_branch,
            "commit_id": get_head_commit_id(root, set_branch),
        }
        if fmt == "text":
            if short:
                typer.echo(set_branch)
            else:
                typer.echo(f"refs/heads/{set_branch}")
            return
        typer.echo(json.dumps(dict(result)))
        return

    # Read mode
    try:
        result = _read_symbolic_ref(root)
    except OSError as exc:
        logger.debug("symbolic-ref read error: %s", exc)
        typer.echo(json.dumps({"error": str(exc)}))
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)

    if fmt == "text":
        if short:
            typer.echo(result["branch"])
        else:
            typer.echo(result["symbolic_target"])
        return

    typer.echo(json.dumps(dict(result)))

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

import json
import logging
import pathlib

import typer

from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.store import get_head_commit_id, read_current_branch
from muse.core.validation import sanitize_display, validate_branch_name

logger = logging.getLogger(__name__)

app = typer.Typer()


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


@app.callback(invoke_without_command=True)
def branch(
    ctx: typer.Context,
    name: str | None = typer.Argument(None, help="Branch name to create."),
    delete: str | None = typer.Option(None, "-d", "--delete", help="Delete a branch."),
    verbose: bool = typer.Option(False, "-v", "--verbose", help="Show commit ID for each branch."),
    all_branches: bool = typer.Option(False, "-a", "--all", help="List all branches."),
    fmt: str = typer.Option("text", "--format", "-f", help="Output format: text or json."),
) -> None:
    """List, create, or delete branches.

    Agents should pass ``--format json`` when listing to receive a JSON array
    of ``{name, current, commit_id}`` objects, or a single result object when
    creating or deleting a branch.
    """
    if fmt not in ("text", "json"):
        typer.echo(f"❌ Unknown --format '{sanitize_display(fmt)}'. Choose text or json.", err=True)
        raise typer.Exit(code=ExitCode.USER_ERROR)
    root = require_repo()
    current = _read_current_branch(root)

    if delete:
        try:
            validate_branch_name(delete)
        except ValueError as exc:
            typer.echo(f"❌ Invalid branch name: {exc}")
            raise typer.Exit(code=ExitCode.USER_ERROR)
        if delete == current:
            typer.echo(f"❌ Cannot delete the currently checked-out branch '{sanitize_display(delete)}'.")
            raise typer.Exit(code=ExitCode.USER_ERROR)
        ref_file = root / ".muse" / "refs" / "heads" / delete
        if not ref_file.exists():
            typer.echo(f"❌ Branch '{sanitize_display(delete)}' not found.")
            raise typer.Exit(code=ExitCode.USER_ERROR)
        ref_file.unlink()
        if fmt == "json":
            typer.echo(json.dumps({"action": "deleted", "branch": delete}))
        else:
            typer.echo(f"Deleted branch {sanitize_display(delete)}.")
        return

    if name:
        try:
            validate_branch_name(name)
        except ValueError as exc:
            typer.echo(f"❌ Invalid branch name: {exc}")
            raise typer.Exit(code=ExitCode.USER_ERROR)
        ref_file = root / ".muse" / "refs" / "heads" / name
        if ref_file.exists():
            typer.echo(f"❌ Branch '{sanitize_display(name)}' already exists.")
            raise typer.Exit(code=ExitCode.USER_ERROR)
        # Point new branch at current HEAD commit
        current_commit = get_head_commit_id(root, current) or ""
        ref_file.parent.mkdir(parents=True, exist_ok=True)
        ref_file.write_text(current_commit)
        if fmt == "json":
            typer.echo(json.dumps({"action": "created", "branch": name, "commit_id": current_commit}))
        else:
            typer.echo(f"Created branch {sanitize_display(name)}.")
        return

    # List branches
    branches = _list_branches(root)
    if fmt == "json":
        result = []
        for b in branches:
            commit_id = get_head_commit_id(root, b) or ""
            result.append({"name": b, "current": b == current, "commit_id": commit_id})
        typer.echo(json.dumps(result))
        return
    for b in branches:
        marker = "* " if b == current else "  "
        if verbose:
            commit_id = get_head_commit_id(root, b) or "(empty)"
            typer.echo(f"{marker}{b}  {commit_id[:8]}")
        else:
            typer.echo(f"{marker}{b}")

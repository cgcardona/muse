"""muse branch — list, create, rename, or delete branches."""

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
) -> None:
    """List, create, or delete branches."""
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
        typer.echo(f"Created branch {sanitize_display(name)}.")
        return

    # List branches
    branches = _list_branches(root)
    for b in branches:
        marker = "* " if b == current else "  "
        if verbose:
            commit_id = get_head_commit_id(root, b) or "(empty)"
            typer.echo(f"{marker}{b}  {commit_id[:8]}")
        else:
            typer.echo(f"{marker}{b}")

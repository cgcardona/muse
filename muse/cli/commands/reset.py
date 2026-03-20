"""muse reset — move HEAD to a prior commit.

Modes::

    --soft   — move the branch pointer only; working tree is untouched.
    --hard   — move the branch pointer AND restore the working tree from the target snapshot.
"""

from __future__ import annotations

import json
import logging
import pathlib

import typer

from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.store import read_snapshot, resolve_commit_ref
from muse.core.reflog import append_reflog
from muse.core.validation import sanitize_display, validate_branch_name
from muse.core.workdir import apply_manifest

logger = logging.getLogger(__name__)

app = typer.Typer()


def _read_branch(root: pathlib.Path) -> str:
    head_ref = (root / ".muse" / "HEAD").read_text().strip()
    return head_ref.removeprefix("refs/heads/").strip()


def _read_repo_id(root: pathlib.Path) -> str:
    return str(json.loads((root / ".muse" / "repo.json").read_text())["repo_id"])


@app.callback(invoke_without_command=True)
def reset(
    ctx: typer.Context,
    ref: str = typer.Argument(..., help="Commit ID or branch to reset to."),
    hard: bool = typer.Option(False, "--hard", help="Reset branch pointer AND restore state/."),
    soft: bool = typer.Option(False, "--soft", help="Reset branch pointer only (default)."),
) -> None:
    """Move HEAD to a prior commit."""
    root = require_repo()
    repo_id = _read_repo_id(root)
    branch = _read_branch(root)

    commit = resolve_commit_ref(root, repo_id, branch, ref)
    if commit is None:
        typer.echo(f"❌ '{ref}' not found.")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    try:
        validate_branch_name(branch)
    except ValueError as exc:
        typer.echo(f"❌ Current branch name is invalid: {exc}")
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)
    ref_file = root / ".muse" / "refs" / "heads" / branch
    old_commit_id = ref_file.read_text().strip() if ref_file.exists() else None
    ref_file.write_text(commit.commit_id)

    mode = "hard" if hard else "soft"
    append_reflog(
        root, branch, old_id=old_commit_id, new_id=commit.commit_id,
        author="user",
        operation=f"reset ({mode}): moving to {commit.commit_id[:12]}",
    )

    if hard:
        snapshot = read_snapshot(root, commit.snapshot_id)
        if snapshot is None:
            typer.echo(f"❌ Snapshot {commit.snapshot_id[:8]} not found in object store.")
            raise typer.Exit(code=ExitCode.INTERNAL_ERROR)
        apply_manifest(root, snapshot.manifest)
        typer.echo(f"HEAD is now at {commit.commit_id[:8]} {sanitize_display(commit.message)}")
    else:
        typer.echo(f"Moved {sanitize_display(branch)} to {commit.commit_id[:8]}")

"""muse checkout — switch branches or restore working tree from a commit.

Usage::

    muse checkout <branch>           — switch to existing branch
    muse checkout -b <branch>        — create and switch to new branch
    muse checkout <commit-id>        — detach HEAD at a specific commit
"""
from __future__ import annotations

import json
import logging
import pathlib
import shutil

import typer

from muse.core.errors import ExitCode
from muse.core.object_store import restore_object
from muse.core.repo import require_repo
from muse.core.store import (
    get_head_commit_id,
    get_head_snapshot_id,
    read_commit,
    read_snapshot,
    resolve_commit_ref,
)

logger = logging.getLogger(__name__)

app = typer.Typer()


def _read_current_branch(root: pathlib.Path) -> str:
    head_ref = (root / ".muse" / "HEAD").read_text().strip()
    return head_ref.removeprefix("refs/heads/").strip()


def _read_repo_id(root: pathlib.Path) -> str:
    return json.loads((root / ".muse" / "repo.json").read_text())["repo_id"]


def _restore_workdir(root: pathlib.Path, snapshot_id: str) -> None:
    """Replace muse-work/ with the contents of the given snapshot."""
    snapshot = read_snapshot(root, snapshot_id)
    if snapshot is None:
        typer.echo(f"❌ Snapshot {snapshot_id[:8]} not found in object store.")
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)

    workdir = root / "muse-work"
    if workdir.exists():
        shutil.rmtree(workdir)
    workdir.mkdir()

    for rel_path, object_id in snapshot.manifest.items():
        dest = workdir / rel_path
        if not restore_object(root, object_id, dest):
            typer.echo(f"⚠️  Object {object_id[:8]} for '{rel_path}' not in local store — skipped.")


@app.callback(invoke_without_command=True)
def checkout(
    ctx: typer.Context,
    target: str = typer.Argument(..., help="Branch name or commit ID to check out."),
    create: bool = typer.Option(False, "-b", "--create", help="Create a new branch."),
    force: bool = typer.Option(False, "--force", "-f", help="Discard uncommitted changes."),
) -> None:
    """Switch branches or restore working tree from a commit."""
    root = require_repo()
    repo_id = _read_repo_id(root)
    current_branch = _read_current_branch(root)
    muse_dir = root / ".muse"

    if create:
        ref_file = muse_dir / "refs" / "heads" / target
        if ref_file.exists():
            typer.echo(f"❌ Branch '{target}' already exists. Use 'muse checkout {target}' to switch to it.")
            raise typer.Exit(code=ExitCode.USER_ERROR)
        current_commit = get_head_commit_id(root, current_branch) or ""
        ref_file.parent.mkdir(parents=True, exist_ok=True)
        ref_file.write_text(current_commit)
        (muse_dir / "HEAD").write_text(f"refs/heads/{target}\n")
        typer.echo(f"Switched to a new branch '{target}'")
        return

    # Check if target is a known branch
    ref_file = muse_dir / "refs" / "heads" / target
    if ref_file.exists():
        if target == current_branch:
            typer.echo(f"Already on '{target}'")
            return

        target_snapshot_id = get_head_snapshot_id(root, repo_id, target)
        if target_snapshot_id:
            _restore_workdir(root, target_snapshot_id)

        (muse_dir / "HEAD").write_text(f"refs/heads/{target}\n")
        typer.echo(f"Switched to branch '{target}'")
        return

    # Try as a commit ID (detached HEAD)
    commit = resolve_commit_ref(root, repo_id, current_branch, target)
    if commit is None:
        typer.echo(f"❌ '{target}' is not a branch or commit ID.")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    _restore_workdir(root, commit.snapshot_id)
    (muse_dir / "HEAD").write_text(commit.commit_id + "\n")
    typer.echo(f"HEAD is now at {commit.commit_id[:8]} {commit.message}")

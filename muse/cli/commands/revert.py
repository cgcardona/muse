"""muse revert — create a new commit that undoes a prior commit."""

from __future__ import annotations

import datetime
import json
import logging
import pathlib
import shutil

import typer

from muse.core.errors import ExitCode
from muse.core.object_store import restore_object
from muse.core.repo import require_repo
from muse.core.snapshot import compute_commit_id
from muse.core.store import (
    CommitRecord,
    get_head_commit_id,
    read_commit,
    read_snapshot,
    resolve_commit_ref,
    write_commit,
)
from muse.core.validation import contain_path, sanitize_display

logger = logging.getLogger(__name__)

app = typer.Typer()


def _read_branch(root: pathlib.Path) -> str:
    head_ref = (root / ".muse" / "HEAD").read_text().strip()
    return head_ref.removeprefix("refs/heads/").strip()


def _read_repo_id(root: pathlib.Path) -> str:
    return str(json.loads((root / ".muse" / "repo.json").read_text())["repo_id"])


@app.callback(invoke_without_command=True)
def revert(
    ctx: typer.Context,
    ref: str = typer.Argument(..., help="Commit to revert."),
    message: str | None = typer.Option(None, "-m", "--message", help="Override revert commit message."),
    no_commit: bool = typer.Option(False, "--no-commit", "-n", help="Apply changes but do not commit."),
) -> None:
    """Create a new commit that undoes a prior commit."""
    root = require_repo()
    repo_id = _read_repo_id(root)
    branch = _read_branch(root)

    target = resolve_commit_ref(root, repo_id, branch, ref)
    if target is None:
        typer.echo(f"❌ Commit '{ref}' not found.")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    # The revert of a commit restores its parent snapshot
    if target.parent_commit_id is None:
        typer.echo("❌ Cannot revert the root commit (no parent to restore).")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    parent_commit = read_commit(root, target.parent_commit_id)
    if parent_commit is None:
        typer.echo(f"❌ Parent commit {target.parent_commit_id[:8]} not found.")
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)

    target_snapshot = read_snapshot(root, parent_commit.snapshot_id)
    if target_snapshot is None:
        typer.echo(f"❌ Snapshot {parent_commit.snapshot_id[:8]} not found.")
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)

    # Restore parent snapshot to state/
    workdir = root / "state"
    if workdir.exists():
        shutil.rmtree(workdir)
    workdir.mkdir()
    for rel_path, object_id in target_snapshot.manifest.items():
        try:
            safe_dest = contain_path(workdir, rel_path)
        except ValueError as exc:
            logger.warning("⚠️ Skipping unsafe manifest path %r: %s", rel_path, exc)
            continue
        restore_object(root, object_id, safe_dest)

    if no_commit:
        typer.echo(f"Reverted changes from {target.commit_id[:8]} applied to state/. Run 'muse commit' to record.")
        return

    revert_message = message or f"Revert \"{target.message}\""
    head_commit_id = get_head_commit_id(root, branch)

    # The parent snapshot is already content-addressed in the object store —
    # reuse its snapshot_id directly rather than re-scanning the workdir.
    snapshot_id = parent_commit.snapshot_id
    committed_at = datetime.datetime.now(datetime.timezone.utc)
    commit_id = compute_commit_id(
        parent_ids=[head_commit_id] if head_commit_id else [],
        snapshot_id=snapshot_id,
        message=revert_message,
        committed_at_iso=committed_at.isoformat(),
    )

    write_commit(root, CommitRecord(
        commit_id=commit_id,
        repo_id=repo_id,
        branch=branch,
        snapshot_id=snapshot_id,
        message=revert_message,
        committed_at=committed_at,
        parent_commit_id=head_commit_id,
    ))
    (root / ".muse" / "refs" / "heads" / branch).write_text(commit_id)

    typer.echo(f"[{sanitize_display(branch)} {commit_id[:8]}] {sanitize_display(revert_message)}")

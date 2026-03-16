"""muse revert — create a new commit that undoes a prior commit."""
from __future__ import annotations

import datetime
import json
import logging
import pathlib
import shutil
from typing import Optional

import typer

from muse.core.errors import ExitCode
from muse.core.object_store import restore_object, write_object_from_path
from muse.core.repo import require_repo
from muse.core.snapshot import build_snapshot_manifest, compute_commit_id, compute_snapshot_id
from muse.core.store import (
    CommitRecord,
    SnapshotRecord,
    get_head_commit_id,
    read_commit,
    resolve_commit_ref,
    write_commit,
    write_snapshot,
)

logger = logging.getLogger(__name__)

app = typer.Typer()


def _read_branch(root: pathlib.Path) -> str:
    head_ref = (root / ".muse" / "HEAD").read_text().strip()
    return head_ref.removeprefix("refs/heads/").strip()


def _read_repo_id(root: pathlib.Path) -> str:
    return json.loads((root / ".muse" / "repo.json").read_text())["repo_id"]


@app.callback(invoke_without_command=True)
def revert(
    ctx: typer.Context,
    ref: str = typer.Argument(..., help="Commit to revert."),
    message: Optional[str] = typer.Option(None, "-m", "--message", help="Override revert commit message."),
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

    from muse.core.store import read_snapshot
    target_snapshot = read_snapshot(root, parent_commit.snapshot_id)
    if target_snapshot is None:
        typer.echo(f"❌ Snapshot {parent_commit.snapshot_id[:8]} not found.")
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)

    # Restore parent snapshot to muse-work/
    workdir = root / "muse-work"
    if workdir.exists():
        shutil.rmtree(workdir)
    workdir.mkdir()
    for rel_path, object_id in target_snapshot.manifest.items():
        restore_object(root, object_id, workdir / rel_path)

    if no_commit:
        typer.echo(f"Reverted changes from {target.commit_id[:8]} applied to muse-work/. Run 'muse commit' to record.")
        return

    revert_message = message or f"Revert \"{target.message}\""
    head_commit_id = get_head_commit_id(root, branch)
    parent_ids = [head_commit_id] if head_commit_id else []

    manifest = build_snapshot_manifest(workdir)
    snapshot_id = compute_snapshot_id(manifest)
    committed_at = datetime.datetime.now(datetime.timezone.utc)
    commit_id = compute_commit_id(
        parent_ids=parent_ids,
        snapshot_id=snapshot_id,
        message=revert_message,
        committed_at_iso=committed_at.isoformat(),
    )

    for rel_path, object_id in manifest.items():
        write_object_from_path(root, object_id, workdir / rel_path)

    write_snapshot(root, SnapshotRecord(snapshot_id=snapshot_id, manifest=manifest))
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

    typer.echo(f"[{branch} {commit_id[:8]}] {revert_message}")

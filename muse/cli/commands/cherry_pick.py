"""muse cherry-pick — apply a specific commit's changes on top of HEAD."""
from __future__ import annotations

import datetime
import json
import logging
import pathlib

import typer

from muse.core.errors import ExitCode
from muse.core.merge_engine import apply_merge, detect_conflicts, diff_snapshots, write_merge_state
from muse.core.object_store import restore_object, write_object_from_path
from muse.core.repo import require_repo
from muse.core.snapshot import build_snapshot_manifest, compute_commit_id, compute_snapshot_id
from muse.core.store import (
    CommitRecord,
    SnapshotRecord,
    get_head_commit_id,
    get_head_snapshot_manifest,
    read_commit,
    read_snapshot,
    write_commit,
    write_snapshot,
)

logger = logging.getLogger(__name__)

app = typer.Typer()


def _read_branch(root: pathlib.Path) -> str:
    head_ref = (root / ".muse" / "HEAD").read_text().strip()
    return head_ref.removeprefix("refs/heads/").strip()


def _read_repo_id(root: pathlib.Path) -> str:
    return str(json.loads((root / ".muse" / "repo.json").read_text())["repo_id"])


@app.callback(invoke_without_command=True)
def cherry_pick(
    ctx: typer.Context,
    ref: str = typer.Argument(..., help="Commit ID to apply."),
    no_commit: bool = typer.Option(False, "-n", "--no-commit", help="Apply but do not commit."),
) -> None:
    """Apply a specific commit's changes on top of HEAD."""
    root = require_repo()
    repo_id = _read_repo_id(root)
    branch = _read_branch(root)

    # Find the commit to cherry-pick
    from muse.core.store import resolve_commit_ref
    target = resolve_commit_ref(root, repo_id, branch, ref)
    if target is None:
        typer.echo(f"❌ Commit '{ref}' not found.")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    # Delta = target vs its parent
    base_manifest: dict[str, str] = {}
    if target.parent_commit_id:
        parent_commit = read_commit(root, target.parent_commit_id)
        if parent_commit:
            parent_snap = read_snapshot(root, parent_commit.snapshot_id)
            if parent_snap:
                base_manifest = parent_snap.manifest

    target_snap = read_snapshot(root, target.snapshot_id)
    target_manifest = target_snap.manifest if target_snap else {}

    # Apply that delta on top of current HEAD
    ours_manifest = get_head_snapshot_manifest(root, repo_id, branch) or {}
    theirs_changed = diff_snapshots(base_manifest, target_manifest)
    ours_changed = diff_snapshots(base_manifest, ours_manifest)
    conflicts = detect_conflicts(ours_changed, theirs_changed)

    if conflicts:
        write_merge_state(
            root,
            base_commit=target.parent_commit_id or "",
            ours_commit=get_head_commit_id(root, branch) or "",
            theirs_commit=target.commit_id,
            conflict_paths=list(conflicts),
        )
        typer.echo(f"❌ Cherry-pick conflict in {len(conflicts)} file(s):")
        for p in sorted(conflicts):
            typer.echo(f"  CONFLICT: {p}")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    merged_manifest = apply_merge(
        base_manifest, ours_manifest, target_manifest,
        ours_changed, theirs_changed, set(),
    )

    import shutil
    workdir = root / "muse-work"
    if workdir.exists():
        shutil.rmtree(workdir)
    workdir.mkdir()
    for rel_path, object_id in merged_manifest.items():
        restore_object(root, object_id, workdir / rel_path)

    if no_commit:
        typer.echo(f"Applied {target.commit_id[:8]} to muse-work/. Run 'muse commit' to record.")
        return

    head_commit_id = get_head_commit_id(root, branch)
    manifest = build_snapshot_manifest(workdir)
    snapshot_id = compute_snapshot_id(manifest)
    committed_at = datetime.datetime.now(datetime.timezone.utc)
    commit_id = compute_commit_id(
        parent_ids=[head_commit_id] if head_commit_id else [],
        snapshot_id=snapshot_id,
        message=target.message,
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
        message=target.message,
        committed_at=committed_at,
        parent_commit_id=head_commit_id,
    ))
    (root / ".muse" / "refs" / "heads" / branch).write_text(commit_id)
    typer.echo(f"[{branch} {commit_id[:8]}] {target.message}")

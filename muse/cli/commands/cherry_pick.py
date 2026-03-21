"""muse cherry-pick — apply a specific commit's changes on top of HEAD."""

from __future__ import annotations

import datetime
import json
import logging
import pathlib

import typer

from muse.core.errors import ExitCode
from muse.core.merge_engine import write_merge_state
from muse.core.repo import require_repo
from muse.core.snapshot import compute_commit_id, compute_snapshot_id
from muse.core.store import (
    CommitRecord,
    SnapshotRecord,
    get_head_commit_id,
    get_head_snapshot_manifest,
    read_commit,
    read_current_branch,
    read_snapshot,
    resolve_commit_ref,
    write_commit,
    write_snapshot,
)
from muse.core.validation import sanitize_display
from muse.core.workdir import apply_manifest
from muse.domain import SnapshotManifest
from muse.plugins.registry import read_domain, resolve_plugin

logger = logging.getLogger(__name__)

app = typer.Typer()


def _read_branch(root: pathlib.Path) -> str:
    return read_current_branch(root)


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
    domain = read_domain(root)
    plugin = resolve_plugin(root)

    target = resolve_commit_ref(root, repo_id, branch, ref)
    if target is None:
        typer.echo(f"❌ Commit '{ref}' not found.")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    # The delta for this cherry-pick is: target vs its parent.
    # Applying that delta on top of HEAD is a three-way merge where the
    # base is the target's parent, left is HEAD, and right is the target.
    base_manifest: dict[str, str] = {}
    if target.parent_commit_id:
        parent_commit = read_commit(root, target.parent_commit_id)
        if parent_commit:
            parent_snap = read_snapshot(root, parent_commit.snapshot_id)
            if parent_snap:
                base_manifest = parent_snap.manifest

    target_snap_rec = read_snapshot(root, target.snapshot_id)
    target_manifest = target_snap_rec.manifest if target_snap_rec else {}
    ours_manifest = get_head_snapshot_manifest(root, repo_id, branch) or {}

    base_snap = SnapshotManifest(files=base_manifest, domain=domain)
    ours_snap = SnapshotManifest(files=ours_manifest, domain=domain)
    target_snap = SnapshotManifest(files=target_manifest, domain=domain)

    result = plugin.merge(base_snap, ours_snap, target_snap)

    if not result.is_clean:
        write_merge_state(
            root,
            base_commit=target.parent_commit_id or "",
            ours_commit=get_head_commit_id(root, branch) or "",
            theirs_commit=target.commit_id,
            conflict_paths=result.conflicts,
        )
        typer.echo(f"❌ Cherry-pick conflict in {len(result.conflicts)} file(s):")
        for p in sorted(result.conflicts):
            typer.echo(f"  CONFLICT (both modified): {p}")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    merged_manifest = result.merged["files"]
    apply_manifest(root, merged_manifest)

    if no_commit:
        typer.echo(f"Applied {target.commit_id[:8]} to working tree. Run 'muse commit' to record.")
        return

    head_commit_id = get_head_commit_id(root, branch)
    # merged_manifest contains only object IDs already in the store
    # (sourced from base, ours, or theirs — all previously committed).
    # No re-scan or object re-write is needed.
    manifest = merged_manifest
    snapshot_id = compute_snapshot_id(manifest)
    committed_at = datetime.datetime.now(datetime.timezone.utc)
    commit_id = compute_commit_id(
        parent_ids=[head_commit_id] if head_commit_id else [],
        snapshot_id=snapshot_id,
        message=target.message,
        committed_at_iso=committed_at.isoformat(),
    )

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
    typer.echo(f"[{sanitize_display(branch)} {commit_id[:8]}] {sanitize_display(target.message)}")

"""muse merge — three-way merge a branch into the current branch.

Algorithm
---------
1. Find the merge base (LCA) of HEAD and the target branch.
2. Delegate conflict detection and manifest reconciliation to the domain plugin.
3. If clean → apply merged manifest, write new commit, advance HEAD.
4. If conflicts → write muse-work/ with conflict markers, write
   ``.muse/MERGE_STATE.json``, exit non-zero.
"""
from __future__ import annotations

import datetime
import json
import logging
import pathlib
import shutil

import typer

from muse.core.errors import ExitCode
from muse.core.merge_engine import (
    find_merge_base,
    write_merge_state,
)
from muse.core.object_store import restore_object
from muse.core.repo import require_repo
from muse.core.snapshot import compute_commit_id, compute_snapshot_id
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
from muse.domain import SnapshotManifest, StructuredMergePlugin
from muse.plugins.registry import read_domain, resolve_plugin

logger = logging.getLogger(__name__)

app = typer.Typer()


def _read_branch(root: pathlib.Path) -> str:
    head_ref = (root / ".muse" / "HEAD").read_text().strip()
    return head_ref.removeprefix("refs/heads/").strip()


def _read_repo_id(root: pathlib.Path) -> str:
    return str(json.loads((root / ".muse" / "repo.json").read_text())["repo_id"])


def _restore_from_manifest(root: pathlib.Path, manifest: dict[str, str]) -> None:
    workdir = root / "muse-work"
    if workdir.exists():
        shutil.rmtree(workdir)
    workdir.mkdir()
    for rel_path, object_id in manifest.items():
        restore_object(root, object_id, workdir / rel_path)


@app.callback(invoke_without_command=True)
def merge(
    ctx: typer.Context,
    branch: str = typer.Argument(..., help="Branch to merge into the current branch."),
    no_ff: bool = typer.Option(False, "--no-ff", help="Always create a merge commit, even for fast-forward."),
    message: str | None = typer.Option(None, "-m", "--message", help="Override the merge commit message."),
) -> None:
    """Three-way merge a branch into the current branch."""
    root = require_repo()
    repo_id = _read_repo_id(root)
    current_branch = _read_branch(root)
    domain = read_domain(root)
    plugin = resolve_plugin(root)

    if branch == current_branch:
        typer.echo("❌ Cannot merge a branch into itself.")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    ours_commit_id = get_head_commit_id(root, current_branch)
    theirs_commit_id = get_head_commit_id(root, branch)

    if theirs_commit_id is None:
        typer.echo(f"❌ Branch '{branch}' has no commits.")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    if ours_commit_id is None:
        typer.echo("❌ Current branch has no commits.")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    base_commit_id = find_merge_base(root, ours_commit_id, theirs_commit_id)

    if base_commit_id == theirs_commit_id:
        typer.echo("Already up to date.")
        return

    if base_commit_id == ours_commit_id and not no_ff:
        theirs_commit = read_commit(root, theirs_commit_id)
        if theirs_commit:
            snapshot = json.loads((root / ".muse" / "snapshots" / f"{theirs_commit.snapshot_id}.json").read_text())
            _restore_from_manifest(root, snapshot["manifest"])
        (root / ".muse" / "refs" / "heads" / current_branch).write_text(theirs_commit_id)
        typer.echo(f"Fast-forward to {theirs_commit_id[:8]}")
        return

    ours_manifest = get_head_snapshot_manifest(root, repo_id, current_branch) or {}
    theirs_manifest = get_head_snapshot_manifest(root, repo_id, branch) or {}
    base_manifest: dict[str, str] = {}
    if base_commit_id:
        base_commit = read_commit(root, base_commit_id)
        if base_commit:
            base_snap = read_snapshot(root, base_commit.snapshot_id)
            if base_snap:
                base_manifest = base_snap.manifest

    base_snap_obj = SnapshotManifest(files=base_manifest, domain=domain)
    ours_snap_obj = SnapshotManifest(files=ours_manifest, domain=domain)
    theirs_snap_obj = SnapshotManifest(files=theirs_manifest, domain=domain)

    # Phase 3: prefer operation-level merge when the plugin supports it.
    # Produces finer-grained conflict detection (sub-file / note level).
    # Falls back to file-level merge() for plugins without this capability.
    if isinstance(plugin, StructuredMergePlugin):
        ours_delta = plugin.diff(base_snap_obj, ours_snap_obj, repo_root=root)
        theirs_delta = plugin.diff(base_snap_obj, theirs_snap_obj, repo_root=root)
        result = plugin.merge_ops(
            base_snap_obj,
            ours_snap_obj,
            theirs_snap_obj,
            ours_delta["ops"],
            theirs_delta["ops"],
            repo_root=root,
        )
        logger.debug(
            "merge: used operation-level merge (%s); %d conflict(s)",
            type(plugin).__name__,
            len(result.conflicts),
        )
    else:
        result = plugin.merge(base_snap_obj, ours_snap_obj, theirs_snap_obj, repo_root=root)

    # Report any .museattributes auto-resolutions.
    if result.applied_strategies:
        for p, strategy in sorted(result.applied_strategies.items()):
            if strategy == "dimension-merge":
                dim_detail = result.dimension_reports.get(p, {})
                dim_summary = ", ".join(
                    f"{d}={v}" for d, v in sorted(dim_detail.items())
                )
                typer.echo(f"  ✔ dimension-merge: {p} ({dim_summary})")
            elif strategy != "manual":
                typer.echo(f"  ✔ [{strategy}] {p}")

    if not result.is_clean:
        write_merge_state(
            root,
            base_commit=base_commit_id or "",
            ours_commit=ours_commit_id,
            theirs_commit=theirs_commit_id,
            conflict_paths=result.conflicts,
            other_branch=branch,
        )
        typer.echo(f"❌ Merge conflict in {len(result.conflicts)} file(s):")
        for p in sorted(result.conflicts):
            typer.echo(f"  CONFLICT (both modified): {p}")
        typer.echo('\nFix conflicts and run "muse commit" to complete the merge.')
        raise typer.Exit(code=ExitCode.USER_ERROR)

    merged_manifest = result.merged["files"]
    _restore_from_manifest(root, merged_manifest)

    snapshot_id = compute_snapshot_id(merged_manifest)
    committed_at = datetime.datetime.now(datetime.timezone.utc)
    merge_message = message or f"Merge branch '{branch}' into {current_branch}"
    commit_id = compute_commit_id(
        parent_ids=[ours_commit_id, theirs_commit_id],
        snapshot_id=snapshot_id,
        message=merge_message,
        committed_at_iso=committed_at.isoformat(),
    )

    write_snapshot(root, SnapshotRecord(snapshot_id=snapshot_id, manifest=merged_manifest))
    write_commit(root, CommitRecord(
        commit_id=commit_id,
        repo_id=repo_id,
        branch=current_branch,
        snapshot_id=snapshot_id,
        message=merge_message,
        committed_at=committed_at,
        parent_commit_id=ours_commit_id,
        parent2_commit_id=theirs_commit_id,
    ))
    (root / ".muse" / "refs" / "heads" / current_branch).write_text(commit_id)

    typer.echo(f"Merged '{branch}' into '{current_branch}' ({commit_id[:8]})")

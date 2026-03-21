"""muse merge — three-way merge a branch into the current branch.

Algorithm
---------
1. Find the merge base (LCA) of HEAD and the target branch.
2. Delegate conflict detection and manifest reconciliation to the domain plugin.
3. If clean → apply merged manifest, write new commit, advance HEAD.
4. If conflicts → write conflict markers to the working tree, write
   ``.muse/MERGE_STATE.json``, exit non-zero.
"""

from __future__ import annotations

import datetime
import json
import logging
import pathlib
import typer

from muse.core.errors import ExitCode
from muse.core.merge_engine import (
    find_merge_base,
    write_merge_state,
)
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
    write_commit,
    write_snapshot,
)
from muse.core.reflog import append_reflog
from muse.core.validation import sanitize_display, validate_branch_name
from muse.core.workdir import apply_manifest
from muse.domain import SnapshotManifest, StructuredMergePlugin
from muse.plugins.registry import read_domain, resolve_plugin

logger = logging.getLogger(__name__)

app = typer.Typer()


def _read_branch(root: pathlib.Path) -> str:
    """Return the current branch name by reading ``.muse/HEAD``."""
    return read_current_branch(root)


def _read_repo_id(root: pathlib.Path) -> str:
    """Return the repository UUID from ``.muse/repo.json``."""
    return str(json.loads((root / ".muse" / "repo.json").read_text())["repo_id"])


def _restore_from_manifest(root: pathlib.Path, manifest: dict[str, str]) -> None:
    """Apply *manifest* to the working tree at *root*.

    Delegates to :func:`muse.core.workdir.apply_manifest` which surgically
    removes files no longer present in the target and restores the rest from
    the content-addressed object store.

    Args:
        root:     Repository root (the directory containing ``.muse/``).
        manifest: Mapping of POSIX-relative paths to SHA-256 object IDs.
    """
    apply_manifest(root, manifest)


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
            ff_snap = read_snapshot(root, theirs_commit.snapshot_id)
            if ff_snap:
                _restore_from_manifest(root, ff_snap.manifest)
        try:
            validate_branch_name(current_branch)
        except ValueError as exc:
            typer.echo(f"❌ Current branch name is invalid: {exc}")
            raise typer.Exit(code=ExitCode.INTERNAL_ERROR)
        (root / ".muse" / "refs" / "heads" / current_branch).write_text(theirs_commit_id)
        append_reflog(
            root, current_branch, old_id=ours_commit_id, new_id=theirs_commit_id,
            author="user",
            operation=f"merge: fast-forward {sanitize_display(branch)} → {sanitize_display(current_branch)}",
        )
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

    # Prefer operation-level merge when the plugin supports it.
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
    try:
        validate_branch_name(current_branch)
    except ValueError as exc:
        typer.echo(f"❌ Current branch name is invalid: {exc}")
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)
    (root / ".muse" / "refs" / "heads" / current_branch).write_text(commit_id)

    append_reflog(
        root, current_branch, old_id=ours_commit_id, new_id=commit_id,
        author="user",
        operation=f"merge: {sanitize_display(branch)} into {sanitize_display(current_branch)}",
    )

    typer.echo(f"Merged '{sanitize_display(branch)}' into '{sanitize_display(current_branch)}' ({commit_id[:8]})")

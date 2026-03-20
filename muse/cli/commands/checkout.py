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

import typer

from muse.core.errors import ExitCode
from muse.core.object_store import restore_object
from muse.core.repo import require_repo
from muse.core.store import (
    get_head_commit_id,
    get_head_snapshot_id,
    read_snapshot,
    resolve_commit_ref,
)
from muse.core.validation import contain_path, sanitize_display, validate_branch_name
from muse.domain import SnapshotManifest
from muse.plugins.registry import read_domain, resolve_plugin

logger = logging.getLogger(__name__)

app = typer.Typer()


def _read_current_branch(root: pathlib.Path) -> str:
    head_ref = (root / ".muse" / "HEAD").read_text().strip()
    return head_ref.removeprefix("refs/heads/").strip()


def _read_repo_id(root: pathlib.Path) -> str:
    return str(json.loads((root / ".muse" / "repo.json").read_text())["repo_id"])


def _checkout_snapshot(
    root: pathlib.Path,
    target_snapshot_id: str,
    current_snapshot_id: str | None,
) -> None:
    """Incrementally update state/ from current to target snapshot.

    Uses the domain plugin to compute the delta between the two snapshots and
    only touches files that actually changed — removing deleted paths and
    restoring added/modified ones from the object store.  Calls
    ``plugin.apply()`` as the domain-level post-checkout hook.
    """
    plugin = resolve_plugin(root)
    domain = read_domain(root)

    target_snap_rec = read_snapshot(root, target_snapshot_id)
    if target_snap_rec is None:
        typer.echo(f"❌ Snapshot {target_snapshot_id[:8]} not found in object store.")
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)

    target_snap = SnapshotManifest(files=target_snap_rec.manifest, domain=domain)

    if current_snapshot_id is not None:
        cur_rec = read_snapshot(root, current_snapshot_id)
        current_snap = (
            SnapshotManifest(files=cur_rec.manifest, domain=domain)
            if cur_rec else SnapshotManifest(files={}, domain=domain)
        )
    else:
        current_snap = SnapshotManifest(files={}, domain=domain)

    delta = plugin.diff(current_snap, target_snap)

    workdir = root / "state"
    workdir.mkdir(exist_ok=True)

    # Remove files that no longer exist in the target snapshot.
    removed = [op["address"] for op in delta["ops"] if op["op"] == "delete"]
    for rel_path in removed:
        fp = workdir / rel_path
        if fp.exists():
            fp.unlink()

    # Restore added and modified files from the content-addressed store.
    # InsertOp, ReplaceOp, and PatchOp all mean the file's content changed;
    # the authoritative hash for each is in the target snapshot manifest.
    to_restore = [
        op["address"] for op in delta["ops"]
        if op["op"] in ("insert", "replace", "patch")
    ]
    for rel_path in to_restore:
        object_id = target_snap_rec.manifest[rel_path]
        try:
            safe_dest = contain_path(workdir, rel_path)
        except ValueError as exc:
            logger.warning("⚠️ Skipping unsafe manifest path %r: %s", rel_path, exc)
            continue
        if not restore_object(root, object_id, safe_dest):
            typer.echo(f"⚠️  Object {object_id[:8]} for '{sanitize_display(rel_path)}' not in local store — skipped.")

    # Domain-level post-checkout hook: rescan the workdir to confirm state.
    plugin.apply(delta, workdir)


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

    current_snapshot_id = get_head_snapshot_id(root, repo_id, current_branch)

    if create:
        try:
            validate_branch_name(target)
        except ValueError as exc:
            typer.echo(f"❌ Invalid branch name: {exc}")
            raise typer.Exit(code=ExitCode.USER_ERROR)
        ref_file = muse_dir / "refs" / "heads" / target
        if ref_file.exists():
            typer.echo(f"❌ Branch '{sanitize_display(target)}' already exists. Use 'muse checkout {sanitize_display(target)}' to switch to it.")
            raise typer.Exit(code=ExitCode.USER_ERROR)
        current_commit = get_head_commit_id(root, current_branch) or ""
        ref_file.parent.mkdir(parents=True, exist_ok=True)
        ref_file.write_text(current_commit)
        (muse_dir / "HEAD").write_text(f"refs/heads/{target}\n")
        typer.echo(f"Switched to a new branch '{sanitize_display(target)}'")
        return

    # Check if target is a known branch
    ref_file = muse_dir / "refs" / "heads" / target
    if ref_file.exists():
        if target == current_branch:
            typer.echo(f"Already on '{target}'")
            return

        target_snapshot_id = get_head_snapshot_id(root, repo_id, target)
        if target_snapshot_id:
            _checkout_snapshot(root, target_snapshot_id, current_snapshot_id)

        (muse_dir / "HEAD").write_text(f"refs/heads/{target}\n")
        typer.echo(f"Switched to branch '{target}'")
        return

    # Try as a commit ID (detached HEAD)
    commit = resolve_commit_ref(root, repo_id, current_branch, target)
    if commit is None:
        typer.echo(f"❌ '{target}' is not a branch or commit ID.")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    _checkout_snapshot(root, commit.snapshot_id, current_snapshot_id)
    (muse_dir / "HEAD").write_text(commit.commit_id + "\n")
    typer.echo(f"HEAD is now at {commit.commit_id[:8]} {sanitize_display(commit.message)}")

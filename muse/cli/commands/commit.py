"""muse commit — record the current muse-work/ state as a new version.

Algorithm
---------
1. Resolve repo root (walk up for ``.muse/``).
2. Read repo_id from ``.muse/repo.json``, current branch from ``.muse/HEAD``.
3. Walk ``muse-work/`` and hash each file → snapshot manifest.
4. If HEAD snapshot_id == current snapshot_id → "nothing to commit".
5. Compute deterministic commit_id = sha256(parents | snapshot | message | ts).
6. Write blob objects to ``.muse/objects/``.
7. Write snapshot JSON to ``.muse/snapshots/<snapshot_id>.json``.
8. Write commit JSON to ``.muse/commits/<commit_id>.json``.
9. Advance ``.muse/refs/heads/<branch>`` to the new commit_id.
"""
from __future__ import annotations

import datetime
import json
import logging
import pathlib
from typing import Optional

import typer

from muse.core.errors import ExitCode
from muse.core.merge_engine import read_merge_state
from muse.core.object_store import write_object_from_path
from muse.core.repo import require_repo
from muse.core.snapshot import build_snapshot_manifest, compute_commit_id, compute_snapshot_id
from muse.core.store import (
    CommitRecord,
    SnapshotRecord,
    get_head_snapshot_id,
    write_commit,
    write_snapshot,
)

logger = logging.getLogger(__name__)

app = typer.Typer()


def _read_repo_id(root: pathlib.Path) -> str:
    return json.loads((root / ".muse" / "repo.json").read_text())["repo_id"]


def _read_branch(root: pathlib.Path) -> tuple[str, pathlib.Path]:
    """Return (branch_name, ref_file_path)."""
    head_ref = (root / ".muse" / "HEAD").read_text().strip()
    branch = head_ref.removeprefix("refs/heads/").strip()
    ref_path = root / ".muse" / head_ref
    return branch, ref_path


def _read_parent_id(ref_path: pathlib.Path) -> str | None:
    if not ref_path.exists():
        return None
    raw = ref_path.read_text().strip()
    return raw or None


@app.callback(invoke_without_command=True)
def commit(
    ctx: typer.Context,
    message: Optional[str] = typer.Option(None, "-m", "--message", help="Commit message."),
    allow_empty: bool = typer.Option(False, "--allow-empty", help="Allow committing with no changes."),
    section: Optional[str] = typer.Option(None, "--section", help="Tag this commit with a musical section (verse, chorus, bridge…)."),
    track: Optional[str] = typer.Option(None, "--track", help="Tag this commit with an instrument track (drums, bass, keys…)."),
    emotion: Optional[str] = typer.Option(None, "--emotion", help="Attach an emotion label (joyful, melancholic, tense…)."),
    author: Optional[str] = typer.Option(None, "--author", help="Override the commit author."),
) -> None:
    """Record the current muse-work/ state as a new version."""
    if message is None and not allow_empty:
        typer.echo("❌ Provide a commit message with -m MESSAGE.")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    root = require_repo()

    merge_state = read_merge_state(root)
    if merge_state is not None and merge_state.conflict_paths:
        typer.echo("❌ You have unresolved merge conflicts. Resolve them before committing.")
        for p in sorted(merge_state.conflict_paths):
            typer.echo(f"  both modified: {p}")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    repo_id = _read_repo_id(root)
    branch, ref_path = _read_branch(root)
    parent_id = _read_parent_id(ref_path)

    workdir = root / "muse-work"
    if not workdir.exists():
        typer.echo("❌ No muse-work/ directory found. Run 'muse init' first.")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    manifest = build_snapshot_manifest(workdir)
    if not manifest and not allow_empty:
        typer.echo("⚠️  muse-work/ is empty — nothing to commit.")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    snapshot_id = compute_snapshot_id(manifest)

    if not allow_empty:
        head_snapshot = get_head_snapshot_id(root, repo_id, branch)
        if head_snapshot == snapshot_id:
            typer.echo("Nothing to commit, working tree clean")
            raise typer.Exit(code=ExitCode.SUCCESS)

    committed_at = datetime.datetime.now(datetime.timezone.utc)
    parent_ids = [parent_id] if parent_id else []
    commit_id = compute_commit_id(
        parent_ids=parent_ids,
        snapshot_id=snapshot_id,
        message=message or "",
        committed_at_iso=committed_at.isoformat(),
    )

    metadata: dict[str, str] = {}
    if section:
        metadata["section"] = section
    if track:
        metadata["track"] = track
    if emotion:
        metadata["emotion"] = emotion

    for rel_path, object_id in manifest.items():
        write_object_from_path(root, object_id, workdir / rel_path)

    write_snapshot(root, SnapshotRecord(snapshot_id=snapshot_id, manifest=manifest))

    write_commit(root, CommitRecord(
        commit_id=commit_id,
        repo_id=repo_id,
        branch=branch,
        snapshot_id=snapshot_id,
        message=message or "",
        committed_at=committed_at,
        parent_commit_id=parent_id,
        author=author or "",
        metadata=metadata,
    ))

    ref_path.parent.mkdir(parents=True, exist_ok=True)
    ref_path.write_text(commit_id)

    typer.echo(f"[{branch} {commit_id[:8]}] {message or ''}")

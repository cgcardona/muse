"""muse commit — record the current state/ state as a new version.

Algorithm
---------
1. Resolve repo root (walk up for ``.muse/``).
2. Read repo_id from ``.muse/repo.json``, current branch from ``.muse/HEAD``.
3. Walk ``state/`` and hash each file → snapshot manifest.
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
import os
import pathlib

import typer

from muse.core.errors import ExitCode
from muse.core.merge_engine import clear_merge_state, read_merge_state
from muse.core.object_store import write_object_from_path
from muse.core.rerere import record_resolutions as rerere_record_resolutions
from muse.core.provenance import make_agent_identity, read_agent_key, sign_commit_hmac
from muse.core.repo import require_repo
from muse.core.snapshot import compute_commit_id, compute_snapshot_id
from muse.core.store import (
    CommitRecord,
    SnapshotRecord,
    get_head_snapshot_id,
    read_commit,
    read_current_branch,
    read_snapshot,
    write_commit,
    write_snapshot,
)
from muse.core.reflog import append_reflog
from muse.core.validation import sanitize_display, validate_branch_name
from muse.domain import SemVerBump, SnapshotManifest, StructuredDelta, infer_sem_ver_bump
from muse.plugins.registry import read_domain, resolve_plugin

logger = logging.getLogger(__name__)

app = typer.Typer()


def _read_repo_id(root: pathlib.Path) -> str:
    return str(json.loads((root / ".muse" / "repo.json").read_text())["repo_id"])


def _read_branch(root: pathlib.Path) -> tuple[str, pathlib.Path]:
    """Return (branch_name, ref_file_path).

    Delegates HEAD parsing and branch-name validation to the store so
    that format details are not duplicated across commands.
    """
    branch = read_current_branch(root)
    ref_path = root / ".muse" / "refs" / "heads" / branch
    return branch, ref_path


def _read_parent_id(ref_path: pathlib.Path) -> str | None:
    if not ref_path.exists():
        return None
    raw = ref_path.read_text().strip()
    return raw or None


@app.callback(invoke_without_command=True)
def commit(
    ctx: typer.Context,
    message: str | None = typer.Option(None, "-m", "--message", help="Commit message."),
    allow_empty: bool = typer.Option(False, "--allow-empty", help="Allow committing with no changes."),
    section: str | None = typer.Option(None, "--section", help="Tag this commit with a musical section (verse, chorus, bridge…)."),
    track: str | None = typer.Option(None, "--track", help="Tag this commit with an instrument track (drums, bass, keys…)."),
    emotion: str | None = typer.Option(None, "--emotion", help="Attach an emotion label (joyful, melancholic, tense…)."),
    author: str | None = typer.Option(None, "--author", help="Override the commit author."),
    agent_id: str | None = typer.Option(None, "--agent-id", help="Agent identity string (overrides MUSE_AGENT_ID env var)."),
    model_id: str | None = typer.Option(None, "--model-id", help="Model identifier for AI agents (overrides MUSE_MODEL_ID env var)."),
    toolchain_id: str | None = typer.Option(None, "--toolchain-id", help="Toolchain string (overrides MUSE_TOOLCHAIN_ID env var)."),
    sign: bool = typer.Option(False, "--sign", help="HMAC-sign the commit using the agent's stored key (requires --agent-id or MUSE_AGENT_ID)."),
) -> None:
    """Record the current state/ state as a new version."""
    if message is None and not allow_empty:
        typer.echo("❌ Provide a commit message with -m MESSAGE.")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    root = require_repo()

    # Read merge state before any writes — needed for rerere recording below.
    merge_state = read_merge_state(root)
    if merge_state is not None and merge_state.conflict_paths:
        typer.echo("❌ You have unresolved merge conflicts. Resolve them before committing.")
        for p in sorted(merge_state.conflict_paths):
            typer.echo(f"  both modified: {p}")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    repo_id = _read_repo_id(root)
    branch, ref_path = _read_branch(root)
    parent_id = _read_parent_id(ref_path)

    plugin = resolve_plugin(root)
    snap = plugin.snapshot(root)
    manifest = snap["files"]
    if not manifest and not allow_empty:
        typer.echo("⚠️  Nothing tracked — working tree is empty.")
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
        write_object_from_path(root, object_id, root / rel_path)

    write_snapshot(root, SnapshotRecord(snapshot_id=snapshot_id, manifest=manifest))

    # Compute a structured delta against the parent snapshot so muse show
    # can display note-level changes without reloading blobs.
    structured_delta: StructuredDelta | None = None
    sem_ver_bump: SemVerBump = "none"
    breaking_changes: list[str] = []
    if parent_id is not None:
        parent_commit_rec = read_commit(root, parent_id)
        if parent_commit_rec is not None:
            parent_snap_record = read_snapshot(root, parent_commit_rec.snapshot_id)
            if parent_snap_record is not None:
                domain = read_domain(root)
                base_snap = SnapshotManifest(
                    files=dict(parent_snap_record.manifest),
                    domain=domain,
                )
                try:
                    structured_delta = plugin.diff(base_snap, snap, repo_root=root)
                except Exception:
                    structured_delta = None

    # Infer semantic version bump from the structured delta.
    if structured_delta is not None:
        sem_ver_bump, breaking_changes = infer_sem_ver_bump(structured_delta)
        structured_delta["sem_ver_bump"] = sem_ver_bump
        structured_delta["breaking_changes"] = breaking_changes

    # Resolve agent provenance: CLI flags take priority over environment vars.
    # Truncate to 256 chars to prevent environment injection of arbitrarily
    # long or control-character-laden strings into commit records.
    _MAX_PROV = 256
    resolved_agent_id = (agent_id or os.environ.get("MUSE_AGENT_ID", ""))[:_MAX_PROV]
    resolved_model_id = (model_id or os.environ.get("MUSE_MODEL_ID", ""))[:_MAX_PROV]
    resolved_toolchain_id = (toolchain_id or os.environ.get("MUSE_TOOLCHAIN_ID", ""))[:_MAX_PROV]
    resolved_prompt_hash = os.environ.get("MUSE_PROMPT_HASH", "")[:_MAX_PROV]

    signature = ""
    signer_key_id = ""
    if sign and resolved_agent_id:
        key = read_agent_key(root, resolved_agent_id)
        if key is not None:
            signature = sign_commit_hmac(commit_id, key)
            from muse.core.provenance import key_fingerprint
            signer_key_id = key_fingerprint(key)
        else:
            logger.warning("No signing key found for agent %r — commit will be unsigned.", resolved_agent_id)

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
        structured_delta=structured_delta,
        sem_ver_bump=sem_ver_bump,
        breaking_changes=breaking_changes,
        agent_id=resolved_agent_id,
        model_id=resolved_model_id,
        toolchain_id=resolved_toolchain_id,
        prompt_hash=resolved_prompt_hash,
        signature=signature,
        signer_key_id=signer_key_id,
    ))

    ref_path.parent.mkdir(parents=True, exist_ok=True)
    ref_path.write_text(commit_id)

    append_reflog(
        root,
        branch,
        old_id=parent_id,
        new_id=commit_id,
        author=author or "unknown",
        operation=f"commit: {sanitize_display(message or '(no message)')}",
    )

    # If this commit completed a conflicted merge, record how each conflict was
    # resolved so rerere can replay it on future identical conflicts.
    if merge_state is not None and merge_state.ours_commit and merge_state.theirs_commit:
        from muse.core.store import read_commit as _read_commit, read_snapshot as _read_snap

        def _manifest_for(cid: str) -> dict[str, str]:
            cr = _read_commit(root, cid)
            if cr is None:
                return {}
            snap = _read_snap(root, cr.snapshot_id)
            return snap.manifest if snap else {}

        ours_manifest = _manifest_for(merge_state.ours_commit)
        theirs_manifest = _manifest_for(merge_state.theirs_commit)
        domain = read_domain(root)
        rerere_record_resolutions(
            root,
            list(merge_state.conflict_paths),
            ours_manifest,
            theirs_manifest,
            manifest,
            domain,
            plugin,
        )
        clear_merge_state(root)

    typer.echo(f"[{sanitize_display(branch)} {commit_id[:8]}] {sanitize_display(message or '')}")

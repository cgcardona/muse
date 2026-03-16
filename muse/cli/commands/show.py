"""muse show — inspect a commit: metadata, diff, and files."""
from __future__ import annotations

import json
import logging
import pathlib
from typing import Optional

import typer

from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.store import get_commit_snapshot_manifest, read_commit, resolve_commit_ref

logger = logging.getLogger(__name__)

app = typer.Typer()


def _read_branch(root: pathlib.Path) -> str:
    head_ref = (root / ".muse" / "HEAD").read_text().strip()
    return head_ref.removeprefix("refs/heads/").strip()


def _read_repo_id(root: pathlib.Path) -> str:
    return json.loads((root / ".muse" / "repo.json").read_text())["repo_id"]


@app.callback(invoke_without_command=True)
def show(
    ctx: typer.Context,
    ref: Optional[str] = typer.Argument(None, help="Commit ID or branch (default: HEAD)."),
    stat: bool = typer.Option(True, "--stat/--no-stat", help="Show file change summary."),
    json_out: bool = typer.Option(False, "--json", help="Output as JSON."),
) -> None:
    """Inspect a commit: metadata, diff, and files."""
    root = require_repo()
    repo_id = _read_repo_id(root)
    branch = _read_branch(root)

    commit = resolve_commit_ref(root, repo_id, branch, ref)
    if commit is None:
        typer.echo(f"❌ Commit '{ref}' not found.")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    if json_out:
        import json as json_mod
        data = commit.to_dict()
        if stat:
            current = get_commit_snapshot_manifest(root, commit.commit_id) or {}
            parent = get_commit_snapshot_manifest(root, commit.parent_commit_id) if commit.parent_commit_id else {}
            parent = parent or {}
            data["files_added"] = sorted(set(current) - set(parent))
            data["files_removed"] = sorted(set(parent) - set(current))
            data["files_modified"] = sorted(
                p for p in set(current) & set(parent) if current[p] != parent[p]
            )
        typer.echo(json_mod.dumps(data, indent=2, default=str))
        return

    typer.echo(f"commit {commit.commit_id}")
    if commit.parent_commit_id:
        typer.echo(f"Parent: {commit.parent_commit_id[:8]}")
    if commit.parent2_commit_id:
        typer.echo(f"Parent: {commit.parent2_commit_id[:8]} (merge)")
    if commit.author:
        typer.echo(f"Author: {commit.author}")
    typer.echo(f"Date:   {commit.committed_at}")
    if commit.metadata:
        for k, v in sorted(commit.metadata.items()):
            typer.echo(f"        {k}: {v}")
    typer.echo(f"\n    {commit.message}\n")

    if stat:
        current = get_commit_snapshot_manifest(root, commit.commit_id) or {}
        parent: dict[str, str] = {}
        if commit.parent_commit_id:
            parent = get_commit_snapshot_manifest(root, commit.parent_commit_id) or {}

        added = sorted(set(current) - set(parent))
        removed = sorted(set(parent) - set(current))
        modified = sorted(p for p in set(current) & set(parent) if current[p] != parent[p])

        for p in added:
            typer.echo(f" + {p}")
        for p in removed:
            typer.echo(f" - {p}")
        for p in modified:
            typer.echo(f" M {p}")

        total = len(added) + len(removed) + len(modified)
        if total:
            typer.echo(f"\n {total} file(s) changed")

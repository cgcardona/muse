"""muse show — inspect a commit: metadata, diff, and files."""

from __future__ import annotations

import json
import logging
import pathlib

import typer

from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.store import get_commit_snapshot_manifest, read_commit, resolve_commit_ref
from muse.domain import DomainOp

logger = logging.getLogger(__name__)

app = typer.Typer()


def _read_branch(root: pathlib.Path) -> str:
    head_ref = (root / ".muse" / "HEAD").read_text().strip()
    return head_ref.removeprefix("refs/heads/").strip()


def _read_repo_id(root: pathlib.Path) -> str:
    return str(json.loads((root / ".muse" / "repo.json").read_text())["repo_id"])


def _format_op(op: DomainOp) -> list[str]:
    """Return one or more display lines for a single domain op.

    Each branch checks ``op["op"]`` directly so mypy can narrow the
    TypedDict union to the specific subtype before accessing its fields.
    """
    if op["op"] == "insert":
        return [f" A  {op['address']}"]
    if op["op"] == "delete":
        return [f" D  {op['address']}"]
    if op["op"] == "replace":
        return [f" M  {op['address']}"]
    if op["op"] == "move":
        return [f" R  {op['address']}  ({op['from_position']} → {op['to_position']})"]
    if op["op"] == "mutate":
        fields = ", ".join(
            f"{k}: {v['old']}→{v['new']}" for k, v in op.get("fields", {}).items()
        )
        return [f" ~ {op['address']}  ({fields or op.get('old_summary', '')}→{op.get('new_summary', '')})"]
    # op["op"] == "patch" — the only remaining variant.
    lines = [f" M  {op['address']}"]
    if op["child_summary"]:
        lines.append(f"    └─ {op['child_summary']}")
    return lines


@app.callback(invoke_without_command=True)
def show(
    ctx: typer.Context,
    ref: str | None = typer.Argument(None, help="Commit ID or branch (default: HEAD)."),
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
        commit_data = commit.to_dict()
        if stat:
            cur = get_commit_snapshot_manifest(root, commit.commit_id) or {}
            par: dict[str, str] = (
                get_commit_snapshot_manifest(root, commit.parent_commit_id) or {}
                if commit.parent_commit_id else {}
            )
            stats = {
                "files_added": sorted(set(cur) - set(par)),
                "files_removed": sorted(set(par) - set(cur)),
                "files_modified": sorted(
                    p for p in set(cur) & set(par) if cur[p] != par[p]
                ),
            }
            typer.echo(json_mod.dumps({**commit_data, **stats}, indent=2, default=str))
        else:
            typer.echo(json_mod.dumps(commit_data, indent=2, default=str))
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

    if not stat:
        return

    # Prefer the structured delta stored on the commit.
    # It carries rich note-level detail and is faster (no blob reloading).
    if commit.structured_delta is not None:
        delta = commit.structured_delta
        if not delta["ops"]:
            typer.echo(" (no changes)")
            return
        lines: list[str] = []
        for op in delta["ops"]:
            lines.extend(_format_op(op))
        for line in lines:
            typer.echo(line)
        typer.echo(f"\n {delta['summary']}")
        return

    # Fallback for initial commits or pre-Phase-1 commits: compute file-level
    # diff from snapshot manifests directly.
    current = get_commit_snapshot_manifest(root, commit.commit_id) or {}
    parent: dict[str, str] = {}
    if commit.parent_commit_id:
        parent = get_commit_snapshot_manifest(root, commit.parent_commit_id) or {}

    added = sorted(set(current) - set(parent))
    removed = sorted(set(parent) - set(current))
    modified = sorted(p for p in set(current) & set(parent) if current[p] != parent[p])

    for p in added:
        typer.echo(f" A  {p}")
    for p in removed:
        typer.echo(f" D  {p}")
    for p in modified:
        typer.echo(f" M  {p}")

    total = len(added) + len(removed) + len(modified)
    if total:
        typer.echo(f"\n {total} file(s) changed")

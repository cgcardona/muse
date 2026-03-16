"""muse diff — compare working tree against HEAD, or compare two commits."""
from __future__ import annotations

import json
import logging
import pathlib

import typer

from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.snapshot import build_snapshot_manifest
from muse.core.store import get_commit_snapshot_manifest, get_head_snapshot_manifest, resolve_commit_ref

logger = logging.getLogger(__name__)

app = typer.Typer()


def _read_branch(root: pathlib.Path) -> str:
    head_ref = (root / ".muse" / "HEAD").read_text().strip()
    return head_ref.removeprefix("refs/heads/").strip()


def _read_repo_id(root: pathlib.Path) -> str:
    return str(json.loads((root / ".muse" / "repo.json").read_text())["repo_id"])


def _print_diff(base_manifest: dict[str, str], target_manifest: dict[str, str]) -> int:
    """Print a file-level diff. Returns number of changed files."""
    base_paths = set(base_manifest)
    target_paths = set(target_manifest)

    added = sorted(target_paths - base_paths)
    removed = sorted(base_paths - target_paths)
    common = base_paths & target_paths
    modified = sorted(p for p in common if base_manifest[p] != target_manifest[p])

    for p in added:
        typer.echo(f"A  {p}")
    for p in removed:
        typer.echo(f"D  {p}")
    for p in modified:
        typer.echo(f"M  {p}")

    return len(added) + len(removed) + len(modified)


@app.callback(invoke_without_command=True)
def diff(
    ctx: typer.Context,
    commit_a: str | None = typer.Argument(None, help="Base commit ID (default: HEAD)."),
    commit_b: str | None = typer.Argument(None, help="Target commit ID (default: working tree)."),
    stat: bool = typer.Option(False, "--stat", help="Show summary statistics only."),
) -> None:
    """Compare working tree against HEAD, or compare two commits."""
    root = require_repo()
    repo_id = _read_repo_id(root)
    branch = _read_branch(root)

    if commit_a is None:
        # Working tree vs HEAD
        base = get_head_snapshot_manifest(root, repo_id, branch) or {}
        target = build_snapshot_manifest(root / "muse-work")
    elif commit_b is None:
        # HEAD vs commit_a
        base = get_head_snapshot_manifest(root, repo_id, branch) or {}
        target = get_commit_snapshot_manifest(root, commit_a) or {}
    else:
        base = get_commit_snapshot_manifest(root, commit_a) or {}
        target = get_commit_snapshot_manifest(root, commit_b) or {}

    changed = _print_diff(base, target)
    if changed == 0:
        typer.echo("No differences.")
    elif stat:
        typer.echo(f"\n{changed} file(s) changed")

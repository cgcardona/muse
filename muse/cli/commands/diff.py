"""muse diff — compare working tree against HEAD, or compare two commits."""
from __future__ import annotations

import json
import logging
import pathlib

import typer

from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.store import get_commit_snapshot_manifest, get_head_snapshot_manifest, resolve_commit_ref
from muse.domain import SnapshotManifest
from muse.plugins.registry import read_domain, resolve_plugin

logger = logging.getLogger(__name__)

app = typer.Typer()


def _read_branch(root: pathlib.Path) -> str:
    head_ref = (root / ".muse" / "HEAD").read_text().strip()
    return head_ref.removeprefix("refs/heads/").strip()


def _read_repo_id(root: pathlib.Path) -> str:
    return str(json.loads((root / ".muse" / "repo.json").read_text())["repo_id"])


def _print_delta(added: list[str], removed: list[str], modified: list[str]) -> int:
    """Print a file-level delta. Returns number of changed files."""
    for p in sorted(added):
        typer.echo(f"A  {p}")
    for p in sorted(removed):
        typer.echo(f"D  {p}")
    for p in sorted(modified):
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
    domain = read_domain(root)
    plugin = resolve_plugin(root)

    if commit_a is None:
        base_snap = SnapshotManifest(
            files=get_head_snapshot_manifest(root, repo_id, branch) or {},
            domain=domain,
        )
        target_snap = plugin.snapshot(root / "muse-work")
    elif commit_b is None:
        base_snap = SnapshotManifest(
            files=get_head_snapshot_manifest(root, repo_id, branch) or {},
            domain=domain,
        )
        target_snap = SnapshotManifest(
            files=get_commit_snapshot_manifest(root, commit_a) or {},
            domain=domain,
        )
    else:
        base_snap = SnapshotManifest(
            files=get_commit_snapshot_manifest(root, commit_a) or {},
            domain=domain,
        )
        target_snap = SnapshotManifest(
            files=get_commit_snapshot_manifest(root, commit_b) or {},
            domain=domain,
        )

    delta = plugin.diff(base_snap, target_snap)
    changed = _print_delta(delta["added"], delta["removed"], delta["modified"])

    if changed == 0:
        typer.echo("No differences.")
    elif stat:
        typer.echo(f"\n{changed} file(s) changed")

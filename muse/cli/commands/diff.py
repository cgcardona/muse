"""muse diff — compare working tree against HEAD, or compare two commits."""

from __future__ import annotations

import json
import logging
import pathlib

import typer

from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.store import get_commit_snapshot_manifest, get_head_snapshot_manifest, resolve_commit_ref
from muse.core.validation import sanitize_display
from muse.domain import DomainOp, SnapshotManifest
from muse.plugins.registry import read_domain, resolve_plugin

logger = logging.getLogger(__name__)

app = typer.Typer()


def _read_branch(root: pathlib.Path) -> str:
    head_ref = (root / ".muse" / "HEAD").read_text().strip()
    return head_ref.removeprefix("refs/heads/").strip()


def _read_repo_id(root: pathlib.Path) -> str:
    return str(json.loads((root / ".muse" / "repo.json").read_text())["repo_id"])


def _print_structured_delta(ops: list[DomainOp]) -> int:
    """Print a structured delta op-by-op. Returns the number of ops printed.

    Each branch checks ``op["op"]`` directly so mypy can narrow the
    TypedDict union to the specific subtype before accessing its fields.
    """
    for op in ops:
        if op["op"] == "insert":
            typer.echo(f"A  {op['address']}")
        elif op["op"] == "delete":
            typer.echo(f"D  {op['address']}")
        elif op["op"] == "replace":
            typer.echo(f"M  {op['address']}")
        elif op["op"] == "move":
            typer.echo(
                f"R  {op['address']}  ({op['from_position']} → {op['to_position']})"
            )
        elif op["op"] == "patch":
            typer.echo(f"M  {op['address']}")
            if op["child_summary"]:
                typer.echo(f"   └─ {op['child_summary']}")
    return len(ops)


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

    def _resolve_manifest(ref: str) -> dict[str, str]:
        """Resolve a ref (branch, short SHA, full SHA) to its snapshot manifest."""
        resolved = resolve_commit_ref(root, repo_id, branch, ref)
        if resolved is None:
            typer.echo(f"⚠️ Commit '{sanitize_display(ref)}' not found.")
            raise typer.Exit(code=ExitCode.USER_ERROR)
        return get_commit_snapshot_manifest(root, resolved.commit_id) or {}

    if commit_a is None:
        base_snap = SnapshotManifest(
            files=get_head_snapshot_manifest(root, repo_id, branch) or {},
            domain=domain,
        )
        target_snap = plugin.snapshot(root / "state")
    elif commit_b is None:
        # Single ref provided: diff HEAD vs that ref's snapshot.
        base_snap = SnapshotManifest(
            files=get_head_snapshot_manifest(root, repo_id, branch) or {},
            domain=domain,
        )
        target_snap = SnapshotManifest(
            files=_resolve_manifest(commit_a),
            domain=domain,
        )
    else:
        base_snap = SnapshotManifest(
            files=_resolve_manifest(commit_a),
            domain=domain,
        )
        target_snap = SnapshotManifest(
            files=_resolve_manifest(commit_b),
            domain=domain,
        )

    delta = plugin.diff(base_snap, target_snap, repo_root=root)

    if stat:
        typer.echo(delta["summary"] if delta["ops"] else "No differences.")
        return

    changed = _print_structured_delta(delta["ops"])

    if changed == 0:
        typer.echo("No differences.")
    else:
        typer.echo(f"\n{delta['summary']}")

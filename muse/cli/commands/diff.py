"""muse diff — compare working tree against HEAD, or compare two commits."""

from __future__ import annotations

import json
import logging
import pathlib

import typer

from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.store import get_commit_snapshot_manifest, get_head_snapshot_manifest, read_current_branch, resolve_commit_ref
from muse.core.validation import sanitize_display
from muse.domain import DomainOp, SnapshotManifest
from muse.plugins.registry import read_domain, resolve_plugin

logger = logging.getLogger(__name__)

app = typer.Typer()


def _read_branch(root: pathlib.Path) -> str:
    return read_current_branch(root)


def _read_repo_id(root: pathlib.Path) -> str:
    return str(json.loads((root / ".muse" / "repo.json").read_text())["repo_id"])


_MAX_INLINE_CHILDREN = 12


def _green(text: str) -> str:
    return typer.style(text, fg=typer.colors.GREEN)


def _red(text: str) -> str:
    return typer.style(text, fg=typer.colors.RED)


def _yellow(text: str) -> str:
    return typer.style(text, fg=typer.colors.YELLOW)


def _cyan(text: str) -> str:
    return typer.style(text, fg=typer.colors.CYAN)


def _print_child_ops(child_ops: list[DomainOp]) -> None:
    """Render symbol-level child ops with tree connectors and colours.

    Shows up to ``_MAX_INLINE_CHILDREN`` entries inline; summarises the rest
    on a single trailing line so the output stays readable for large files.
    """
    visible = child_ops[:_MAX_INLINE_CHILDREN]
    overflow = len(child_ops) - len(visible)

    for i, cop in enumerate(visible):
        is_last = (i == len(visible) - 1) and overflow == 0
        connector = "└─" if is_last else "├─"
        if cop["op"] == "insert":
            typer.echo(f"   {connector} " + _green(cop["content_summary"]))
        elif cop["op"] == "delete":
            typer.echo(f"   {connector} " + _red(cop["content_summary"]))
        elif cop["op"] == "replace":
            typer.echo(f"   {connector} " + _yellow(cop["new_summary"]))
        elif cop["op"] == "move":
            typer.echo(
                f"   {connector} "
                + _cyan(f"{cop['address']}  ({cop['from_position']} → {cop['to_position']})")
            )

    if overflow > 0:
        typer.echo(f"   └─ … and {overflow} more")


def _print_structured_delta(ops: list[DomainOp]) -> int:
    """Print a colour-coded delta op-by-op. Returns the number of ops printed.

    Colour scheme mirrors standard diff conventions:
    - Green  → added   (A)
    - Red    → deleted (D)
    - Yellow → modified (M)
    - Cyan   → moved / renamed (R)

    Each branch checks ``op["op"]`` directly so mypy can narrow the
    TypedDict union to the specific subtype before accessing its fields.
    """
    for op in ops:
        if op["op"] == "insert":
            typer.echo(_green(f"A  {op['address']}"))
        elif op["op"] == "delete":
            typer.echo(_red(f"D  {op['address']}"))
        elif op["op"] == "replace":
            typer.echo(_yellow(f"M  {op['address']}"))
        elif op["op"] == "move":
            typer.echo(
                _cyan(f"R  {op['address']}  ({op['from_position']} → {op['to_position']})")
            )
        elif op["op"] == "patch":
            child_ops = op["child_ops"]
            from_address = op.get("from_address")
            if from_address:
                # File was renamed AND edited simultaneously.
                typer.echo(_cyan(f"R  {from_address} → {op['address']}"))
            else:
                # Classify the patch: all-inserts = new file, all-deletes =
                # removed file, mixed = modification.  Use the right status
                # prefix so the output reads like `git diff --name-status`.
                all_insert = all(c["op"] == "insert" for c in child_ops)
                all_delete = all(c["op"] == "delete" for c in child_ops)
                if all_insert:
                    typer.echo(_green(f"A  {op['address']}"))
                elif all_delete:
                    typer.echo(_red(f"D  {op['address']}"))
                else:
                    typer.echo(_yellow(f"M  {op['address']}"))
            _print_child_ops(child_ops)
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
        target_snap = plugin.snapshot(root)
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

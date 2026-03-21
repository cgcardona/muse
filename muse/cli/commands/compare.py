"""muse compare — semantic comparison between any two historical snapshots.

``muse diff`` compares the working tree to HEAD.  ``muse compare`` compares
any two historical commits — a full semantic diff between a release tag and
the current HEAD, between the start and end of a sprint, between two branches.

Usage::

    muse compare HEAD~10 HEAD
    muse compare v1.0 v2.0
    muse compare a3f2c9 cb4afa
    muse compare main feature/auth --kind class

Output::

    Semantic comparison
      From: a3f2c9e1  "Add billing module"
      To:   cb4afaed  "Merge: release v1.0"

    src/billing.py
      added     compute_invoice_total        (renamed from calculate_total)
      modified  Invoice.to_dict              (signature changed)
      moved     validate_amount              → src/validation.py

    src/validation.py  (new file)
      added     validate_amount              (moved from src/billing.py)

    api/server.go  (new file)
      added     HandleRequest
      added     process

    7 symbol changes across 3 files
"""

from __future__ import annotations

import json
import logging
import pathlib
from typing import TypedDict

import typer

from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.store import get_commit_snapshot_manifest, read_current_branch, resolve_commit_ref
from muse.domain import DomainOp
from muse.plugins.code._query import language_of, symbols_for_snapshot
from muse.plugins.code.symbol_diff import build_diff_ops

logger = logging.getLogger(__name__)

app = typer.Typer()


class _OpSummary(TypedDict):
    op: str
    address: str
    detail: str


def _read_repo_id(root: pathlib.Path) -> str:
    return str(json.loads((root / ".muse" / "repo.json").read_text())["repo_id"])


def _read_branch(root: pathlib.Path) -> str:
    return read_current_branch(root)


def _format_child_op(op: DomainOp) -> str:
    """Return a compact one-line description of a symbol-level op."""
    addr = op["address"]
    name = addr.split("::")[-1] if "::" in addr else addr
    if op["op"] == "insert":
        summary = op.get("content_summary", "")
        moved = (
            f"  (moved from {summary.split('moved from')[-1].strip()})"
            if "moved from" in summary else ""
        )
        return f"  added     {name}{moved}"
    if op["op"] == "delete":
        summary = op.get("content_summary", "")
        moved = (
            f"  (moved to {summary.split('moved to')[-1].strip()})"
            if "moved to" in summary else ""
        )
        return f"  removed   {name}{moved}"
    if op["op"] == "replace":
        ns: str = op.get("new_summary", "")
        detail = f"  ({ns})" if ns else ""
        return f"  modified  {name}{detail}"
    return f"  changed   {name}"


def _flatten_ops(ops: list[DomainOp]) -> list[_OpSummary]:
    """Flatten all ops to a serialisable summary list."""
    result: list[_OpSummary] = []
    for op in ops:
        if op["op"] == "patch":
            for child in op["child_ops"]:
                if child["op"] == "insert":
                    detail: str = child["content_summary"]
                elif child["op"] == "delete":
                    detail = child["content_summary"]
                elif child["op"] == "replace":
                    detail = child["new_summary"]
                else:
                    detail = ""
                result.append(_OpSummary(
                    op=child["op"],
                    address=child["address"],
                    detail=detail,
                ))
        elif op["op"] == "insert":
            result.append(_OpSummary(op="insert", address=op["address"], detail=op["content_summary"]))
        elif op["op"] == "delete":
            result.append(_OpSummary(op="delete", address=op["address"], detail=op["content_summary"]))
        elif op["op"] == "replace":
            result.append(_OpSummary(op="replace", address=op["address"], detail=op["new_summary"]))
        else:
            result.append(_OpSummary(op=op["op"], address=op["address"], detail=""))
    return result


@app.callback(invoke_without_command=True)
def compare(
    ctx: typer.Context,
    ref_a: str = typer.Argument(..., metavar="REF-A", help="Base commit (older)."),
    ref_b: str = typer.Argument(..., metavar="REF-B", help="Target commit (newer)."),
    kind_filter: str | None = typer.Option(
        None, "--kind", "-k", metavar="KIND",
        help="Restrict to symbols of this kind (function, class, method, …).",
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit results as JSON."),
) -> None:
    """Deep semantic comparison between any two historical snapshots.

    ``muse compare`` is the two-point historical version of ``muse diff``.
    It reads both commits from the object store, parses AST symbol trees for
    all semantic files, and produces a full symbol-level delta: which functions
    were added, removed, renamed, moved, and modified between these two points.

    Use it to understand the semantic scope of a release, a sprint, or a
    branch divergence — at the function level, not the line level.
    """
    root = require_repo()
    repo_id = _read_repo_id(root)
    branch = _read_branch(root)

    commit_a = resolve_commit_ref(root, repo_id, branch, ref_a)
    if commit_a is None:
        typer.echo(f"❌ Commit '{ref_a}' not found.", err=True)
        raise typer.Exit(code=ExitCode.USER_ERROR)

    commit_b = resolve_commit_ref(root, repo_id, branch, ref_b)
    if commit_b is None:
        typer.echo(f"❌ Commit '{ref_b}' not found.", err=True)
        raise typer.Exit(code=ExitCode.USER_ERROR)

    # get_commit_snapshot_manifest returns a flat dict[str, str] of path → sha256.
    manifest_a: dict[str, str] = get_commit_snapshot_manifest(root, commit_a.commit_id) or {}
    manifest_b: dict[str, str] = get_commit_snapshot_manifest(root, commit_b.commit_id) or {}

    trees_a = symbols_for_snapshot(root, manifest_a, kind_filter=kind_filter)
    trees_b = symbols_for_snapshot(root, manifest_b, kind_filter=kind_filter)

    ops = build_diff_ops(manifest_a, manifest_b, trees_a, trees_b)

    if as_json:
        typer.echo(json.dumps(
            {
                "from": {"commit_id": commit_a.commit_id, "message": commit_a.message},
                "to": {"commit_id": commit_b.commit_id, "message": commit_b.message},
                "ops": [dict(s) for s in _flatten_ops(ops)],
            },
            indent=2,
        ))
        return

    typer.echo("\nSemantic comparison")
    typer.echo(f'  From: {commit_a.commit_id[:8]}  "{commit_a.message}"')
    typer.echo(f'  To:   {commit_b.commit_id[:8]}  "{commit_b.message}"')

    if not ops:
        typer.echo("\n  (no semantic changes between these two commits)")
        return

    total_symbols = 0
    files_changed: set[str] = set()

    for op in ops:
        if op["op"] == "patch":
            fp = op["address"]
            child_ops = op["child_ops"]
            if not child_ops:
                continue
            files_changed.add(fp)
            is_new = fp not in manifest_a
            is_gone = fp not in manifest_b
            suffix = "  (new file)" if is_new else ("  (removed)" if is_gone else "")
            typer.echo(f"\n{fp}{suffix}")
            for child in child_ops:
                typer.echo(_format_child_op(child))
                total_symbols += 1
        else:
            fp = op["address"]
            files_changed.add(fp)
            if op["op"] == "insert":
                typer.echo(f"\n{fp}  (new file)")
                typer.echo(f"  added     {fp}  (file)")
            elif op["op"] == "delete":
                typer.echo(f"\n{fp}  (removed)")
                typer.echo(f"  removed   {fp}  (file)")
            else:
                typer.echo(f"\n{fp}")
                typer.echo(f"  modified  {fp}  (file)")
            total_symbols += 1

    typer.echo(f"\n{total_symbols} symbol change(s) across {len(files_changed)} file(s)")

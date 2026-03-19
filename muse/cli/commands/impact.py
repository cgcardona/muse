"""muse impact — transitive blast-radius analysis.

Answers the question every engineer asks before touching a function:
*"If I change this, what else could break?"*

``muse impact`` builds the reverse call graph for the committed snapshot,
then performs a BFS from the target symbol's bare name through every caller,
then every caller's callers, until the full transitive closure is reached.

The result is a depth-ordered blast-radius map: depth 1 = direct callers,
depth 2 = callers of callers, and so on.  This tells you exactly how far a
change propagates through the codebase.

This is structurally impossible in Git.  Git stores files as blobs — it has
no concept of call relationships between functions.  You would need an
external static-analysis tool and a separate dependency graph.  In Muse,
the symbol graph is a first-class citizen of every committed snapshot.

Usage::

    muse impact "src/billing.py::compute_invoice_total"
    muse impact "src/billing.py::compute_invoice_total" --depth 2
    muse impact "src/auth.py::validate_token" --commit HEAD~5
    muse impact "src/core.py::content_hash" --json

Output::

    Impact analysis: src/billing.py::compute_invoice_total
    ──────────────────────────────────────────────────────────────

    Depth 1 — direct callers (2):
      src/api.py::create_invoice
      src/billing.py::process_order

    Depth 2 — callers of callers (1):
      src/api.py::handle_request

    ──────────────────────────────────────────────────────────────
    Total blast radius: 3 symbols across 2 files
    High impact — consider adding tests before changing this symbol.

Flags:

``--depth, -d N``
    Stop BFS after N levels (default: 0 = unlimited).

``--commit, -c REF``
    Analyse a historical snapshot instead of HEAD.

``--json``
    Emit the full blast-radius map as JSON.
"""

from __future__ import annotations

import json
import logging
import pathlib

import typer

from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.store import get_commit_snapshot_manifest, resolve_commit_ref
from muse.plugins.code._callgraph import build_reverse_graph, transitive_callers
from muse.plugins.code._query import language_of

logger = logging.getLogger(__name__)

app = typer.Typer()


def _read_repo_id(root: pathlib.Path) -> str:
    return str(json.loads((root / ".muse" / "repo.json").read_text())["repo_id"])


def _read_branch(root: pathlib.Path) -> str:
    head_ref = (root / ".muse" / "HEAD").read_text().strip()
    return head_ref.removeprefix("refs/heads/").strip()


@app.callback(invoke_without_command=True)
def impact(
    ctx: typer.Context,
    address: str = typer.Argument(
        ..., metavar="ADDRESS",
        help='Symbol address, e.g. "src/billing.py::compute_invoice_total".',
    ),
    depth: int = typer.Option(
        0, "--depth", "-d", metavar="N",
        help="Maximum BFS depth (0 = unlimited).",
    ),
    ref: str | None = typer.Option(
        None, "--commit", "-c", metavar="REF",
        help="Analyse a historical snapshot instead of HEAD.",
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit results as JSON."),
) -> None:
    """Show the transitive blast-radius of changing a symbol.

    Builds the reverse call graph for the committed snapshot, then BFS-walks
    it from the target symbol outwards.  Depth 1 = direct callers; depth 2 =
    callers of callers; and so on until no new callers are found.

    The blast-radius map reveals exactly how far a change propagates through
    the codebase — information that is impossible to derive from Git alone.

    Python only (call-graph analysis uses stdlib ``ast``).
    """
    root = require_repo()
    repo_id = _read_repo_id(root)
    branch = _read_branch(root)
    lang = language_of(address.split("::")[0]) if "::" in address else ""
    if lang and lang != "Python":
        typer.echo(
            f"⚠️  Impact analysis is currently Python-only.  '{address}' is {lang}.",
            err=True,
        )
        raise typer.Exit(code=ExitCode.USER_ERROR)

    commit = resolve_commit_ref(root, repo_id, branch, ref)
    if commit is None:
        typer.echo(f"❌ Commit '{ref or 'HEAD'}' not found.", err=True)
        raise typer.Exit(code=ExitCode.USER_ERROR)

    manifest = get_commit_snapshot_manifest(root, commit.commit_id) or {}
    reverse = build_reverse_graph(root, manifest)

    target_name = address.split("::")[-1].split(".")[-1] if "::" in address else address
    blast = transitive_callers(target_name, reverse, max_depth=depth)

    if as_json:
        typer.echo(json.dumps(
            {
                "address": address,
                "target_name": target_name,
                "commit": commit.commit_id[:8],
                "depth_limit": depth,
                "blast_radius": {
                    str(d): addrs for d, addrs in sorted(blast.items())
                },
                "total": sum(len(v) for v in blast.values()),
            },
            indent=2,
        ))
        return

    typer.echo(f"\nImpact analysis: {address}")
    typer.echo("─" * 62)

    if not blast:
        typer.echo(
            f"\n  (no callers detected — '{target_name}' may be an entry point or dead code)"
        )
        typer.echo(
            "\n  Note: analysis covers Python only; external callers are not detected."
        )
        return

    total = sum(len(v) for v in blast.values())
    all_files: set[str] = set()

    for d in sorted(blast.keys()):
        callers = blast[d]
        label = "direct callers" if d == 1 else "callers of callers" if d == 2 else f"depth-{d} callers"
        typer.echo(f"\nDepth {d} — {label} ({len(callers)}):")
        for addr in sorted(callers):
            typer.echo(f"  {addr}")
            if "::" in addr:
                all_files.add(addr.split("::")[0])

    typer.echo("\n" + "─" * 62)
    file_label = "file" if len(all_files) == 1 else "files"
    typer.echo(f"Total blast radius: {total} symbol(s) across {len(all_files)} {file_label}")
    if total >= 10:
        typer.echo("🔴 High impact — add tests before changing this symbol.")
    elif total >= 3:
        typer.echo("🟡 Medium impact — review callers before changing this symbol.")
    else:
        typer.echo("🟢 Low impact — change is well-contained.")
    typer.echo(
        "\nNote: analysis covers Python call-sites only."
        " Dynamic dispatch (getattr, decorators) is not detected."
    )

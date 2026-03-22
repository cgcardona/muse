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

import argparse
import json
import logging
import pathlib
import sys

from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.store import get_commit_snapshot_manifest, read_current_branch, resolve_commit_ref
from muse.plugins.code._callgraph import build_reverse_graph, transitive_callers
from muse.plugins.code._query import language_of

logger = logging.getLogger(__name__)


def _read_repo_id(root: pathlib.Path) -> str:
    return str(json.loads((root / ".muse" / "repo.json").read_text())["repo_id"])


def _read_branch(root: pathlib.Path) -> str:
    return read_current_branch(root)


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the impact subcommand."""
    parser = subparsers.add_parser(
        "impact",
        help="Show the transitive blast-radius of changing a symbol.",
        description=__doc__,
    )
    parser.add_argument(
        "address", metavar="ADDRESS",
        help='Symbol address, e.g. "src/billing.py::compute_invoice_total".',
    )
    parser.add_argument(
        "--depth", "-d", type=int, default=0, metavar="N",
        help="Maximum BFS depth (0 = unlimited).",
    )
    parser.add_argument(
        "--commit", "-c", default=None, metavar="REF", dest="ref",
        help="Analyse a historical snapshot instead of HEAD.",
    )
    parser.add_argument(
        "--json", action="store_true", dest="as_json",
        help="Emit results as JSON.",
    )
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> None:
    """Show the transitive blast-radius of changing a symbol.

    Builds the reverse call graph for the committed snapshot, then BFS-walks
    it from the target symbol outwards.  Depth 1 = direct callers; depth 2 =
    callers of callers; and so on until no new callers are found.

    The blast-radius map reveals exactly how far a change propagates through
    the codebase — information that is impossible to derive from Git alone.

    Python only (call-graph analysis uses stdlib ``ast``).
    """
    address: str = args.address
    depth: int = args.depth
    ref: str | None = args.ref
    as_json: bool = args.as_json

    root = require_repo()
    repo_id = _read_repo_id(root)
    branch = _read_branch(root)
    lang = language_of(address.split("::")[0]) if "::" in address else ""
    if lang and lang != "Python":
        print(
            f"⚠️  Impact analysis is currently Python-only.  '{address}' is {lang}.",
            file=sys.stderr,
        )
        raise SystemExit(ExitCode.USER_ERROR)

    commit = resolve_commit_ref(root, repo_id, branch, ref)
    if commit is None:
        print(f"❌ Commit '{ref or 'HEAD'}' not found.", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)

    manifest = get_commit_snapshot_manifest(root, commit.commit_id) or {}
    reverse = build_reverse_graph(root, manifest)

    target_name = address.split("::")[-1].split(".")[-1] if "::" in address else address
    blast = transitive_callers(target_name, reverse, max_depth=depth)

    if as_json:
        print(json.dumps(
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

    print(f"\nImpact analysis: {address}")
    print("─" * 62)

    if not blast:
        print(
            f"\n  (no callers detected — '{target_name}' may be an entry point or dead code)"
        )
        print(
            "\n  Note: analysis covers Python only; external callers are not detected."
        )
        return

    total = sum(len(v) for v in blast.values())
    all_files: set[str] = set()

    for d in sorted(blast.keys()):
        callers = blast[d]
        label = "direct callers" if d == 1 else "callers of callers" if d == 2 else f"depth-{d} callers"
        print(f"\nDepth {d} — {label} ({len(callers)}):")
        for addr in sorted(callers):
            print(f"  {addr}")
            if "::" in addr:
                all_files.add(addr.split("::")[0])

    print("\n" + "─" * 62)
    file_label = "file" if len(all_files) == 1 else "files"
    print(f"Total blast radius: {total} symbol(s) across {len(all_files)} {file_label}")
    if total >= 10:
        print("🔴 High impact — add tests before changing this symbol.")
    elif total >= 3:
        print("🟡 Medium impact — review callers before changing this symbol.")
    else:
        print("🟢 Low impact — change is well-contained.")
    print(
        "\nNote: analysis covers Python call-sites only."
        " Dynamic dispatch (getattr, decorators) is not detected."
    )

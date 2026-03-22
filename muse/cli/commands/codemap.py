"""muse codemap — repository semantic topology.

Generates a structural map of the codebase from committed snapshot data:

* **Modules ranked by size** — symbol count and lines of code per file
* **Import in-degree** — how many other files import each module
* **Import cycles** — circular dependency chains detected via DFS
* **High-centrality symbols** — functions called from the most callers
* **Boundary files** — high fan-out (imports many) but low fan-in (few import it)

This is a semantic topology view, not a file-system listing.  It reveals the
actual shape of a codebase — where the load-bearing columns are, where the
cycles hide, and where parallel agents can safely work without collision.

Usage::

    muse codemap
    muse codemap --commit HEAD~10
    muse codemap --language Python
    muse codemap --top 20
    muse codemap --json

Output::

    Semantic codemap — commit a1b2c3d4
    ──────────────────────────────────────────────────────────────

    Top modules by size:
      src/billing.py          42 symbols  (12 importers)  ⬛ HIGH CENTRALITY
      src/models.py           31 symbols  (8 importers)
      src/auth.py             18 symbols  (5 importers)

    Import cycles (2):
      src/billing.py → src/utils.py → src/billing.py
      src/api.py → src/auth.py → src/api.py

    High-centrality symbols (most callers):
      src/billing.py::compute_total        14 callers
      src/auth.py::validate_token           9 callers

    Boundary files (high fan-out, low fan-in):
      src/cli.py        imports 8 modules  ← imported by 0

Flags:

``--commit, -c REF``
    Analyse a historical snapshot instead of HEAD.

``--language LANG``
    Restrict analysis to files of this language.

``--top N``
    Show top N entries in each section (default: 15).

``--json``
    Emit the full codemap as JSON.
"""

from __future__ import annotations

import argparse
import json
import logging
import pathlib
import sys

from muse._version import __version__
from muse.core.errors import ExitCode
from muse.core.object_store import read_object
from muse.core.repo import require_repo
from muse.core.store import get_commit_snapshot_manifest, read_current_branch, resolve_commit_ref
from muse.plugins.code._callgraph import build_reverse_graph
from muse.plugins.code._query import language_of, symbols_for_snapshot
from muse.plugins.code.ast_parser import parse_symbols

logger = logging.getLogger(__name__)

_PY_SUFFIXES: frozenset[str] = frozenset({".py", ".pyi"})


def _read_repo_id(root: pathlib.Path) -> str:
    return str(json.loads((root / ".muse" / "repo.json").read_text())["repo_id"])


def _read_branch(root: pathlib.Path) -> str:
    return read_current_branch(root)


def _file_stem(file_path: str) -> str:
    return pathlib.PurePosixPath(file_path).stem


def _build_import_graph(
    root: pathlib.Path,
    manifest: dict[str, str],
    language_filter: str | None,
) -> tuple[dict[str, list[str]], dict[str, int]]:
    """Return ``(imports_out, import_in_degree)`` for all files in manifest.

    ``imports_out[file_path]`` is the list of file_paths that *file_path* imports
    (best-effort heuristic matching by module stem).
    ``import_in_degree[file_path]`` counts how many files import *file_path*.
    """
    # Step 1: build stem → file_path map
    stem_to_file: dict[str, str] = {}
    for fp in manifest:
        if language_filter and language_of(fp) != language_filter:
            continue
        stem_to_file[_file_stem(fp)] = fp

    # Step 2: scan import symbols in each file
    imports_out: dict[str, list[str]] = {fp: [] for fp in manifest}
    in_degree: dict[str, int] = {fp: 0 for fp in manifest}

    for file_path, obj_id in sorted(manifest.items()):
        if language_filter and language_of(file_path) != language_filter:
            continue
        raw = read_object(root, obj_id)
        if raw is None:
            continue
        tree = parse_symbols(raw, file_path)
        for rec in tree.values():
            if rec["kind"] != "import":
                continue
            # Match the imported module name against known stems.
            imported = rec["qualified_name"].split(".")[-1]
            target = stem_to_file.get(imported)
            if target and target != file_path:
                imports_out[file_path].append(target)
                in_degree[target] = in_degree.get(target, 0) + 1

    return imports_out, in_degree


def _find_cycles(imports_out: dict[str, list[str]]) -> list[list[str]]:
    """Detect import cycles via iterative DFS.  Returns cycle paths.

    Uses an explicit stack instead of recursion so that deeply nested import
    graphs (thousands of files in a chain) cannot exhaust Python's call stack.
    O(V+E) — every node is visited at most once.
    """
    cycles: list[list[str]] = []
    visited: set[str] = set()

    for start in imports_out:
        if start in visited:
            continue
        # Each stack frame: (node, path-so-far, in-stack set for this path)
        stack: list[tuple[str, list[str], set[str]]] = [(start, [], set())]
        while stack:
            node, path, in_stack = stack.pop()
            if node in in_stack:
                idx = path.index(node)
                cycles.append(path[idx:] + [node])
                continue
            if node in visited:
                continue
            visited.add(node)
            new_in_stack = in_stack | {node}
            for neighbour in imports_out.get(node, []):
                stack.append((neighbour, path + [node], new_in_stack))

    return cycles


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the codemap subcommand."""
    parser = subparsers.add_parser(
        "codemap",
        help="Generate a semantic topology map of the repository.",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--commit", "-c", default=None, metavar="REF", dest="ref",
        help="Analyse this commit instead of HEAD.",
    )
    parser.add_argument(
        "--language", "-l", default=None, metavar="LANG", dest="language",
        help="Restrict analysis to this language.",
    )
    parser.add_argument(
        "--top", "-n", type=int, default=15, metavar="N", dest="top",
        help="Number of entries to show in each ranked section.",
    )
    parser.add_argument(
        "--json", action="store_true", dest="as_json",
        help="Emit results as JSON.",
    )
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> None:
    """Generate a semantic topology map of the repository.

    Ranks modules by size, detects import cycles, finds high-centrality
    symbols, and identifies boundary files (high fan-out, low fan-in).

    This reveals the structural shape of the codebase — load-bearing modules,
    hidden cycles, and safe parallel-work zones — without reading a single
    working-tree file.
    """
    ref: str | None = args.ref
    language: str | None = args.language
    top: int = args.top
    as_json: bool = args.as_json

    root = require_repo()
    repo_id = _read_repo_id(root)
    branch = _read_branch(root)

    commit = resolve_commit_ref(root, repo_id, branch, ref)
    if commit is None:
        print(f"❌ Commit '{ref or 'HEAD'}' not found.", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)

    manifest = get_commit_snapshot_manifest(root, commit.commit_id) or {}

    # Symbol counts per file.
    sym_map = symbols_for_snapshot(root, manifest, language_filter=language)
    file_sym_counts: dict[str, int] = {
        fp: len(tree) for fp, tree in sym_map.items()
    }

    # Import graph.
    imports_out, in_degree = _build_import_graph(root, manifest, language)

    # Cycles.
    cycles = _find_cycles(imports_out)

    # High-centrality symbols (Python only — needs call graph).
    reverse = build_reverse_graph(root, manifest)
    centrality: list[tuple[str, int]] = sorted(
        [(name, len(callers)) for name, callers in reverse.items()],
        key=lambda t: t[1],
        reverse=True,
    )[:top]

    # Boundary files: imports many but is imported by few.
    fan_out = {fp: len(targets) for fp, targets in imports_out.items() if targets}
    boundaries: list[tuple[str, int, int]] = sorted(
        [
            (fp, fan_out.get(fp, 0), in_degree.get(fp, 0))
            for fp in manifest
            if fan_out.get(fp, 0) >= 3 and in_degree.get(fp, 0) == 0
        ],
        key=lambda t: t[1],
        reverse=True,
    )[:top]

    # Ranked modules.
    ranked = sorted(
        file_sym_counts.items(),
        key=lambda t: t[1],
        reverse=True,
    )[:top]

    if as_json:
        print(json.dumps(
            {
                "schema_version": __version__,
                "commit": commit.commit_id[:8],
                "language_filter": language,
                "modules": [
                    {
                        "file": fp,
                        "symbol_count": cnt,
                        "importers": in_degree.get(fp, 0),
                        "imports": len(imports_out.get(fp, [])),
                    }
                    for fp, cnt in ranked
                ],
                "import_cycles": [c for c in cycles],
                "high_centrality": [
                    {"name": name, "callers": cnt}
                    for name, cnt in centrality
                ],
                "boundary_files": [
                    {"file": fp, "fan_out": fo, "fan_in": fi}
                    for fp, fo, fi in boundaries
                ],
            },
            indent=2,
        ))
        return

    print(f"\nSemantic codemap — commit {commit.commit_id[:8]}")
    if language:
        print(f"  (language: {language})")
    print("─" * 62)

    print(f"\nTop modules by size (top {min(top, len(ranked))}):")
    if ranked:
        max_fp = max(len(fp) for fp, _ in ranked)
        for fp, cnt in ranked:
            imp = in_degree.get(fp, 0)
            imp_label = f"({imp} importers)" if imp else "(not imported)"
            print(f"  {fp:<{max_fp}}  {cnt:>3} symbols  {imp_label}")
    else:
        print("  (no semantic files found)")

    print(f"\nImport cycles ({len(cycles)}):")
    if cycles:
        for cycle in cycles[:top]:
            print("  " + " → ".join(cycle))
    else:
        print("  ✅ No import cycles detected")

    print(f"\nHigh-centrality symbols — most callers (Python):")
    if centrality:
        for name, cnt in centrality:
            print(f"  {name:<40}  {cnt} caller(s)")
    else:
        print("  (no Python call graph available)")

    print(f"\nBoundary files — high fan-out, zero fan-in:")
    if boundaries:
        for fp, fo, fi in boundaries:
            print(f"  {fp}  imports {fo}  ← imported by {fi}")
    else:
        print("  (none detected)")

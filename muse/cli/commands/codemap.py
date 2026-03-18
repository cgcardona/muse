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

import json
import logging
import pathlib

import typer

from muse.core.errors import ExitCode
from muse.core.object_store import read_object
from muse.core.repo import require_repo
from muse.core.store import get_commit_snapshot_manifest, resolve_commit_ref
from muse.plugins.code._callgraph import build_reverse_graph
from muse.plugins.code._query import language_of, symbols_for_snapshot
from muse.plugins.code.ast_parser import parse_symbols

logger = logging.getLogger(__name__)

app = typer.Typer()

_PY_SUFFIXES: frozenset[str] = frozenset({".py", ".pyi"})


def _read_repo_id(root: pathlib.Path) -> str:
    return str(json.loads((root / ".muse" / "repo.json").read_text())["repo_id"])


def _read_branch(root: pathlib.Path) -> str:
    head_ref = (root / ".muse" / "HEAD").read_text().strip()
    return head_ref.removeprefix("refs/heads/").strip()


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
    """Detect import cycles via iterative DFS.  Returns cycle paths."""
    cycles: list[list[str]] = []
    visited: set[str] = set()
    in_stack: set[str] = set()

    def dfs(node: str, path: list[str]) -> None:
        if node in in_stack:
            # Found a cycle — extract the cycle portion.
            start = path.index(node)
            cycles.append(path[start:] + [node])
            return
        if node in visited:
            return
        visited.add(node)
        in_stack.add(node)
        for neighbour in imports_out.get(node, []):
            dfs(neighbour, path + [node])
        in_stack.discard(node)

    for node in imports_out:
        if node not in visited:
            dfs(node, [])

    return cycles


@app.callback(invoke_without_command=True)
def codemap(
    ctx: typer.Context,
    ref: str | None = typer.Option(
        None, "--commit", "-c", metavar="REF",
        help="Analyse this commit instead of HEAD.",
    ),
    language: str | None = typer.Option(
        None, "--language", "-l", metavar="LANG",
        help="Restrict analysis to this language.",
    ),
    top: int = typer.Option(
        15, "--top", "-n", metavar="N",
        help="Number of entries to show in each ranked section.",
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit results as JSON."),
) -> None:
    """Generate a semantic topology map of the repository.

    Ranks modules by size, detects import cycles, finds high-centrality
    symbols, and identifies boundary files (high fan-out, low fan-in).

    This reveals the structural shape of the codebase — load-bearing modules,
    hidden cycles, and safe parallel-work zones — without reading a single
    working-tree file.
    """
    root = require_repo()
    repo_id = _read_repo_id(root)
    branch = _read_branch(root)

    commit = resolve_commit_ref(root, repo_id, branch, ref)
    if commit is None:
        typer.echo(f"❌ Commit '{ref or 'HEAD'}' not found.", err=True)
        raise typer.Exit(code=ExitCode.USER_ERROR)

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
        typer.echo(json.dumps(
            {
                "schema_version": 1,
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

    typer.echo(f"\nSemantic codemap — commit {commit.commit_id[:8]}")
    if language:
        typer.echo(f"  (language: {language})")
    typer.echo("─" * 62)

    typer.echo(f"\nTop modules by size (top {min(top, len(ranked))}):")
    if ranked:
        max_fp = max(len(fp) for fp, _ in ranked)
        for fp, cnt in ranked:
            imp = in_degree.get(fp, 0)
            imp_label = f"({imp} importers)" if imp else "(not imported)"
            typer.echo(f"  {fp:<{max_fp}}  {cnt:>3} symbols  {imp_label}")
    else:
        typer.echo("  (no semantic files found)")

    typer.echo(f"\nImport cycles ({len(cycles)}):")
    if cycles:
        for cycle in cycles[:top]:
            typer.echo("  " + " → ".join(cycle))
    else:
        typer.echo("  ✅ No import cycles detected")

    typer.echo(f"\nHigh-centrality symbols — most callers (Python):")
    if centrality:
        for name, cnt in centrality:
            typer.echo(f"  {name:<40}  {cnt} caller(s)")
    else:
        typer.echo("  (no Python call graph available)")

    typer.echo(f"\nBoundary files — high fan-out, zero fan-in:")
    if boundaries:
        for fp, fo, fi in boundaries:
            typer.echo(f"  {fp}  imports {fo}  ← imported by {fi}")
    else:
        typer.echo("  (none detected)")

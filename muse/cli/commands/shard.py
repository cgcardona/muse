"""muse shard — partition a codebase into low-coupling work zones.

Divides files into N groups (shards) such that files within each shard are
tightly coupled to each other (many imports between them) and loosely coupled
to files in other shards.  Each shard is a safe parallel-work zone for agents.

Agents assigned to different shards are unlikely to create merge conflicts
because they work on different parts of the dependency graph.

Algorithm
---------
1. Build the import graph from the committed snapshot.
2. Compute connected components of the import graph.
3. Distribute components greedily into N shards, balancing by symbol count.
4. Report each shard with its files, symbol count, and coupling score.

The coupling score is the count of cross-shard import edges (lower = better).

Usage::

    muse shard --agents 4
    muse shard --agents 8 --commit HEAD~10
    muse shard --agents 4 --language Python
    muse shard --agents 4 --json

Output::

    Shard plan — 4 agents, commit a1b2c3d4
    ──────────────────────────────────────────────────────────────

    Shard 1  (12 symbols, 3 files, coupling 1):
      src/billing.py
      src/billing_utils.py
      src/tax.py

    Shard 2  (8 symbols, 2 files, coupling 0):
      src/auth.py
      src/session.py
    ...

    Cross-shard edges: 1

Flags:

``--agents N``
    Number of parallel work zones to create (default: 4).

``--commit, -c REF``
    Use a historical snapshot instead of HEAD.

``--language LANG``
    Restrict to files of this language.

``--json``
    Emit the shard plan as JSON.
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
from muse.plugins.code._query import language_of, symbols_for_snapshot
from muse.plugins.code.ast_parser import parse_symbols

logger = logging.getLogger(__name__)

app = typer.Typer()


def _read_repo_id(root: pathlib.Path) -> str:
    return str(json.loads((root / ".muse" / "repo.json").read_text())["repo_id"])


def _read_branch(root: pathlib.Path) -> str:
    head_ref = (root / ".muse" / "HEAD").read_text().strip()
    return head_ref.removeprefix("refs/heads/").strip()


def _file_stem(fp: str) -> str:
    return pathlib.PurePosixPath(fp).stem


def _build_import_edges(
    root: pathlib.Path,
    manifest: dict[str, str],
    language_filter: str | None,
) -> list[tuple[str, str]]:
    """Return list of (importer, importee) file path pairs."""
    stem_to_file: dict[str, str] = {}
    for fp in manifest:
        if language_filter and language_of(fp) != language_filter:
            continue
        stem_to_file[_file_stem(fp)] = fp

    edges: list[tuple[str, str]] = []
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
            imported = rec["qualified_name"].split(".")[-1].replace("import::", "")
            target = stem_to_file.get(imported)
            if target and target != file_path:
                edges.append((file_path, target))
    return edges


def _connected_components(
    files: list[str],
    edges: list[tuple[str, str]],
) -> list[frozenset[str]]:
    """Return weakly-connected components of the import graph."""
    adj: dict[str, set[str]] = {f: set() for f in files}
    for a, b in edges:
        adj.setdefault(a, set()).add(b)
        adj.setdefault(b, set()).add(a)

    visited: set[str] = set()
    components: list[frozenset[str]] = []

    for start in files:
        if start in visited:
            continue
        component: set[str] = set()
        stack = [start]
        while stack:
            node = stack.pop()
            if node in visited:
                continue
            visited.add(node)
            component.add(node)
            for neighbour in adj.get(node, set()):
                if neighbour not in visited:
                    stack.append(neighbour)
        components.append(frozenset(component))

    return components


def _greedy_partition(
    components: list[frozenset[str]],
    sym_counts: dict[str, int],
    n_shards: int,
) -> list[frozenset[str]]:
    """Greedily distribute components into n_shards shards, balancing symbol count."""
    shards: list[set[str]] = [set() for _ in range(n_shards)]
    shard_sizes: list[int] = [0] * n_shards

    # Sort components largest-first for better balance.
    sorted_comps = sorted(
        components,
        key=lambda c: sum(sym_counts.get(f, 0) for f in c),
        reverse=True,
    )
    for comp in sorted_comps:
        # Assign to the smallest shard.
        smallest = min(range(n_shards), key=lambda i: shard_sizes[i])
        shards[smallest].update(comp)
        shard_sizes[smallest] += sum(sym_counts.get(f, 0) for f in comp)

    return [frozenset(s) for s in shards]


@app.callback(invoke_without_command=True)
def shard(
    ctx: typer.Context,
    agents: int = typer.Option(4, "--agents", "-n", metavar="N", help="Number of work zones."),
    ref: str | None = typer.Option(None, "--commit", "-c", metavar="REF", help="Use this commit."),
    language: str | None = typer.Option(None, "--language", "-l", metavar="LANG"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Partition the codebase into N low-coupling work zones for parallel agents.

    Uses the import graph connectivity to group files that are tightly coupled
    together and loosely coupled to other shards.  Agents assigned to different
    shards minimize merge conflicts.

    Shards are balanced by symbol count.  The coupling score is the number of
    cross-shard import edges.
    """
    root = require_repo()
    repo_id = _read_repo_id(root)
    branch = _read_branch(root)

    if agents < 1:
        typer.echo("❌ --agents must be >= 1.", err=True)
        raise typer.Exit(code=ExitCode.USER_ERROR)

    commit = resolve_commit_ref(root, repo_id, branch, ref)
    if commit is None:
        typer.echo(f"❌ Commit '{ref or 'HEAD'}' not found.", err=True)
        raise typer.Exit(code=ExitCode.USER_ERROR)

    manifest = get_commit_snapshot_manifest(root, commit.commit_id) or {}
    sym_map = symbols_for_snapshot(root, manifest, language_filter=language)
    sym_counts: dict[str, int] = {fp: len(tree) for fp, tree in sym_map.items()}

    files = sorted(sym_counts.keys())
    if not files:
        typer.echo("  (no semantic files found in snapshot)")
        return

    edges = _build_import_edges(root, manifest, language)
    components = _connected_components(files, edges)
    n = min(agents, len(components))  # Can't have more shards than components.
    shard_sets = _greedy_partition(components, sym_counts, n)

    # Compute cross-shard edges.
    file_to_shard: dict[str, int] = {}
    for i, s in enumerate(shard_sets):
        for f in s:
            file_to_shard[f] = i
    cross_edges = sum(
        1 for a, b in edges
        if file_to_shard.get(a, -1) != file_to_shard.get(b, -2)
    )

    if as_json:
        typer.echo(json.dumps(
            {
                "schema_version": 1,
                "commit": commit.commit_id[:8],
                "agents": agents,
                "shards_created": n,
                "cross_shard_edges": cross_edges,
                "shards": [
                    {
                        "shard": i + 1,
                        "files": sorted(s),
                        "symbol_count": sum(sym_counts.get(f, 0) for f in s),
                        "coupling_score": sum(
                            1 for a, b in edges
                            if (a in s) != (b in s)
                        ),
                    }
                    for i, s in enumerate(shard_sets) if s
                ],
            },
            indent=2,
        ))
        return

    typer.echo(f"\nShard plan — {n} agent(s), commit {commit.commit_id[:8]}")
    if language:
        typer.echo(f"  (language: {language})")
    typer.echo("─" * 62)

    for i, s in enumerate(shard_sets):
        if not s:
            continue
        sym_total = sum(sym_counts.get(f, 0) for f in s)
        coupling = sum(1 for a, b in edges if (a in s) != (b in s))
        typer.echo(f"\n  Shard {i + 1}  ({sym_total} symbols, {len(s)} files, coupling {coupling}):")
        for fp in sorted(s):
            typer.echo(f"    {fp}")

    typer.echo(f"\n  Cross-shard edges: {cross_edges}")
    if cross_edges == 0:
        typer.echo("  ✅ Perfect isolation — no cross-shard dependencies")

"""muse stable — symbol stability leaderboard.

The inverse of ``muse hotspots``.  Finds the symbols that have been
unchanged the longest — your bedrock, the code you can safely build on.

A function that hasn't needed modification across 50 commits is either
perfectly written or perfectly scoped.  Either way, it's stable.  Build
your architecture around stable symbols.

Usage::

    muse stable
    muse stable --top 20
    muse stable --kind function --language Python

Output::

    Symbol stability — top 10 most stable symbols
    Commits analysed: 47

      1   src/core.py::content_hash       unchanged for 47 commits  (since first commit)
      2   src/core.py::sha256_bytes       unchanged for 43 commits
      3   src/utils.py::retry             unchanged for 38 commits

    These are your bedrock. High stability = safe to build on.
"""
from __future__ import annotations

import json
import logging
import pathlib

import typer

from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.store import resolve_commit_ref
from muse.plugins.code._query import (
    flat_symbol_ops,
    language_of,
    symbols_for_snapshot,
    walk_commits,
)
from muse.core.store import get_commit_snapshot_manifest

logger = logging.getLogger(__name__)

app = typer.Typer()


def _read_repo_id(root: pathlib.Path) -> str:
    return str(json.loads((root / ".muse" / "repo.json").read_text())["repo_id"])


def _read_branch(root: pathlib.Path) -> str:
    head_ref = (root / ".muse" / "HEAD").read_text().strip()
    return head_ref.removeprefix("refs/heads/").strip()


@app.callback(invoke_without_command=True)
def stable(
    ctx: typer.Context,
    top: int = typer.Option(20, "--top", "-n", metavar="N", help="Number of symbols to show (default: 20)."),
    kind_filter: str | None = typer.Option(
        None, "--kind", "-k", metavar="KIND",
        help="Restrict to symbols of this kind (function, class, method, …).",
    ),
    language_filter: str | None = typer.Option(
        None, "--language", "-l", metavar="LANG",
        help="Restrict to symbols from files of this language.",
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit results as JSON."),
) -> None:
    """Show the symbols that have been unchanged the longest.

    ``muse stable`` is the complement of ``muse hotspots``.  It identifies
    the bedrock of your codebase — the functions, classes, and methods that
    have been stable across the most commits.

    These are the symbols safest to build on: they haven't changed because
    they don't need to.  They reveal your stable API surface.
    """
    root = require_repo()
    repo_id = _read_repo_id(root)
    branch = _read_branch(root)

    head_commit = resolve_commit_ref(root, repo_id, branch, None)
    if head_commit is None:
        typer.echo("❌ No commits found.", err=True)
        raise typer.Exit(code=ExitCode.USER_ERROR)

    # 1. Collect all symbols that exist in HEAD snapshot.
    manifest = get_commit_snapshot_manifest(root, head_commit.commit_id) or {}
    symbol_map = symbols_for_snapshot(
        root, manifest, kind_filter=kind_filter, language_filter=language_filter
    )
    all_current_addrs: set[str] = set()
    for tree in symbol_map.values():
        all_current_addrs.update(tree.keys())

    # 2. Walk commits newest-first; record the last commit index at which each
    #    symbol was touched.  Index 0 = most recent commit.
    commits = walk_commits(root, head_commit.commit_id)
    last_touched: dict[str, int] = {}
    for idx, commit in enumerate(commits):
        if commit.structured_delta is None:
            continue
        for op in flat_symbol_ops(commit.structured_delta["ops"]):
            addr = op["address"]
            if addr in all_current_addrs and addr not in last_touched:
                last_touched[addr] = idx

    total_commits = len(commits)

    # 3. Symbols never touched = stable for all commits.
    #    Symbols touched at index N = unchanged for N commits.
    stability: list[tuple[str, int, bool]] = []
    for addr in sorted(all_current_addrs):
        touch_idx = last_touched.get(addr)
        if touch_idx is None:
            stability.append((addr, total_commits, True))
        else:
            stability.append((addr, touch_idx, False))

    # Sort by stability descending.
    stability.sort(key=lambda t: t[1], reverse=True)
    ranked = stability[:top]

    if as_json:
        typer.echo(json.dumps(
            {
                "commits_analysed": total_commits,
                "stable": [
                    {"address": a, "unchanged_for": s, "since_first_commit": sf}
                    for a, s, sf in ranked
                ],
            },
            indent=2,
        ))
        return

    filters = ""
    if kind_filter:
        filters += f"  kind={kind_filter}"
    if language_filter:
        filters += f"  language={language_filter}"
    typer.echo(f"\nSymbol stability — top {len(ranked)} most stable symbols{filters}")
    typer.echo(f"Commits analysed: {total_commits}")
    typer.echo("")

    width = len(str(len(ranked)))
    for rank, (addr, count, since_first) in enumerate(ranked, 1):
        suffix = "  (since first commit)" if since_first else ""
        label = "commit" if count == 1 else "commits"
        typer.echo(f"  {rank:>{width}}   {addr:<60}  unchanged for {count:>4} {label}{suffix}")

    typer.echo("")
    typer.echo("These are your bedrock. High stability = safe to build on.")

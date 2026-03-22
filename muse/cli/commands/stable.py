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

import argparse
import json
import logging
import pathlib
import sys

from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.store import read_current_branch, resolve_commit_ref
from muse.plugins.code._query import (
    flat_symbol_ops,
    language_of,
    symbols_for_snapshot,
    walk_commits,
)
from muse.core.store import get_commit_snapshot_manifest

logger = logging.getLogger(__name__)


def _read_repo_id(root: pathlib.Path) -> str:
    return str(json.loads((root / ".muse" / "repo.json").read_text())["repo_id"])


def _read_branch(root: pathlib.Path) -> str:
    return read_current_branch(root)


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the stable subcommand."""
    parser = subparsers.add_parser(
        "stable",
        help="Show the symbols that have been unchanged the longest.",
        description=__doc__,
    )
    parser.add_argument(
        "--top", "-n",
        type=int,
        default=20,
        metavar="N",
        help="Number of symbols to show (default: 20).",
    )
    parser.add_argument(
        "--kind", "-k",
        dest="kind_filter",
        default=None,
        metavar="KIND",
        help="Restrict to symbols of this kind (function, class, method, …).",
    )
    parser.add_argument(
        "--language", "-l",
        dest="language_filter",
        default=None,
        metavar="LANG",
        help="Restrict to symbols from files of this language.",
    )
    parser.add_argument("--json", dest="as_json", action="store_true", help="Emit results as JSON.")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> None:
    """Show the symbols that have been unchanged the longest.

    ``muse stable`` is the complement of ``muse hotspots``.  It identifies
    the bedrock of your codebase — the functions, classes, and methods that
    have been stable across the most commits.

    These are the symbols safest to build on: they haven't changed because
    they don't need to.  They reveal your stable API surface.
    """
    top: int = args.top
    kind_filter: str | None = args.kind_filter
    language_filter: str | None = args.language_filter
    as_json: bool = args.as_json

    root = require_repo()
    repo_id = _read_repo_id(root)
    branch = _read_branch(root)

    head_commit = resolve_commit_ref(root, repo_id, branch, None)
    if head_commit is None:
        print("❌ No commits found.", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)

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
        print(json.dumps(
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
    print(f"\nSymbol stability — top {len(ranked)} most stable symbols{filters}")
    print(f"Commits analysed: {total_commits}")
    print("")

    width = len(str(len(ranked)))
    for rank, (addr, count, since_first) in enumerate(ranked, 1):
        suffix = "  (since first commit)" if since_first else ""
        label = "commit" if count == 1 else "commits"
        print(f"  {rank:>{width}}   {addr:<60}  unchanged for {count:>4} {label}{suffix}")

    print("")
    print("These are your bedrock. High stability = safe to build on.")

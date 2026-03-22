"""muse plumbing commit-graph — emit the commit DAG as JSON.

Walks the commit graph from a tip commit (defaulting to HEAD) and emits
every reachable commit as a JSON array of nodes, suitable for agent
consumption, visualization, and graph analysis.

New flags extend the original BFS walk:

- ``--count`` — emit only the integer count, not the full node list.
- ``--first-parent`` — follow only ``parent_commit_id``, ignoring merge parents.
  Produces a strict linear history, equivalent to ``git log --first-parent``.
- ``--ancestry-path`` — when used with ``--stop-at``, restricts output to
  commits that are on a *direct ancestry path* between the tip and the
  stop-at commit.  Commits that are reachable from the tip but not
  ancestors of ``--stop-at`` are excluded.

Output (JSON, default)::

    {
      "tip": "<sha256>",
      "count": 42,
      "truncated": false,
      "commits": [
        {
          "commit_id": "<sha256>",
          "parent_commit_id": "<sha256> | null",
          "parent2_commit_id": null,
          "message": "Add verse melody",
          "branch": "main",
          "committed_at": "2026-03-18T12:00:00+00:00",
          "snapshot_id": "<sha256>",
          "author": ""
        },
        ...
      ]
    }

With ``--count``::

    {"tip": "<sha256>", "count": 42}

Plumbing contract
-----------------

- Exit 0: graph emitted.
- Exit 1: tip commit not found; ``--ancestry-path`` used without ``--stop-at``;
  unknown ``--format`` value.
"""

from __future__ import annotations

import argparse
import json
import logging
import pathlib
import sys
from collections import deque
from typing import TypedDict

from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.store import get_head_commit_id, read_commit, read_current_branch

logger = logging.getLogger(__name__)

_DEFAULT_MAX = 10_000
_FORMAT_CHOICES = ("json", "text")


class _CommitNode(TypedDict):
    commit_id: str
    parent_commit_id: str | None
    parent2_commit_id: str | None
    message: str
    branch: str
    committed_at: str
    snapshot_id: str
    author: str


_ANCESTRY_PATH_MAX = 100_000  # hard ceiling for --ancestry-path BFS


def _ancestors_of(root: pathlib.Path, start: str) -> set[str]:
    """Return the set of all commit IDs reachable from *start* (inclusive).

    Used by ``--ancestry-path`` to identify which commits lie on a direct
    path between the tip and the stop-at commit.  Capped at
    ``_ANCESTRY_PATH_MAX`` to prevent unbounded I/O on very large repos.
    """
    visited: set[str] = set()
    queue: deque[str] = deque([start])
    while queue and len(visited) < _ANCESTRY_PATH_MAX:
        cid = queue.popleft()
        if cid in visited:
            continue
        visited.add(cid)
        record = read_commit(root, cid)
        if record is None:
            continue
        if record.parent_commit_id:
            queue.append(record.parent_commit_id)
        if record.parent2_commit_id:
            queue.append(record.parent2_commit_id)
    return visited


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the commit-graph subcommand."""
    parser = subparsers.add_parser(
        "commit-graph",
        help="Emit commit DAG as JSON.",
        description=__doc__,
    )
    parser.add_argument(
        "--tip",
        default=None,
        metavar="COMMIT_ID",
        help="Commit ID to start from (default: HEAD).",
    )
    parser.add_argument(
        "--stop-at",
        default=None,
        dest="stop_at",
        metavar="COMMIT_ID",
        help="Stop BFS at this commit ID (exclusive).",
    )
    parser.add_argument(
        "--max", "-n",
        type=int,
        default=_DEFAULT_MAX,
        dest="max_commits",
        metavar="N",
        help=f"Maximum commits to traverse (default: {_DEFAULT_MAX}).",
    )
    parser.add_argument(
        "--count", "-c",
        action="store_true",
        dest="count_only",
        help="Emit only the integer commit count, not the full node list.",
    )
    parser.add_argument(
        "--first-parent", "-1",
        action="store_true",
        dest="first_parent",
        help="Follow only first-parent links, producing a linear history.",
    )
    parser.add_argument(
        "--ancestry-path", "-a",
        action="store_true",
        dest="ancestry_path",
        help="With --stop-at: restrict output to commits on the direct ancestry path.",
    )
    parser.add_argument(
        "--format", "-f",
        dest="fmt",
        default="json",
        metavar="FORMAT",
        help="Output format: json or text (one ID per line). (default: json)",
    )
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> None:
    """Emit the commit DAG reachable from a tip commit.

    Performs a BFS walk from the tip, following ``parent_commit_id`` and
    (unless ``--first-parent``) ``parent2_commit_id`` pointers.  The
    ``--stop-at`` commit and its ancestors are excluded — useful for
    computing the commits in a branch since it diverged from another.
    """
    fmt: str = args.fmt
    tip: str | None = args.tip
    stop_at: str | None = args.stop_at
    max_commits: int = args.max_commits
    count_only: bool = args.count_only
    first_parent: bool = args.first_parent
    ancestry_path: bool = args.ancestry_path

    if fmt not in _FORMAT_CHOICES:
        print(
            json.dumps(
                {
                    "error": f"Unknown format {fmt!r}. "
                    f"Valid choices: {', '.join(_FORMAT_CHOICES)}"
                }
            )
        )
        raise SystemExit(ExitCode.USER_ERROR)

    if ancestry_path and stop_at is None:
        print(json.dumps({"error": "--ancestry-path requires --stop-at to be set."}))
        raise SystemExit(ExitCode.USER_ERROR)

    root = require_repo()

    if tip is None:
        branch = read_current_branch(root)
        tip = get_head_commit_id(root, branch)
        if tip is None:
            print(json.dumps({"error": "No commits on current branch."}))
            raise SystemExit(ExitCode.USER_ERROR)

    if read_commit(root, tip) is None:
        print(json.dumps({"error": f"Tip commit not found: {tip}"}))
        raise SystemExit(ExitCode.USER_ERROR)

    # For --ancestry-path, pre-compute ancestors of stop_at so we can filter.
    stop_ancestors: set[str] = set()
    if ancestry_path and stop_at is not None:
        stop_ancestors = _ancestors_of(root, stop_at)

    stop_set: set[str] = {stop_at} if stop_at else set()
    visited: set[str] = set()
    queue: deque[str] = deque([tip])
    nodes: list[_CommitNode] = []

    while queue and len(nodes) < max_commits:
        cid = queue.popleft()
        if cid in visited or cid in stop_set:
            continue
        visited.add(cid)
        record = read_commit(root, cid)
        if record is None:
            continue

        # --ancestry-path: skip commits not on the path to stop_at.
        if ancestry_path and cid not in stop_ancestors and cid != tip:
            if record.parent_commit_id:
                queue.append(record.parent_commit_id)
            if not first_parent and record.parent2_commit_id:
                queue.append(record.parent2_commit_id)
            continue

        nodes.append(
            _CommitNode(
                commit_id=record.commit_id,
                parent_commit_id=record.parent_commit_id,
                parent2_commit_id=record.parent2_commit_id,
                message=record.message,
                branch=record.branch,
                committed_at=record.committed_at.isoformat(),
                snapshot_id=record.snapshot_id,
                author=record.author,
            )
        )
        if record.parent_commit_id:
            queue.append(record.parent_commit_id)
        if not first_parent and record.parent2_commit_id:
            queue.append(record.parent2_commit_id)

    if count_only:
        print(json.dumps({"tip": tip, "count": len(nodes)}))
        return

    if fmt == "text":
        for node in nodes:
            print(node["commit_id"])
        return

    print(
        json.dumps(
            {
                "tip": tip,
                "count": len(nodes),
                "truncated": len(nodes) >= max_commits,
                "commits": nodes,
            },
            indent=2,
        )
    )

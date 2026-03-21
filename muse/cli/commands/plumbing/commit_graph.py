"""muse plumbing commit-graph — emit the commit DAG as JSON.

Walks the commit graph from a tip commit (defaulting to HEAD) and emits
every reachable commit as a JSON array of nodes, suitable for agent
consumption, visualization, and graph analysis.

Output::

    {
      "tip": "<sha256>",
      "count": 42,
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

Plumbing contract
-----------------

- Exit 0: graph emitted.
- Exit 1: tip commit not found, or unknown --format value.
"""

from __future__ import annotations

import json
import logging
from collections import deque
from typing import TypedDict

import typer

from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.store import get_head_commit_id, read_commit, read_current_branch

logger = logging.getLogger(__name__)

app = typer.Typer()

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


@app.callback(invoke_without_command=True)
def commit_graph(
    ctx: typer.Context,
    tip: str | None = typer.Option(
        None,
        "--tip",
        help="Commit ID to start from (default: HEAD).",
    ),
    stop_at: str | None = typer.Option(
        None,
        "--stop-at",
        help="Stop BFS at this commit ID (exclusive). Useful for range queries.",
    ),
    max_commits: int = typer.Option(
        _DEFAULT_MAX,
        "--max",
        "-n",
        help=f"Maximum commits to traverse (default: {_DEFAULT_MAX}).",
    ),
    fmt: str = typer.Option(
        "json", "--format", "-f", help="Output format: json or text (one ID per line)."
    ),
) -> None:
    """Emit the commit DAG reachable from a tip commit.

    Performs a BFS walk from the tip, following ``parent_commit_id`` and
    ``parent2_commit_id`` pointers.  The ``--stop-at`` commit and its
    ancestors are excluded — useful for computing the commits in a branch
    since it diverged from another.
    """
    if fmt not in _FORMAT_CHOICES:
        typer.echo(
            f"❌ Unknown format {fmt!r}. Valid choices: {', '.join(_FORMAT_CHOICES)}",
            err=True,
        )
        raise typer.Exit(code=ExitCode.USER_ERROR)

    root = require_repo()

    if tip is None:
        branch = read_current_branch(root)
        tip = get_head_commit_id(root, branch)
        if tip is None:
            typer.echo(json.dumps({"error": "No commits on current branch."}))
            raise typer.Exit(code=ExitCode.USER_ERROR)

    if read_commit(root, tip) is None:
        typer.echo(json.dumps({"error": f"Tip commit not found: {tip}"}))
        raise typer.Exit(code=ExitCode.USER_ERROR)

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
        if record.parent2_commit_id:
            queue.append(record.parent2_commit_id)

    if fmt == "text":
        for node in nodes:
            typer.echo(node["commit_id"])
        return

    typer.echo(
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

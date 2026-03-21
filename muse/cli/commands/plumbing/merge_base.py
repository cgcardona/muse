"""muse plumbing merge-base — find the lowest common ancestor of two commits.

Walks the commit DAG from two starting points and returns the nearest shared
ancestor (the Lowest Common Ancestor, or LCA).  Used by merge engines, CI
systems, and agent pipelines to compute the divergence point between branches.

Output (JSON, default)::

    {
      "commit_a":   "<sha256>",
      "commit_b":   "<sha256>",
      "merge_base": "<sha256>"
    }

When no common ancestor exists::

    {
      "commit_a":   "<sha256>",
      "commit_b":   "<sha256>",
      "merge_base": null,
      "error":      "no common ancestor"
    }

Plumbing contract
-----------------

- Exit 0: operation completed (check ``merge_base`` field for null vs. found).
- Exit 1: a commit ID or ref cannot be resolved; bad ``--format`` value.
- Exit 3: DAG walk failed (I/O error or malformed graph).
"""

from __future__ import annotations

import json
import logging
import pathlib

import typer

from muse.core.errors import ExitCode
from muse.core.merge_engine import find_merge_base
from muse.core.repo import require_repo
from muse.core.store import get_head_commit_id, read_commit, read_current_branch
from muse.core.validation import validate_object_id

logger = logging.getLogger(__name__)

app = typer.Typer()

_FORMAT_CHOICES = ("json", "text")


def _resolve_ref(root: pathlib.Path, ref: str) -> str | None:
    """Resolve a branch name, HEAD, or full 64-char commit ID to a commit ID.

    Returns ``None`` when the ref cannot be resolved to a known commit.
    """
    if ref.upper() == "HEAD":
        branch = read_current_branch(root)
        return get_head_commit_id(root, branch)

    # Try as branch name first.
    cid = get_head_commit_id(root, ref)
    if cid is not None:
        return cid

    # Try as full commit ID.
    try:
        validate_object_id(ref)
        record = read_commit(root, ref)
        return record.commit_id if record else None
    except ValueError:
        return None


@app.callback(invoke_without_command=True)
def merge_base(
    ctx: typer.Context,
    commit_a: str = typer.Argument(..., help="First commit ID, branch name, or HEAD."),
    commit_b: str = typer.Argument(..., help="Second commit ID, branch name, or HEAD."),
    fmt: str = typer.Option(
        "json", "--format", "-f", help="Output format: json or text."
    ),
) -> None:
    """Find the lowest common ancestor of two commits.

    Accepts full SHA-256 commit IDs, branch names, or ``HEAD``.  The result is
    the commit that is reachable from both inputs and is closest to both tips —
    the point at which their histories diverged.

    Use this to compute how far apart two branches are before merging, or to
    identify the base for a ``snapshot-diff`` range query.
    """
    if fmt not in _FORMAT_CHOICES:
        typer.echo(
            json.dumps(
                {"error": f"Unknown format {fmt!r}. Valid: {', '.join(_FORMAT_CHOICES)}"}
            )
        )
        raise typer.Exit(code=ExitCode.USER_ERROR)

    root = require_repo()

    resolved_a = _resolve_ref(root, commit_a)
    if resolved_a is None:
        typer.echo(json.dumps({"error": f"Cannot resolve ref: {commit_a!r}"}))
        raise typer.Exit(code=ExitCode.USER_ERROR)

    resolved_b = _resolve_ref(root, commit_b)
    if resolved_b is None:
        typer.echo(json.dumps({"error": f"Cannot resolve ref: {commit_b!r}"}))
        raise typer.Exit(code=ExitCode.USER_ERROR)

    try:
        base = find_merge_base(root, resolved_a, resolved_b)
    except Exception as exc:
        logger.debug("merge-base DAG walk failed: %s", exc)
        typer.echo(json.dumps({"error": str(exc)}))
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)

    if fmt == "text":
        if base is None:
            typer.echo("(no common ancestor)")
        else:
            typer.echo(base)
        return

    if base is None:
        typer.echo(
            json.dumps(
                {
                    "commit_a": resolved_a,
                    "commit_b": resolved_b,
                    "merge_base": None,
                    "error": "no common ancestor",
                }
            )
        )
        return

    typer.echo(
        json.dumps(
            {
                "commit_a": resolved_a,
                "commit_b": resolved_b,
                "merge_base": base,
            }
        )
    )

"""muse plumbing read-commit — emit full commit metadata as JSON.

Reads a commit record by its SHA-256 ID and emits the complete JSON
representation including provenance fields, CRDT annotations, and the
structured delta.  Equivalent to ``git cat-file commit`` but producing
the Muse JSON schema directly.

Output::

    {
      "format_version": 5,
      "commit_id": "<sha256>",
      "repo_id": "<uuid>",
      "branch": "main",
      "snapshot_id": "<sha256>",
      "message": "Add verse melody",
      "committed_at": "2026-03-18T12:00:00+00:00",
      "parent_commit_id": "<sha256> | null",
      "parent2_commit_id": null,
      "author": "gabriel",
      "agent_id": "",
      "model_id": "",
      "sem_ver_bump": "none",
      "breaking_changes": [],
      "reviewed_by": [],
      "test_runs": 0,
      ...
    }

Plumbing contract
-----------------

- Exit 0: commit found and printed.
- Exit 1: commit not found.
"""

from __future__ import annotations

import json
import logging

import typer

from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.store import find_commits_by_prefix, read_commit

logger = logging.getLogger(__name__)

app = typer.Typer()


@app.callback(invoke_without_command=True)
def read_commit_cmd(
    ctx: typer.Context,
    commit_id: str = typer.Argument(
        ..., help="Full or abbreviated SHA-256 commit ID."
    ),
) -> None:
    """Emit full commit metadata as JSON.

    Accepts a full 64-character commit ID or a unique prefix.  The output
    schema matches ``CommitRecord.to_dict()`` and is stable across Muse
    versions (use ``format_version`` to detect schema changes).
    """
    root = require_repo()

    record = read_commit(root, commit_id)
    if record is None and len(commit_id) < 64:
        matches = find_commits_by_prefix(root, commit_id)
        if len(matches) == 1:
            record = matches[0]
        elif len(matches) > 1:
            typer.echo(
                json.dumps({
                    "error": "ambiguous prefix",
                    "candidates": [m.commit_id for m in matches],
                })
            )
            raise typer.Exit(code=ExitCode.USER_ERROR)

    if record is None:
        typer.echo(json.dumps({"error": f"Commit not found: {commit_id}"}))
        raise typer.Exit(code=ExitCode.USER_ERROR)

    typer.echo(json.dumps(record.to_dict(), indent=2, default=str))

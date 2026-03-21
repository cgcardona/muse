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
- Exit 1: commit not found, ambiguous prefix, or invalid commit ID format.
"""

from __future__ import annotations

import json
import logging

import typer

from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.store import find_commits_by_prefix, read_commit
from muse.core.validation import validate_object_id

logger = logging.getLogger(__name__)

app = typer.Typer()


_FORMAT_CHOICES = ("json", "text")


@app.callback(invoke_without_command=True)
def read_commit_cmd(
    ctx: typer.Context,
    commit_id: str = typer.Argument(
        ..., help="Full or abbreviated SHA-256 commit ID."
    ),
    fmt: str = typer.Option(
        "json", "--format", "-f", help="Output format: json (default) or text."
    ),
) -> None:
    """Emit full commit metadata as JSON (default) or a compact text summary.

    Accepts a full 64-character commit ID or a unique prefix.  The JSON output
    schema matches ``CommitRecord.to_dict()`` and is stable across Muse
    versions (use ``format_version`` to detect schema changes).

    Text format (``--format text``)::

        <commit_id>  <branch>  <author>  <committed_at>  <message>
    """
    if fmt not in _FORMAT_CHOICES:
        typer.echo(
            json.dumps({"error": f"Unknown format {fmt!r}. Valid: {', '.join(_FORMAT_CHOICES)}"})
        )
        raise typer.Exit(code=ExitCode.USER_ERROR)
    root = require_repo()

    record = None

    if len(commit_id) == 64:
        try:
            validate_object_id(commit_id)
        except ValueError as exc:
            # JSON to stdout so scripts that parse this command's output can
            # detect the error without switching to stderr.
            typer.echo(json.dumps({"error": f"Invalid commit ID: {exc}"}))
            raise typer.Exit(code=ExitCode.USER_ERROR)
        record = read_commit(root, commit_id)
    else:
        matches = find_commits_by_prefix(root, commit_id)
        if len(matches) == 1:
            record = matches[0]
        elif len(matches) > 1:
            typer.echo(
                json.dumps(
                    {
                        "error": "ambiguous prefix",
                        "candidates": [m.commit_id for m in matches],
                    }
                )
            )
            raise typer.Exit(code=ExitCode.USER_ERROR)

    if record is None:
        typer.echo(json.dumps({"error": f"Commit not found: {commit_id}"}))
        raise typer.Exit(code=ExitCode.USER_ERROR)

    if fmt == "text":
        msg = (record.message or "").replace("\n", " ")
        typer.echo(
            f"{record.commit_id[:12]}  {record.branch}  {record.author or ''}  "
            f"{record.committed_at.isoformat()}  {msg}"
        )
        return

    typer.echo(json.dumps(record.to_dict(), indent=2, default=str))

"""muse plumbing ls-files — list tracked files in a snapshot.

Lists every file tracked in a commit's snapshot, along with the SHA-256
object ID of its content.  Defaults to the HEAD commit of the current branch.

Output (JSON, default)::

    {
      "commit_id": "<sha256>",
      "snapshot_id": "<sha256>",
      "file_count": 3,
      "files": [
        {"path": "tracks/drums.mid", "object_id": "<sha256>"},
        ...
      ]
    }

Output (--format text)::

    <object_id>\\t<path>
    ...

Plumbing contract
-----------------

- Exit 0: manifest listed successfully.
- Exit 1: commit or snapshot not found, or unknown --format value.
"""

from __future__ import annotations

import json
import logging

import typer

from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.store import (
    get_commit_snapshot_manifest,
    get_head_commit_id,
    read_commit,
    read_current_branch,
)
from muse.core.validation import validate_object_id

logger = logging.getLogger(__name__)

app = typer.Typer()

_FORMAT_CHOICES = ("json", "text")


@app.callback(invoke_without_command=True)
def ls_files(
    ctx: typer.Context,
    commit: str | None = typer.Option(
        None, "--commit", "-c", help="Commit ID to read (default: HEAD)."
    ),
    fmt: str = typer.Option(
        "json", "--format", "-f", help="Output format: json or text."
    ),
) -> None:
    """List all tracked files and their object IDs in a snapshot.

    Analogous to ``git ls-files --stage``.  Reads the snapshot manifest of
    the given commit (or HEAD) and prints each tracked file path together
    with its content-addressed object ID.
    """
    if fmt not in _FORMAT_CHOICES:
        typer.echo(
            f"❌ Unknown format {fmt!r}. Valid choices: {', '.join(_FORMAT_CHOICES)}",
            err=True,
        )
        raise typer.Exit(code=ExitCode.USER_ERROR)

    root = require_repo()

    if commit is None:
        branch = read_current_branch(root)
        commit_id = get_head_commit_id(root, branch)
        if commit_id is None:
            typer.echo(json.dumps({"error": "No commits on current branch."}))
            raise typer.Exit(code=ExitCode.USER_ERROR)
    else:
        try:
            validate_object_id(commit)
        except ValueError as exc:
            typer.echo(json.dumps({"error": f"Invalid commit ID: {exc}"}))
            raise typer.Exit(code=ExitCode.USER_ERROR)
        commit_id = commit

    commit_record = read_commit(root, commit_id)
    if commit_record is None:
        typer.echo(json.dumps({"error": f"Commit not found: {commit_id}"}))
        raise typer.Exit(code=ExitCode.USER_ERROR)

    manifest = get_commit_snapshot_manifest(root, commit_id)
    if manifest is None:
        typer.echo(json.dumps({"error": f"Snapshot not found for commit: {commit_id}"}))
        raise typer.Exit(code=ExitCode.USER_ERROR)

    files = [{"path": p, "object_id": oid} for p, oid in sorted(manifest.items())]

    if fmt == "text":
        for entry in files:
            typer.echo(f"{entry['object_id']}\t{entry['path']}")
        return

    typer.echo(
        json.dumps(
            {
                "commit_id": commit_id,
                "snapshot_id": commit_record.snapshot_id,
                "file_count": len(files),
                "files": files,
            },
            indent=2,
        )
    )

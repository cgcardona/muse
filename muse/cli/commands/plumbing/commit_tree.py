"""muse plumbing commit-tree — create a commit from an explicit snapshot ID.

Low-level commit creation: takes a snapshot ID (which must already exist in the
store), optional parent commit IDs, and a message, and writes a new
``CommitRecord`` to the store.  Does not touch ``HEAD`` or any branch ref.

Analogous to ``git commit-tree``.  Porcelain commands like ``muse commit`` call
this internally after staging changes and writing the snapshot.

Output::

    {"commit_id": "<sha256>"}

Plumbing contract
-----------------

- Exit 0: commit written, commit_id printed.
- Exit 1: snapshot not found, parent commit not found, or repo.json unreadable.
- Exit 3: write failure.
"""

from __future__ import annotations

import datetime
import json
import logging
import pathlib

import typer

from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.snapshot import compute_commit_id
from muse.core.store import (
    CommitRecord,
    read_commit,
    read_current_branch,
    read_snapshot,
    write_commit,
)
from muse.core.validation import validate_object_id

logger = logging.getLogger(__name__)

app = typer.Typer()


def _read_repo_id(root: pathlib.Path) -> str:
    """Read the repo UUID from repo.json.

    Returns the repo_id string, or raises SystemExit if the file is missing,
    malformed, or the field is absent — a commit without a valid repo_id would
    be permanently corrupt.
    """
    repo_json = root / ".muse" / "repo.json"
    try:
        data = json.loads(repo_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        typer.echo(f"❌ Cannot read repo.json: {exc}", err=True)
        raise typer.Exit(code=ExitCode.USER_ERROR)
    repo_id = data.get("repo_id", "")
    if not isinstance(repo_id, str) or not repo_id:
        typer.echo("❌ repo.json is missing a valid 'repo_id' field.", err=True)
        raise typer.Exit(code=ExitCode.USER_ERROR)
    return repo_id


_FORMAT_CHOICES = ("json", "text")


@app.callback(invoke_without_command=True)
def commit_tree(
    ctx: typer.Context,
    snapshot_id: str = typer.Option(..., "--snapshot", "-s", help="SHA-256 snapshot ID."),
    parent: list[str] = typer.Option(
        [], "--parent", "-p", help="Parent commit ID (repeat for merge commits)."
    ),
    message: str = typer.Option("", "--message", "-m", help="Commit message."),
    author: str = typer.Option("", "--author", "-a", help="Author name."),
    branch: str | None = typer.Option(
        None, "--branch", "-b", help="Branch name to record (default: current branch)."
    ),
    fmt: str = typer.Option(
        "json", "--format", "-f", help="Output format: json (default) or text (bare commit_id)."
    ),
) -> None:
    """Create a commit from an explicit snapshot ID.

    The snapshot must already exist in ``.muse/snapshots/``.  Each ``--parent``
    flag adds a parent commit (use once for linear history, twice for merge
    commits).  The commit is written to ``.muse/commits/`` but no branch ref
    is updated — use ``muse plumbing update-ref`` to advance a branch.

    Output (``--format json``, default)::

        {"commit_id": "<sha256>"}

    Output (``--format text``)::

        <sha256>
    """
    if fmt not in _FORMAT_CHOICES:
        typer.echo(
            json.dumps({"error": f"Unknown format {fmt!r}. Valid: {', '.join(_FORMAT_CHOICES)}"})
        )
        raise typer.Exit(code=ExitCode.USER_ERROR)
    root = require_repo()

    try:
        validate_object_id(snapshot_id)
    except ValueError as exc:
        typer.echo(json.dumps({"error": f"Invalid snapshot ID: {exc}"}))
        raise typer.Exit(code=ExitCode.USER_ERROR)

    for pid in parent:
        try:
            validate_object_id(pid)
        except ValueError as exc:
            typer.echo(json.dumps({"error": f"Invalid parent commit ID: {exc}"}))
            raise typer.Exit(code=ExitCode.USER_ERROR)

    snap = read_snapshot(root, snapshot_id)
    if snap is None:
        typer.echo(json.dumps({"error": f"Snapshot not found: {snapshot_id}"}))
        raise typer.Exit(code=ExitCode.USER_ERROR)

    for pid in parent:
        if read_commit(root, pid) is None:
            typer.echo(json.dumps({"error": f"Parent commit not found: {pid}"}))
            raise typer.Exit(code=ExitCode.USER_ERROR)

    repo_id = _read_repo_id(root)
    branch_name = branch or read_current_branch(root)
    committed_at = datetime.datetime.now(datetime.timezone.utc)

    commit_id = compute_commit_id(
        parent_ids=parent,
        snapshot_id=snapshot_id,
        message=message,
        committed_at_iso=committed_at.isoformat(),
    )

    record = CommitRecord(
        commit_id=commit_id,
        repo_id=repo_id,
        branch=branch_name,
        snapshot_id=snapshot_id,
        message=message,
        committed_at=committed_at,
        author=author,
        parent_commit_id=parent[0] if len(parent) >= 1 else None,
        parent2_commit_id=parent[1] if len(parent) >= 2 else None,
    )
    write_commit(root, record)

    if fmt == "text":
        typer.echo(commit_id)
        return

    typer.echo(json.dumps({"commit_id": commit_id}))

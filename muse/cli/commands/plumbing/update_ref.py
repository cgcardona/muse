"""muse plumbing update-ref — move a branch HEAD to a specific commit.

Directly writes a branch reference file under ``.muse/refs/heads/``.  This is
the lowest-level way to advance or rewind a branch without any merge logic.

Analogous to ``git update-ref``.  Porcelain commands (``muse commit``,
``muse merge``, ``muse reset``) call this internally after computing the new
commit ID.

Output::

    {"branch": "main", "commit_id": "<sha256>", "previous": "<sha256> | null"}

Plumbing contract
-----------------

- Exit 0: ref updated.
- Exit 1: commit not found in the store, or ``--delete`` on a non-existent ref.
- Exit 3: file write failure.
"""

from __future__ import annotations

import json
import logging
import pathlib

import typer

from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.store import get_head_commit_id, read_commit

logger = logging.getLogger(__name__)

app = typer.Typer()


@app.callback(invoke_without_command=True)
def update_ref(
    ctx: typer.Context,
    branch: str = typer.Argument(..., help="Branch name to update."),
    commit_id: str | None = typer.Argument(
        None,
        help="Commit ID to point the branch at. Omit with --delete to remove the branch.",
    ),
    delete: bool = typer.Option(
        False, "--delete", "-d", help="Delete the branch ref entirely."
    ),
    verify: bool = typer.Option(
        True,
        "--verify/--no-verify",
        help="Verify the commit exists in the store before updating (default: on).",
    ),
) -> None:
    """Move a branch HEAD to a specific commit ID.

    Directly writes (or deletes) a branch ref file.  When ``--verify`` is set
    (the default), the commit must already exist in ``.muse/commits/``.
    Pass ``--no-verify`` to write the ref even if the commit is not yet in
    the local store (e.g. after ``muse plumbing unpack-objects``).
    """
    root = require_repo()

    ref_path = root / ".muse" / "refs" / "heads" / branch

    if delete:
        if not ref_path.exists():
            typer.echo(json.dumps({"error": f"Branch ref does not exist: {branch}"}))
            raise typer.Exit(code=ExitCode.USER_ERROR)
        ref_path.unlink()
        typer.echo(json.dumps({"branch": branch, "deleted": True}))
        return

    if commit_id is None:
        typer.echo(json.dumps({"error": "commit_id is required unless --delete is used."}))
        raise typer.Exit(code=ExitCode.USER_ERROR)

    if verify and read_commit(root, commit_id) is None:
        typer.echo(json.dumps({"error": f"Commit not found in store: {commit_id}"}))
        raise typer.Exit(code=ExitCode.USER_ERROR)

    previous = get_head_commit_id(root, branch)
    try:
        ref_path.parent.mkdir(parents=True, exist_ok=True)
        ref_path.write_text(commit_id)
    except OSError as exc:
        typer.echo(json.dumps({"error": str(exc)}))
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)

    typer.echo(json.dumps({
        "branch": branch,
        "commit_id": commit_id,
        "previous": previous,
    }))

"""muse plumbing rev-parse — resolve a ref to a full commit ID.

Resolves a branch name, ``HEAD``, or an abbreviated SHA prefix to the full
64-character SHA-256 commit ID.

Output (JSON, default)::

    {"ref": "main", "commit_id": "<sha256>"}

Output (--format text)::

    <sha256>

Plumbing contract
-----------------

- Exit 0: ref resolved successfully.
- Exit 1: ref not found, ambiguous, or unknown --format value.
"""

from __future__ import annotations

import json
import logging

import typer

from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.store import (
    find_commits_by_prefix,
    get_head_commit_id,
    read_commit,
    read_current_branch,
)

logger = logging.getLogger(__name__)

app = typer.Typer()

_FORMAT_CHOICES = ("json", "text")


@app.callback(invoke_without_command=True)
def rev_parse(
    ctx: typer.Context,
    ref: str = typer.Argument(
        ...,
        help="Ref to resolve: branch name, 'HEAD', or commit ID prefix.",
    ),
    fmt: str = typer.Option(
        "json", "--format", "-f", help="Output format: json or text."
    ),
) -> None:
    """Resolve a branch name, HEAD, or SHA prefix to a full commit ID.

    Analogous to ``git rev-parse``.  Useful for canonicalising refs in
    scripts and agent pipelines before passing them to other plumbing
    commands.
    """
    if fmt not in _FORMAT_CHOICES:
        typer.echo(
            f"❌ Unknown format {fmt!r}. Valid choices: {', '.join(_FORMAT_CHOICES)}",
            err=True,
        )
        raise typer.Exit(code=ExitCode.USER_ERROR)

    root = require_repo()

    commit_id: str | None = None

    if ref.upper() == "HEAD":
        branch = read_current_branch(root)
        commit_id = get_head_commit_id(root, branch)
        if commit_id is None:
            typer.echo(
                json.dumps({"ref": ref, "commit_id": None, "error": "HEAD has no commits"})
            )
            raise typer.Exit(code=ExitCode.USER_ERROR)
    else:
        # Try as branch name first.
        candidate = get_head_commit_id(root, ref)
        if candidate is not None:
            commit_id = candidate
        else:
            # Try as full or abbreviated commit ID.
            if len(ref) == 64:
                record = read_commit(root, ref)
                if record is not None:
                    commit_id = record.commit_id
            else:
                matches = find_commits_by_prefix(root, ref)
                if len(matches) == 1:
                    commit_id = matches[0].commit_id
                elif len(matches) > 1:
                    typer.echo(
                        json.dumps(
                            {
                                "ref": ref,
                                "commit_id": None,
                                "error": "ambiguous",
                                "candidates": [m.commit_id for m in matches],
                            }
                        )
                    )
                    raise typer.Exit(code=ExitCode.USER_ERROR)

    if commit_id is None:
        typer.echo(json.dumps({"ref": ref, "commit_id": None, "error": "not found"}))
        raise typer.Exit(code=ExitCode.USER_ERROR)

    if fmt == "text":
        typer.echo(commit_id)
        return

    typer.echo(json.dumps({"ref": ref, "commit_id": commit_id}))

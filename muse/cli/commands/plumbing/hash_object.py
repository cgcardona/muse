"""muse plumbing hash-object — compute the SHA-256 object ID of a file.

Computes the content-addressed object ID (SHA-256 hex digest) of a file.
With ``--write`` the object is also stored in ``.muse/objects/`` so it can be
referenced by future snapshots and commits.

Output (JSON, default)::

    {"object_id": "<sha256>", "stored": false}

Output (--format text)::

    <sha256>

Plumbing contract
-----------------

- Exit 0: hash computed successfully.
- Exit 1: file not found or path is a directory.
- Exit 3: I/O error writing to the store.
"""

from __future__ import annotations

import json
import logging
import pathlib
import sys

import typer

from muse.core.errors import ExitCode
from muse.core.object_store import write_object
from muse.core.repo import find_repo_root
from muse.core.snapshot import hash_file

logger = logging.getLogger(__name__)

app = typer.Typer()

_FORMAT_CHOICES = ["json", "text"]


@app.callback(invoke_without_command=True)
def hash_object(
    ctx: typer.Context,
    path: pathlib.Path = typer.Argument(..., help="File to hash."),
    write: bool = typer.Option(
        False, "--write", "-w", help="Store the object in .muse/objects/."
    ),
    fmt: str = typer.Option("json", "--format", help="Output format: json or text."),
) -> None:
    """Compute the SHA-256 object ID of a file.

    Analogous to ``git hash-object``.  The object ID is deterministic —
    identical bytes always produce the same ID.  Pass ``--write`` to also
    store the object so it can be referenced by future ``muse plumbing
    commit-tree`` calls.
    """
    if not path.exists():
        typer.echo(f"❌ Path does not exist: {path}", err=True)
        raise typer.Exit(code=ExitCode.USER_ERROR)
    if path.is_dir():
        typer.echo(f"❌ Path is a directory, not a file: {path}", err=True)
        raise typer.Exit(code=ExitCode.USER_ERROR)

    object_id = hash_file(path)
    stored = False

    if write:
        root = find_repo_root(pathlib.Path.cwd())
        if root is None:
            typer.echo("❌ Not inside a Muse repository. Cannot write object.", err=True)
            raise typer.Exit(code=ExitCode.USER_ERROR)
        try:
            raw = path.read_bytes()
            stored = write_object(root, object_id, raw)
        except OSError as exc:
            typer.echo(f"❌ Failed to write object: {exc}", err=True)
            raise typer.Exit(code=ExitCode.INTERNAL_ERROR)

    if fmt == "text":
        typer.echo(object_id)
        return

    typer.echo(json.dumps({"object_id": object_id, "stored": stored}))

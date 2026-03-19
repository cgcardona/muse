"""muse plumbing cat-object — read a stored object from the object store.

Reads the raw bytes of a content-addressed object and writes them to stdout.
Useful for inspecting stored blobs, verifying round-trips, or piping raw
content to other tools.

Output
------

With ``--format raw`` (default): raw bytes written directly to stdout.
With ``--format info``: JSON metadata about the object.

    {"object_id": "<sha256>", "size_bytes": 1234, "present": true}

Plumbing contract
-----------------

- Exit 0: blob found and written to stdout.
- Exit 1: blob not found in the store.
- Exit 3: I/O error reading from the store.
"""

from __future__ import annotations

import json
import logging
import pathlib
import sys

import typer

from muse.core.errors import ExitCode
from muse.core.object_store import has_object, read_object
from muse.core.repo import require_repo

logger = logging.getLogger(__name__)

app = typer.Typer()


@app.callback(invoke_without_command=True)
def cat_object(
    ctx: typer.Context,
    object_id: str = typer.Argument(..., help="SHA-256 object ID to read."),
    fmt: str = typer.Option(
        "raw", "--format", help="Output format: raw (bytes to stdout) or info (JSON metadata)."
    ),
) -> None:
    """Read a stored object from the content-addressed object store.

    Analogous to ``git cat-file``.  With ``--format raw`` the raw bytes are
    written to stdout (suitable for piping or redirection).  With
    ``--format info`` a JSON summary of the object is printed without
    emitting its contents.
    """
    root = require_repo()

    if not has_object(root, object_id):
        typer.echo(
            json.dumps({"object_id": object_id, "present": False, "size_bytes": 0})
        ) if fmt == "info" else typer.echo(
            f"❌ Object not found: {object_id}", err=True
        )
        raise typer.Exit(code=ExitCode.USER_ERROR)

    if fmt == "info":
        raw = read_object(root, object_id)
        size = len(raw) if raw is not None else 0
        typer.echo(
            json.dumps({"object_id": object_id, "present": True, "size_bytes": size})
        )
        return

    # Raw output: write bytes directly to stdout binary stream.
    raw = read_object(root, object_id)
    if raw is None:
        typer.echo(f"❌ Object vanished during read: {object_id}", err=True)
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)
    sys.stdout.buffer.write(raw)

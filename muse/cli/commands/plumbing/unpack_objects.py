"""muse plumbing unpack-objects — read a PackBundle from stdin and write to store.

Reads a PackBundle JSON document from stdin and idempotently writes its
commits, snapshots, and objects into the local ``.muse/`` store.  Analogous
to ``git unpack-objects``.

Usage::

    cat pack.json | muse plumbing unpack-objects
    muse plumbing pack-objects HEAD | muse plumbing unpack-objects

Output::

    {
      "commits_written": 12,
      "snapshots_written": 12,
      "objects_written": 47,
      "objects_skipped": 3
    }

Plumbing contract
-----------------

- Exit 0: objects unpacked (idempotent — already-present objects are skipped).
- Exit 1: invalid JSON from stdin.
- Exit 3: write failure.
"""

from __future__ import annotations

import json
import logging
import sys

import typer

from muse.core.errors import ExitCode
from muse.core.pack import PackBundle, apply_pack
from muse.core.repo import require_repo

logger = logging.getLogger(__name__)

app = typer.Typer()


@app.callback(invoke_without_command=True)
def unpack_objects(ctx: typer.Context) -> None:
    """Read a PackBundle JSON from stdin and write to the local store.

    Idempotent: if a commit, snapshot, or object already exists in the store
    it is silently skipped.  Partial packs (interrupted transfers) are safe
    to re-apply.  The exit code is 0 as long as the store is consistent at
    the end of the operation.
    """
    root = require_repo()

    raw_bytes = sys.stdin.buffer.read()
    try:
        raw_dict = json.loads(raw_bytes)
    except json.JSONDecodeError as exc:
        typer.echo(json.dumps({"error": f"Invalid JSON from stdin: {exc}"}))
        raise typer.Exit(code=ExitCode.USER_ERROR)

    # json.loads returns a dynamically-typed dict; PackBundle is a TypedDict
    # with the same shape.  We extract each key explicitly so the bundle is
    # well-formed even if the source JSON has extra or missing keys.
    bundle = PackBundle(
        commits=raw_dict.get("commits") or [],
        snapshots=raw_dict.get("snapshots") or [],
        objects=raw_dict.get("objects") or [],
        branch_heads=raw_dict.get("branch_heads") or {},
    )

    result = apply_pack(root, bundle)
    typer.echo(json.dumps({
        "commits_written": result["commits_written"],
        "snapshots_written": result["snapshots_written"],
        "objects_written": result["objects_written"],
        "objects_skipped": result["objects_skipped"],
    }))

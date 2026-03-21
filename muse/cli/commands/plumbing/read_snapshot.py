"""muse plumbing read-snapshot — emit full snapshot metadata as JSON.

Reads a snapshot record by its SHA-256 ID and emits the complete JSON
representation including the file manifest.

Output::

    {
      "snapshot_id": "<sha256>",
      "created_at": "2026-03-18T12:00:00+00:00",
      "file_count": 3,
      "manifest": {
        "tracks/drums.mid": "<sha256>",
        "tracks/bass.mid":  "<sha256>",
        "tracks/piano.mid": "<sha256>"
      }
    }

Plumbing contract
-----------------

- Exit 0: snapshot found and printed.
- Exit 1: snapshot not found or invalid snapshot ID format.
"""

from __future__ import annotations

import json
import logging

import typer

from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.store import read_snapshot
from muse.core.validation import validate_object_id

logger = logging.getLogger(__name__)

app = typer.Typer()


@app.callback(invoke_without_command=True)
def read_snapshot_cmd(
    ctx: typer.Context,
    snapshot_id: str = typer.Argument(..., help="SHA-256 snapshot ID (64 hex chars)."),
) -> None:
    """Emit full snapshot metadata as JSON.

    A snapshot holds the complete file manifest (path → object_id mapping)
    for a point in time.  Every commit references exactly one snapshot.
    Use ``muse plumbing ls-files --commit <id>`` if you want to look up a
    snapshot from a commit ID rather than from the snapshot ID directly.
    """
    try:
        validate_object_id(snapshot_id)
    except ValueError as exc:
        # JSON to stdout so scripts that parse this command's output can
        # detect the error without switching to stderr.
        typer.echo(json.dumps({"error": f"Invalid snapshot ID: {exc}"}))
        raise typer.Exit(code=ExitCode.USER_ERROR)

    root = require_repo()

    record = read_snapshot(root, snapshot_id)
    if record is None:
        typer.echo(json.dumps({"error": f"Snapshot not found: {snapshot_id}"}))
        raise typer.Exit(code=ExitCode.USER_ERROR)

    output = {
        "snapshot_id": record.snapshot_id,
        "created_at": record.created_at.isoformat(),
        "file_count": len(record.manifest),
        "manifest": record.manifest,
    }
    typer.echo(json.dumps(output, indent=2))

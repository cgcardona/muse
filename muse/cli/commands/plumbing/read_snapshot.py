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

import argparse
import json
import logging
import sys

from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.store import read_snapshot
from muse.core.validation import validate_object_id

logger = logging.getLogger(__name__)

_FORMAT_CHOICES = ("json", "text")


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the read-snapshot subcommand."""
    parser = subparsers.add_parser(
        "read-snapshot",
        help="Emit full snapshot metadata and manifest as JSON.",
        description=__doc__,
    )
    parser.add_argument(
        "snapshot_id",
        help="SHA-256 snapshot ID (64 hex chars).",
    )
    parser.add_argument(
        "--format", "-f",
        dest="fmt",
        default="json",
        metavar="FORMAT",
        help="Output format: json (default) or text.",
    )
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> None:
    """Emit full snapshot metadata as JSON (default) or a compact text summary.

    A snapshot holds the complete file manifest (path → object_id mapping)
    for a point in time.  Every commit references exactly one snapshot.
    Use ``muse plumbing ls-files --commit <id>`` if you want to look up a
    snapshot from a commit ID rather than from the snapshot ID directly.

    Text format (``--format text``)::

        <snapshot_id>  <file_count> files  <created_at>
    """
    fmt: str = args.fmt
    snapshot_id: str = args.snapshot_id

    if fmt not in _FORMAT_CHOICES:
        print(
            json.dumps({"error": f"Unknown format {fmt!r}. Valid: {', '.join(_FORMAT_CHOICES)}"})
        )
        raise SystemExit(ExitCode.USER_ERROR)

    try:
        validate_object_id(snapshot_id)
    except ValueError as exc:
        print(json.dumps({"error": f"Invalid snapshot ID: {exc}"}))
        raise SystemExit(ExitCode.USER_ERROR)

    root = require_repo()

    record = read_snapshot(root, snapshot_id)
    if record is None:
        print(json.dumps({"error": f"Snapshot not found: {snapshot_id}"}))
        raise SystemExit(ExitCode.USER_ERROR)

    if fmt == "text":
        print(
            f"{record.snapshot_id[:12]}  {len(record.manifest)} files  "
            f"{record.created_at.isoformat()}"
        )
        return

    output = {
        "snapshot_id": record.snapshot_id,
        "created_at": record.created_at.isoformat(),
        "file_count": len(record.manifest),
        "manifest": record.manifest,
    }
    print(json.dumps(output, indent=2))

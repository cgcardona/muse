"""muse plumbing cat-object — read a stored object from the object store.

Reads the raw bytes of a content-addressed object and writes them to stdout.
Useful for inspecting stored blobs, verifying round-trips, or piping raw
content to other tools.

Output
------

With ``--format raw`` (default): bytes streamed directly to stdout at 64 KiB
at a time — no heap spike, no size ceiling.

With ``--format info``: JSON metadata about the object (no content emitted).

    {"object_id": "<sha256>", "size_bytes": 1234, "present": true}

Plumbing contract
-----------------

- Exit 0: found — bytes written to stdout or metadata printed.
- Exit 1: not found in the store, or invalid object-id format.
- Exit 3: I/O error reading from the store.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

from muse.core.errors import ExitCode
from muse.core.object_store import has_object, object_path
from muse.core.repo import require_repo
from muse.core.validation import validate_object_id

logger = logging.getLogger(__name__)

_FORMAT_CHOICES = ("raw", "info")
_CHUNK = 65536


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the cat-object subcommand."""
    parser = subparsers.add_parser(
        "cat-object",
        help="Emit raw bytes of a stored object to stdout.",
        description=__doc__,
    )
    parser.add_argument(
        "object_id",
        help="SHA-256 object ID to read (64 hex chars).",
    )
    parser.add_argument(
        "--format", "-f",
        dest="fmt",
        default="raw",
        metavar="FORMAT",
        help="Output format: raw (bytes to stdout) or info (JSON metadata). (default: raw)",
    )
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> None:
    """Read a stored object from the content-addressed object store.

    Analogous to ``git cat-file``.  With ``--format raw`` (default) the raw
    bytes are streamed to stdout at 64 KiB at a time — suitable for piping or
    redirection with no heap spike and no size ceiling.  With ``--format info``
    a JSON summary is printed without emitting the object's contents.
    """
    fmt: str = args.fmt
    object_id: str = args.object_id

    if fmt not in _FORMAT_CHOICES:
        print(
            f"❌ Unknown format {fmt!r}. Valid choices: {', '.join(_FORMAT_CHOICES)}",
            file=sys.stderr,
        )
        raise SystemExit(ExitCode.USER_ERROR)

    try:
        validate_object_id(object_id)
    except ValueError as exc:
        print(f"❌ Invalid object ID: {exc}", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)

    root = require_repo()

    if not has_object(root, object_id):
        if fmt == "info":
            print(json.dumps({"object_id": object_id, "present": False, "size_bytes": 0}))
        else:
            print(f"❌ Object not found: {object_id}", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)

    obj = object_path(root, object_id)

    if fmt == "info":
        # stat() gives the size without reading any content.
        size = obj.stat().st_size
        print(json.dumps({"object_id": object_id, "present": True, "size_bytes": size}))
        return

    # Raw: stream directly to the binary stdout buffer so arbitrarily large
    # blobs (dense MIDI renders, audio, genomics files) never spike the heap.
    try:
        with obj.open("rb") as fh:
            for chunk in iter(lambda: fh.read(_CHUNK), b""):
                sys.stdout.buffer.write(chunk)
    except OSError as exc:
        print(f"❌ Failed to read object: {exc}", file=sys.stderr)
        raise SystemExit(ExitCode.INTERNAL_ERROR)

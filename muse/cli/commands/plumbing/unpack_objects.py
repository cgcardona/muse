"""muse plumbing unpack-objects — read a PackBundle from stdin and write to store.

Reads a PackBundle msgpack binary from stdin and idempotently writes its
commits, snapshots, and objects into the local ``.muse/`` store.  Analogous
to ``git unpack-objects``.

Usage::

    cat pack.muse | muse plumbing unpack-objects
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
- Exit 1: invalid msgpack from stdin.
- Exit 3: write failure.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

import msgpack

from muse.core.errors import ExitCode
from muse.core.pack import PackBundle, apply_pack
from muse.core.repo import require_repo

logger = logging.getLogger(__name__)

_FORMAT_CHOICES = ("json", "text")


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the unpack-objects subcommand."""
    parser = subparsers.add_parser(
        "unpack-objects",
        help="Read a PackBundle JSON from stdin, write to the local store.",
        description=__doc__,
    )
    parser.add_argument(
        "--format", "-f",
        dest="fmt",
        default="json",
        metavar="FORMAT",
        help="Output format: json (default) or text (human-readable summary).",
    )
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> None:
    """Read a PackBundle JSON from stdin and write to the local store.

    Idempotent: if a commit, snapshot, or object already exists in the store
    it is silently skipped.  Partial packs (interrupted transfers) are safe
    to re-apply.  The exit code is 0 as long as the store is consistent at
    the end of the operation.
    """
    fmt: str = args.fmt

    if fmt not in _FORMAT_CHOICES:
        print(
            json.dumps({"error": f"Unknown format {fmt!r}. Valid: {', '.join(_FORMAT_CHOICES)}"})
        )
        raise SystemExit(ExitCode.USER_ERROR)

    root = require_repo()

    raw_bytes = sys.stdin.buffer.read()
    try:
        raw_dict = msgpack.unpackb(raw_bytes, raw=False)
    except Exception as exc:
        print(json.dumps({"error": f"Invalid msgpack from stdin: {exc}"}))
        raise SystemExit(ExitCode.USER_ERROR)

    if not isinstance(raw_dict, dict):
        print(json.dumps({"error": "Expected a msgpack map at the top level."}))
        raise SystemExit(ExitCode.USER_ERROR)

    from muse.core.pack import ObjectPayload

    raw_objects: list[ObjectPayload] = []
    for item in raw_dict.get("objects") or []:
        if isinstance(item, dict):
            oid = item.get("object_id")
            content = item.get("content")
            if isinstance(oid, str) and isinstance(content, (bytes, bytearray)):
                raw_objects.append(ObjectPayload(object_id=oid, content=bytes(content)))

    bundle = PackBundle(
        commits=raw_dict.get("commits") or [],
        snapshots=raw_dict.get("snapshots") or [],
        objects=raw_objects,
        branch_heads=raw_dict.get("branch_heads") or {},
    )

    result = apply_pack(root, bundle)

    if fmt == "text":
        print(
            f"Wrote {result['commits_written']} commits, "
            f"{result['snapshots_written']} snapshots, "
            f"{result['objects_written']} objects "
            f"({result['objects_skipped']} skipped)."
        )
        return

    print(json.dumps({
        "commits_written": result["commits_written"],
        "snapshots_written": result["snapshots_written"],
        "objects_written": result["objects_written"],
        "objects_skipped": result["objects_skipped"],
    }))

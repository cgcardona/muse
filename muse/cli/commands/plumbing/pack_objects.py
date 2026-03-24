"""muse plumbing pack-objects — build a PackBundle and write to stdout.

Collects a set of commits (and all referenced snapshots and objects) into a
single msgpack PackBundle suitable for transport to a remote.  Analogous to
``git pack-objects`` using a binary packfile format — efficient binary
encoding with raw bytes for object content (no base64 overhead).

Usage::

    muse plumbing pack-objects <want_id>... [--have <id>...]

The ``--have`` IDs are commits the receiver already has.  Objects reachable
exclusively from ``--have`` ancestors are pruned from the bundle.

Output: a PackBundle msgpack binary written to stdout (pipe to a file or HTTP
request body).

Plumbing contract
-----------------

- Exit 0: pack written to stdout.
- Exit 1: a wanted commit not found or HEAD has no commits.
- Exit 3: I/O error reading objects or snapshots from the local store.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

import msgpack

from muse.core.errors import ExitCode
from muse.core.pack import build_pack
from muse.core.repo import require_repo
from muse.core.store import get_head_commit_id, read_current_branch

logger = logging.getLogger(__name__)


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the pack-objects subcommand."""
    parser = subparsers.add_parser(
        "pack-objects",
        help="Build a PackBundle JSON from wanted commits and write to stdout.",
        description=__doc__,
    )
    parser.add_argument(
        "want",
        nargs="+",
        help="Commit IDs to pack. May be full IDs or 'HEAD'.",
    )
    parser.add_argument(
        "--have",
        action="append",
        default=[],
        dest="have",
        metavar="COMMIT_ID",
        help="Commits the receiver already has (pruned from pack). Repeat for multiple.",
    )
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> None:
    """Build a PackBundle JSON from wanted commits and write to stdout.

    Traverses the commit graph from each ``want`` ID, collecting all
    commits, snapshots, and objects not already reachable from ``--have``
    ancestors.  The resulting JSON bundle can be piped directly to
    ``muse plumbing unpack-objects`` on the receiving side, or sent via
    HTTP to a MuseHub endpoint.
    """
    want: list[str] = args.want
    have: list[str] = args.have

    root = require_repo()

    resolved_wants: list[str] = []
    for w in want:
        if w.upper() == "HEAD":
            branch = read_current_branch(root)
            cid = get_head_commit_id(root, branch)
            if cid is None:
                print(json.dumps({"error": "HEAD has no commits"}), file=sys.stderr)
                raise SystemExit(ExitCode.USER_ERROR)
            resolved_wants.append(cid)
        else:
            resolved_wants.append(w)

    bundle = build_pack(root, commit_ids=resolved_wants, have=have)
    sys.stdout.buffer.write(msgpack.packb(bundle, use_bin_type=True))

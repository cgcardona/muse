"""muse plumbing pack-objects — build a PackBundle JSON and write to stdout.

Collects a set of commits (and all referenced snapshots and objects) into a
single JSON PackBundle suitable for transport to a remote.  Analogous to
``git pack-objects`` but uses JSON + base64 rather than a binary packfile
format — optimised for agent pipelines and HTTP transport.

Usage::

    muse plumbing pack-objects <want_id>... [--have <id>...]

The ``--have`` IDs are commits the receiver already has.  Objects reachable
exclusively from ``--have`` ancestors are pruned from the bundle.

Output: a PackBundle JSON object written to stdout (pipe to a file or HTTP
request body).

Plumbing contract
-----------------

- Exit 0: pack written to stdout.
- Exit 1: a wanted commit not found.
"""

from __future__ import annotations

import json
import logging
import sys

import typer

from muse.core.errors import ExitCode
from muse.core.pack import build_pack
from muse.core.repo import require_repo
from muse.core.store import get_head_commit_id, read_current_branch

logger = logging.getLogger(__name__)

app = typer.Typer()


@app.callback(invoke_without_command=True)
def pack_objects(
    ctx: typer.Context,
    want: list[str] = typer.Argument(
        ...,
        help="Commit IDs to pack. May be full IDs or 'HEAD'.",
    ),
    have: list[str] = typer.Option(
        [],
        "--have",
        help="Commits the receiver already has (pruned from pack).",
    ),
) -> None:
    """Build a PackBundle JSON from wanted commits and write to stdout.

    Traverses the commit graph from each ``want`` ID, collecting all
    commits, snapshots, and objects not already reachable from ``--have``
    ancestors.  The resulting JSON bundle can be piped directly to
    ``muse plumbing unpack-objects`` on the receiving side, or sent via
    HTTP to a MuseHub endpoint.
    """
    root = require_repo()

    resolved_wants: list[str] = []
    for w in want:
        if w.upper() == "HEAD":
            branch = read_current_branch(root)
            cid = get_head_commit_id(root, branch)
            if cid is None:
                typer.echo(json.dumps({"error": "HEAD has no commits"}), err=True)
                raise typer.Exit(code=ExitCode.USER_ERROR)
            resolved_wants.append(cid)
        else:
            resolved_wants.append(w)

    bundle = build_pack(root, commit_ids=resolved_wants, have=have)
    json.dump(bundle, sys.stdout)

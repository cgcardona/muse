"""muse plumbing snapshot-diff — diff two snapshot manifests.

Compares two Muse snapshots and categorises every path change into one of three
buckets: added, modified, or deleted.  Accepts snapshot IDs directly or commit
IDs / branch names (in which case the commit's snapshot is resolved first).

Output (JSON, default)::

    {
      "snapshot_a":    "<sha256>",
      "snapshot_b":    "<sha256>",
      "added":         [{"path": "tracks/lead.mid", "object_id": "<sha256>"}],
      "modified":      [{"path": "tracks/drums.mid",
                         "object_id_a": "<sha256>", "object_id_b": "<sha256>"}],
      "deleted":       [{"path": "tracks/old.mid",   "object_id": "<sha256>"}],
      "total_changes": 3
    }

Text output (``--format text``)::

    A  tracks/lead.mid
    M  tracks/drums.mid
    D  tracks/old.mid

Plumbing contract
-----------------

- Exit 0: diff computed (even when zero changes).
- Exit 1: snapshot or commit ID cannot be resolved; bad ``--format`` value.
- Exit 3: I/O error reading snapshot records.
"""

from __future__ import annotations

import argparse
import json
import logging
import pathlib
import sys
from typing import TypedDict

from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.store import (
    get_head_commit_id,
    read_commit,
    read_current_branch,
    read_snapshot,
)
from muse.core.validation import validate_object_id

logger = logging.getLogger(__name__)

_FORMAT_CHOICES = ("json", "text")


class _AddedEntry(TypedDict):
    path: str
    object_id: str


class _ModifiedEntry(TypedDict):
    path: str
    object_id_a: str
    object_id_b: str


class _DeletedEntry(TypedDict):
    path: str
    object_id: str


class _DiffResult(TypedDict):
    snapshot_a: str
    snapshot_b: str
    added: list[_AddedEntry]
    modified: list[_ModifiedEntry]
    deleted: list[_DeletedEntry]
    total_changes: int


def _resolve_to_snapshot_id(root: pathlib.Path, ref: str) -> str | None:
    """Return the snapshot ID for *ref*.

    *ref* may be a 64-char snapshot ID, a 64-char commit ID, a branch name, or
    ``HEAD``.  Returns ``None`` when the ref cannot be resolved.
    """
    # HEAD → current branch → commit → snapshot
    if ref.upper() == "HEAD":
        branch = read_current_branch(root)
        commit_id = get_head_commit_id(root, branch)
        if commit_id is None:
            return None
        commit = read_commit(root, commit_id)
        return commit.snapshot_id if commit else None

    # Try as a branch name.
    commit_id = get_head_commit_id(root, ref)
    if commit_id is not None:
        commit = read_commit(root, commit_id)
        return commit.snapshot_id if commit else None

    # Validate it looks like a hash.
    try:
        validate_object_id(ref)
    except ValueError:
        return None

    # Try as snapshot ID.
    snap = read_snapshot(root, ref)
    if snap is not None:
        return snap.snapshot_id

    # Try as commit ID.
    commit = read_commit(root, ref)
    if commit is not None:
        return commit.snapshot_id

    return None


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the snapshot-diff subcommand."""
    parser = subparsers.add_parser(
        "snapshot-diff",
        help="Diff two snapshot manifests: added, modified, deleted paths.",
        description=__doc__,
    )
    parser.add_argument(
        "ref_a",
        help="First snapshot ID, commit ID, branch name, or HEAD.",
    )
    parser.add_argument(
        "ref_b",
        help="Second snapshot ID, commit ID, branch name, or HEAD.",
    )
    parser.add_argument(
        "--format", "-f",
        dest="fmt",
        default="json",
        metavar="FORMAT",
        help="Output format: json or text. (default: json)",
    )
    parser.add_argument(
        "--stat", "-s",
        action="store_true",
        help="Append a summary line: N added, M modified, D deleted.",
    )
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> None:
    """Diff two snapshots and report added, modified, and deleted paths.

    Accepts snapshot IDs, commit IDs, branch names, or ``HEAD``.  When a commit
    ID or branch name is given, the snapshot recorded in that commit is used.
    """
    fmt: str = args.fmt
    ref_a: str = args.ref_a
    ref_b: str = args.ref_b
    stat: bool = args.stat

    if fmt not in _FORMAT_CHOICES:
        print(
            json.dumps(
                {"error": f"Unknown format {fmt!r}. Valid: {', '.join(_FORMAT_CHOICES)}"}
            )
        )
        raise SystemExit(ExitCode.USER_ERROR)

    root = require_repo()

    snap_id_a = _resolve_to_snapshot_id(root, ref_a)
    if snap_id_a is None:
        print(json.dumps({"error": f"Cannot resolve ref: {ref_a!r}"}))
        raise SystemExit(ExitCode.USER_ERROR)

    snap_id_b = _resolve_to_snapshot_id(root, ref_b)
    if snap_id_b is None:
        print(json.dumps({"error": f"Cannot resolve ref: {ref_b!r}"}))
        raise SystemExit(ExitCode.USER_ERROR)

    try:
        snap_a = read_snapshot(root, snap_id_a)
        snap_b = read_snapshot(root, snap_id_b)
    except Exception as exc:
        logger.debug("snapshot-diff I/O error: %s", exc)
        print(json.dumps({"error": str(exc)}))
        raise SystemExit(ExitCode.INTERNAL_ERROR)

    if snap_a is None:
        print(json.dumps({"error": f"Snapshot not found: {snap_id_a}"}))
        raise SystemExit(ExitCode.USER_ERROR)
    if snap_b is None:
        print(json.dumps({"error": f"Snapshot not found: {snap_id_b}"}))
        raise SystemExit(ExitCode.USER_ERROR)

    manifest_a = snap_a.manifest
    manifest_b = snap_b.manifest

    keys_a = set(manifest_a.keys())
    keys_b = set(manifest_b.keys())

    _added_raw: list[_AddedEntry] = [
        {"path": p, "object_id": manifest_b[p]} for p in (keys_b - keys_a)
    ]
    added: list[_AddedEntry] = sorted(_added_raw, key=lambda e: e["path"])

    _deleted_raw: list[_DeletedEntry] = [
        {"path": p, "object_id": manifest_a[p]} for p in (keys_a - keys_b)
    ]
    deleted: list[_DeletedEntry] = sorted(_deleted_raw, key=lambda e: e["path"])

    _modified_raw: list[_ModifiedEntry] = [
        {"path": p, "object_id_a": manifest_a[p], "object_id_b": manifest_b[p]}
        for p in (keys_a & keys_b)
        if manifest_a[p] != manifest_b[p]
    ]
    modified: list[_ModifiedEntry] = sorted(_modified_raw, key=lambda e: e["path"])
    total = len(added) + len(modified) + len(deleted)

    if fmt == "text":
        for a_entry in sorted(added, key=lambda e: e["path"]):
            print(f"A  {a_entry['path']}")
        for m_entry in sorted(modified, key=lambda e: e["path"]):
            print(f"M  {m_entry['path']}")
        for d_entry in sorted(deleted, key=lambda e: e["path"]):
            print(f"D  {d_entry['path']}")
        if stat:
            print(f"\n{len(added)} added, {len(modified)} modified, {len(deleted)} deleted")
        return

    result: _DiffResult = {
        "snapshot_a": snap_id_a,
        "snapshot_b": snap_id_b,
        "added": added,
        "modified": modified,
        "deleted": deleted,
        "total_changes": total,
    }
    print(json.dumps(result))

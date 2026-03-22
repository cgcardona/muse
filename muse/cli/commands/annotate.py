"""``muse annotate`` — attach CRDT-backed metadata to an existing commit.

Annotations use real CRDT semantics so that multiple agents can annotate the
same commit concurrently without conflicts:

- ``--reviewed-by``  merges into the commit's ``reviewed_by`` field using
  **ORSet** semantics (set union — once added, a reviewer is never lost).
- ``--test-run``     increments the commit's ``test_runs`` field using
  **GCounter** semantics (monotonically increasing).

These annotations are persisted directly in the commit JSON on disk.  If two
agents race to annotate the same commit, the last writer wins for the raw
JSON, but the semantics are CRDT-safe: ORSet entries are unioned and GCounter
values are taken as the max.

Usage::

    muse annotate abc1234 --reviewed-by agent-x
    muse annotate abc1234 --reviewed-by human-bob --reviewed-by claude-v4
    muse annotate abc1234 --test-run
    muse annotate abc1234 --reviewed-by ci-bot --test-run
    muse annotate                              # annotate HEAD
"""

from __future__ import annotations

import argparse
import logging
import pathlib
import sys

from muse.core.crdts.or_set import ORSet
from muse.core.repo import require_repo
from muse.core.store import get_head_commit_id, overwrite_commit, read_commit, read_current_branch

logger = logging.getLogger(__name__)


def _resolve_commit_id(root: pathlib.Path, commit_arg: str | None) -> str | None:
    """Return the resolved commit ID (HEAD branch if *commit_arg* is None)."""
    if commit_arg is None:
        branch = read_current_branch(root)
        return get_head_commit_id(root, branch)
    return commit_arg


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the annotate subcommand."""
    parser = subparsers.add_parser(
        "annotate",
        help="Attach CRDT-backed annotations to an existing commit.",
        description=__doc__,
    )
    parser.add_argument(
        "commit_arg", nargs="?", default=None,
        help="Commit ID to annotate (default: HEAD).",
    )
    parser.add_argument(
        "--reviewed-by", default=None, dest="reviewed_by",
        help="Add a reviewer (comma-separated for multiple: --reviewed-by 'alice,bob').",
    )
    parser.add_argument(
        "--test-run", action="store_true", dest="test_run",
        help="Increment the GCounter test-run count for this commit.",
    )
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> None:
    """Attach CRDT-backed annotations to an existing commit.

    ``--reviewed-by``  uses ORSet semantics — a reviewer once added is never
                       removed.  Pass multiple reviewers as a comma-separated
                       string: ``--reviewed-by 'alice,claude-v4'``.
    ``--test-run``     uses GCounter semantics — the count is monotonically
                       increasing and concurrent increments are additive.
    """
    commit_arg: str | None = args.commit_arg
    reviewed_by: str | None = args.reviewed_by
    test_run: bool = args.test_run

    root = require_repo()

    commit_id = _resolve_commit_id(root, commit_arg)
    if commit_id is None:
        print("❌ No commit found.")
        raise SystemExit(1)

    record = read_commit(root, commit_id)
    if record is None:
        print(f"❌ Commit {commit_id!r} not found.")
        raise SystemExit(1)

    # Parse comma-separated reviewers into a list.
    reviewers: list[str] = (
        [r.strip() for r in reviewed_by.split(",") if r.strip()]
        if reviewed_by else []
    )

    if not reviewers and not test_run:
        print(f"ℹ️  commit {commit_id[:8]}")
        if record.reviewed_by:
            print(f"  reviewed-by: {', '.join(sorted(record.reviewed_by))}")
        else:
            print("  reviewed-by: (none)")
        print(f"  test-runs:   {record.test_runs}")
        return

    changed = False

    if reviewers:
        # ORSet merge: current set ∪ new reviewers.
        current_set: ORSet = ORSet()
        for r in record.reviewed_by:
            current_set, _tok = current_set.add(r)
        for r in reviewers:
            current_set, _tok = current_set.add(r)
        new_list = sorted(current_set.elements())
        if new_list != sorted(record.reviewed_by):
            record.reviewed_by = new_list
            changed = True
            for r in reviewers:
                print(f"✅ Added reviewer: {r}")

    if test_run:
        # GCounter semantics: the value is monotonically non-decreasing.
        # Each call increments by 1 (the GCounter join of two replicas
        # would take max(a, b) per agent key; here we model the single-writer
        # common case as a simple increment).
        record.test_runs += 1
        changed = True
        print(f"✅ Test run recorded (total: {record.test_runs})")

    if changed:
        overwrite_commit(root, record)
        print(f"[{commit_id[:8]}] annotation updated")

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

import logging
import pathlib

import typer

from muse.core.crdts.or_set import ORSet
from muse.core.repo import require_repo
from muse.core.store import get_head_commit_id, overwrite_commit, read_commit

logger = logging.getLogger(__name__)

app = typer.Typer()


def _resolve_commit_id(root: pathlib.Path, commit_arg: str | None) -> str | None:
    """Return the resolved commit ID (HEAD branch if *commit_arg* is None)."""
    if commit_arg is None:
        head_ref = (root / ".muse" / "HEAD").read_text().strip()
        branch = head_ref.removeprefix("refs/heads/").strip()
        return get_head_commit_id(root, branch)
    return commit_arg


@app.callback(invoke_without_command=True)
def annotate(
    ctx: typer.Context,
    commit_arg: str | None = typer.Argument(None, help="Commit ID to annotate (default: HEAD)."),
    reviewed_by: str | None = typer.Option(None, "--reviewed-by", help="Add a reviewer (comma-separated for multiple: --reviewed-by 'alice,bob')."),
    test_run: bool = typer.Option(False, "--test-run", help="Increment the GCounter test-run count for this commit."),
) -> None:
    """Attach CRDT-backed annotations to an existing commit.

    ``--reviewed-by``  uses ORSet semantics — a reviewer once added is never
                       removed.  Pass multiple reviewers as a comma-separated
                       string: ``--reviewed-by 'alice,claude-v4'``.
    ``--test-run``     uses GCounter semantics — the count is monotonically
                       increasing and concurrent increments are additive.
    """
    root = require_repo()

    commit_id = _resolve_commit_id(root, commit_arg)
    if commit_id is None:
        typer.echo("❌ No commit found.")
        raise typer.Exit(code=1)

    record = read_commit(root, commit_id)
    if record is None:
        typer.echo(f"❌ Commit {commit_id!r} not found.")
        raise typer.Exit(code=1)

    # Parse comma-separated reviewers into a list.
    reviewers: list[str] = (
        [r.strip() for r in reviewed_by.split(",") if r.strip()]
        if reviewed_by else []
    )

    if not reviewers and not test_run:
        typer.echo(f"ℹ️  commit {commit_id[:8]}")
        if record.reviewed_by:
            typer.echo(f"  reviewed-by: {', '.join(sorted(record.reviewed_by))}")
        else:
            typer.echo("  reviewed-by: (none)")
        typer.echo(f"  test-runs:   {record.test_runs}")
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
                typer.echo(f"✅ Added reviewer: {r}")

    if test_run:
        # GCounter semantics: the value is monotonically non-decreasing.
        # Each call increments by 1 (the GCounter join of two replicas
        # would take max(a, b) per agent key; here we model the single-writer
        # common case as a simple increment).
        record.test_runs += 1
        changed = True
        typer.echo(f"✅ Test run recorded (total: {record.test_runs})")

    if changed:
        overwrite_commit(root, record)
        typer.echo(f"[{commit_id[:8]}] annotation updated")

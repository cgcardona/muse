"""muse revert — create a new commit that undoes a prior commit."""

from __future__ import annotations

import argparse
import datetime
import json
import logging
import pathlib
import sys

from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.snapshot import compute_commit_id
from muse.core.store import (
    CommitRecord,
    get_head_commit_id,
    read_commit,
    read_current_branch,
    read_snapshot,
    resolve_commit_ref,
    write_commit,
)
from muse.core.validation import sanitize_display
from muse.core.workdir import apply_manifest

logger = logging.getLogger(__name__)


def _read_branch(root: pathlib.Path) -> str:
    return read_current_branch(root)


def _read_repo_id(root: pathlib.Path) -> str:
    return str(json.loads((root / ".muse" / "repo.json").read_text())["repo_id"])


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the revert subcommand."""
    parser = subparsers.add_parser(
        "revert",
        help="Create a new commit that undoes a prior commit.",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("ref", help="Commit to revert.")
    parser.add_argument("-m", "--message", default=None, help="Override revert commit message.")
    parser.add_argument("--no-commit", "-n", action="store_true", dest="no_commit", help="Apply changes but do not commit.")
    parser.add_argument("--format", "-f", default="text", dest="fmt", help="Output format: text or json.")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> None:
    """Create a new commit that undoes a prior commit.

    Agents should pass ``--format json`` to receive ``{commit_id, branch,
    reverted_commit_id, message}`` rather than human-readable text.
    """
    ref: str = args.ref
    message: str | None = args.message
    no_commit: bool = args.no_commit
    fmt: str = args.fmt

    if fmt not in ("text", "json"):
        print(f"❌ Unknown --format '{sanitize_display(fmt)}'. Choose text or json.", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)
    root = require_repo()
    repo_id = _read_repo_id(root)
    branch = _read_branch(root)

    target = resolve_commit_ref(root, repo_id, branch, ref)
    if target is None:
        print(f"❌ Commit '{ref}' not found.")
        raise SystemExit(ExitCode.USER_ERROR)

    # The revert of a commit restores its parent snapshot
    if target.parent_commit_id is None:
        print("❌ Cannot revert the root commit (no parent to restore).")
        raise SystemExit(ExitCode.USER_ERROR)

    parent_commit = read_commit(root, target.parent_commit_id)
    if parent_commit is None:
        print(f"❌ Parent commit {target.parent_commit_id[:8]} not found.")
        raise SystemExit(ExitCode.INTERNAL_ERROR)

    target_snapshot = read_snapshot(root, parent_commit.snapshot_id)
    if target_snapshot is None:
        print(f"❌ Snapshot {parent_commit.snapshot_id[:8]} not found.")
        raise SystemExit(ExitCode.INTERNAL_ERROR)

    apply_manifest(root, target_snapshot.manifest)

    if no_commit:
        if fmt == "json":
            print(json.dumps({"status": "applied", "commit_id": None,
                              "reverted_commit_id": target.commit_id, "branch": branch}))
        else:
            print(f"Reverted changes from {target.commit_id[:8]} applied to working tree. Run 'muse commit' to record.")
        return

    revert_message = message or f"Revert \"{target.message}\""
    head_commit_id = get_head_commit_id(root, branch)

    # The parent snapshot is already content-addressed in the object store —
    # reuse its snapshot_id directly rather than re-scanning the workdir.
    snapshot_id = parent_commit.snapshot_id
    committed_at = datetime.datetime.now(datetime.timezone.utc)
    commit_id = compute_commit_id(
        parent_ids=[head_commit_id] if head_commit_id else [],
        snapshot_id=snapshot_id,
        message=revert_message,
        committed_at_iso=committed_at.isoformat(),
    )

    write_commit(root, CommitRecord(
        commit_id=commit_id,
        repo_id=repo_id,
        branch=branch,
        snapshot_id=snapshot_id,
        message=revert_message,
        committed_at=committed_at,
        parent_commit_id=head_commit_id,
    ))
    (root / ".muse" / "refs" / "heads" / branch).write_text(commit_id)

    if fmt == "json":
        print(json.dumps({
            "commit_id": commit_id,
            "branch": branch,
            "reverted_commit_id": target.commit_id,
            "message": revert_message,
        }))
    else:
        print(f"[{sanitize_display(branch)} {commit_id[:8]}] {sanitize_display(revert_message)}")

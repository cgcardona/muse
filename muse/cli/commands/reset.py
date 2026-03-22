"""muse reset — move HEAD to a prior commit.

Modes::

    --soft   — move the branch pointer only; working tree is untouched.
    --hard   — move the branch pointer AND restore the working tree from the target snapshot.
"""

from __future__ import annotations

import argparse
import json
import logging
import pathlib
import sys

from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.store import read_current_branch, read_snapshot, resolve_commit_ref
from muse.core.reflog import append_reflog
from muse.core.validation import sanitize_display, validate_branch_name
from muse.core.workdir import apply_manifest

logger = logging.getLogger(__name__)


def _read_branch(root: pathlib.Path) -> str:
    return read_current_branch(root)


def _read_repo_id(root: pathlib.Path) -> str:
    return str(json.loads((root / ".muse" / "repo.json").read_text())["repo_id"])


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the reset subcommand."""
    parser = subparsers.add_parser(
        "reset",
        help="Move HEAD to a prior commit.",
        description=__doc__,
    )
    parser.add_argument("ref", help="Commit ID or branch to reset to.")
    parser.add_argument("--hard", action="store_true", help="Reset branch pointer AND restore state/.")
    parser.add_argument("--soft", action="store_true", help="Reset branch pointer only (default).")
    parser.add_argument("--format", "-f", default="text", dest="fmt", help="Output format: text or json.")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> None:
    """Move HEAD to a prior commit.

    Agents should pass ``--format json`` to receive ``{branch, old_commit_id,
    new_commit_id, mode}`` rather than human-readable text.
    """
    ref: str = args.ref
    hard: bool = args.hard
    soft: bool = args.soft
    fmt: str = args.fmt

    if fmt not in ("text", "json"):
        print(f"❌ Unknown --format '{sanitize_display(fmt)}'. Choose text or json.", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)
    root = require_repo()
    repo_id = _read_repo_id(root)
    branch = _read_branch(root)

    commit = resolve_commit_ref(root, repo_id, branch, ref)
    if commit is None:
        print(f"❌ '{ref}' not found.")
        raise SystemExit(ExitCode.USER_ERROR)

    try:
        validate_branch_name(branch)
    except ValueError as exc:
        print(f"❌ Current branch name is invalid: {exc}")
        raise SystemExit(ExitCode.INTERNAL_ERROR)
    ref_file = root / ".muse" / "refs" / "heads" / branch
    old_commit_id = ref_file.read_text().strip() if ref_file.exists() else None
    ref_file.write_text(commit.commit_id)

    mode = "hard" if hard else "soft"
    append_reflog(
        root, branch, old_id=old_commit_id, new_id=commit.commit_id,
        author="user",
        operation=f"reset ({mode}): moving to {commit.commit_id[:12]}",
    )

    if hard:
        snapshot = read_snapshot(root, commit.snapshot_id)
        if snapshot is None:
            print(f"❌ Snapshot {commit.snapshot_id[:8]} not found in object store.")
            raise SystemExit(ExitCode.INTERNAL_ERROR)
        apply_manifest(root, snapshot.manifest)

    if fmt == "json":
        print(json.dumps({
            "branch": branch,
            "old_commit_id": old_commit_id,
            "new_commit_id": commit.commit_id,
            "mode": mode,
        }))
    elif hard:
        print(f"HEAD is now at {commit.commit_id[:8]} {sanitize_display(commit.message)}")
    else:
        print(f"Moved {sanitize_display(branch)} to {commit.commit_id[:8]}")

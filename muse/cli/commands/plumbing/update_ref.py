"""muse plumbing update-ref — move a branch HEAD to a specific commit.

Directly writes a branch reference file under ``.muse/refs/heads/``.  This is
the lowest-level way to advance or rewind a branch without any merge logic.

Analogous to ``git update-ref``.  Porcelain commands (``muse commit``,
``muse merge``, ``muse reset``) call this internally after computing the new
commit ID.

Output::

    {"branch": "main", "commit_id": "<sha256>", "previous": "<sha256> | null"}

Plumbing contract
-----------------

- Exit 0: ref updated.
- Exit 1: commit not found in the store, invalid commit ID format, or
  ``--delete`` on a non-existent ref.
- Exit 3: file write failure.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.store import get_head_commit_id, read_commit
from muse.core.validation import validate_branch_name, validate_object_id

logger = logging.getLogger(__name__)

_FORMAT_CHOICES = ("json", "text")


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the update-ref subcommand."""
    parser = subparsers.add_parser(
        "update-ref",
        help="Move a branch HEAD to a specific commit ID.",
        description=__doc__,
    )
    parser.add_argument(
        "branch",
        help="Branch name to update.",
    )
    parser.add_argument(
        "commit_id",
        nargs="?",
        default=None,
        help="Commit ID to point the branch at. Omit with --delete to remove the branch.",
    )
    parser.add_argument(
        "--delete", "-d",
        action="store_true",
        help="Delete the branch ref entirely.",
    )
    parser.add_argument(
        "--no-verify",
        dest="verify",
        action="store_false",
        help="Skip verifying the commit exists before updating.",
    )
    parser.add_argument(
        "--format", "-f",
        dest="fmt",
        default="json",
        metavar="FORMAT",
        help="Output format: json (default) or text (silent on success).",
    )
    parser.set_defaults(func=run, verify=True)


def run(args: argparse.Namespace) -> None:
    """Move a branch HEAD to a specific commit ID.

    Directly writes (or deletes) a branch ref file.  When ``--verify`` is set
    (the default), the commit must already exist in ``.muse/commits/``.
    Pass ``--no-verify`` to write the ref even if the commit is not yet in
    the local store (e.g. after ``muse plumbing unpack-objects``).

    Output (``--format json``, default)::

        {"branch": "main", "commit_id": "<sha256>", "previous": "<sha256> | null"}

    Output (``--format text``)::

        (silent on success — exits 0)
    """
    fmt: str = args.fmt
    branch: str = args.branch
    commit_id: str | None = args.commit_id
    delete: bool = args.delete
    verify: bool = args.verify

    if fmt not in _FORMAT_CHOICES:
        print(
            json.dumps({"error": f"Unknown format {fmt!r}. Valid: {', '.join(_FORMAT_CHOICES)}"})
        )
        raise SystemExit(ExitCode.USER_ERROR)

    root = require_repo()

    try:
        validate_branch_name(branch)
    except ValueError as exc:
        print(json.dumps({"error": f"Invalid branch name: {exc}"}))
        raise SystemExit(ExitCode.USER_ERROR)

    ref_path = root / ".muse" / "refs" / "heads" / branch

    if delete:
        if not ref_path.exists():
            print(json.dumps({"error": f"Branch ref does not exist: {branch}"}))
            raise SystemExit(ExitCode.USER_ERROR)
        ref_path.unlink()
        if fmt == "json":
            print(json.dumps({"branch": branch, "deleted": True}))
        return

    if commit_id is None:
        print(json.dumps({"error": "commit_id is required unless --delete is used."}))
        raise SystemExit(ExitCode.USER_ERROR)

    # Always validate the format — writing a malformed ID to a ref file would
    # silently corrupt the repository regardless of the --verify flag.
    try:
        validate_object_id(commit_id)
    except ValueError as exc:
        print(json.dumps({"error": f"Invalid commit ID: {exc}"}))
        raise SystemExit(ExitCode.USER_ERROR)

    if verify and read_commit(root, commit_id) is None:
        print(json.dumps({"error": f"Commit not found in store: {commit_id}"}))
        raise SystemExit(ExitCode.USER_ERROR)

    previous = get_head_commit_id(root, branch)
    try:
        ref_path.parent.mkdir(parents=True, exist_ok=True)
        ref_path.write_text(commit_id, encoding="utf-8")
    except OSError as exc:
        print(json.dumps({"error": str(exc)}))
        raise SystemExit(ExitCode.INTERNAL_ERROR)

    if fmt == "json":
        print(
            json.dumps(
                {
                    "branch": branch,
                    "commit_id": commit_id,
                    "previous": previous,
                }
            )
        )

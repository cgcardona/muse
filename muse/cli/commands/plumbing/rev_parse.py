"""muse plumbing rev-parse — resolve a ref to a full commit ID.

Resolves a branch name, ``HEAD``, or an abbreviated SHA prefix to the full
64-character SHA-256 commit ID.

Output (JSON, default)::

    {"ref": "main", "commit_id": "<sha256>"}

Output (--format text)::

    <sha256>

Plumbing contract
-----------------

- Exit 0: ref resolved successfully.
- Exit 1: ref not found, ambiguous, or unknown --format value.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.store import (
    find_commits_by_prefix,
    get_head_commit_id,
    read_commit,
    read_current_branch,
)

logger = logging.getLogger(__name__)

_FORMAT_CHOICES = ("json", "text")


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the rev-parse subcommand."""
    parser = subparsers.add_parser(
        "rev-parse",
        help="Resolve branch/HEAD/SHA prefix → full commit_id.",
        description=__doc__,
    )
    parser.add_argument(
        "ref",
        help="Ref to resolve: branch name, 'HEAD', or commit ID prefix.",
    )
    parser.add_argument(
        "--format", "-f",
        dest="fmt",
        default="json",
        metavar="FORMAT",
        help="Output format: json or text. (default: json)",
    )
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> None:
    """Resolve a branch name, HEAD, or SHA prefix to a full commit ID.

    Analogous to ``git rev-parse``.  Useful for canonicalising refs in
    scripts and agent pipelines before passing them to other plumbing
    commands.
    """
    fmt: str = args.fmt
    ref: str = args.ref

    if fmt not in _FORMAT_CHOICES:
        print(
            f"❌ Unknown format {fmt!r}. Valid choices: {', '.join(_FORMAT_CHOICES)}",
            file=sys.stderr,
        )
        raise SystemExit(ExitCode.USER_ERROR)

    root = require_repo()

    commit_id: str | None = None

    if ref.upper() == "HEAD":
        branch = read_current_branch(root)
        commit_id = get_head_commit_id(root, branch)
        if commit_id is None:
            print(json.dumps({"ref": ref, "commit_id": None, "error": "HEAD has no commits"}))
            raise SystemExit(ExitCode.USER_ERROR)
    else:
        # Try as branch name first.
        candidate = get_head_commit_id(root, ref)
        if candidate is not None:
            commit_id = candidate
        else:
            # Try as full or abbreviated commit ID.
            if len(ref) == 64:
                record = read_commit(root, ref)
                if record is not None:
                    commit_id = record.commit_id
            else:
                matches = find_commits_by_prefix(root, ref)
                if len(matches) == 1:
                    commit_id = matches[0].commit_id
                elif len(matches) > 1:
                    print(
                        json.dumps(
                            {
                                "ref": ref,
                                "commit_id": None,
                                "error": "ambiguous",
                                "candidates": [m.commit_id for m in matches],
                            }
                        )
                    )
                    raise SystemExit(ExitCode.USER_ERROR)

    if commit_id is None:
        print(json.dumps({"ref": ref, "commit_id": None, "error": "not found"}))
        raise SystemExit(ExitCode.USER_ERROR)

    if fmt == "text":
        print(commit_id)
        return

    print(json.dumps({"ref": ref, "commit_id": commit_id}))

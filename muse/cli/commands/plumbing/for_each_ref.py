"""muse plumbing for-each-ref — iterate all refs with rich commit metadata.

Enumerates every branch ref and emits the full commit metadata it points to.
Supports sorting by any commit field and glob-pattern filtering so agent
pipelines can slice the ref list without post-processing.

Output (JSON, default)::

    {
      "refs": [
        {
          "ref":          "refs/heads/dev",
          "branch":       "dev",
          "commit_id":    "<sha256>",
          "author":       "gabriel",
          "message":      "Add verse melody",
          "committed_at": "2026-01-01T00:00:00+00:00",
          "snapshot_id":  "<sha256>"
        }
      ],
      "count": 1
    }

Text output (``--format text``)::

    <sha256>  refs/heads/dev  2026-01-01T00:00:00+00:00  gabriel

Plumbing contract
-----------------

- Exit 0: refs emitted (list may be empty).
- Exit 1: unknown ``--sort`` field; bad ``--format``.
- Exit 3: I/O error reading refs or commit records.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import logging
import pathlib
import sys
from typing import TypedDict

from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.store import read_commit

logger = logging.getLogger(__name__)

_FORMAT_CHOICES = ("json", "text")
_SORT_FIELDS = ("ref", "branch", "commit_id", "author", "committed_at", "message")


class _RefDetail(TypedDict):
    ref: str
    branch: str
    commit_id: str
    author: str
    message: str
    committed_at: str
    snapshot_id: str


class _ForEachRefResult(TypedDict):
    refs: list[_RefDetail]
    count: int


def _list_all_refs(root: pathlib.Path) -> list[tuple[str, str]]:
    """Return sorted (branch_name, commit_id) pairs from .muse/refs/heads/."""
    heads_dir = root / ".muse" / "refs" / "heads"
    if not heads_dir.exists():
        return []
    pairs: list[tuple[str, str]] = []
    for child in sorted(heads_dir.iterdir()):
        if not child.is_file():
            continue
        commit_id = child.read_text(encoding="utf-8").strip()
        if commit_id:
            pairs.append((child.name, commit_id))
    return pairs


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the for-each-ref subcommand."""
    parser = subparsers.add_parser(
        "for-each-ref",
        help="Iterate all refs with rich commit metadata.",
        description=__doc__,
    )
    parser.add_argument(
        "--pattern", "-p",
        default="",
        dest="pattern",
        metavar="GLOB",
        help="fnmatch glob filter applied to the full ref name (e.g. 'refs/heads/feat/*').",
    )
    parser.add_argument(
        "--sort", "-s",
        default="ref",
        dest="sort_by",
        metavar="FIELD",
        help=f"Field to sort by. One of: {', '.join(_SORT_FIELDS)}. (default: ref)",
    )
    parser.add_argument(
        "--desc", "-d",
        action="store_true",
        dest="descending",
        help="Reverse the sort order (descending).",
    )
    parser.add_argument(
        "--count", "-n",
        type=int,
        default=0,
        dest="count_limit",
        metavar="N",
        help="Limit output to the first N refs after sorting (0 = unlimited).",
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
    """Iterate all branch refs with full commit metadata.

    Emits each branch ref together with the commit it points to, including
    the author, message, timestamp, and snapshot ID.
    """
    fmt: str = args.fmt
    pattern: str = args.pattern
    sort_by: str = args.sort_by
    descending: bool = args.descending
    count_limit: int = args.count_limit

    if fmt not in _FORMAT_CHOICES:
        print(
            json.dumps(
                {"error": f"Unknown format {fmt!r}. Valid: {', '.join(_FORMAT_CHOICES)}"}
            )
        )
        raise SystemExit(ExitCode.USER_ERROR)

    if sort_by not in _SORT_FIELDS:
        print(
            json.dumps(
                {
                    "error": f"Unknown sort field {sort_by!r}. "
                    f"Valid: {', '.join(_SORT_FIELDS)}"
                }
            )
        )
        raise SystemExit(ExitCode.USER_ERROR)

    root = require_repo()

    try:
        pairs = _list_all_refs(root)
    except OSError as exc:
        logger.debug("for-each-ref I/O error listing refs: %s", exc)
        print(json.dumps({"error": str(exc)}))
        raise SystemExit(ExitCode.INTERNAL_ERROR)

    # Apply glob filter.
    if pattern:
        pairs = [(b, c) for b, c in pairs if fnmatch.fnmatch(f"refs/heads/{b}", pattern)]

    # Build detailed ref list (read each commit once).
    details: list[_RefDetail] = []
    for branch, commit_id in pairs:
        try:
            record = read_commit(root, commit_id)
        except Exception as exc:
            logger.debug("for-each-ref: cannot read commit %s: %s", commit_id[:12], exc)
            record = None

        if record is None:
            details.append(
                _RefDetail(
                    ref=f"refs/heads/{branch}",
                    branch=branch,
                    commit_id=commit_id,
                    author="",
                    message="(commit record missing)",
                    committed_at="",
                    snapshot_id="",
                )
            )
        else:
            details.append(
                _RefDetail(
                    ref=f"refs/heads/{branch}",
                    branch=branch,
                    commit_id=commit_id,
                    author=record.author,
                    message=record.message,
                    committed_at=record.committed_at.isoformat(),
                    snapshot_id=record.snapshot_id,
                )
            )

    # Sort — explicit dispatcher avoids TypedDict literal-key constraint.
    def _sort_key(d: _RefDetail) -> str:
        if sort_by == "branch":
            return d["branch"]
        if sort_by == "commit_id":
            return d["commit_id"]
        if sort_by == "author":
            return d["author"]
        if sort_by == "committed_at":
            return d["committed_at"]
        if sort_by == "message":
            return d["message"]
        return d["ref"]

    details.sort(key=_sort_key, reverse=descending)

    # Limit.
    if count_limit > 0:
        details = details[:count_limit]

    if fmt == "text":
        for d in details:
            print(f"{d['commit_id']}  {d['ref']}  {d['committed_at']}  {d['author']}")
        return

    result: _ForEachRefResult = {"refs": details, "count": len(details)}
    print(json.dumps(result))

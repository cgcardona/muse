"""``muse describe`` — label a commit by its nearest tag and hop distance.

Walks backward from a commit (default: HEAD) through the ancestry graph and
finds the nearest tag.  The output is ``<tag>~N`` where N is the number of
hops from the tag to the commit.  N=0 means the commit is exactly on the tag
and the ``~0`` suffix is omitted (bare tag name).

This is the porcelain equivalent of ``git describe`` — useful for generating
human-readable release labels in CI, changelogs, and agent pipelines.

Usage::

    muse describe                      # describe HEAD
    muse describe --ref feat/audio     # describe the tip of a branch
    muse describe --long               # always show distance + SHA
    muse describe --format json        # machine-readable output

Exit codes::

    0 — description produced
    1 — ref not found, or no tags exist in the repository
"""

from __future__ import annotations

import argparse
import json
import logging
import pathlib
import sys


from muse.core.describe import describe_commit
from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.store import get_head_commit_id, read_current_branch, resolve_commit_ref
from muse.core.validation import sanitize_display

logger = logging.getLogger(__name__)


def _read_repo_id(root: pathlib.Path) -> str:
    return str(json.loads((root / ".muse" / "repo.json").read_text(encoding="utf-8"))["repo_id"])


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the describe subcommand."""
    parser = subparsers.add_parser(
        "describe",
        help="Label a commit by its nearest tag and hop distance.",
        description=__doc__,
    )
    parser.add_argument(
        "--ref", default=None,
        help="Commit ref (SHA, branch, tag) to describe (default: HEAD).",
    )
    parser.add_argument(
        "--long", action="store_true", dest="long_format",
        help="Always show distance + SHA even when on an exact tag.",
    )
    parser.add_argument(
        "--require-tag", action="store_true", dest="require_tag",
        help="Exit 1 if no tags exist in the ancestry.",
    )
    parser.add_argument(
        "--format", "-f", default="text", dest="fmt",
        help="Output format: text or json.",
    )
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> None:
    """Label a commit by its nearest tag and hop distance.

    Walks backward from the commit's ancestry until it finds the nearest tag.
    The result is ``<tag>~N`` for N hops, or just ``<tag>`` when N=0.  Falls
    back to the 12-character short SHA when no tag is reachable.

    Examples::

        muse describe                       # → v1.0.0~3-gabc123456789
        muse describe --ref v1.0.0          # → v1.0.0  (on the tag itself)
        muse describe --long                # → v1.0.0-0-gabc123456789
        muse describe --require-tag         # → exit 1 if no tags exist
        muse describe --format json         # machine-readable
    """
    ref: str | None = args.ref
    long_format: bool = args.long_format
    require_tag: bool = args.require_tag
    fmt: str = args.fmt

    if fmt not in {"json", "text"}:
        print(f"❌ Unknown --format '{sanitize_display(fmt)}'. Choose json or text.", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)

    root = require_repo()
    repo_id = _read_repo_id(root)
    branch = read_current_branch(root)

    if ref is None:
        commit_id = get_head_commit_id(root, branch)
        if commit_id is None:
            print("❌ No commits on current branch.", file=sys.stderr)
            raise SystemExit(ExitCode.USER_ERROR)
    else:
        commit_rec = resolve_commit_ref(root, repo_id, branch, ref)
        if commit_rec is None:
            print(f"❌ Ref '{sanitize_display(ref)}' not found.", file=sys.stderr)
            raise SystemExit(ExitCode.USER_ERROR)
        commit_id = commit_rec.commit_id

    result = describe_commit(root, repo_id, commit_id, long_format=long_format)

    if require_tag and result["tag"] is None:
        print(
            f"❌ No tags found in the ancestry of {commit_id[:12]}.", file=sys.stderr
        )
        raise SystemExit(ExitCode.USER_ERROR)

    if fmt == "json":
        print(json.dumps({
            "commit_id": result["commit_id"],
            "tag": result["tag"],
            "distance": result["distance"],
            "short_sha": result["short_sha"],
            "name": result["name"],
        }))
    else:
        print(result["name"])

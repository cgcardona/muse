"""``muse shortlog`` — commit summary grouped by author or agent.

Groups the commit history by author (humans) or agent_id (agents), counts
commits per group, and optionally lists commit messages under each.  Useful
for changelogs, release notes, and auditing agent contribution.

Muse's rich commit metadata — ``author``, ``agent_id``, ``model_id`` — makes
shortlog especially expressive: you can see exactly which human or which agent
class contributed each set of commits.

Usage::

    muse shortlog                  # current branch, group by author
    muse shortlog --all            # all branches
    muse shortlog --numbered       # sort by commit count (most active first)
    muse shortlog --format json    # machine-readable

Exit codes::

    0 — output produced (even if empty)
    1 — branch not found or ref invalid
"""

from __future__ import annotations

import argparse
import sys

import json
import logging
import pathlib
from collections import defaultdict


from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.store import (
    CommitRecord,
    get_commits_for_branch,
    get_head_commit_id,
    read_current_branch,
)
from muse.core.validation import sanitize_display

logger = logging.getLogger(__name__)


def _read_repo_id(root: pathlib.Path) -> str:
    return str(json.loads((root / ".muse" / "repo.json").read_text(encoding="utf-8"))["repo_id"])


def _branch_names(root: pathlib.Path) -> list[str]:
    heads_dir = root / ".muse" / "refs" / "heads"
    if not heads_dir.exists():
        return []
    branches: list[str] = []
    for ref_file in sorted(heads_dir.rglob("*")):
        if ref_file.is_file():
            branches.append(str(ref_file.relative_to(heads_dir).as_posix()))
    return branches


def _author_key(commit: CommitRecord) -> str:
    """Return the display key for grouping: prefer author, fall back to agent_id."""
    if commit.author:
        return commit.author
    if commit.agent_id:
        return f"{commit.agent_id} (agent)"
    return "(unknown)"


def _build_groups(
    commits: list[CommitRecord],
    *,
    by_email: bool,
) -> dict[str, list[CommitRecord]]:
    groups: dict[str, list[CommitRecord]] = defaultdict(list)
    for c in commits:
        key = _author_key(c)
        if by_email and c.agent_id and c.agent_id != c.author:
            key = f"{key} <{c.agent_id}>"
        groups[key].append(c)
    return dict(groups)


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the shortlog subcommand."""
    parser = subparsers.add_parser(
        "shortlog",
        help='Summarise commit history grouped by author or agent.',
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "branch_opt",
        nargs="?",
        default=None,
        metavar="BRANCH",
        help="Branch to summarise (default: current branch).",
    )
    parser.add_argument(
        "--all",
        dest="all_branches",
        action="store_true",
        help="Include all branches.",
    )
    parser.add_argument(
        "--numbered", "-n",
        action="store_true",
        help="Sort by commit count (most active first).",
    )
    parser.add_argument(
        "--email",
        dest="by_email",
        action="store_true",
        help="Include agent_id alongside author.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        metavar="N",
        help="Cap the number of commits loaded (0 = no limit).",
    )
    parser.add_argument(
        "--format", "-f",
        dest="fmt",
        default="text",
        choices=["text", "json"],
        help="Output format: text (default) or json.",
    )
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> None:
    """Summarise commit history grouped by author or agent.

    Each group lists the author, commit count, and (in text mode) each commit
    message indented beneath.  Use ``--numbered`` to rank by activity.

    In agent pipelines, ``--format json`` returns structured data that can be
    piped to any downstream processor.

    Examples::

        muse shortlog                      # current branch
        muse shortlog --all --numbered     # all branches, ranked by count
        muse shortlog --email              # include agent_id
        muse shortlog --format json        # JSON for agent consumption
    """
    branch_opt: str | None = args.branch_opt
    all_branches: bool = args.all_branches
    numbered: bool = args.numbered
    by_email: bool = args.by_email
    limit: int = args.limit
    fmt: str = args.fmt

    if fmt not in {"text", "json"}:
        print(f"❌ Unknown --format '{sanitize_display(fmt)}'. Choose text or json.", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)

    root = require_repo()
    repo_id = _read_repo_id(root)

    branches: list[str]
    if all_branches:
        branches = _branch_names(root)
        if not branches:
            if fmt == "json":
                print("[]")
            else:
                print("No commits found.")
            return
    else:
        branches = [branch_opt or read_current_branch(root)]

    # Collect all commits across selected branches (deduplicated by commit_id).
    seen_ids: set[str] = set()
    all_commits: list[CommitRecord] = []
    for br in branches:
        branch_commits = get_commits_for_branch(root, repo_id, br)
        for c in branch_commits:
            if c.commit_id not in seen_ids:
                seen_ids.add(c.commit_id)
                all_commits.append(c)
        if limit and len(all_commits) >= limit:
            all_commits = all_commits[:limit]
            break

    if not all_commits:
        if fmt == "json":
            print("[]")
        else:
            print("No commits found.")
        return

    groups = _build_groups(all_commits, by_email=by_email)

    # Sort: by count descending (if --numbered), then alphabetically.
    sorted_keys: list[str]
    if numbered:
        sorted_keys = sorted(groups, key=lambda k: -len(groups[k]))
    else:
        sorted_keys = sorted(groups)

    if fmt == "json":
        output = [
            {
                "author": key,
                "count": len(groups[key]),
                "commits": [
                    {
                        "commit_id": c.commit_id,
                        "message": c.message,
                        "committed_at": c.committed_at.isoformat(),
                    }
                    for c in groups[key]
                ],
            }
            for key in sorted_keys
        ]
        print(json.dumps(output, indent=2))
    else:
        for key in sorted_keys:
            commits_in_group = groups[key]
            print(f"{sanitize_display(key)} ({len(commits_in_group)}):")
            for c in commits_in_group:
                print(f"      {sanitize_display(c.message)}")
            print("")

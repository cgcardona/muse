"""muse coupling — file co-change analysis.

Identifies files that change together most often.  High co-change frequency
between two files signals a hidden dependency — they are logically coupled
even if there is no explicit import between them.

This is structurally impossible in Git at the semantic level: Git could
count raw file modifications, but ``muse coupling`` counts only *semantic*
co-changes — commits where both files had AST-level symbol modifications,
not formatting-only edits (which Muse already separates from real changes).

Usage::

    muse coupling
    muse coupling --top 20
    muse coupling --from HEAD~30

Output::

    File co-change analysis — top 10 most coupled pairs
    Commits analysed: 47

      1   src/billing.py  ↔  src/models.py          co-changed in 18 commits
      2   src/api.py      ↔  src/auth.py             co-changed in 12 commits
      3   src/billing.py  ↔  tests/test_billing.py   co-changed in 11 commits

    High coupling = hidden dependency. Consider extracting a shared interface.
"""

from __future__ import annotations

import argparse
import json
import logging
import pathlib
import sys

from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.store import read_current_branch, resolve_commit_ref
from muse.plugins.code._query import file_pairs, touched_files, walk_commits_range

logger = logging.getLogger(__name__)


def _read_repo_id(root: pathlib.Path) -> str:
    return str(json.loads((root / ".muse" / "repo.json").read_text())["repo_id"])


def _read_branch(root: pathlib.Path) -> str:
    return read_current_branch(root)


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the coupling subcommand."""
    parser = subparsers.add_parser(
        "coupling",
        help="Find files that change together most often — hidden dependencies.",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--top", "-n", type=int, default=20, metavar="N",
        help="Number of pairs to show (default: 20).",
    )
    parser.add_argument(
        "--from", default=None, metavar="REF", dest="from_ref",
        help="Exclusive start of the commit range (default: initial commit).",
    )
    parser.add_argument(
        "--to", default=None, metavar="REF", dest="to_ref",
        help="Inclusive end of the commit range (default: HEAD).",
    )
    parser.add_argument(
        "--min", type=int, default=2, metavar="N", dest="min_count",
        help="Minimum co-change count to include in results (default: 2).",
    )
    parser.add_argument(
        "--json", action="store_true", dest="as_json",
        help="Emit results as JSON.",
    )
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> None:
    """Find files that change together most often — hidden dependencies.

    ``muse coupling`` identifies semantic co-change: file pairs that had
    AST-level symbol modifications in the same commit.  This is stricter
    than raw file co-change — formatting-only edits and non-code files
    are excluded.

    High coupling between two files means they share unstated dependencies.
    Consider extracting a shared interface, a common module, or an
    explicit contract between them.

    Use ``--from`` / ``--to`` to scope the analysis to a sprint or release.
    Use ``--min`` to raise the minimum co-change threshold.
    """
    top: int = args.top
    from_ref: str | None = args.from_ref
    to_ref: str | None = args.to_ref
    min_count: int = args.min_count
    as_json: bool = args.as_json

    root = require_repo()
    repo_id = _read_repo_id(root)
    branch = _read_branch(root)

    to_commit = resolve_commit_ref(root, repo_id, branch, to_ref)
    if to_commit is None:
        print(f"❌ Commit '{to_ref or 'HEAD'}' not found.", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)

    from_commit_id: str | None = None
    if from_ref is not None:
        from_commit = resolve_commit_ref(root, repo_id, branch, from_ref)
        if from_commit is None:
            print(f"❌ Commit '{from_ref}' not found.", file=sys.stderr)
            raise SystemExit(ExitCode.USER_ERROR)
        from_commit_id = from_commit.commit_id

    commits = walk_commits_range(root, to_commit.commit_id, from_commit_id)

    pair_counts: dict[tuple[str, str], int] = {}
    for commit in commits:
        if commit.structured_delta is None:
            continue
        files = touched_files(commit.structured_delta["ops"])
        if len(files) < 2:
            continue
        for a, b in file_pairs(files):
            key = (a, b)
            pair_counts[key] = pair_counts.get(key, 0) + 1

    filtered = {pair: cnt for pair, cnt in pair_counts.items() if cnt >= min_count}
    ranked = sorted(filtered.items(), key=lambda kv: kv[1], reverse=True)[:top]

    if as_json:
        print(json.dumps(
            {
                "commits_analysed": len(commits),
                "pairs": [{"file_a": a, "file_b": b, "co_changes": c} for (a, b), c in ranked],
            },
            indent=2,
        ))
        return

    print(f"\nFile co-change analysis — top {len(ranked)} most coupled pairs")
    print(f"Commits analysed: {len(commits)}")
    print("")

    if not ranked:
        print(f"  (no file pairs co-changed {min_count}+ times)")
        return

    width = len(str(len(ranked)))
    # Align the ↔ separator.
    max_a = max(len(a) for (a, _), _ in ranked)
    for rank, ((a, b), count) in enumerate(ranked, 1):
        label = "commit" if count == 1 else "commits"
        print(
            f"  {rank:>{width}}   {a:<{max_a}}  ↔  {b:<50}  "
            f"co-changed in {count:>3} {label}"
        )

    print("")
    print("High coupling = hidden dependency. Consider extracting a shared interface.")

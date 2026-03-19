"""Domain-agnostic commit-history query engine for Muse.

Any domain can walk the commit graph, evaluate a predicate per commit, and
collect structured matches — without reimplementing the graph-traversal loop.

Architecture
------------
::

    muse/core/query_engine.py            ← this file: generic history walker
    muse/plugins/midi/_midi_query.py     ← MIDI predicate evaluator
    muse/plugins/code/_code_query.py     ← code predicate evaluator
    muse/cli/commands/midi_query.py      ← CLI for MIDI query
    muse/cli/commands/code_query.py      ← CLI for code query

Usage pattern::

    from muse.core.query_engine import walk_history, QueryMatch

    def my_evaluator(
        commit: CommitRecord,
        manifest: dict[str, str],
        repo_root: pathlib.Path,
    ) -> list[QueryMatch]:
        matches = []
        if "interesting-file.py" in manifest:
            matches.append(QueryMatch(
                commit_id=commit.commit_id,
                author=commit.author,
                committed_at=commit.committed_at.isoformat(),
                branch=commit.branch,
                detail="found interesting-file.py",
                extra={},
            ))
        return matches

    results = walk_history(repo_root, branch="main", evaluator=my_evaluator)

Public API
----------
- :class:`QueryMatch`   — one result row from the evaluator.
- :class:`CommitEvaluator` — type alias for the evaluator callable.
- :func:`walk_history`  — traverse commits and collect matches.
"""
from __future__ import annotations

import logging
import pathlib
from collections.abc import Callable
from typing import TypedDict

from muse.core.store import CommitRecord, get_commit_snapshot_manifest, read_commit

logger = logging.getLogger(__name__)

_DEFAULT_MAX_COMMITS = 500


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


class QueryMatch(TypedDict, total=False):
    """One match returned by a predicate evaluator.

    Required fields:
    ``commit_id``    The commit that produced this match.
    ``author``       Commit author string.
    ``committed_at`` ISO-8601 timestamp string.
    ``branch``       Branch name.
    ``detail``       Short human-readable description of what matched.

    Optional:
    ``extra``        Domain-specific data (e.g. ``{"symbol": "my_fn"}``).
    ``agent_id``     Agent identity from commit provenance (if present).
    ``model_id``     Model ID from commit provenance (if present).
    """

    commit_id: str
    author: str
    committed_at: str
    branch: str
    detail: str
    extra: dict[str, str]
    agent_id: str
    model_id: str


# ---------------------------------------------------------------------------
# Evaluator type alias
# ---------------------------------------------------------------------------

#: Signature every domain evaluator must satisfy.
#: Returns a (possibly empty) list of :class:`QueryMatch` for the commit.
CommitEvaluator = Callable[
    [CommitRecord, dict[str, str], pathlib.Path],
    list[QueryMatch],
]


# ---------------------------------------------------------------------------
# Core history walker
# ---------------------------------------------------------------------------


def walk_history(
    repo_root: pathlib.Path,
    branch: str,
    evaluator: CommitEvaluator,
    *,
    max_commits: int = _DEFAULT_MAX_COMMITS,
    head_commit_id: str | None = None,
) -> list[QueryMatch]:
    """Walk the commit graph from HEAD and collect matches from *evaluator*.

    Traverses the linear commit chain (``parent_commit_id``) up to
    *max_commits* commits.  For each commit the evaluator receives the
    :class:`~muse.core.store.CommitRecord`, the raw file manifest (path →
    SHA-256 hash), and the repository root.  It returns a list of
    :class:`QueryMatch` dicts (empty list if the commit has no matches).

    The walk is breadth-first on the main parent chain (``parent_commit_id``),
    not a full DAG traversal, which is sufficient for the common single-branch
    query case.

    Args:
        repo_root:      Repository root containing ``.muse/``.
        branch:         Branch to start from (used to resolve HEAD when
                        *head_commit_id* is ``None``).
        evaluator:      Domain-specific callable — see :data:`CommitEvaluator`.
        max_commits:    Maximum commits to inspect.  Default 500.
        head_commit_id: Override the starting commit.  ``None`` → resolve HEAD
                        from the branch ref file.

    Returns:
        All :class:`QueryMatch` records collected, ordered newest-first.
    """
    if head_commit_id is None:
        ref_file = repo_root / ".muse" / "refs" / "heads" / branch
        if not ref_file.exists():
            logger.warning("Branch ref not found: %s", ref_file)
            return []
        head_commit_id = ref_file.read_text().strip()

    if not head_commit_id:
        return []

    results: list[QueryMatch] = []
    current_id: str | None = head_commit_id
    seen = 0

    while current_id and seen < max_commits:
        commit = read_commit(repo_root, current_id)
        if commit is None:
            break
        seen += 1

        manifest_rec = get_commit_snapshot_manifest(repo_root, current_id)
        manifest: dict[str, str] = dict(manifest_rec) if manifest_rec else {}

        try:
            matches = evaluator(commit, manifest, repo_root)
        except Exception:
            logger.exception("Evaluator error on commit %s", current_id)
            matches = []

        results.extend(matches)
        current_id = commit.parent_commit_id

    return results


def format_matches(matches: list[QueryMatch], *, max_results: int = 50) -> str:
    """Format a list of matches as a human-readable table.

    Args:
        matches:     The results from :func:`walk_history`.
        max_results: Maximum rows to show.

    Returns:
        Multi-line string ready for ``typer.echo()``.
    """
    if not matches:
        return "No matches found."

    lines: list[str] = [f"Found {len(matches)} match(es):\n"]
    for m in matches[:max_results]:
        cid = m.get("commit_id", "?")[:8]
        author = m.get("author", "unknown")
        ts = m.get("committed_at", "")[:10]
        detail = m.get("detail", "")
        agent = m.get("agent_id", "")
        agent_str = f" [{agent}]" if agent else ""
        lines.append(f"  {cid} {ts} {author}{agent_str} — {detail}")

    if len(matches) > max_results:
        lines.append(f"\n  … {len(matches) - max_results} more (use --max to show all)")

    return "\n".join(lines)

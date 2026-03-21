"""Tag-based commit description for ``muse describe``.

Walks backward from a commit through its ancestor graph and finds the nearest
tag.  Returns a human-readable ``<tag>~N`` label where N is the hop count from
the tag's commit to the described commit.  N=0 means the commit is exactly on
the tag.

The walk is a simple BFS that visits each ancestor at most once (cycle-safe).
It stops as soon as the first tag is found, so it is O(commits between tag and
HEAD) — not O(all commits).

If no tag is found, ``DescribeResult.tag`` is ``None`` and ``name`` falls back
to the short SHA.
"""

from __future__ import annotations

import logging
import pathlib
from collections import deque
from typing import TypedDict

from muse.core.store import get_all_tags, read_commit

logger = logging.getLogger(__name__)

_MAX_WALK = 50_000


class DescribeResult(TypedDict):
    """Result of describing a commit by its nearest tag."""

    commit_id: str
    tag: str | None
    distance: int
    short_sha: str
    name: str


def describe_commit(
    root: pathlib.Path,
    repo_id: str,
    commit_id: str,
    *,
    long_format: bool = False,
) -> DescribeResult:
    """Return a human-readable description of *commit_id*.

    Walks backward from *commit_id* through the parent chain using BFS and
    finds the nearest tag.  The description is ``<tag>~N`` where N is the
    number of hops from the tag's commit to *commit_id*.

    When *long_format* is ``True`` the name always includes the distance and
    short SHA even when N=0, matching Git's ``--long`` behaviour::

        v1.0.0-0-gabc12345     # long: on the tag itself
        v1.0.0~3-gabc12345     # long: 3 hops past the tag

    Args:
        root:        Repository root.
        repo_id:     Repository UUID (used to look up tags).
        commit_id:   Starting commit to describe (typically HEAD).
        long_format: Always include distance and short SHA in the name.

    Returns:
        A :class:`DescribeResult` with the nearest tag name, hop count, and
        formatted description string.
    """
    short_sha = commit_id[:12]

    # Build a set of tagged commit IDs keyed by commit_id → tag name.
    # When a commit has multiple tags we take the lexicographically last one
    # (consistent with Git's ``--tags`` behaviour which picks the most recent
    # annotated tag, or the highest-sorting tag for lightweight tags).
    all_tags = get_all_tags(root, repo_id)
    tag_by_commit: dict[str, str] = {}
    for t in all_tags:
        existing = tag_by_commit.get(t.commit_id)
        if existing is None or t.tag > existing:
            tag_by_commit[t.commit_id] = t.tag

    if not tag_by_commit:
        return DescribeResult(
            commit_id=commit_id,
            tag=None,
            distance=0,
            short_sha=short_sha,
            name=short_sha,
        )

    # BFS backward through parent chain.
    visited: set[str] = set()
    queue: deque[tuple[str, int]] = deque([(commit_id, 0)])

    while queue:
        cid, distance = queue.popleft()
        if cid in visited:
            continue
        visited.add(cid)

        if len(visited) > _MAX_WALK:
            logger.warning("⚠️ describe: exceeded %d-commit walk limit", _MAX_WALK)
            break

        if cid in tag_by_commit:
            tag_name = tag_by_commit[cid]
            if long_format:
                name = f"{tag_name}-{distance}-g{short_sha}"
            elif distance == 0:
                name = tag_name
            else:
                name = f"{tag_name}~{distance}"
            return DescribeResult(
                commit_id=commit_id,
                tag=tag_name,
                distance=distance,
                short_sha=short_sha,
                name=name,
            )

        commit = read_commit(root, cid)
        if commit is None:
            continue
        if commit.parent_commit_id:
            queue.append((commit.parent_commit_id, distance + 1))
        if commit.parent2_commit_id:
            queue.append((commit.parent2_commit_id, distance + 1))

    # No tag found in ancestry.
    return DescribeResult(
        commit_id=commit_id,
        tag=None,
        distance=0,
        short_sha=short_sha,
        name=short_sha,
    )

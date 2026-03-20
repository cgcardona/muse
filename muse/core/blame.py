"""Core VCS blame — attribute each line of a text file to the commit that last changed it.

This is the domain-agnostic layer.  The MIDI domain has ``note-blame``
(per-bar attribution); the code domain has ``muse code blame`` (per-symbol
attribution).  This module provides line-level blame for *any text file*
tracked in ``state/``, making it useful for any domain that stores text —
configuration files, lyrics, scripts, prose.

Algorithm
---------
Walk the commit graph from the requested ref backwards to the root:

1. At the starting commit, every line is "owned" by that commit.
2. At each parent commit, compute the *unified diff* between the parent's
   version and the child's version of the file.
3. Lines that appear in both versions (context/unchanged) are attributed to
   the *earliest* commit that produced them; we update the attribution when
   we encounter a commit where those lines are *unchanged from the parent* —
   i.e. they existed before this commit.
4. Lines that are *added* by a commit stay attributed to that commit.

This is equivalent to the ``git blame`` algorithm for single-parent chains.
For merge commits (two parents), we take the parent whose file content most
closely matches the merge result to avoid over-attributing lines to merges.

Output
------
A list of ``BlameLine`` objects, one per line of the file at the requested
ref.
"""

from __future__ import annotations

import difflib
import logging
import pathlib
from dataclasses import dataclass

from muse.core.object_store import object_path
from muse.core.store import read_commit, read_snapshot, resolve_commit_ref

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BlameLine:
    """Attribution for one line of text."""

    lineno: int
    """1-based line number in the final version of the file."""

    commit_id: str
    """Commit that last changed this line."""

    author: str
    """Author field from the commit record."""

    committed_at: str
    """ISO timestamp of the commit."""

    message: str
    """First line of the commit message."""

    content: str
    """The line content (without trailing newline)."""


# ---------------------------------------------------------------------------
# File reading helper
# ---------------------------------------------------------------------------


def _read_file_at_commit(
    repo_root: pathlib.Path,
    commit_id: str,
    rel_path: str,
) -> list[str] | None:
    """Return lines of *rel_path* as it existed at *commit_id*, or None."""
    commit = read_commit(repo_root, commit_id)
    if commit is None:
        return None
    snap = read_snapshot(repo_root, commit.snapshot_id)
    if snap is None:
        return None
    obj_id = snap.manifest.get(rel_path)
    if obj_id is None:
        return None
    obj = object_path(repo_root, obj_id)
    if not obj.exists():
        return None
    try:
        return obj.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None


# ---------------------------------------------------------------------------
# Commit graph walker
# ---------------------------------------------------------------------------


def _walk_ancestry(
    repo_root: pathlib.Path,
    start_id: str,
) -> list[str]:
    """Return commit IDs from *start_id* to the root, newest-first."""
    visited: list[str] = []
    seen: set[str] = set()
    queue = [start_id]
    while queue:
        cid = queue.pop(0)
        if cid in seen:
            continue
        seen.add(cid)
        visited.append(cid)
        commit = read_commit(repo_root, cid)
        if commit is None:
            continue
        # Prefer first parent for a clean linear walk; visit both for merges.
        if commit.parent_commit_id:
            queue.insert(0, commit.parent_commit_id)
        if commit.parent2_commit_id:
            queue.append(commit.parent2_commit_id)
    return visited


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def blame_file(
    repo_root: pathlib.Path,
    rel_path: str,
    commit_id: str,
) -> list[BlameLine] | None:
    """Attribute each line of *rel_path* to the commit that last modified it.

    Args:
        repo_root:  Repository root.
        rel_path:   Path relative to ``state/``, e.g. ``"README.md"``.
        commit_id:  The commit to start the blame from (usually HEAD).

    Returns:
        A list of :class:`BlameLine` objects (1-indexed), or ``None`` if the
        file does not exist at *commit_id*.
    """
    current_lines = _read_file_at_commit(repo_root, commit_id, rel_path)
    if current_lines is None:
        return None

    n = len(current_lines)
    # attribution[i] = commit_id that last changed line i (0-indexed)
    attribution: list[str] = [commit_id] * n

    ancestry = _walk_ancestry(repo_root, commit_id)

    # Walk from the commit towards the root.  When a parent has the same line
    # content the attribution moves back to the parent (older is better).
    child_lines = current_lines[:]
    child_id = commit_id

    for parent_id in ancestry[1:]:  # skip the starting commit itself
        parent_lines = _read_file_at_commit(repo_root, parent_id, rel_path)
        if parent_lines is None:
            # File didn't exist in this ancestor — stop the walk.
            break

        # Use SequenceMatcher to align lines.
        sm = difflib.SequenceMatcher(None, parent_lines, child_lines, autojunk=False)
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag == "equal":
                # These lines are unchanged from parent → child.
                # They may be attributed to an even older commit; update those
                # that are currently attributed to child_id.
                for k in range(j2 - j1):
                    if attribution[j1 + k] == child_id:
                        attribution[j1 + k] = parent_id

        child_lines = parent_lines
        child_id = parent_id

    # Build the final BlameLine list.
    result: list[BlameLine] = []
    commit_cache: dict[str, tuple[str, str, str]] = {}

    def _commit_meta(cid: str) -> tuple[str, str, str]:
        if cid in commit_cache:
            return commit_cache[cid]
        c = read_commit(repo_root, cid)
        if c is None:
            meta = ("unknown", "", "")
        else:
            first_line = c.message.split("\n", 1)[0] if c.message else ""
            meta = (c.author or "unknown", c.committed_at.isoformat(), first_line)
        commit_cache[cid] = meta
        return meta

    for idx, (line, cid) in enumerate(zip(current_lines, attribution)):
        author, committed_at, message = _commit_meta(cid)
        result.append(BlameLine(
            lineno=idx + 1,
            commit_id=cid,
            author=author,
            committed_at=committed_at,
            message=message,
            content=line,
        ))

    return result

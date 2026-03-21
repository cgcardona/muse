"""Shared query helpers for the code-domain CLI commands.

This module provides the low-level primitives that multiple code-domain
commands need — symbol extraction from snapshots, commit-graph walking,
and language classification — so each command can stay thin.

None of these functions are part of the public ``CodePlugin`` API.  They
are internal helpers for the CLI layer and must not be imported by any
core module.
"""

from __future__ import annotations

import itertools
import logging
import pathlib
from collections.abc import Iterator

from muse.core.object_store import read_object
from muse.core.store import CommitRecord, read_commit
from muse.domain import DomainOp
from muse.plugins.code.ast_parser import (
    SymbolRecord,
    SymbolTree,
    parse_symbols,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Language classification
# ---------------------------------------------------------------------------

_SUFFIX_LANG: dict[str, str] = {
    ".py": "Python",   ".pyi": "Python",
    ".ts": "TypeScript", ".tsx": "TypeScript",
    ".js": "JavaScript", ".jsx": "JavaScript",
    ".mjs": "JavaScript", ".cjs": "JavaScript",
    ".go": "Go",
    ".rs": "Rust",
    ".java": "Java",
    ".cs": "C#",
    ".c": "C",  ".h": "C",
    ".cpp": "C++", ".cc": "C++", ".cxx": "C++", ".hpp": "C++", ".hxx": "C++",
    ".rb": "Ruby",
    ".kt": "Kotlin", ".kts": "Kotlin",
}


def language_of(file_path: str) -> str:
    """Return a display language name for *file_path* based on its suffix."""
    suffix = pathlib.PurePosixPath(file_path).suffix.lower()
    return _SUFFIX_LANG.get(suffix, suffix or "(no ext)")


def is_semantic(file_path: str) -> bool:
    """Return ``True`` if *file_path* has a suffix with AST-level support."""
    from muse.plugins.code.ast_parser import SEMANTIC_EXTENSIONS
    suffix = pathlib.PurePosixPath(file_path).suffix.lower()
    return suffix in SEMANTIC_EXTENSIONS


# ---------------------------------------------------------------------------
# Symbol extraction from a snapshot manifest
# ---------------------------------------------------------------------------


def symbols_for_snapshot(
    root: pathlib.Path,
    manifest: dict[str, str],
    *,
    kind_filter: str | None = None,
    file_filter: str | None = None,
    language_filter: str | None = None,
) -> dict[str, SymbolTree]:
    """Extract symbol trees for all semantic files in *manifest*.

    Args:
        root:            Repository root (used to locate the object store).
        manifest:        Snapshot manifest mapping file path → SHA-256.
        kind_filter:     If set, only include symbols with this ``kind``.
        file_filter:     If set, only include symbols from this exact file path.
        language_filter: If set, only include symbols from files of this language.

    Returns:
        Dict mapping ``file_path → SymbolTree``; empty trees are omitted.
    """
    result: dict[str, SymbolTree] = {}
    for file_path, object_id in sorted(manifest.items()):
        if not is_semantic(file_path):
            continue
        if file_filter and file_path != file_filter:
            continue
        if language_filter and language_of(file_path) != language_filter:
            continue
        raw = read_object(root, object_id)
        if raw is None:
            logger.debug("Object %s missing for %s — skipping", object_id[:8], file_path)
            continue
        tree = parse_symbols(raw, file_path)
        if kind_filter:
            tree = {addr: rec for addr, rec in tree.items() if rec["kind"] == kind_filter}
        if tree:
            result[file_path] = tree
    return result


# ---------------------------------------------------------------------------
# Commit-graph walking
# ---------------------------------------------------------------------------


def walk_commits(
    root: pathlib.Path,
    start_commit_id: str,
    max_commits: int = 10_000,
) -> list[CommitRecord]:
    """Walk the parent chain from *start_commit_id*, newest-first.

    Args:
        root:            Repository root.
        start_commit_id: SHA-256 of the commit to start from.
        max_commits:     Safety cap — stop after this many commits.

    Returns:
        List of ``CommitRecord`` objects, newest first.
    """
    commits: list[CommitRecord] = []
    seen: set[str] = set()
    current_id: str | None = start_commit_id
    while current_id and current_id not in seen and len(commits) < max_commits:
        seen.add(current_id)
        commit = read_commit(root, current_id)
        if commit is None:
            break
        commits.append(commit)
        current_id = commit.parent_commit_id
    return commits


def walk_commits_range(
    root: pathlib.Path,
    to_commit_id: str,
    from_commit_id: str | None,
    max_commits: int = 10_000,
) -> list[CommitRecord]:
    """Collect commits from *to_commit_id* back to (not including) *from_commit_id*.

    Args:
        root:            Repository root.
        to_commit_id:    Inclusive end of the range.
        from_commit_id:  Exclusive start; ``None`` means walk to the initial commit.
        max_commits:     Safety cap.

    Returns:
        List of ``CommitRecord`` objects, newest first.
    """
    commits: list[CommitRecord] = []
    seen: set[str] = set()
    current_id: str | None = to_commit_id
    while current_id and current_id not in seen and len(commits) < max_commits:
        seen.add(current_id)
        if current_id == from_commit_id:
            break
        commit = read_commit(root, current_id)
        if commit is None:
            break
        commits.append(commit)
        current_id = commit.parent_commit_id
    return commits


# ---------------------------------------------------------------------------
# Op traversal helpers
# ---------------------------------------------------------------------------


def flat_symbol_ops(ops: list[DomainOp]) -> Iterator[DomainOp]:
    """Yield all leaf ops, recursing into PatchOp.child_ops.

    Only yields ops that have a symbol-level address (i.e. contain ``::``).
    """
    for op in ops:
        if op["op"] == "patch":
            for child in op["child_ops"]:
                if "::" in child["address"]:
                    yield child
        elif "::" in op["address"]:
            yield op


def touched_files(ops: list[DomainOp]) -> frozenset[str]:
    """Return the set of file paths that appear as PatchOp addresses in *ops*.

    Only counts files that had symbol-level child ops (semantic changes),
    not coarse file-level replace/insert/delete ops.
    """
    files: set[str] = set()
    for op in ops:
        if op["op"] == "patch" and op["child_ops"]:
            files.add(op["address"])
    return frozenset(files)


def file_pairs(files: frozenset[str]) -> Iterator[tuple[str, str]]:
    """Yield all ordered pairs ``(a, b)`` with ``a < b`` from *files*."""
    yield from itertools.combinations(sorted(files), 2)

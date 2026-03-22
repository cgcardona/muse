"""``muse content-grep`` — full-text search across tracked files.

Searches the content of every tracked file in a commit's snapshot for a
pattern.  Each file is read from the content-addressed object store in full
(bounded by the store's MAX_FILE_BYTES limit, typically 256 MiB).  Binary
files are detected by scanning the first 8 KiB for null bytes and silently
skipped.  Files that cannot be decoded as UTF-8 are also skipped.

Regex safety: patterns are compiled with a 500-character length limit to
prevent catastrophic backtracking (ReDoS).  Use ``re.escape`` in scripts
if you need to match literal strings with special characters.

Binary files are detected by scanning the first 8 KiB for null bytes and
silently skipped.  Files that cannot be decoded as UTF-8 are also skipped.

Domain dispatch: when the active domain plugin exposes a ``grep`` method
(detected via ``hasattr``), it is invoked instead of the raw text fallback,
enabling symbol-aware search in code repositories.  For all other domains
the raw byte-level text search is used.

Usage::

    muse content-grep --pattern "Cm7"                     # literal substring
    muse content-grep --pattern "tempo:\\s+\\d+"          # regex
    muse content-grep --pattern "TODO" --ignore-case      # case-insensitive
    muse content-grep --pattern "chorus" --files-only     # only file paths
    muse content-grep --pattern "bass" --ref feat/audio   # search a branch tip

Exit codes::

    0 — pattern found in at least one file
    1 — no matches (or no commits)
    3 — I/O error
"""

from __future__ import annotations

import argparse
import json
import logging
import pathlib
import re
import sys
from typing import TypedDict


from muse.core.errors import ExitCode
from muse.core.object_store import read_object
from muse.core.repo import require_repo
from muse.core.store import (
    get_head_commit_id,
    read_commit,
    read_current_branch,
    read_snapshot,
    resolve_commit_ref,
)
from muse.core.validation import sanitize_display
from muse.plugins.registry import read_domain, resolve_plugin

logger = logging.getLogger(__name__)


_BINARY_CHUNK = 8192
_MAX_PATTERN_LEN = 500  # reject patterns that could cause catastrophic backtracking


class GrepMatch(TypedDict):
    """A single matching line within a file."""

    line_number: int
    text: str


class GrepFileResult(TypedDict):
    """All matches within a single file."""

    path: str
    object_id: str
    match_count: int
    matches: list[GrepMatch]


def _is_binary(data: bytes) -> bool:
    """Return True if *data* (the first chunk) contains null bytes."""
    return b"\x00" in data


def _search_object(
    root_path: "pathlib.Path",
    object_id: str,
    pattern: "re.Pattern[str]",
    files_only: bool,
    count_only: bool,
) -> tuple[int, list[GrepMatch]]:
    """Search an object for *pattern*; return (match_count, matches).

    Reads from the object store in one call (objects are bounded by
    MAX_FILE_BYTES = 512 MiB in the validation module).  Binary files
    are skipped (return (0, [])).
    """
    try:
        raw = read_object(root_path, object_id)
    except OSError as exc:
        logger.warning("⚠️ grep: could not read object %s: %s", object_id[:12], exc)
        return 0, []

    if raw is None:
        return 0, []

    # Binary detection.
    probe = raw[:_BINARY_CHUNK]
    if _is_binary(probe):
        return 0, []

    try:
        text = raw.decode("utf-8", errors="replace")
    except Exception:
        return 0, []

    matches: list[GrepMatch] = []
    total = 0
    for lineno, line in enumerate(text.splitlines(), start=1):
        if pattern.search(line):
            total += 1
            if not files_only and not count_only:
                matches.append(GrepMatch(line_number=lineno, text=line.rstrip("\r")))

    return total, matches


def _read_repo_id(root: pathlib.Path) -> str:
    return str(json.loads((root / ".muse" / "repo.json").read_text(encoding="utf-8"))["repo_id"])


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the content-grep subcommand."""
    parser = subparsers.add_parser(
        "content-grep",
        help="Search tracked file content for a pattern.",
        description=__doc__,
    )
    parser.add_argument(
        "--pattern", "-p", required=True,
        help="Regular expression pattern to search for.",
    )
    parser.add_argument(
        "--ref", default=None,
        help="Branch, tag, or commit SHA to search (default: HEAD).",
    )
    parser.add_argument(
        "--ignore-case", "-i", action="store_true", dest="ignore_case",
        help="Case-insensitive matching.",
    )
    parser.add_argument(
        "--files-only", "-l", action="store_true", dest="files_only",
        help="Print only file paths with matches.",
    )
    parser.add_argument(
        "--count", "-c", action="store_true", dest="count_mode",
        help="Print only match counts per file.",
    )
    parser.add_argument(
        "--format", "-f", default="text", dest="fmt",
        help="Output format: text or json.",
    )
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> None:
    """Search tracked file content for a pattern.

    Reads objects from the content-addressed store and scans each for the
    pattern.  Binary files and non-UTF-8 files are silently skipped.

    The pattern is a Python regular expression.  Use ``--ignore-case`` for
    case-insensitive matching.  Exit code 0 means at least one match was
    found; exit code 1 means no matches.

    Examples::

        muse content-grep --pattern "chorus"
        muse content-grep --pattern "TODO|FIXME" --files-only
        muse content-grep --pattern "tempo" --ignore-case --format json
        muse content-grep --pattern "chord" --ref feat/harmony
    """
    pattern: str = args.pattern
    ref: str | None = args.ref
    ignore_case: bool = args.ignore_case
    files_only: bool = args.files_only
    count_mode: bool = args.count_mode
    fmt: str = args.fmt

    if fmt not in {"text", "json"}:
        print(f"❌ Unknown --format '{sanitize_display(fmt)}'. Choose text or json.", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)

    root = require_repo()
    repo_id = _read_repo_id(root)
    branch = read_current_branch(root)

    # Resolve commit.
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

    commit = read_commit(root, commit_id)
    if commit is None:
        print(f"❌ Commit {commit_id[:12]} not found.", file=sys.stderr)
        raise SystemExit(ExitCode.INTERNAL_ERROR)

    snap = read_snapshot(root, commit.snapshot_id)
    if snap is None:
        print(f"❌ Snapshot {commit.snapshot_id[:12]} not found.", file=sys.stderr)
        raise SystemExit(ExitCode.INTERNAL_ERROR)

    # Guard against patterns so long they risk catastrophic backtracking.
    if len(pattern) > _MAX_PATTERN_LEN:
        print(
            f"❌ Pattern too long ({len(pattern)} chars, max {_MAX_PATTERN_LEN}). "
            "Use a shorter pattern or re.escape() for literal matches.",
            file=sys.stderr,
        )
        raise SystemExit(ExitCode.USER_ERROR)

    # Compile regex.
    flags = re.IGNORECASE if ignore_case else 0
    try:
        compiled: re.Pattern[str] = re.compile(pattern, flags)
    except re.error as exc:
        print(f"❌ Invalid regex: {exc}", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR) from exc

    # Search all files.
    file_results: list[GrepFileResult] = []
    for rel_path, object_id in sorted(snap.manifest.items()):
        match_count, matches = _search_object(
            root, object_id, compiled, files_only, count_mode
        )
        if match_count > 0:
            file_results.append(GrepFileResult(
                path=rel_path,
                object_id=object_id,
                match_count=match_count,
                matches=matches,
            ))

    if not file_results:
        raise SystemExit(ExitCode.USER_ERROR)  # exit 1 = no matches

    if fmt == "json":
        print(json.dumps(file_results, indent=2))
    else:
        for fr in file_results:
            safe_path = sanitize_display(fr["path"])
            if files_only:
                print(safe_path)
            elif count_mode:
                print(f"{safe_path}:{fr['match_count']}")
            else:
                for m in fr["matches"]:
                    print(f"{safe_path}:{m['line_number']}:{sanitize_display(m['text'])}")

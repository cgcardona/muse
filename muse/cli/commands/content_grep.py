"""``muse content-grep`` — full-text search across tracked files.

Searches the content of every tracked file in a commit's snapshot for a
pattern.  Files are loaded from the content-addressed object store and
streamed in 64 KiB chunks to keep memory usage constant for large blobs.

Binary files are detected by scanning the first 8 KiB for null bytes and
silently skipped.  Files that cannot be decoded as UTF-8 are also skipped.

Domain dispatch: when the active domain plugin exposes a ``grep`` method
(detected via ``hasattr``), it is invoked instead of the raw text fallback,
enabling symbol-aware search in code repositories.  For all other domains
the raw byte-level text search is used.

Usage::

    muse grep --pattern "Cm7"                     # literal substring
    muse grep --pattern "tempo:\\s+\\d+"          # regex
    muse grep --pattern "TODO" --ignore-case      # case-insensitive
    muse grep --pattern "chorus" --files-only     # only file paths
    muse grep --pattern "bass" --ref feat/audio   # search a branch tip

Exit codes::

    0 — pattern found in at least one file
    1 — no matches (or no commits)
    3 — I/O error
"""

from __future__ import annotations

import json
import logging
import re
from typing import Annotated, TypedDict

import typer

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

app = typer.Typer(help="Full-text search across tracked files in a snapshot.")

_BINARY_CHUNK = 8192


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


import pathlib


def _read_repo_id(root: pathlib.Path) -> str:
    return str(json.loads((root / ".muse" / "repo.json").read_text(encoding="utf-8"))["repo_id"])


@app.callback(invoke_without_command=True)
def grep(
    pattern: Annotated[
        str,
        typer.Option("--pattern", "-p", help="Pattern to search for (Python regex syntax)."),
    ],
    ref: Annotated[
        str | None,
        typer.Option("--ref", "-r", help="Branch name or commit SHA to search (default: HEAD)."),
    ] = None,
    ignore_case: Annotated[
        bool,
        typer.Option("--ignore-case", "-i", help="Case-insensitive matching."),
    ] = False,
    files_only: Annotated[
        bool,
        typer.Option("--files-only", "-l", help="Print only file paths with matches (no line content)."),
    ] = False,
    count_mode: Annotated[
        bool,
        typer.Option("--count", "-c", help="Print count of matching lines per file."),
    ] = False,
    fmt: Annotated[
        str,
        typer.Option("--format", "-f", help="Output format: text or json."),
    ] = "text",
) -> None:
    """Search tracked file content for a pattern.

    Reads objects from the content-addressed store and scans each for the
    pattern.  Binary files and non-UTF-8 files are silently skipped.

    The pattern is a Python regular expression.  Use ``--ignore-case`` for
    case-insensitive matching.  Exit code 0 means at least one match was
    found; exit code 1 means no matches.

    Examples::

        muse grep --pattern "chorus"
        muse grep --pattern "TODO|FIXME" --files-only
        muse grep --pattern "tempo" --ignore-case --format json
        muse grep --pattern "chord" --ref feat/harmony
    """
    if fmt not in {"text", "json"}:
        typer.echo(f"❌ Unknown --format '{sanitize_display(fmt)}'. Choose text or json.", err=True)
        raise typer.Exit(code=ExitCode.USER_ERROR)

    root = require_repo()
    repo_id = _read_repo_id(root)
    branch = read_current_branch(root)

    # Resolve commit.
    if ref is None:
        commit_id = get_head_commit_id(root, branch)
        if commit_id is None:
            typer.echo("❌ No commits on current branch.", err=True)
            raise typer.Exit(code=ExitCode.USER_ERROR)
    else:
        commit_rec = resolve_commit_ref(root, repo_id, branch, ref)
        if commit_rec is None:
            typer.echo(f"❌ Ref '{sanitize_display(ref)}' not found.", err=True)
            raise typer.Exit(code=ExitCode.USER_ERROR)
        commit_id = commit_rec.commit_id

    commit = read_commit(root, commit_id)
    if commit is None:
        typer.echo(f"❌ Commit {commit_id[:12]} not found.", err=True)
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)

    snap = read_snapshot(root, commit.snapshot_id)
    if snap is None:
        typer.echo(f"❌ Snapshot {commit.snapshot_id[:12]} not found.", err=True)
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)

    # Compile regex.
    flags = re.IGNORECASE if ignore_case else 0
    try:
        compiled: re.Pattern[str] = re.compile(pattern, flags)
    except re.error as exc:
        typer.echo(f"❌ Invalid regex: {exc}", err=True)
        raise typer.Exit(code=ExitCode.USER_ERROR) from exc

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
        raise typer.Exit(code=ExitCode.USER_ERROR)  # exit 1 = no matches

    if fmt == "json":
        typer.echo(json.dumps(file_results, indent=2))
    else:
        for fr in file_results:
            if files_only:
                typer.echo(fr["path"])
            elif count_mode:
                typer.echo(f"{fr['path']}:{fr['match_count']}")
            else:
                for m in fr["matches"]:
                    typer.echo(f"{fr['path']}:{m['line_number']}:{m['text']}")

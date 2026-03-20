"""Reflog — record every HEAD and branch-ref movement.

The reflog is a append-only per-ref journal that records every time a branch
pointer moves, regardless of cause: commit, checkout, merge, reset, cherry-pick,
stash pop.  It is a safety net — if a ``muse reset --hard`` goes wrong the
reflog tells you exactly what ``HEAD`` was before.

Storage layout::

    .muse/logs/
        HEAD                  — log for the symbolic HEAD pointer
        refs/
            heads/
                main          — log for the main branch ref
                dev           — log for the dev branch ref
                …

Each log file is a plain-text append-only sequence of lines::

    <old_id> <new_id> <author> <timestamp_unix> <tz_offset> \\t<operation>

This format mirrors Git's reflog so tooling that understands both can be
built without translation.

Fields
------
old_id          64-hex SHA-256 or ``0000…0000`` when there is no predecessor
                (initial commit on a branch, new branch creation).
new_id          64-hex SHA-256 of the new commit.
author          ``Name <email>`` or a short label for automated operations.
timestamp_unix  Unix seconds as a decimal integer.
tz_offset       UTC offset in ``+HHMM`` / ``-HHMM`` form.
operation       Free-form description, e.g. ``commit: add verse``,
                ``checkout: moving from main to dev``,
                ``reset: moving to <sha12>``.
"""

from __future__ import annotations

import datetime
import logging
import pathlib
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_NULL_ID = "0" * 64
_LOG_DIR_NAME = "logs"


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReflogEntry:
    """One line of a reflog."""

    old_id: str
    new_id: str
    author: str
    timestamp: datetime.datetime
    operation: str


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def _logs_dir(repo_root: pathlib.Path) -> pathlib.Path:
    return repo_root / ".muse" / _LOG_DIR_NAME


def _head_log_path(repo_root: pathlib.Path) -> pathlib.Path:
    return _logs_dir(repo_root) / "HEAD"


def _ref_log_path(repo_root: pathlib.Path, branch: str) -> pathlib.Path:
    return _logs_dir(repo_root) / "refs" / "heads" / branch


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------


def append_reflog(
    repo_root: pathlib.Path,
    branch: str,
    old_id: str | None,
    new_id: str,
    author: str,
    operation: str,
) -> None:
    """Append one entry to both the branch log and the HEAD log.

    Args:
        repo_root:  Root of the Muse repository.
        branch:     Branch whose ref is moving (e.g. ``"main"``).
        old_id:     Previous commit SHA-256 (``None`` for initial commit).
        new_id:     New commit SHA-256.
        author:     ``"Name <email>"`` or a short label.
        operation:  Human-readable description of the operation.
    """
    effective_old = old_id or _NULL_ID
    now = datetime.datetime.now(tz=datetime.timezone.utc)
    ts = int(now.timestamp())
    tz_offset = "+0000"  # we always store UTC; offset is for display parity

    line = f"{effective_old} {new_id} {author} {ts} {tz_offset}\t{operation}\n"

    for log_path in (_ref_log_path(repo_root, branch), _head_log_path(repo_root)):
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(line)


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


def _parse_line(line: str) -> ReflogEntry | None:
    """Parse one reflog line; return None on malformed input."""
    line = line.rstrip("\n")
    # Split on the tab that separates the metadata from the operation string.
    parts = line.split("\t", 1)
    if len(parts) != 2:
        return None
    meta, operation = parts
    tokens = meta.split()
    if len(tokens) < 5:
        return None
    old_id, new_id = tokens[0], tokens[1]
    # author may contain spaces — everything between tokens[2] and the last
    # two tokens (timestamp, tz_offset) is the author.
    ts_str = tokens[-2]
    author = " ".join(tokens[2:-2])
    try:
        ts = datetime.datetime.fromtimestamp(int(ts_str), tz=datetime.timezone.utc)
    except (ValueError, OSError):
        ts = datetime.datetime.now(tz=datetime.timezone.utc)
    return ReflogEntry(
        old_id=old_id,
        new_id=new_id,
        author=author,
        timestamp=ts,
        operation=operation,
    )


def read_reflog(
    repo_root: pathlib.Path,
    branch: str | None = None,
    limit: int = 100,
) -> list[ReflogEntry]:
    """Return reflog entries newest-first.

    Args:
        repo_root:  Root of the Muse repository.
        branch:     Branch name, or ``None`` to read the HEAD log.
        limit:      Maximum number of entries to return.
    """
    log_path = (
        _head_log_path(repo_root) if branch is None else _ref_log_path(repo_root, branch)
    )
    if not log_path.exists():
        return []
    try:
        lines = log_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        logger.warning("⚠️ Could not read reflog %s: %s", log_path, exc)
        return []
    entries: list[ReflogEntry] = []
    for line in reversed(lines):
        if not line.strip():
            continue
        entry = _parse_line(line)
        if entry is not None:
            entries.append(entry)
        if len(entries) >= limit:
            break
    return entries


def list_reflog_refs(repo_root: pathlib.Path) -> list[str]:
    """Return branch names that have a reflog, sorted."""
    refs_dir = _logs_dir(repo_root) / "refs" / "heads"
    if not refs_dir.exists():
        return []
    return sorted(p.name for p in refs_dir.iterdir() if p.is_file())

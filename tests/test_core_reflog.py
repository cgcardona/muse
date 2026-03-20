"""Tests for muse/core/reflog.py — reflog append, read, parse."""

from __future__ import annotations

import datetime
import pathlib

import pytest

from muse.core.reflog import (
    ReflogEntry,
    append_reflog,
    list_reflog_refs,
    read_reflog,
)

_NULL_ID = "0" * 64
_SHA_A = "a" * 64
_SHA_B = "b" * 64
_SHA_C = "c" * 64


# ---------------------------------------------------------------------------
# append_reflog
# ---------------------------------------------------------------------------


def test_append_creates_log_files(tmp_path: pathlib.Path) -> None:
    append_reflog(tmp_path, "main", old_id=None, new_id=_SHA_A, author="Alice", operation="commit: init")
    assert (tmp_path / ".muse" / "logs" / "refs" / "heads" / "main").exists()
    assert (tmp_path / ".muse" / "logs" / "HEAD").exists()


def test_append_null_old_id(tmp_path: pathlib.Path) -> None:
    append_reflog(tmp_path, "main", old_id=None, new_id=_SHA_A, author="Alice", operation="commit: init")
    entries = read_reflog(tmp_path, "main")
    assert len(entries) == 1
    assert entries[0].old_id == _NULL_ID
    assert entries[0].new_id == _SHA_A


def test_append_multiple_entries(tmp_path: pathlib.Path) -> None:
    append_reflog(tmp_path, "main", old_id=None, new_id=_SHA_A, author="A", operation="commit: first")
    append_reflog(tmp_path, "main", old_id=_SHA_A, new_id=_SHA_B, author="B", operation="commit: second")
    append_reflog(tmp_path, "main", old_id=_SHA_B, new_id=_SHA_C, author="C", operation="commit: third")
    entries = read_reflog(tmp_path, "main")
    assert len(entries) == 3
    # Newest first.
    assert entries[0].new_id == _SHA_C
    assert entries[1].new_id == _SHA_B
    assert entries[2].new_id == _SHA_A


def test_append_head_log_also_updated(tmp_path: pathlib.Path) -> None:
    append_reflog(tmp_path, "dev", old_id=_SHA_A, new_id=_SHA_B, author="X", operation="checkout: moving")
    head_entries = read_reflog(tmp_path, branch=None)
    assert len(head_entries) == 1
    assert head_entries[0].new_id == _SHA_B


def test_append_operation_preserved(tmp_path: pathlib.Path) -> None:
    op = "merge: feat/audio into main"
    append_reflog(tmp_path, "main", old_id=_SHA_A, new_id=_SHA_B, author="Alice", operation=op)
    entries = read_reflog(tmp_path, "main")
    assert entries[0].operation == op


def test_append_author_preserved(tmp_path: pathlib.Path) -> None:
    append_reflog(tmp_path, "main", old_id=None, new_id=_SHA_A, author="Alice <alice@example.com>", operation="commit: x")
    entries = read_reflog(tmp_path, "main")
    assert "Alice" in entries[0].author


# ---------------------------------------------------------------------------
# read_reflog
# ---------------------------------------------------------------------------


def test_read_returns_empty_for_missing_log(tmp_path: pathlib.Path) -> None:
    entries = read_reflog(tmp_path, "nonexistent")
    assert entries == []


def test_read_limit(tmp_path: pathlib.Path) -> None:
    for i in range(10):
        append_reflog(tmp_path, "main", old_id=None, new_id=_SHA_A, author="A", operation=f"commit: {i}")
    entries = read_reflog(tmp_path, "main", limit=3)
    assert len(entries) == 3


def test_read_head_log(tmp_path: pathlib.Path) -> None:
    append_reflog(tmp_path, "main", old_id=None, new_id=_SHA_A, author="A", operation="commit: x")
    entries = read_reflog(tmp_path, branch=None)
    assert len(entries) == 1


def test_read_timestamp_is_utc_datetime(tmp_path: pathlib.Path) -> None:
    append_reflog(tmp_path, "main", old_id=None, new_id=_SHA_A, author="A", operation="commit: x")
    entries = read_reflog(tmp_path, "main")
    assert isinstance(entries[0].timestamp, datetime.datetime)
    assert entries[0].timestamp.tzinfo is not None


# ---------------------------------------------------------------------------
# list_reflog_refs
# ---------------------------------------------------------------------------


def test_list_reflog_refs_empty(tmp_path: pathlib.Path) -> None:
    assert list_reflog_refs(tmp_path) == []


def test_list_reflog_refs_returns_branch_names(tmp_path: pathlib.Path) -> None:
    append_reflog(tmp_path, "main", old_id=None, new_id=_SHA_A, author="A", operation="commit: x")
    append_reflog(tmp_path, "dev", old_id=None, new_id=_SHA_B, author="B", operation="commit: y")
    refs = list_reflog_refs(tmp_path)
    assert "main" in refs
    assert "dev" in refs


def test_list_reflog_refs_sorted(tmp_path: pathlib.Path) -> None:
    for name in ("zzz", "aaa", "mmm"):
        append_reflog(tmp_path, name, old_id=None, new_id=_SHA_A, author="A", operation="commit: x")
    refs = list_reflog_refs(tmp_path)
    assert refs == sorted(refs)


# ---------------------------------------------------------------------------
# Stress test: many entries
# ---------------------------------------------------------------------------


def test_stress_many_entries(tmp_path: pathlib.Path) -> None:
    """500 entries must round-trip correctly."""
    n = 500
    for i in range(n):
        sha = format(i, "064x")
        append_reflog(tmp_path, "main", old_id=None, new_id=sha, author="A", operation=f"commit: {i}")
    entries = read_reflog(tmp_path, "main", limit=n)
    assert len(entries) == n
    # Newest first — last appended sha should be entries[0].
    assert entries[0].new_id == format(n - 1, "064x")


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_entry_with_tab_in_operation(tmp_path: pathlib.Path) -> None:
    """Tab characters in the operation string must be escaped/handled gracefully."""
    op = "commit: message with some text"
    append_reflog(tmp_path, "main", old_id=None, new_id=_SHA_A, author="A", operation=op)
    entries = read_reflog(tmp_path, "main")
    assert entries[0].operation == op


def test_multiple_branches_isolated(tmp_path: pathlib.Path) -> None:
    append_reflog(tmp_path, "main", old_id=None, new_id=_SHA_A, author="A", operation="commit: main")
    append_reflog(tmp_path, "dev", old_id=None, new_id=_SHA_B, author="B", operation="commit: dev")
    main_entries = read_reflog(tmp_path, "main")
    dev_entries = read_reflog(tmp_path, "dev")
    assert len(main_entries) == 1
    assert len(dev_entries) == 1
    assert main_entries[0].new_id == _SHA_A
    assert dev_entries[0].new_id == _SHA_B

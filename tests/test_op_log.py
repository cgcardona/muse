"""Tests for muse.core.op_log — OpEntry, OpLogCheckpoint, OpLog."""

import pathlib

import pytest

from muse.core.op_log import (
    OpEntry,
    OpLog,
    list_sessions,
    make_op_entry,
)
from muse.domain import InsertOp


# ---------------------------------------------------------------------------
# make_op_entry factory
# ---------------------------------------------------------------------------


class TestMakeOpEntry:
    def test_all_required_fields_present(self) -> None:
        op = InsertOp(
            op="insert",
            address="note:0",
            position=0,
            content_id="abc123",
            content_summary="C4",
        )
        entry = make_op_entry(
            actor_id="agent-x",
            domain="midi",
            domain_op=op,
            lamport_ts=1,
        )
        assert entry["actor_id"] == "agent-x"
        assert entry["domain"] == "midi"
        assert entry["lamport_ts"] == 1
        assert entry["parent_op_ids"] == []
        assert entry["intent_id"] == ""
        assert entry["reservation_id"] == ""
        assert len(entry["op_id"]) == 36  # UUID4

    def test_parent_op_ids_are_copied(self) -> None:
        op = InsertOp(op="insert", address="note:0", position=0, content_id="x", content_summary="")
        parent_ids = ["aaa", "bbb"]
        entry = make_op_entry("a", "midi", op, 1, parent_op_ids=parent_ids)
        assert entry["parent_op_ids"] == ["aaa", "bbb"]
        # Mutating the original should not affect the entry.
        parent_ids.append("ccc")
        assert entry["parent_op_ids"] == ["aaa", "bbb"]

    def test_op_ids_are_unique(self) -> None:
        op = InsertOp(op="insert", address="note:0", position=0, content_id="x", content_summary="")
        ids = {make_op_entry("a", "midi", op, i)["op_id"] for i in range(20)}
        assert len(ids) == 20


# ---------------------------------------------------------------------------
# OpLog.append and read_all
# ---------------------------------------------------------------------------


class TestOpLogAppendRead:
    def test_append_and_read_all_roundtrip(self, tmp_path: pathlib.Path) -> None:
        log = OpLog(tmp_path, "session-1")
        op = InsertOp(op="insert", address="note:0", position=0, content_id="c1", content_summary="C4")
        e1 = make_op_entry("agent-a", "midi", op, 1)
        e2 = make_op_entry("agent-a", "midi", op, 2)
        log.append(e1)
        log.append(e2)
        entries = log.read_all()
        assert len(entries) == 2
        assert entries[0]["op_id"] == e1["op_id"]
        assert entries[1]["op_id"] == e2["op_id"]

    def test_empty_log_returns_empty_list(self, tmp_path: pathlib.Path) -> None:
        log = OpLog(tmp_path, "empty-session")
        assert log.read_all() == []

    def test_append_creates_directory(self, tmp_path: pathlib.Path) -> None:
        log = OpLog(tmp_path, "new-session")
        op = InsertOp(op="insert", address="note:0", position=0, content_id="c1", content_summary="")
        log.append(make_op_entry("a", "midi", op, 1))
        assert (tmp_path / ".muse" / "op_log" / "new-session").is_dir()


# ---------------------------------------------------------------------------
# Lamport timestamp counter
# ---------------------------------------------------------------------------


class TestLamportTs:
    def test_lamport_is_monotonic(self, tmp_path: pathlib.Path) -> None:
        log = OpLog(tmp_path, "ts-session")
        ts_values = [log.next_lamport_ts() for _ in range(10)]
        assert ts_values == sorted(ts_values)
        assert len(set(ts_values)) == 10

    def test_lamport_continues_after_reopen(self, tmp_path: pathlib.Path) -> None:
        log1 = OpLog(tmp_path, "reopen-session")
        op = InsertOp(op="insert", address="note:0", position=0, content_id="c", content_summary="")
        for i in range(5):
            ts = log1.next_lamport_ts()
            log1.append(make_op_entry("a", "midi", op, ts))

        # Reopen the same session.
        log2 = OpLog(tmp_path, "reopen-session")
        new_ts = log2.next_lamport_ts()
        assert new_ts > 5


# ---------------------------------------------------------------------------
# Checkpoint
# ---------------------------------------------------------------------------


class TestCheckpoint:
    def test_checkpoint_written_and_readable(self, tmp_path: pathlib.Path) -> None:
        log = OpLog(tmp_path, "ckpt-session")
        op = InsertOp(op="insert", address="note:0", position=0, content_id="c", content_summary="")
        for i in range(3):
            log.append(make_op_entry("a", "midi", op, i + 1))

        ckpt = log.checkpoint("snap-abc")
        assert ckpt["snapshot_id"] == "snap-abc"
        assert ckpt["op_count"] == 3
        assert ckpt["lamport_ts"] == 3

        recovered = log.read_checkpoint()
        assert recovered is not None
        assert recovered["snapshot_id"] == "snap-abc"

    def test_no_checkpoint_returns_none(self, tmp_path: pathlib.Path) -> None:
        log = OpLog(tmp_path, "no-ckpt-session")
        assert log.read_checkpoint() is None

    def test_replay_since_checkpoint_returns_newer_only(self, tmp_path: pathlib.Path) -> None:
        log = OpLog(tmp_path, "replay-session")
        op = InsertOp(op="insert", address="note:0", position=0, content_id="c", content_summary="")

        for i in range(3):
            log.append(make_op_entry("a", "midi", op, i + 1))
        log.checkpoint("snap-1")

        # Add more entries after checkpoint.
        for i in range(3, 6):
            log.append(make_op_entry("a", "midi", op, i + 1))

        entries = log.replay_since_checkpoint()
        assert len(entries) == 3
        assert all(e["lamport_ts"] > 3 for e in entries)


# ---------------------------------------------------------------------------
# to_structured_delta
# ---------------------------------------------------------------------------


class TestToStructuredDelta:
    def test_produces_correct_domain_ops_filtered_by_domain(self, tmp_path: pathlib.Path) -> None:
        log = OpLog(tmp_path, "delta-session")
        op = InsertOp(op="insert", address="note:0", position=0, content_id="c", content_summary="C4")

        for i in range(4):
            log.append(make_op_entry("a", "midi", op, i + 1))
        # Add one code op that should be filtered out.
        code_op = InsertOp(op="insert", address="sym:0", position=0, content_id="d", content_summary="f()")
        log.append(make_op_entry("a", "code", code_op, 5))

        delta = log.to_structured_delta("midi")
        assert delta["domain"] == "midi_notes_tracked" or delta["domain"] == "midi"
        # Only the 4 music ops should be included.
        assert len(delta["ops"]) == 4

    def test_summary_mentions_insert(self, tmp_path: pathlib.Path) -> None:
        log = OpLog(tmp_path, "summary-session")
        op = InsertOp(op="insert", address="note:0", position=0, content_id="c", content_summary="C4")
        log.append(make_op_entry("a", "midi", op, 1))
        delta = log.to_structured_delta("midi")
        assert "insert" in delta["summary"]


# ---------------------------------------------------------------------------
# Session listing
# ---------------------------------------------------------------------------


class TestListSessions:
    def test_lists_all_sessions(self, tmp_path: pathlib.Path) -> None:
        op = InsertOp(op="insert", address="note:0", position=0, content_id="c", content_summary="")
        for sid in ["alpha", "beta", "gamma"]:
            log = OpLog(tmp_path, sid)
            log.append(make_op_entry("a", "midi", op, 1))

        sessions = list_sessions(tmp_path)
        assert "alpha" in sessions
        assert "beta" in sessions
        assert "gamma" in sessions

    def test_empty_repo_returns_empty_list(self, tmp_path: pathlib.Path) -> None:
        assert list_sessions(tmp_path) == []

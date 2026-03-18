"""Tests for muse/core/indices.py — optional local index layer.

Coverage
--------
SymbolHistoryEntry
    - to_dict / from_dict round-trip.
    - All six fields preserved.

symbol_history index
    - save_symbol_history writes a valid JSON file.
    - load_symbol_history reads it back correctly.
    - load returns empty dict when file absent.
    - load returns empty dict on corrupt JSON.
    - Sorting: entries dict is sorted by address.
    - Multiple addresses, multiple events per address.

hash_occurrence index
    - save_hash_occurrence writes a valid JSON file.
    - load_hash_occurrence reads it back correctly.
    - load returns empty dict when file absent.
    - load returns empty dict on corrupt JSON.
    - Addresses within each hash entry are sorted.

index_info
    - Reports "absent" for missing indexes.
    - Reports "present" + correct entry count for existing indexes.
    - Reports "corrupt" for malformed JSON.
    - Reports both indexes.

Schema compliance
    - schema_version == 1.
    - updated_at is present and is a non-empty string.
    - index field matches the index name.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from muse.core.indices import (
    HashOccurrenceIndex,
    SymbolHistoryEntry,
    SymbolHistoryIndex,
    index_info,
    load_hash_occurrence,
    load_symbol_history,
    save_hash_occurrence,
    save_symbol_history,
)


# ---------------------------------------------------------------------------
# SymbolHistoryEntry
# ---------------------------------------------------------------------------


class TestSymbolHistoryEntry:
    def test_to_dict_from_dict_round_trip(self) -> None:
        entry = SymbolHistoryEntry(
            commit_id="abc123",
            committed_at="2026-01-01T00:00:00+00:00",
            op="insert",
            content_id="content_abc",
            body_hash="body_hash_xyz",
            signature_id="sig_id_pqr",
        )
        d = entry.to_dict()
        entry2 = SymbolHistoryEntry.from_dict(d)
        assert entry2.commit_id == "abc123"
        assert entry2.committed_at == "2026-01-01T00:00:00+00:00"
        assert entry2.op == "insert"
        assert entry2.content_id == "content_abc"
        assert entry2.body_hash == "body_hash_xyz"
        assert entry2.signature_id == "sig_id_pqr"

    def test_all_ops_preserved(self) -> None:
        for op in ("insert", "delete", "replace", "patch"):
            e = SymbolHistoryEntry("c", "t", op, "cid", "bh", "sig")
            assert SymbolHistoryEntry.from_dict(e.to_dict()).op == op


# ---------------------------------------------------------------------------
# symbol_history index — save / load
# ---------------------------------------------------------------------------


class TestSymbolHistoryIndex:
    def _make_entry(self, op: str = "insert") -> SymbolHistoryEntry:
        return SymbolHistoryEntry(
            commit_id="commit1",
            committed_at="2026-01-01T00:00:00+00:00",
            op=op,
            content_id="cid1",
            body_hash="bh1",
            signature_id="sig1",
        )

    def test_save_creates_file(self, tmp_path: pathlib.Path) -> None:
        index: SymbolHistoryIndex = {
            "src/a.py::f": [self._make_entry()],
        }
        save_symbol_history(tmp_path, index)
        path = tmp_path / ".muse" / "indices" / "symbol_history.json"
        assert path.exists()

    def test_round_trip(self, tmp_path: pathlib.Path) -> None:
        entry = self._make_entry("replace")
        index: SymbolHistoryIndex = {
            "src/billing.py::compute_total": [entry],
        }
        save_symbol_history(tmp_path, index)
        loaded = load_symbol_history(tmp_path)
        assert "src/billing.py::compute_total" in loaded
        entries = loaded["src/billing.py::compute_total"]
        assert len(entries) == 1
        assert entries[0].op == "replace"
        assert entries[0].commit_id == "commit1"

    def test_multiple_addresses(self, tmp_path: pathlib.Path) -> None:
        index: SymbolHistoryIndex = {
            "src/a.py::alpha": [self._make_entry("insert")],
            "src/b.py::beta": [self._make_entry("insert"), self._make_entry("replace")],
        }
        save_symbol_history(tmp_path, index)
        loaded = load_symbol_history(tmp_path)
        assert len(loaded["src/a.py::alpha"]) == 1
        assert len(loaded["src/b.py::beta"]) == 2

    def test_load_absent_returns_empty(self, tmp_path: pathlib.Path) -> None:
        result = load_symbol_history(tmp_path)
        assert result == {}

    def test_load_corrupt_returns_empty(self, tmp_path: pathlib.Path) -> None:
        indices_dir = tmp_path / ".muse" / "indices"
        indices_dir.mkdir(parents=True, exist_ok=True)
        (indices_dir / "symbol_history.json").write_text("{not valid json")
        result = load_symbol_history(tmp_path)
        assert result == {}

    def test_schema_compliance(self, tmp_path: pathlib.Path) -> None:
        index: SymbolHistoryIndex = {"x.py::f": [self._make_entry()]}
        save_symbol_history(tmp_path, index)
        raw = json.loads((tmp_path / ".muse" / "indices" / "symbol_history.json").read_text())
        assert raw["schema_version"] == 1
        assert raw["index"] == "symbol_history"
        assert raw["updated_at"]  # non-empty string
        assert "x.py::f" in raw["entries"]

    def test_empty_index_saved(self, tmp_path: pathlib.Path) -> None:
        save_symbol_history(tmp_path, {})
        loaded = load_symbol_history(tmp_path)
        assert loaded == {}

    def test_entries_sorted_by_address(self, tmp_path: pathlib.Path) -> None:
        index: SymbolHistoryIndex = {
            "z.py::z": [self._make_entry()],
            "a.py::a": [self._make_entry()],
            "m.py::m": [self._make_entry()],
        }
        save_symbol_history(tmp_path, index)
        raw = json.loads((tmp_path / ".muse" / "indices" / "symbol_history.json").read_text())
        keys = list(raw["entries"].keys())
        assert keys == sorted(keys)


# ---------------------------------------------------------------------------
# hash_occurrence index — save / load
# ---------------------------------------------------------------------------


class TestHashOccurrenceIndex:
    def test_save_creates_file(self, tmp_path: pathlib.Path) -> None:
        index: HashOccurrenceIndex = {
            "deadbeef": ["src/a.py::f", "src/b.py::g"],
        }
        save_hash_occurrence(tmp_path, index)
        path = tmp_path / ".muse" / "indices" / "hash_occurrence.json"
        assert path.exists()

    def test_round_trip(self, tmp_path: pathlib.Path) -> None:
        index: HashOccurrenceIndex = {
            "abc123": ["src/a.py::f", "src/b.py::g"],
            "def456": ["src/c.py::h"],
        }
        save_hash_occurrence(tmp_path, index)
        loaded = load_hash_occurrence(tmp_path)
        assert "abc123" in loaded
        assert set(loaded["abc123"]) == {"src/a.py::f", "src/b.py::g"}
        assert loaded["def456"] == ["src/c.py::h"]

    def test_addresses_sorted_within_hash(self, tmp_path: pathlib.Path) -> None:
        index: HashOccurrenceIndex = {
            "hash1": ["z.py::z", "a.py::a", "m.py::m"],
        }
        save_hash_occurrence(tmp_path, index)
        raw = json.loads((tmp_path / ".muse" / "indices" / "hash_occurrence.json").read_text())
        addrs = raw["entries"]["hash1"]
        assert addrs == sorted(addrs)

    def test_hashes_sorted(self, tmp_path: pathlib.Path) -> None:
        index: HashOccurrenceIndex = {
            "zzz": ["a.py::f"],
            "aaa": ["b.py::g"],
        }
        save_hash_occurrence(tmp_path, index)
        raw = json.loads((tmp_path / ".muse" / "indices" / "hash_occurrence.json").read_text())
        keys = list(raw["entries"].keys())
        assert keys == sorted(keys)

    def test_load_absent_returns_empty(self, tmp_path: pathlib.Path) -> None:
        assert load_hash_occurrence(tmp_path) == {}

    def test_load_corrupt_returns_empty(self, tmp_path: pathlib.Path) -> None:
        indices_dir = tmp_path / ".muse" / "indices"
        indices_dir.mkdir(parents=True, exist_ok=True)
        (indices_dir / "hash_occurrence.json").write_text("not json at all")
        assert load_hash_occurrence(tmp_path) == {}

    def test_schema_compliance(self, tmp_path: pathlib.Path) -> None:
        save_hash_occurrence(tmp_path, {"h": ["a.py::f"]})
        raw = json.loads((tmp_path / ".muse" / "indices" / "hash_occurrence.json").read_text())
        assert raw["schema_version"] == 1
        assert raw["index"] == "hash_occurrence"
        assert raw["updated_at"]

    def test_empty_index(self, tmp_path: pathlib.Path) -> None:
        save_hash_occurrence(tmp_path, {})
        assert load_hash_occurrence(tmp_path) == {}


# ---------------------------------------------------------------------------
# index_info
# ---------------------------------------------------------------------------


class TestIndexInfo:
    def test_both_absent(self, tmp_path: pathlib.Path) -> None:
        info = index_info(tmp_path)
        assert len(info) == 2
        names = {i["name"] for i in info}
        assert names == {"symbol_history", "hash_occurrence"}
        for item in info:
            assert item["status"] == "absent"

    def test_symbol_history_present(self, tmp_path: pathlib.Path) -> None:
        entry = SymbolHistoryEntry("c", "t", "insert", "cid", "bh", "sig")
        save_symbol_history(tmp_path, {"a.py::f": [entry], "b.py::g": [entry]})
        info = index_info(tmp_path)
        sh = next(i for i in info if i["name"] == "symbol_history")
        assert sh["status"] == "present"
        assert sh["entries"] == "2"

    def test_hash_occurrence_present(self, tmp_path: pathlib.Path) -> None:
        save_hash_occurrence(tmp_path, {"h1": ["a.py::f"], "h2": ["b.py::g"]})
        info = index_info(tmp_path)
        ho = next(i for i in info if i["name"] == "hash_occurrence")
        assert ho["status"] == "present"
        assert ho["entries"] == "2"

    def test_corrupt_index_reported(self, tmp_path: pathlib.Path) -> None:
        indices_dir = tmp_path / ".muse" / "indices"
        indices_dir.mkdir(parents=True, exist_ok=True)
        (indices_dir / "symbol_history.json").write_text("{bad")
        info = index_info(tmp_path)
        sh = next(i for i in info if i["name"] == "symbol_history")
        assert sh["status"] == "corrupt"

    def test_updated_at_present_when_index_exists(self, tmp_path: pathlib.Path) -> None:
        save_hash_occurrence(tmp_path, {"h": ["f.py::x"]})
        info = index_info(tmp_path)
        ho = next(i for i in info if i["name"] == "hash_occurrence")
        assert ho["updated_at"]  # non-empty string

"""Comprehensive unit tests for build_lineage — the symbol provenance engine.

Coverage matrix
---------------
Created     — InsertOp with no prior live symbol sharing the content_id
Copied      — InsertOp whose content_id matches a currently-live symbol
Renamed     — InsertOp + DeleteOp in same commit with matching content_id, same file
Moved       — InsertOp + DeleteOp in same commit with matching content_id, different file
Modified    — ReplaceOp classified as impl_only / signature_change / full_rewrite
Deleted     — DeleteOp at the target address
Multi-event — symbol created, modified, deleted, re-created in the same history
Registry    — incremental content_id registry enables accurate copy detection
              across many commits without re-parsing blobs
No events   — address absent from all commits → empty list
Empty repo  — no commits at all → empty list
"""

from __future__ import annotations

import datetime
import json
import pathlib

import pytest

from muse.cli.commands.lineage import build_lineage
from muse.core.store import CommitRecord, write_commit


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _make_repo(path: pathlib.Path) -> pathlib.Path:
    muse = path / ".muse"
    (muse / "refs" / "heads").mkdir(parents=True)
    (muse / "objects").mkdir()
    (muse / "commits").mkdir()
    (muse / "snapshots").mkdir()
    (muse / "repo.json").write_text(json.dumps({"repo_id": "test", "domain": "midi"}))
    (muse / "HEAD").write_text("ref: refs/heads/main\n")
    return path


_T0 = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)


def _commit(
    root: pathlib.Path,
    commit_id: str,
    ops: list[dict[str, str | list[dict[str, str]]]],
    offset_days: int = 0,
    parent: str | None = None,
) -> CommitRecord:
    """Write a CommitRecord with a structured_delta containing *ops*."""
    committed_at = _T0 + datetime.timedelta(days=offset_days)
    record = CommitRecord(
        commit_id=commit_id,
        repo_id="test",
        branch="main",
        snapshot_id="snap-" + commit_id,
        message=f"commit {commit_id[:8]}",
        committed_at=committed_at,
        parent_commit_id=parent,
        structured_delta={"ops": ops},
    )
    write_commit(root, record)
    return record


def _insert_op(address: str, content_id: str) -> dict[str, str]:
    return {"op": "insert", "address": address, "content_id": content_id}


def _delete_op(address: str, content_id: str) -> dict[str, str]:
    return {"op": "delete", "address": address, "content_id": content_id}


def _replace_op(
    address: str,
    old_content_id: str,
    new_content_id: str,
    old_summary: str = "impl changed",
    new_summary: str = "impl changed",
) -> dict[str, str]:
    return {
        "op": "replace",
        "address": address,
        "old_content_id": old_content_id,
        "new_content_id": new_content_id,
        "old_summary": old_summary,
        "new_summary": new_summary,
    }


# ---------------------------------------------------------------------------
# Empty / no-op cases
# ---------------------------------------------------------------------------


class TestEmptyCases:
    def test_no_commits_returns_empty(self, tmp_path: pathlib.Path) -> None:
        root = _make_repo(tmp_path)
        events = build_lineage(root, "src/billing.py::compute_total")
        assert events == []

    def test_address_not_in_any_commit(self, tmp_path: pathlib.Path) -> None:
        root = _make_repo(tmp_path)
        _commit(root, "a" * 64, [_insert_op("src/auth.py::login", "cid-login")])
        events = build_lineage(root, "src/billing.py::compute_total")
        assert events == []

    def test_commit_with_no_structured_delta(self, tmp_path: pathlib.Path) -> None:
        root = _make_repo(tmp_path)
        record = CommitRecord(
            commit_id="b" * 64,
            repo_id="test",
            branch="main",
            snapshot_id="snap",
            message="no delta",
            committed_at=_T0,
            structured_delta=None,
        )
        write_commit(root, record)
        events = build_lineage(root, "src/main.py::main")
        assert events == []


# ---------------------------------------------------------------------------
# Created
# ---------------------------------------------------------------------------


class TestCreated:
    def test_first_insert_is_created(self, tmp_path: pathlib.Path) -> None:
        root = _make_repo(tmp_path)
        addr = "src/billing.py::compute_total"
        _commit(root, "c" * 64, [_insert_op(addr, "cid-v1")])
        events = build_lineage(root, addr)
        assert len(events) == 1
        assert events[0].kind == "created"

    def test_created_event_has_correct_commit_id(self, tmp_path: pathlib.Path) -> None:
        root = _make_repo(tmp_path)
        addr = "src/main.py::main"
        cid = "d" * 64
        _commit(root, cid, [_insert_op(addr, "cid-v1")])
        events = build_lineage(root, addr)
        assert events[0].commit_id == cid

    def test_created_event_records_content_id(self, tmp_path: pathlib.Path) -> None:
        root = _make_repo(tmp_path)
        addr = "src/main.py::main"
        _commit(root, "e" * 64, [_insert_op(addr, "cid-abc")])
        events = build_lineage(root, addr)
        assert events[0].new_content_id == "cid-abc"


# ---------------------------------------------------------------------------
# Deleted
# ---------------------------------------------------------------------------


class TestDeleted:
    def test_delete_after_insert_is_deleted(self, tmp_path: pathlib.Path) -> None:
        root = _make_repo(tmp_path)
        addr = "src/api.py::get_user"
        _commit(root, "f" * 64, [_insert_op(addr, "cid-v1")], offset_days=0)
        _commit(root, "a1" * 32, [_delete_op(addr, "cid-v1")], offset_days=1)
        events = build_lineage(root, addr)
        assert events[-1].kind == "deleted"

    def test_deleted_event_records_content_id(self, tmp_path: pathlib.Path) -> None:
        root = _make_repo(tmp_path)
        addr = "src/api.py::delete_user"
        _commit(root, "f1" * 32, [_insert_op(addr, "cid-v1")], offset_days=0)
        _commit(root, "f2" * 32, [_delete_op(addr, "cid-v1")], offset_days=1)
        events = build_lineage(root, addr)
        deleted = events[-1]
        assert deleted.kind == "deleted"
        assert deleted.old_content_id == "cid-v1"


# ---------------------------------------------------------------------------
# Modified
# ---------------------------------------------------------------------------


class TestModified:
    def test_replace_op_is_modified(self, tmp_path: pathlib.Path) -> None:
        root = _make_repo(tmp_path)
        addr = "src/core.py::hash_content"
        _commit(root, "g1" * 32, [_insert_op(addr, "cid-v1")], offset_days=0)
        _commit(
            root, "g2" * 32,
            [_replace_op(addr, "cid-v1", "cid-v2")],
            offset_days=1,
        )
        events = build_lineage(root, addr)
        modified = [e for e in events if e.kind == "modified"]
        assert len(modified) == 1

    def test_modified_signature_only(self, tmp_path: pathlib.Path) -> None:
        root = _make_repo(tmp_path)
        addr = "src/core.py::process"
        _commit(root, "h1" * 32, [_insert_op(addr, "cid-v1")], offset_days=0)
        _commit(
            root, "h2" * 32,
            [_replace_op(addr, "cid-v1", "cid-v2", "signature changed", "signature changed")],
            offset_days=1,
        )
        events = build_lineage(root, addr)
        modified = [e for e in events if e.kind == "modified"]
        assert modified[0].detail == "signature_change"

    def test_modified_full_rewrite(self, tmp_path: pathlib.Path) -> None:
        root = _make_repo(tmp_path)
        addr = "src/core.py::transform"
        _commit(root, "i1" * 32, [_insert_op(addr, "cid-aaaa")], offset_days=0)
        _commit(
            root, "i2" * 32,
            [_replace_op(addr, "cid-aaaa", "cid-bbbb", "impl changed", "impl changed")],
            offset_days=1,
        )
        events = build_lineage(root, addr)
        modified = [e for e in events if e.kind == "modified"]
        assert modified[0].detail == "full_rewrite"

    def test_multiple_modifications(self, tmp_path: pathlib.Path) -> None:
        root = _make_repo(tmp_path)
        addr = "src/worker.py::run"
        _commit(root, "j1" * 32, [_insert_op(addr, "cid-v1")], offset_days=0)
        _commit(root, "j2" * 32, [_replace_op(addr, "cid-v1", "cid-v2")], offset_days=1)
        _commit(root, "j3" * 32, [_replace_op(addr, "cid-v2", "cid-v3")], offset_days=2)
        events = build_lineage(root, addr)
        modified = [e for e in events if e.kind == "modified"]
        assert len(modified) == 2


# ---------------------------------------------------------------------------
# Renamed
# ---------------------------------------------------------------------------


class TestRenamed:
    def test_insert_delete_same_file_is_renamed(self, tmp_path: pathlib.Path) -> None:
        root = _make_repo(tmp_path)
        old_addr = "src/billing.py::_compute_total"
        new_addr = "src/billing.py::compute_total"
        _commit(root, "k1" * 32, [_insert_op(old_addr, "cid-body")], offset_days=0)
        _commit(
            root, "k2" * 32,
            [
                _insert_op(new_addr, "cid-body"),
                _delete_op(old_addr, "cid-body"),
            ],
            offset_days=1,
        )
        events = build_lineage(root, new_addr)
        assert any(e.kind == "renamed_from" for e in events)

    def test_renamed_from_detail_is_source_address(self, tmp_path: pathlib.Path) -> None:
        root = _make_repo(tmp_path)
        old_addr = "src/billing.py::_inner"
        new_addr = "src/billing.py::public_api"
        _commit(root, "l1" * 32, [_insert_op(old_addr, "cid-body")], offset_days=0)
        _commit(
            root, "l2" * 32,
            [_insert_op(new_addr, "cid-body"), _delete_op(old_addr, "cid-body")],
            offset_days=1,
        )
        events = build_lineage(root, new_addr)
        renamed = [e for e in events if e.kind == "renamed_from"]
        assert renamed[0].detail == old_addr


# ---------------------------------------------------------------------------
# Moved
# ---------------------------------------------------------------------------


class TestMoved:
    def test_insert_delete_different_file_is_moved(self, tmp_path: pathlib.Path) -> None:
        root = _make_repo(tmp_path)
        old_addr = "old/billing.py::compute_invoice_total"
        new_addr = "src/billing.py::compute_invoice_total"
        _commit(root, "m1" * 32, [_insert_op(old_addr, "cid-body")], offset_days=0)
        _commit(
            root, "m2" * 32,
            [_insert_op(new_addr, "cid-body"), _delete_op(old_addr, "cid-body")],
            offset_days=1,
        )
        events = build_lineage(root, new_addr)
        assert any(e.kind == "moved_from" for e in events)

    def test_moved_from_detail_is_original_address(self, tmp_path: pathlib.Path) -> None:
        root = _make_repo(tmp_path)
        old_addr = "legacy/module.py::process"
        new_addr = "src/processing.py::process"
        _commit(root, "n1" * 32, [_insert_op(old_addr, "cid-body")], offset_days=0)
        _commit(
            root, "n2" * 32,
            [_insert_op(new_addr, "cid-body"), _delete_op(old_addr, "cid-body")],
            offset_days=1,
        )
        events = build_lineage(root, new_addr)
        moved = [e for e in events if e.kind == "moved_from"]
        assert moved[0].detail == old_addr


# ---------------------------------------------------------------------------
# Copied
# ---------------------------------------------------------------------------


class TestCopied:
    def test_insert_matching_live_symbol_is_copied(self, tmp_path: pathlib.Path) -> None:
        root = _make_repo(tmp_path)
        original_addr = "src/utils.py::helper"
        copy_addr = "src/other.py::helper"
        shared_cid = "cid-shared-body"
        # Commit 1: create original
        _commit(root, "o1" * 32, [_insert_op(original_addr, shared_cid)], offset_days=0)
        # Commit 2: insert copy (same content_id, different address, no delete)
        _commit(root, "o2" * 32, [_insert_op(copy_addr, shared_cid)], offset_days=1)
        events = build_lineage(root, copy_addr)
        assert any(e.kind == "copied_from" for e in events)

    def test_copied_from_detail_is_source_address(self, tmp_path: pathlib.Path) -> None:
        root = _make_repo(tmp_path)
        original = "src/utils.py::helper"
        copy = "src/other.py::helper"
        shared = "cid-shared"
        _commit(root, "p1" * 32, [_insert_op(original, shared)], offset_days=0)
        _commit(root, "p2" * 32, [_insert_op(copy, shared)], offset_days=1)
        events = build_lineage(root, copy)
        copied = [e for e in events if e.kind == "copied_from"]
        assert copied[0].detail == original

    def test_no_copy_if_source_is_dead(self, tmp_path: pathlib.Path) -> None:
        """If the source was deleted before the copy, it should be 'created' not 'copied'."""
        root = _make_repo(tmp_path)
        original = "src/utils.py::helper"
        copy = "src/other.py::helper"
        shared = "cid-shared"
        _commit(root, "q1" * 32, [_insert_op(original, shared)], offset_days=0)
        _commit(root, "q2" * 32, [_delete_op(original, shared)], offset_days=1)
        _commit(root, "q3" * 32, [_insert_op(copy, shared)], offset_days=2)
        events = build_lineage(root, copy)
        # After delete, the registry no longer has original as live.
        # So re-insert at copy address should be 'created', not 'copied'.
        insert_events = [e for e in events if e.kind in ("created", "copied_from")]
        assert insert_events[0].kind == "created"


# ---------------------------------------------------------------------------
# Complex multi-event sequences
# ---------------------------------------------------------------------------


class TestMultiEvent:
    def test_create_modify_delete_sequence(self, tmp_path: pathlib.Path) -> None:
        root = _make_repo(tmp_path)
        addr = "src/core.py::process"
        _commit(root, "r1" * 32, [_insert_op(addr, "cid-v1")], offset_days=0)
        _commit(root, "r2" * 32, [_replace_op(addr, "cid-v1", "cid-v2")], offset_days=1)
        _commit(root, "r3" * 32, [_delete_op(addr, "cid-v2")], offset_days=2)
        events = build_lineage(root, addr)
        kinds = [e.kind for e in events]
        assert kinds == ["created", "modified", "deleted"]

    def test_delete_then_recreate(self, tmp_path: pathlib.Path) -> None:
        root = _make_repo(tmp_path)
        addr = "src/api.py::endpoint"
        _commit(root, "s1" * 32, [_insert_op(addr, "cid-v1")], offset_days=0)
        _commit(root, "s2" * 32, [_delete_op(addr, "cid-v1")], offset_days=1)
        _commit(root, "s3" * 32, [_insert_op(addr, "cid-v2")], offset_days=2)
        events = build_lineage(root, addr)
        kinds = [e.kind for e in events]
        assert "created" in kinds
        assert "deleted" in kinds
        assert kinds.count("created") == 2 or kinds[-1] == "created"

    def test_ordered_by_commit_timestamp(self, tmp_path: pathlib.Path) -> None:
        root = _make_repo(tmp_path)
        addr = "src/main.py::main"
        # Write commits out of temporal order — lineage must sort them.
        _commit(root, "t2" * 32, [_replace_op(addr, "cid-v1", "cid-v2")], offset_days=2)
        _commit(root, "t1" * 32, [_insert_op(addr, "cid-v1")], offset_days=0)
        events = build_lineage(root, addr)
        assert events[0].kind == "created"
        assert events[1].kind == "modified"

    def test_many_commits_accumulate_all_events(self, tmp_path: pathlib.Path) -> None:
        root = _make_repo(tmp_path)
        addr = "src/worker.py::run"
        _commit(root, "u0" * 32, [_insert_op(addr, "cid-0")], offset_days=0)
        prev = "cid-0"
        for i in range(1, 10):
            nxt = f"cid-{i}"
            _commit(root, f"u{i}" * (64 // len(f"u{i}")), [_replace_op(addr, prev, nxt)], offset_days=i)
            prev = nxt
        events = build_lineage(root, addr)
        assert len(events) == 10  # 1 created + 9 modified


# ---------------------------------------------------------------------------
# Incremental registry — copy detection across many commits
# ---------------------------------------------------------------------------


class TestIncrementalRegistry:
    def test_registry_tracks_all_live_symbols(self, tmp_path: pathlib.Path) -> None:
        """The incremental registry must track symbols in commits that don't touch the target."""
        root = _make_repo(tmp_path)
        shared_cid = "cid-shared"
        # Commit 1: insert original at a *different* address (not the target).
        _commit(root, "v1" * 32, [_insert_op("src/a.py::foo", shared_cid)], offset_days=0)
        # Commit 2: insert the target using the same content_id → should be copied_from.
        _commit(root, "v2" * 32, [_insert_op("src/b.py::foo", shared_cid)], offset_days=1)
        events = build_lineage(root, "src/b.py::foo")
        assert events[0].kind == "copied_from"
        assert events[0].detail == "src/a.py::foo"

    def test_registry_prunes_deleted_symbols(self, tmp_path: pathlib.Path) -> None:
        """After deleting the original, its content_id must leave the live registry."""
        root = _make_repo(tmp_path)
        shared = "cid-shared"
        original = "src/a.py::foo"
        target = "src/b.py::foo"
        _commit(root, "w1" * 32, [_insert_op(original, shared)], offset_days=0)
        _commit(root, "w2" * 32, [_delete_op(original, shared)], offset_days=1)
        _commit(root, "w3" * 32, [_insert_op(target, shared)], offset_days=2)
        events = build_lineage(root, target)
        assert events[0].kind == "created"  # not copied — source is dead

    def test_registry_across_ten_intermediate_commits(self, tmp_path: pathlib.Path) -> None:
        """Original inserted in commit 1; target copied in commit 12 — registry must survive."""
        root = _make_repo(tmp_path)
        shared = "cid-shared"
        original = "src/lib.py::util"
        target = "src/app.py::util"

        _commit(root, "x0" * 32, [_insert_op(original, shared)], offset_days=0)
        # 10 unrelated commits that don't touch original or target.
        for i in range(1, 11):
            _commit(
                root, f"x{i}" * (64 // len(f"x{i}")),
                [_insert_op(f"src/other_{i}.py::fn", f"cid-other-{i}")],
                offset_days=i,
            )
        # Target is inserted 11 days later — registry must still know original is live.
        _commit(root, "x11" * 16, [_insert_op(target, shared)], offset_days=11)
        events = build_lineage(root, target)
        assert events[0].kind == "copied_from"

    def test_json_output_shape(self, tmp_path: pathlib.Path) -> None:
        """to_dict() must return the expected keys in correct types."""
        root = _make_repo(tmp_path)
        addr = "src/main.py::main"
        _commit(root, "y1" * 32, [_insert_op(addr, "cid-abc123456789")], offset_days=0)
        events = build_lineage(root, addr)
        d = events[0].to_dict()
        assert "commit_id" in d
        assert "committed_at" in d
        assert "event" in d
        assert d["event"] == "created"
        assert len(d["commit_id"]) == 8  # truncated to 8 chars

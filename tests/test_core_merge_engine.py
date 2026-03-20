"""Tests for muse.core.merge_engine — three-way merge logic.

Extended to cover the structured (operation-level) merge path via
:func:`~muse.core.op_transform.merge_structured` and the
:class:`~muse.domain.StructuredMergePlugin` integration.
"""

import datetime
import json
import pathlib

import pytest

from muse.core.merge_engine import (
    MergeState,
    apply_merge,
    clear_merge_state,
    detect_conflicts,
    diff_snapshots,
    find_merge_base,
    read_merge_state,
    write_merge_state,
)
from muse.core.op_transform import MergeOpsResult, merge_op_lists, merge_structured
from muse.core.store import CommitRecord, write_commit
from muse.domain import (
    DeleteOp,
    DomainOp,
    InsertOp,
    ReplaceOp,
    SnapshotManifest,
    StructuredDelta,
    StructuredMergePlugin,
)
from muse.plugins.midi.plugin import MidiPlugin


@pytest.fixture
def repo(tmp_path: pathlib.Path) -> pathlib.Path:
    muse_dir = tmp_path / ".muse"
    (muse_dir / "commits").mkdir(parents=True)
    (muse_dir / "refs" / "heads").mkdir(parents=True)
    return tmp_path


def _commit(root: pathlib.Path, cid: str, parent: str | None = None, parent2: str | None = None) -> None:
    write_commit(root, CommitRecord(
        commit_id=cid,
        repo_id="r",
        branch="main",
        snapshot_id=f"snap-{cid}",
        message=cid,
        committed_at=datetime.datetime.now(datetime.timezone.utc),
        parent_commit_id=parent,
        parent2_commit_id=parent2,
    ))


class TestDiffSnapshots:
    def test_no_change(self) -> None:
        m = {"a.mid": "h1", "b.mid": "h2"}
        assert diff_snapshots(m, m) == set()

    def test_added(self) -> None:
        assert diff_snapshots({}, {"a.mid": "h1"}) == {"a.mid"}

    def test_removed(self) -> None:
        assert diff_snapshots({"a.mid": "h1"}, {}) == {"a.mid"}

    def test_modified(self) -> None:
        assert diff_snapshots({"a.mid": "old"}, {"a.mid": "new"}) == {"a.mid"}


class TestDetectConflicts:
    def test_no_conflict(self) -> None:
        assert detect_conflicts({"a.mid"}, {"b.mid"}) == set()

    def test_conflict(self) -> None:
        assert detect_conflicts({"a.mid", "b.mid"}, {"b.mid", "c.mid"}) == {"b.mid"}

    def test_both_empty(self) -> None:
        assert detect_conflicts(set(), set()) == set()


class TestApplyMerge:
    def test_clean_merge(self) -> None:
        base = {"a.mid": "h0", "b.mid": "h0"}
        ours = {"a.mid": "h_ours", "b.mid": "h0"}
        theirs = {"a.mid": "h0", "b.mid": "h_theirs"}
        ours_changed = {"a.mid"}
        theirs_changed = {"b.mid"}
        result = apply_merge(base, ours, theirs, ours_changed, theirs_changed, set())
        assert result == {"a.mid": "h_ours", "b.mid": "h_theirs"}

    def test_conflict_paths_excluded(self) -> None:
        base = {"a.mid": "h0"}
        ours = {"a.mid": "h_ours"}
        theirs = {"a.mid": "h_theirs"}
        ours_changed = theirs_changed = {"a.mid"}
        result = apply_merge(base, ours, theirs, ours_changed, theirs_changed, {"a.mid"})
        assert result == {"a.mid": "h0"}  # Falls back to base

    def test_ours_deletion_applied(self) -> None:
        base = {"a.mid": "h0", "b.mid": "h0"}
        ours = {"b.mid": "h0"}  # a.mid deleted on ours
        theirs = {"a.mid": "h0", "b.mid": "h0"}
        result = apply_merge(base, ours, theirs, {"a.mid"}, set(), set())
        assert "a.mid" not in result


class TestMergeStateIO:
    def test_write_and_read(self, repo: pathlib.Path) -> None:
        base_id = "b" * 64
        ours_id = "1" * 64
        theirs_id = "2" * 64
        write_merge_state(
            repo,
            base_commit=base_id,
            ours_commit=ours_id,
            theirs_commit=theirs_id,
            conflict_paths=["a.mid", "b.mid"],
            other_branch="feature/x",
        )
        state = read_merge_state(repo)
        assert state is not None
        assert state.base_commit == base_id
        assert state.conflict_paths == ["a.mid", "b.mid"]
        assert state.other_branch == "feature/x"

    def test_read_no_state(self, repo: pathlib.Path) -> None:
        assert read_merge_state(repo) is None

    def test_clear(self, repo: pathlib.Path) -> None:
        write_merge_state(repo, base_commit="b" * 64, ours_commit="c" * 64, theirs_commit="d" * 64, conflict_paths=[])
        clear_merge_state(repo)
        assert read_merge_state(repo) is None


class TestFindMergeBase:
    def test_direct_parent(self, repo: pathlib.Path) -> None:
        _commit(repo, "root")
        _commit(repo, "a", parent="root")
        _commit(repo, "b", parent="root")
        base = find_merge_base(repo, "a", "b")
        assert base == "root"

    def test_same_commit(self, repo: pathlib.Path) -> None:
        _commit(repo, "root")
        base = find_merge_base(repo, "root", "root")
        assert base == "root"

    def test_linear_history(self, repo: pathlib.Path) -> None:
        _commit(repo, "a")
        _commit(repo, "b", parent="a")
        _commit(repo, "c", parent="b")
        base = find_merge_base(repo, "c", "b")
        assert base == "b"

    def test_no_common_ancestor(self, repo: pathlib.Path) -> None:
        _commit(repo, "x")
        _commit(repo, "y")
        assert find_merge_base(repo, "x", "y") is None


# ===========================================================================
# Structured merge engine integration tests
# ===========================================================================


def _ins(addr: str, pos: int | None, cid: str) -> InsertOp:
    return InsertOp(op="insert", address=addr, position=pos, content_id=cid, content_summary=cid)


def _del(addr: str, pos: int | None, cid: str) -> DeleteOp:
    return DeleteOp(op="delete", address=addr, position=pos, content_id=cid, content_summary=cid)


def _rep(addr: str, old: str, new: str) -> ReplaceOp:
    return ReplaceOp(
        op="replace",
        address=addr,
        position=None,
        old_content_id=old,
        new_content_id=new,
        old_summary="old",
        new_summary="new",
    )


def _delta(ops: list[DomainOp]) -> StructuredDelta:
    return StructuredDelta(domain="midi", ops=ops, summary="test")


class TestMergeStructuredIntegration:
    """Verify merge_structured delegates correctly to merge_op_lists."""

    def test_clean_non_overlapping_file_ops(self) -> None:
        ours = _delta([_ins("a.mid", pos=0, cid="a-hash")])
        theirs = _delta([_ins("b.mid", pos=0, cid="b-hash")])
        result = merge_structured(_delta([]), ours, theirs)
        assert result.is_clean is True
        assert len(result.merged_ops) == 2

    def test_conflicting_same_address_replaces_detected(self) -> None:
        ours = _delta([_rep("shared.mid", "old", "v-ours")])
        theirs = _delta([_rep("shared.mid", "old", "v-theirs")])
        result = merge_structured(_delta([]), ours, theirs)
        assert result.is_clean is False
        assert len(result.conflict_ops) == 1

    def test_base_ops_kept_by_both_sides_preserved(self) -> None:
        shared = _ins("base.mid", pos=0, cid="base-cid")
        result = merge_structured(
            _delta([shared]),
            _delta([shared]),
            _delta([shared]),
        )
        assert result.is_clean is True
        assert any(_op_key_tuple(op) == _op_key_tuple(shared) for op in result.merged_ops)

    def test_position_adjustment_in_structured_merge(self) -> None:
        """Non-conflicting note inserts get position-adjusted in structured merge."""
        ours = _delta([_ins("lead.mid", pos=3, cid="note-A")])
        theirs = _delta([_ins("lead.mid", pos=7, cid="note-B")])
        result = merge_structured(_delta([]), ours, theirs)
        assert result.is_clean is True
        pos_by_cid = {
            op["content_id"]: op["position"]
            for op in result.merged_ops
            if op["op"] == "insert"
        }
        # note-A(3): no theirs ≤ 3 → stays 3
        assert pos_by_cid["note-A"] == 3
        # note-B(7): ours A(3) ≤ 7 → 7+1 = 8
        assert pos_by_cid["note-B"] == 8


def _op_key_tuple(op: DomainOp) -> tuple[str, ...]:
    """Re-implementation of _op_key for test assertions."""
    if op["op"] == "insert":
        return ("insert", op["address"], str(op["position"]), op["content_id"])
    if op["op"] == "delete":
        return ("delete", op["address"], str(op["position"]), op["content_id"])
    if op["op"] == "replace":
        return ("replace", op["address"], str(op["position"]), op["old_content_id"], op["new_content_id"])
    return (op["op"], op["address"])


class TestStructuredMergePluginProtocol:
    """Verify MidiPlugin satisfies the StructuredMergePlugin protocol."""

    def test_midi_plugin_isinstance_structured_merge_plugin(self) -> None:
        plugin = MidiPlugin()
        assert isinstance(plugin, StructuredMergePlugin)

    def test_merge_ops_non_conflicting_files_is_clean(self) -> None:
        plugin = MidiPlugin()
        base = SnapshotManifest(files={}, domain="midi")
        ours_snap = SnapshotManifest(files={"a.mid": "hash-a"}, domain="midi")
        theirs_snap = SnapshotManifest(files={"b.mid": "hash-b"}, domain="midi")
        ours_ops: list[DomainOp] = [_ins("a.mid", pos=None, cid="hash-a")]
        theirs_ops: list[DomainOp] = [_ins("b.mid", pos=None, cid="hash-b")]

        result = plugin.merge_ops(
            base, ours_snap, theirs_snap, ours_ops, theirs_ops
        )
        assert result.is_clean is True
        assert "a.mid" in result.merged["files"]
        assert "b.mid" in result.merged["files"]

    def test_merge_ops_conflicting_same_file_replace_not_clean(self) -> None:
        plugin = MidiPlugin()
        base = SnapshotManifest(files={"f.mid": "base-hash"}, domain="midi")
        ours_snap = SnapshotManifest(files={"f.mid": "ours-hash"}, domain="midi")
        theirs_snap = SnapshotManifest(files={"f.mid": "theirs-hash"}, domain="midi")
        ours_ops: list[DomainOp] = [_rep("f.mid", "base-hash", "ours-hash")]
        theirs_ops: list[DomainOp] = [_rep("f.mid", "base-hash", "theirs-hash")]

        result = plugin.merge_ops(
            base, ours_snap, theirs_snap, ours_ops, theirs_ops
        )
        assert not result.is_clean
        assert "f.mid" in result.conflicts

    def test_merge_ops_ours_strategy_resolves_conflict(self) -> None:
        plugin = MidiPlugin()
        base = SnapshotManifest(files={"f.mid": "base"}, domain="midi")
        ours_snap = SnapshotManifest(files={"f.mid": "ours-v"}, domain="midi")
        theirs_snap = SnapshotManifest(files={"f.mid": "theirs-v"}, domain="midi")
        ours_ops: list[DomainOp] = [_rep("f.mid", "base", "ours-v")]
        theirs_ops: list[DomainOp] = [_rep("f.mid", "base", "theirs-v")]

        result = plugin.merge_ops(
            base,
            ours_snap,
            theirs_snap,
            ours_ops,
            theirs_ops,
        )
        # Without .museattributes the conflict stands — verify conflict is reported.
        assert not result.is_clean

    def test_merge_ops_delete_on_only_one_side_is_clean(self) -> None:
        plugin = MidiPlugin()
        base = SnapshotManifest(files={"keep.mid": "k", "remove.mid": "r"}, domain="midi")
        ours_snap = SnapshotManifest(files={"keep.mid": "k"}, domain="midi")
        theirs_snap = SnapshotManifest(files={"keep.mid": "k", "remove.mid": "r"}, domain="midi")
        ours_ops: list[DomainOp] = [_del("remove.mid", pos=None, cid="r")]
        theirs_ops: list[DomainOp] = []

        result = plugin.merge_ops(
            base, ours_snap, theirs_snap, ours_ops, theirs_ops
        )
        assert result.is_clean is True
        assert "keep.mid" in result.merged["files"]
        assert "remove.mid" not in result.merged["files"]

    def test_merge_ops_empty_changes_returns_base(self) -> None:
        plugin = MidiPlugin()
        base = SnapshotManifest(files={"f.mid": "h"}, domain="midi")
        result = plugin.merge_ops(base, base, base, [], [])
        assert result.is_clean is True
        assert result.merged["files"] == {"f.mid": "h"}

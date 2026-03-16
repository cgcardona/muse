"""Tests for muse.core.merge_engine — three-way merge logic."""
from __future__ import annotations

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
from muse.core.store import CommitRecord, write_commit
import datetime


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
        write_merge_state(
            repo,
            base_commit="base",
            ours_commit="ours",
            theirs_commit="theirs",
            conflict_paths=["a.mid", "b.mid"],
            other_branch="feature/x",
        )
        state = read_merge_state(repo)
        assert state is not None
        assert state.base_commit == "base"
        assert state.conflict_paths == ["a.mid", "b.mid"]
        assert state.other_branch == "feature/x"

    def test_read_no_state(self, repo: pathlib.Path) -> None:
        assert read_merge_state(repo) is None

    def test_clear(self, repo: pathlib.Path) -> None:
        write_merge_state(repo, base_commit="b", ours_commit="o", theirs_commit="t", conflict_paths=[])
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

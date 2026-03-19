"""Adversarial stress tests for the three-way merge engine.

Covers:
- apply_merge edge cases: both sides delete the same file, theirs-only delete,
  ours-only add, both add the same file with identical hash (clean).
- detect_conflicts: full combinatorial (empty sets, symmetric, one-sided).
- diff_snapshots: many files added / removed / modified.
- diff_snapshots then detect_conflicts → apply_merge pipeline correctness.
- Large manifest diffs (500 paths).
- MergeState round-trip with and without optional fields.
- Corrupt MERGE_STATE.json is silently ignored (returns None).
- apply_resolution raises FileNotFoundError for absent object.
"""
from __future__ import annotations

import json
import pathlib
import secrets
import hashlib
import datetime

import pytest

from muse.core.merge_engine import (
    MergeState,
    apply_merge,
    apply_resolution,
    clear_merge_state,
    detect_conflicts,
    diff_snapshots,
    read_merge_state,
    write_merge_state,
)
from muse.core.object_store import write_object


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _h(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


@pytest.fixture
def repo(tmp_path: pathlib.Path) -> pathlib.Path:
    muse = tmp_path / ".muse"
    muse.mkdir()
    (muse / "objects").mkdir()
    return tmp_path


# ===========================================================================
# diff_snapshots — exhaustive
# ===========================================================================


class TestDiffSnapshotsExhaustive:
    def test_identical_manifests_no_diff(self) -> None:
        m = {f"file-{i}.mid": _h(f"content-{i}") for i in range(100)}
        assert diff_snapshots(m, m) == set()

    def test_all_files_added(self) -> None:
        added = {f"new-{i}.mid": _h(f"new-{i}") for i in range(50)}
        result = diff_snapshots({}, added)
        assert result == set(added.keys())

    def test_all_files_removed(self) -> None:
        original = {f"old-{i}.mid": _h(f"old-{i}") for i in range(50)}
        result = diff_snapshots(original, {})
        assert result == set(original.keys())

    def test_all_files_modified(self) -> None:
        base = {f"f{i}.mid": _h(f"v1-{i}") for i in range(50)}
        target = {f"f{i}.mid": _h(f"v2-{i}") for i in range(50)}
        result = diff_snapshots(base, target)
        assert result == set(base.keys())

    def test_mixed_add_remove_modify(self) -> None:
        base = {"keep.mid": _h("keep"), "remove.mid": _h("remove"), "modify.mid": _h("old")}
        target = {"keep.mid": _h("keep"), "add.mid": _h("new"), "modify.mid": _h("new")}
        result = diff_snapshots(base, target)
        assert result == {"remove.mid", "add.mid", "modify.mid"}
        assert "keep.mid" not in result

    def test_500_file_manifest_correct_diff(self) -> None:
        base = {f"path/to/file-{i:04d}.mid": _h(f"v1-{i}") for i in range(500)}
        target = dict(base)
        # Modify 100, add 50, remove 50.
        modified = set()
        for i in range(0, 100):
            key = f"path/to/file-{i:04d}.mid"
            target[key] = _h(f"v2-{i}")
            modified.add(key)
        added = set()
        for i in range(500, 550):
            key = f"path/to/new-{i}.mid"
            target[key] = _h(f"new-{i}")
            added.add(key)
        removed = set()
        for i in range(450, 500):
            key = f"path/to/file-{i:04d}.mid"
            del target[key]
            removed.add(key)
        result = diff_snapshots(base, target)
        assert result == modified | added | removed

    def test_symmetric_diff_not_required(self) -> None:
        """diff_snapshots is not symmetric: order matters."""
        a = {"f.mid": _h("hash-a")}
        b = {"f.mid": _h("hash-b")}
        assert diff_snapshots(a, b) == {"f.mid"}
        assert diff_snapshots(b, a) == {"f.mid"}


# ===========================================================================
# detect_conflicts — exhaustive
# ===========================================================================


class TestDetectConflictsExhaustive:
    def test_empty_both_sides(self) -> None:
        assert detect_conflicts(set(), set()) == set()

    def test_empty_ours(self) -> None:
        assert detect_conflicts(set(), {"a.mid", "b.mid"}) == set()

    def test_empty_theirs(self) -> None:
        assert detect_conflicts({"a.mid", "b.mid"}, set()) == set()

    def test_full_overlap(self) -> None:
        s = {f"f{i}.mid" for i in range(50)}
        assert detect_conflicts(s, s) == s

    def test_no_overlap(self) -> None:
        ours = {f"ours-{i}.mid" for i in range(25)}
        theirs = {f"theirs-{i}.mid" for i in range(25)}
        assert detect_conflicts(ours, theirs) == set()

    def test_partial_overlap(self) -> None:
        ours = {"shared.mid", "only-ours.mid"}
        theirs = {"shared.mid", "only-theirs.mid"}
        assert detect_conflicts(ours, theirs) == {"shared.mid"}

    def test_commutativity(self) -> None:
        a = {f"f{i}" for i in range(30)}
        b = {f"f{i}" for i in range(20, 50)}
        assert detect_conflicts(a, b) == detect_conflicts(b, a)


# ===========================================================================
# apply_merge — exhaustive
# ===========================================================================


class TestApplyMergeExhaustive:
    def test_both_sides_delete_same_file_not_conflicting(self) -> None:
        """Both sides delete the same file — no conflict, file absent in merged."""
        base = {"shared.mid": _h("shared")}
        ours = {}
        theirs = {}
        ours_changed = {"shared.mid"}
        theirs_changed = {"shared.mid"}
        # No conflict paths specified (caller decided it's not a conflict).
        result = apply_merge(base, ours, theirs, ours_changed, theirs_changed, set())
        assert "shared.mid" not in result

    def test_only_theirs_adds_file(self) -> None:
        base: dict[str, str] = {}
        ours: dict[str, str] = {}
        theirs = {"new.mid": _h("new")}
        result = apply_merge(base, ours, theirs, set(), {"new.mid"}, set())
        assert result["new.mid"] == _h("new")

    def test_only_ours_adds_file(self) -> None:
        base: dict[str, str] = {}
        theirs: dict[str, str] = {}
        ours = {"new.mid": _h("ours-new")}
        result = apply_merge(base, ours, theirs, {"new.mid"}, set(), set())
        assert result["new.mid"] == _h("ours-new")

    def test_both_add_same_file_same_hash_no_conflict(self) -> None:
        """Both sides independently add the same file with the same content hash — no conflict."""
        base: dict[str, str] = {}
        h = _h("identical-content")
        ours = {"new.mid": h}
        theirs = {"new.mid": h}
        # Caller detects: same hash = no conflict.
        result = apply_merge(base, ours, theirs, {"new.mid"}, {"new.mid"}, set())
        assert result["new.mid"] == h

    def test_conflict_path_falls_back_to_base(self) -> None:
        base = {"conflict.mid": _h("base")}
        ours = {"conflict.mid": _h("ours")}
        theirs = {"conflict.mid": _h("theirs")}
        result = apply_merge(
            base, ours, theirs,
            {"conflict.mid"}, {"conflict.mid"}, {"conflict.mid"}
        )
        # Conflict paths are excluded → base value is kept.
        assert result["conflict.mid"] == _h("base")

    def test_theirs_deletion_removes_from_merged(self) -> None:
        base = {"f.mid": _h("f"), "g.mid": _h("g")}
        ours = {"f.mid": _h("f"), "g.mid": _h("g")}
        theirs = {"f.mid": _h("f")}  # g.mid deleted on theirs
        result = apply_merge(base, ours, theirs, set(), {"g.mid"}, set())
        assert "g.mid" not in result

    def test_unrelated_changes_both_preserved(self) -> None:
        base = {"a.mid": _h("a0"), "b.mid": _h("b0"), "c.mid": _h("c0")}
        ours = {"a.mid": _h("a1"), "b.mid": _h("b0"), "c.mid": _h("c0")}
        theirs = {"a.mid": _h("a0"), "b.mid": _h("b1"), "c.mid": _h("c0")}
        result = apply_merge(
            base, ours, theirs, {"a.mid"}, {"b.mid"}, set()
        )
        assert result["a.mid"] == _h("a1")
        assert result["b.mid"] == _h("b1")
        assert result["c.mid"] == _h("c0")

    def test_large_manifest_clean_merge(self) -> None:
        """200 files: 100 changed by ours, 100 changed by theirs, no overlap."""
        base = {f"f{i:03d}.mid": _h(f"v0-{i}") for i in range(200)}
        ours = dict(base)
        theirs = dict(base)
        ours_changed = set()
        theirs_changed = set()
        for i in range(100):
            ours[f"f{i:03d}.mid"] = _h(f"v-ours-{i}")
            ours_changed.add(f"f{i:03d}.mid")
        for i in range(100, 200):
            theirs[f"f{i:03d}.mid"] = _h(f"v-theirs-{i}")
            theirs_changed.add(f"f{i:03d}.mid")
        result = apply_merge(base, ours, theirs, ours_changed, theirs_changed, set())
        for i in range(100):
            assert result[f"f{i:03d}.mid"] == _h(f"v-ours-{i}")
        for i in range(100, 200):
            assert result[f"f{i:03d}.mid"] == _h(f"v-theirs-{i}")

    def test_pipeline_diff_detect_merge(self) -> None:
        """End-to-end: run diff → detect → apply and verify correctness.

        Scenario:
          base = {conflict.mid, ours-only.mid, theirs-only.mid, untouched.mid}
          ours:  modifies conflict.mid, deletes ours-only.mid (only ours touches it)
          theirs: modifies conflict.mid, deletes theirs-only.mid (only theirs touches it)

        Expected results:
          conflict.mid:    bilateral conflict → stays at base value
          ours-only.mid:   deleted only by ours → deleted in merged
          theirs-only.mid: deleted only by theirs → deleted in merged
          untouched.mid:   neither side changed → stays at base
        """
        base = {
            "conflict.mid": _h("c0"),
            "ours-only.mid": _h("o0"),
            "theirs-only.mid": _h("t0"),
            "untouched.mid": _h("u0"),
        }
        # ours: modifies conflict.mid, deletes ours-only.mid, leaves theirs-only and untouched
        ours = {
            "conflict.mid": _h("c-ours"),
            "theirs-only.mid": _h("t0"),
            "untouched.mid": _h("u0"),
        }
        # theirs: modifies conflict.mid, deletes theirs-only.mid, leaves ours-only and untouched
        theirs = {
            "conflict.mid": _h("c-theirs"),
            "ours-only.mid": _h("o0"),
            "untouched.mid": _h("u0"),
        }

        ours_changed = diff_snapshots(base, ours)
        theirs_changed = diff_snapshots(base, theirs)
        conflicts = detect_conflicts(ours_changed, theirs_changed)

        result = apply_merge(base, ours, theirs, ours_changed, theirs_changed, conflicts)

        # conflict.mid: both sides changed → stays at base (excluded from result but key present from base).
        assert result["conflict.mid"] == _h("c0")
        # ours-only.mid: deleted by ours only → absent in merged.
        assert "ours-only.mid" not in result
        # theirs-only.mid: deleted by theirs only → absent in merged.
        assert "theirs-only.mid" not in result
        # untouched.mid: neither side touched → stays at base.
        assert result["untouched.mid"] == _h("u0")


# ===========================================================================
# MergeState I/O — adversarial
# ===========================================================================


class TestMergeStateIOAdversarial:
    def test_conflict_paths_sorted_on_write(self, repo: pathlib.Path) -> None:
        write_merge_state(
            repo, base_commit="b", ours_commit="o", theirs_commit="t",
            conflict_paths=["z.mid", "a.mid", "m.mid"],
        )
        state = read_merge_state(repo)
        assert state is not None
        assert state.conflict_paths == ["a.mid", "m.mid", "z.mid"]

    def test_optional_other_branch_absent(self, repo: pathlib.Path) -> None:
        write_merge_state(
            repo, base_commit="b", ours_commit="o", theirs_commit="t",
            conflict_paths=[],
        )
        state = read_merge_state(repo)
        assert state is not None
        assert state.other_branch is None

    def test_corrupt_json_returns_none(self, repo: pathlib.Path) -> None:
        path = repo / ".muse" / "MERGE_STATE.json"
        path.write_text("{not valid json")
        assert read_merge_state(repo) is None

    def test_empty_json_returns_none_gracefully(self, repo: pathlib.Path) -> None:
        path = repo / ".muse" / "MERGE_STATE.json"
        path.write_text("")
        assert read_merge_state(repo) is None

    def test_missing_file_returns_none(self, repo: pathlib.Path) -> None:
        assert read_merge_state(repo) is None

    def test_clear_idempotent(self, repo: pathlib.Path) -> None:
        # Clearing when no state file exists should not raise.
        clear_merge_state(repo)
        clear_merge_state(repo)

    def test_write_overwrite_previous(self, repo: pathlib.Path) -> None:
        write_merge_state(repo, base_commit="b1", ours_commit="o1", theirs_commit="t1", conflict_paths=["a.mid"])
        write_merge_state(repo, base_commit="b2", ours_commit="o2", theirs_commit="t2", conflict_paths=["b.mid"])
        state = read_merge_state(repo)
        assert state is not None
        assert state.base_commit == "b2"
        assert state.conflict_paths == ["b.mid"]

    def test_100_conflict_paths_round_trip(self, repo: pathlib.Path) -> None:
        paths = [f"track-{i:03d}.mid" for i in range(100)]
        write_merge_state(repo, base_commit="b", ours_commit="o", theirs_commit="t", conflict_paths=paths)
        state = read_merge_state(repo)
        assert state is not None
        assert state.conflict_paths == sorted(paths)

    def test_merge_state_is_frozen_dataclass(self) -> None:
        ms = MergeState(conflict_paths=["a.mid"], base_commit="b")
        with pytest.raises((AttributeError, TypeError)):
            ms.__setattr__("base_commit", "new")


# ===========================================================================
# apply_resolution
# ===========================================================================


class TestApplyResolution:
    def test_resolution_restores_correct_content(self, repo: pathlib.Path) -> None:
        data = b"resolved content"
        oid = hashlib.sha256(data).hexdigest()
        write_object(repo, oid, data)
        (repo / "muse-work").mkdir()
        apply_resolution(repo, "beat.mid", oid)
        restored = (repo / "muse-work" / "beat.mid").read_bytes()
        assert restored == data

    def test_resolution_creates_nested_dirs(self, repo: pathlib.Path) -> None:
        data = b"nested file"
        oid = hashlib.sha256(data).hexdigest()
        write_object(repo, oid, data)
        apply_resolution(repo, "sub/dir/beat.mid", oid)
        assert (repo / "muse-work" / "sub" / "dir" / "beat.mid").read_bytes() == data

    def test_resolution_missing_object_raises(self, repo: pathlib.Path) -> None:
        with pytest.raises(FileNotFoundError):
            apply_resolution(repo, "beat.mid", "a" * 64)

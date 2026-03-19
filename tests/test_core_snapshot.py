"""Tests for muse.core.snapshot — content-addressed snapshot computation."""

import pathlib

import pytest

from muse.core.snapshot import (
    build_snapshot_manifest,
    compute_commit_id,
    compute_snapshot_id,
    diff_workdir_vs_snapshot,
    hash_file,
)


@pytest.fixture
def workdir(tmp_path: pathlib.Path) -> pathlib.Path:
    d = tmp_path / "muse-work"
    d.mkdir()
    return d


class TestHashFile:
    def test_consistent(self, tmp_path: pathlib.Path) -> None:
        f = tmp_path / "file.mid"
        f.write_bytes(b"hello world")
        assert hash_file(f) == hash_file(f)

    def test_different_content_different_hash(self, tmp_path: pathlib.Path) -> None:
        a = tmp_path / "a.mid"
        b = tmp_path / "b.mid"
        a.write_bytes(b"aaa")
        b.write_bytes(b"bbb")
        assert hash_file(a) != hash_file(b)

    def test_known_hash(self, tmp_path: pathlib.Path) -> None:
        import hashlib
        content = b"muse"
        f = tmp_path / "f.mid"
        f.write_bytes(content)
        expected = hashlib.sha256(content).hexdigest()
        assert hash_file(f) == expected


class TestBuildSnapshotManifest:
    def test_empty_workdir(self, workdir: pathlib.Path) -> None:
        assert build_snapshot_manifest(workdir) == {}

    def test_single_file(self, workdir: pathlib.Path) -> None:
        (workdir / "beat.mid").write_bytes(b"drums")
        manifest = build_snapshot_manifest(workdir)
        assert "beat.mid" in manifest
        assert len(manifest["beat.mid"]) == 64  # sha256 hex

    def test_nested_file(self, workdir: pathlib.Path) -> None:
        (workdir / "tracks").mkdir()
        (workdir / "tracks" / "bass.mid").write_bytes(b"bass")
        manifest = build_snapshot_manifest(workdir)
        assert "tracks/bass.mid" in manifest

    def test_hidden_files_excluded(self, workdir: pathlib.Path) -> None:
        (workdir / ".DS_Store").write_bytes(b"junk")
        (workdir / "beat.mid").write_bytes(b"drums")
        manifest = build_snapshot_manifest(workdir)
        assert ".DS_Store" not in manifest
        assert "beat.mid" in manifest

    def test_deterministic_order(self, workdir: pathlib.Path) -> None:
        for name in ["c.mid", "a.mid", "b.mid"]:
            (workdir / name).write_bytes(name.encode())
        m1 = build_snapshot_manifest(workdir)
        m2 = build_snapshot_manifest(workdir)
        assert m1 == m2


class TestComputeSnapshotId:
    def test_empty_manifest(self) -> None:
        sid = compute_snapshot_id({})
        assert len(sid) == 64

    def test_deterministic(self) -> None:
        manifest = {"a.mid": "hash1", "b.mid": "hash2"}
        assert compute_snapshot_id(manifest) == compute_snapshot_id(manifest)

    def test_order_independent(self) -> None:
        m1 = {"a.mid": "h1", "b.mid": "h2"}
        m2 = {"b.mid": "h2", "a.mid": "h1"}
        assert compute_snapshot_id(m1) == compute_snapshot_id(m2)

    def test_different_content_different_id(self) -> None:
        m1 = {"a.mid": "h1"}
        m2 = {"a.mid": "h2"}
        assert compute_snapshot_id(m1) != compute_snapshot_id(m2)


class TestComputeCommitId:
    def test_deterministic(self) -> None:
        kwargs = dict(parent_ids=["p1"], snapshot_id="s1", message="msg", committed_at_iso="2026-01-01T00:00:00+00:00")
        assert compute_commit_id(**kwargs) == compute_commit_id(**kwargs)

    def test_parent_order_independent(self) -> None:
        a = compute_commit_id(parent_ids=["p1", "p2"], snapshot_id="s1", message="m", committed_at_iso="t")
        b = compute_commit_id(parent_ids=["p2", "p1"], snapshot_id="s1", message="m", committed_at_iso="t")
        assert a == b

    def test_different_messages_different_ids(self) -> None:
        a = compute_commit_id(parent_ids=[], snapshot_id="s1", message="msg1", committed_at_iso="t")
        b = compute_commit_id(parent_ids=[], snapshot_id="s1", message="msg2", committed_at_iso="t")
        assert a != b


class TestDiffWorkdirVsSnapshot:
    def test_new_repo_all_untracked(self, workdir: pathlib.Path) -> None:
        (workdir / "beat.mid").write_bytes(b"x")
        added, modified, deleted, untracked = diff_workdir_vs_snapshot(workdir, {})
        assert added == set()
        assert untracked == {"beat.mid"}

    def test_added_file(self, workdir: pathlib.Path) -> None:
        (workdir / "beat.mid").write_bytes(b"x")
        last = {"other.mid": "abc"}
        added, modified, deleted, untracked = diff_workdir_vs_snapshot(workdir, last)
        assert "beat.mid" in added
        assert "other.mid" in deleted

    def test_modified_file(self, workdir: pathlib.Path) -> None:
        f = workdir / "beat.mid"
        f.write_bytes(b"new content")
        last = {"beat.mid": "oldhash"}
        added, modified, deleted, untracked = diff_workdir_vs_snapshot(workdir, last)
        assert "beat.mid" in modified

    def test_clean_workdir(self, workdir: pathlib.Path) -> None:
        f = workdir / "beat.mid"
        f.write_bytes(b"content")
        from muse.core.snapshot import hash_file
        h = hash_file(f)
        added, modified, deleted, untracked = diff_workdir_vs_snapshot(workdir, {"beat.mid": h})
        assert not added and not modified and not deleted and not untracked

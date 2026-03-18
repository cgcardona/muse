"""Tests for muse.core.store — file-based commit and snapshot storage."""
from __future__ import annotations

import datetime
import json
import pathlib

import pytest

from muse.core.store import (
    CommitDict,
    CommitRecord,
    SnapshotRecord,
    TagRecord,
    find_commits_by_prefix,
    get_all_commits,
    get_all_tags,
    get_commits_for_branch,
    get_head_commit_id,
    get_head_snapshot_id,
    get_head_snapshot_manifest,
    get_tags_for_commit,
    read_commit,
    read_snapshot,
    update_commit_metadata,
    write_commit,
    write_snapshot,
    write_tag,
)


@pytest.fixture
def repo(tmp_path: pathlib.Path) -> pathlib.Path:
    """Create a minimal .muse/ directory structure."""
    muse_dir = tmp_path / ".muse"
    (muse_dir / "commits").mkdir(parents=True)
    (muse_dir / "snapshots").mkdir(parents=True)
    (muse_dir / "refs" / "heads").mkdir(parents=True)
    (muse_dir / "repo.json").write_text(json.dumps({"repo_id": "test-repo"}))
    (muse_dir / "HEAD").write_text("refs/heads/main\n")
    (muse_dir / "refs" / "heads" / "main").write_text("")
    return tmp_path


def _make_commit(root: pathlib.Path, commit_id: str, snapshot_id: str, message: str, parent: str | None = None) -> CommitRecord:
    c = CommitRecord(
        commit_id=commit_id,
        repo_id="test-repo",
        branch="main",
        snapshot_id=snapshot_id,
        message=message,
        committed_at=datetime.datetime.now(datetime.timezone.utc),
        parent_commit_id=parent,
    )
    write_commit(root, c)
    return c


def _make_snapshot(root: pathlib.Path, snapshot_id: str, manifest: dict[str, str]) -> SnapshotRecord:
    s = SnapshotRecord(snapshot_id=snapshot_id, manifest=manifest)
    write_snapshot(root, s)
    return s


class TestFormatVersion:
    """CommitRecord.format_version tracks schema evolution."""

    def test_new_commit_has_format_version_4(self, repo: pathlib.Path) -> None:
        c = _make_commit(repo, "abc123", "snap1", "msg")
        assert c.format_version == 4

    def test_format_version_round_trips_through_json(self, repo: pathlib.Path) -> None:
        _make_commit(repo, "abc123", "snap1", "msg")
        loaded = read_commit(repo, "abc123")
        assert loaded is not None
        assert loaded.format_version == 4

    def test_format_version_in_serialised_dict(self) -> None:
        c = CommitRecord(
            commit_id="x",
            repo_id="r",
            branch="main",
            snapshot_id="s",
            message="m",
            committed_at=datetime.datetime.now(datetime.timezone.utc),
        )
        d = c.to_dict()
        assert "format_version" in d
        assert d["format_version"] == 4

    def test_missing_format_version_defaults_to_1(self) -> None:
        """Existing JSON without format_version field deserialises as version 1."""
        raw = CommitDict(
            commit_id="abc",
            repo_id="r",
            branch="main",
            snapshot_id="s",
            message="old record",
            committed_at="2025-01-01T00:00:00+00:00",
        )
        c = CommitRecord.from_dict(raw)
        assert c.format_version == 1

    def test_explicit_format_version_preserved(self) -> None:
        raw = CommitDict(
            commit_id="abc",
            repo_id="r",
            branch="main",
            snapshot_id="s",
            message="versioned record",
            committed_at="2025-01-01T00:00:00+00:00",
            format_version=2,
        )
        c = CommitRecord.from_dict(raw)
        assert c.format_version == 2

    def test_format_version_field_is_integer(self, repo: pathlib.Path) -> None:
        _make_commit(repo, "abc123", "snap1", "msg")
        loaded = read_commit(repo, "abc123")
        assert loaded is not None
        assert isinstance(loaded.format_version, int)


class TestWriteReadCommit:
    def test_roundtrip(self, repo: pathlib.Path) -> None:
        c = _make_commit(repo, "abc123", "snap1", "Initial commit")
        loaded = read_commit(repo, "abc123")
        assert loaded is not None
        assert loaded.commit_id == "abc123"
        assert loaded.message == "Initial commit"
        assert loaded.repo_id == "test-repo"

    def test_read_missing_returns_none(self, repo: pathlib.Path) -> None:
        assert read_commit(repo, "nonexistent") is None

    def test_idempotent_write(self, repo: pathlib.Path) -> None:
        _make_commit(repo, "abc123", "snap1", "First")
        _make_commit(repo, "abc123", "snap1", "Second")  # Should not overwrite
        loaded = read_commit(repo, "abc123")
        assert loaded is not None
        assert loaded.message == "First"

    def test_metadata_preserved(self, repo: pathlib.Path) -> None:
        c = CommitRecord(
            commit_id="abc123",
            repo_id="test-repo",
            branch="main",
            snapshot_id="snap1",
            message="With metadata",
            committed_at=datetime.datetime.now(datetime.timezone.utc),
            metadata={"section": "chorus", "emotion": "joyful"},
        )
        write_commit(repo, c)
        loaded = read_commit(repo, "abc123")
        assert loaded is not None
        assert loaded.metadata["section"] == "chorus"
        assert loaded.metadata["emotion"] == "joyful"


class TestUpdateCommitMetadata:
    def test_set_key(self, repo: pathlib.Path) -> None:
        _make_commit(repo, "abc123", "snap1", "msg")
        result = update_commit_metadata(repo, "abc123", "tempo_bpm", 120.0)
        assert result is True
        loaded = read_commit(repo, "abc123")
        assert loaded is not None
        assert loaded.metadata["tempo_bpm"] == 120.0

    def test_missing_commit_returns_false(self, repo: pathlib.Path) -> None:
        assert update_commit_metadata(repo, "missing", "k", "v") is False


class TestWriteReadSnapshot:
    def test_roundtrip(self, repo: pathlib.Path) -> None:
        s = _make_snapshot(repo, "snap1", {"tracks/drums.mid": "deadbeef"})
        loaded = read_snapshot(repo, "snap1")
        assert loaded is not None
        assert loaded.manifest == {"tracks/drums.mid": "deadbeef"}

    def test_read_missing_returns_none(self, repo: pathlib.Path) -> None:
        assert read_snapshot(repo, "nonexistent") is None


class TestHeadQueries:
    def test_get_head_commit_id_empty_branch(self, repo: pathlib.Path) -> None:
        assert get_head_commit_id(repo, "main") is None

    def test_get_head_commit_id(self, repo: pathlib.Path) -> None:
        (repo / ".muse" / "refs" / "heads" / "main").write_text("abc123")
        assert get_head_commit_id(repo, "main") == "abc123"

    def test_get_head_snapshot_id(self, repo: pathlib.Path) -> None:
        _make_commit(repo, "abc123", "snap1", "msg")
        _make_snapshot(repo, "snap1", {"f.mid": "hash1"})
        (repo / ".muse" / "refs" / "heads" / "main").write_text("abc123")
        assert get_head_snapshot_id(repo, "test-repo", "main") == "snap1"

    def test_get_head_snapshot_manifest(self, repo: pathlib.Path) -> None:
        _make_commit(repo, "abc123", "snap1", "msg")
        _make_snapshot(repo, "snap1", {"f.mid": "hash1"})
        (repo / ".muse" / "refs" / "heads" / "main").write_text("abc123")
        manifest = get_head_snapshot_manifest(repo, "test-repo", "main")
        assert manifest == {"f.mid": "hash1"}


class TestGetCommitsForBranch:
    def test_chain(self, repo: pathlib.Path) -> None:
        _make_commit(repo, "root", "snap0", "Root")
        _make_commit(repo, "child", "snap1", "Child", parent="root")
        _make_commit(repo, "grandchild", "snap2", "Grandchild", parent="child")
        (repo / ".muse" / "refs" / "heads" / "main").write_text("grandchild")

        commits = get_commits_for_branch(repo, "test-repo", "main")
        assert [c.commit_id for c in commits] == ["grandchild", "child", "root"]

    def test_empty_branch(self, repo: pathlib.Path) -> None:
        assert get_commits_for_branch(repo, "test-repo", "main") == []


class TestFindByPrefix:
    def test_finds_match(self, repo: pathlib.Path) -> None:
        _make_commit(repo, "abcdef1234", "snap1", "msg")
        results = find_commits_by_prefix(repo, "abcdef")
        assert len(results) == 1
        assert results[0].commit_id == "abcdef1234"

    def test_no_match(self, repo: pathlib.Path) -> None:
        assert find_commits_by_prefix(repo, "zzz") == []


class TestTags:
    def test_write_and_read(self, repo: pathlib.Path) -> None:
        _make_commit(repo, "abc123", "snap1", "msg")
        write_tag(repo, TagRecord(
            tag_id="tag1",
            repo_id="test-repo",
            commit_id="abc123",
            tag="emotion:joyful",
        ))
        tags = get_tags_for_commit(repo, "test-repo", "abc123")
        assert len(tags) == 1
        assert tags[0].tag == "emotion:joyful"

    def test_get_all_tags(self, repo: pathlib.Path) -> None:
        _make_commit(repo, "abc123", "snap1", "msg")
        write_tag(repo, TagRecord(tag_id="t1", repo_id="test-repo", commit_id="abc123", tag="stage:rough-mix"))
        write_tag(repo, TagRecord(tag_id="t2", repo_id="test-repo", commit_id="abc123", tag="key:Am"))
        all_tags = get_all_tags(repo, "test-repo")
        assert len(all_tags) == 2

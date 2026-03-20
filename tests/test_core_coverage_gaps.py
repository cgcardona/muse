"""Tests targeting coverage gaps in core modules: object_store, repo, store, merge_engine."""

import hashlib
import json
import os
import pathlib

import pytest


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

from muse.core.object_store import (
    has_object,
    object_path,
    objects_dir,
    read_object,
    restore_object,
    write_object,
    write_object_from_path,
)
from muse.core.repo import find_repo_root, require_repo
from muse.core.store import (
    CommitRecord,
    SnapshotRecord,
    get_commits_for_branch,
    get_head_commit_id,
    get_head_snapshot_id,
    get_head_snapshot_manifest,
    get_tags_for_commit,
    read_commit,
    read_snapshot,
    resolve_commit_ref,
    update_commit_metadata,
    write_commit,
    write_snapshot,
)
from muse.core.merge_engine import apply_resolution, clear_merge_state, read_merge_state, write_merge_state

import datetime


# ---------------------------------------------------------------------------
# object_store
# ---------------------------------------------------------------------------


class TestObjectStore:
    def test_objects_dir_path(self, tmp_path: pathlib.Path) -> None:
        d = objects_dir(tmp_path)
        assert d == tmp_path / ".muse" / "objects"

    def test_object_path_sharding(self, tmp_path: pathlib.Path) -> None:
        oid = "ab" + "c" * 62
        p = object_path(tmp_path, oid)
        assert p.parent.name == "ab"
        assert p.name == "c" * 62

    def test_has_object_false_when_absent(self, tmp_path: pathlib.Path) -> None:
        assert not has_object(tmp_path, "a" * 64)

    def test_has_object_true_after_write(self, tmp_path: pathlib.Path) -> None:
        content = b"hello"
        oid = _sha256(content)
        write_object(tmp_path, oid, content)
        assert has_object(tmp_path, oid)

    def test_write_object_idempotent_returns_false(self, tmp_path: pathlib.Path) -> None:
        content = b"first"
        oid = _sha256(content)
        assert write_object(tmp_path, oid, content) is True
        # Second write with correct hash but same ID — idempotent
        assert write_object(tmp_path, oid, content) is False
        # content should not change
        assert read_object(tmp_path, oid) == content

    def test_write_object_from_path_idempotent(self, tmp_path: pathlib.Path) -> None:
        content = b"content"
        src = tmp_path / "src.bin"
        src.write_bytes(content)
        oid = _sha256(content)
        assert write_object_from_path(tmp_path, oid, src) is True
        assert write_object_from_path(tmp_path, oid, src) is False

    def test_write_object_from_path_stores_content(self, tmp_path: pathlib.Path) -> None:
        content = b"my bytes"
        src = tmp_path / "file.bin"
        src.write_bytes(content)
        oid = _sha256(content)
        write_object_from_path(tmp_path, oid, src)
        assert read_object(tmp_path, oid) == content

    def test_read_object_returns_none_when_absent(self, tmp_path: pathlib.Path) -> None:
        assert read_object(tmp_path, "e" * 64) is None

    def test_read_object_returns_bytes(self, tmp_path: pathlib.Path) -> None:
        content = b"data"
        oid = _sha256(content)
        write_object(tmp_path, oid, content)
        assert read_object(tmp_path, oid) == content

    def test_restore_object_returns_false_when_absent(self, tmp_path: pathlib.Path) -> None:
        dest = tmp_path / "out.bin"
        result = restore_object(tmp_path, "0" * 64, dest)
        assert result is False
        assert not dest.exists()

    def test_restore_object_creates_dest(self, tmp_path: pathlib.Path) -> None:
        content = b"restored"
        oid = _sha256(content)
        write_object(tmp_path, oid, content)
        dest = tmp_path / "sub" / "out.bin"
        result = restore_object(tmp_path, oid, dest)
        assert result is True
        assert dest.read_bytes() == content

    def test_restore_object_creates_parent_dirs(self, tmp_path: pathlib.Path) -> None:
        content = b"nested"
        oid = _sha256(content)
        write_object(tmp_path, oid, content)
        dest = tmp_path / "a" / "b" / "c" / "file.bin"
        restore_object(tmp_path, oid, dest)
        assert dest.exists()


# ---------------------------------------------------------------------------
# repo
# ---------------------------------------------------------------------------


class TestFindRepoRoot:
    def test_finds_muse_dir_in_cwd(self, tmp_path: pathlib.Path) -> None:
        (tmp_path / ".muse").mkdir()
        result = find_repo_root(tmp_path)
        assert result == tmp_path

    def test_finds_muse_dir_in_parent(self, tmp_path: pathlib.Path) -> None:
        (tmp_path / ".muse").mkdir()
        subdir = tmp_path / "a" / "b"
        subdir.mkdir(parents=True)
        result = find_repo_root(subdir)
        assert result == tmp_path

    def test_returns_none_when_no_repo(self, tmp_path: pathlib.Path) -> None:
        result = find_repo_root(tmp_path)
        assert result is None

    def test_env_override_returns_path(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        (tmp_path / ".muse").mkdir()
        monkeypatch.setenv("MUSE_REPO_ROOT", str(tmp_path))
        result = find_repo_root()
        assert result == tmp_path

    def test_env_override_returns_none_when_not_repo(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        # tmp_path exists but has no .muse/
        monkeypatch.setenv("MUSE_REPO_ROOT", str(tmp_path))
        result = find_repo_root()
        assert result is None

    def test_require_repo_exits_when_no_repo(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import click
        monkeypatch.delenv("MUSE_REPO_ROOT", raising=False)
        monkeypatch.chdir(tmp_path)
        with pytest.raises(click.exceptions.Exit):
            require_repo()


# ---------------------------------------------------------------------------
# store coverage gaps
# ---------------------------------------------------------------------------


class TestStoreGaps:
    def _make_repo(self, tmp_path: pathlib.Path) -> pathlib.Path:
        muse = tmp_path / ".muse"
        for d in ("commits", "snapshots", "objects", "refs/heads"):
            (muse / d).mkdir(parents=True)
        (muse / "HEAD").write_text("refs/heads/main\n")
        (muse / "repo.json").write_text(json.dumps({"repo_id": "test-repo"}))
        (muse / "refs" / "heads" / "main").write_text("")
        return tmp_path

    def test_get_head_commit_id_empty_branch(self, tmp_path: pathlib.Path) -> None:
        root = self._make_repo(tmp_path)
        assert get_head_commit_id(root, "main") is None

    def test_get_head_snapshot_id_no_commits(self, tmp_path: pathlib.Path) -> None:
        root = self._make_repo(tmp_path)
        assert get_head_snapshot_id(root, "test-repo", "main") is None

    def test_get_head_snapshot_manifest_no_commits(self, tmp_path: pathlib.Path) -> None:
        root = self._make_repo(tmp_path)
        assert get_head_snapshot_manifest(root, "test-repo", "main") is None

    def test_get_commits_for_branch_empty(self, tmp_path: pathlib.Path) -> None:
        root = self._make_repo(tmp_path)
        commits = get_commits_for_branch(root, "test-repo", "main")
        assert commits == []

    def test_resolve_commit_ref_with_none_returns_head(self, tmp_path: pathlib.Path) -> None:
        root = self._make_repo(tmp_path)
        snap = SnapshotRecord(snapshot_id="s" * 64, manifest={"a.mid": "h" * 64})
        write_snapshot(root, snap)
        committed_at = datetime.datetime.now(datetime.timezone.utc)
        commit = CommitRecord(
            commit_id="c" * 64,
            repo_id="test-repo",
            branch="main",
            snapshot_id="s" * 64,
            message="test",
            committed_at=committed_at,
        )
        write_commit(root, commit)
        (root / ".muse" / "refs" / "heads" / "main").write_text("c" * 64)

        result = resolve_commit_ref(root, "test-repo", "main", None)
        assert result is not None
        assert result.commit_id == "c" * 64

    def test_read_commit_returns_none_for_unknown(self, tmp_path: pathlib.Path) -> None:
        root = self._make_repo(tmp_path)
        assert read_commit(root, "unknown") is None

    def test_read_snapshot_returns_none_for_unknown(self, tmp_path: pathlib.Path) -> None:
        root = self._make_repo(tmp_path)
        assert read_snapshot(root, "unknown") is None

    def test_update_commit_metadata_false_for_unknown(self, tmp_path: pathlib.Path) -> None:
        root = self._make_repo(tmp_path)
        assert update_commit_metadata(root, "unknown", "key", "val") is False

    def test_get_tags_for_commit_empty(self, tmp_path: pathlib.Path) -> None:
        root = self._make_repo(tmp_path)
        tags = get_tags_for_commit(root, "test-repo", "c" * 64)
        assert tags == []


# ---------------------------------------------------------------------------
# merge_engine coverage gaps
# ---------------------------------------------------------------------------


class TestMergeEngineCoverageGaps:
    def _make_repo(self, tmp_path: pathlib.Path) -> pathlib.Path:
        muse = tmp_path / ".muse"
        muse.mkdir(parents=True)
        return tmp_path

    def test_clear_merge_state_no_file(self, tmp_path: pathlib.Path) -> None:
        root = self._make_repo(tmp_path)
        # Should not raise even if MERGE_STATE.json is absent
        clear_merge_state(root)

    def test_apply_resolution_copies_object(self, tmp_path: pathlib.Path) -> None:
        root = self._make_repo(tmp_path)
        # Write a real object to the store — oid must be the SHA-256 of the content.
        content = b"resolved content"
        oid = _sha256(content)
        write_object(root, oid, content)

        apply_resolution(root, "track.mid", oid)
        dest = root / "state" / "track.mid"
        assert dest.exists()
        assert dest.read_bytes() == b"resolved content"

    def test_apply_resolution_raises_when_object_absent(self, tmp_path: pathlib.Path) -> None:
        root = self._make_repo(tmp_path)
        with pytest.raises(FileNotFoundError):
            apply_resolution(root, "track.mid", "0" * 64)

    def test_read_merge_state_invalid_json_returns_none(self, tmp_path: pathlib.Path) -> None:
        root = self._make_repo(tmp_path)
        (root / ".muse" / "MERGE_STATE.json").write_text("not json {{")
        result = read_merge_state(root)
        assert result is None

    def test_write_then_clear_merge_state(self, tmp_path: pathlib.Path) -> None:
        root = self._make_repo(tmp_path)
        write_merge_state(
            root,
            base_commit="b" * 64,
            ours_commit="o" * 64,
            theirs_commit="t" * 64,
            conflict_paths=["a.mid"],
        )
        assert (root / ".muse" / "MERGE_STATE.json").exists()
        clear_merge_state(root)
        assert not (root / ".muse" / "MERGE_STATE.json").exists()

"""Tests for muse.core.pack — PackBundle build and apply operations."""

from __future__ import annotations

import base64
import datetime
import json
import pathlib

import pytest

from muse.core.object_store import has_object, read_object, write_object
from muse.core.pack import (
    ObjectPayload,
    PackBundle,
    apply_pack,
    build_pack,
)
from muse.core.store import (
    CommitRecord,
    SnapshotRecord,
    read_commit,
    read_snapshot,
    write_commit,
    write_snapshot,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def repo(tmp_path: pathlib.Path) -> pathlib.Path:
    """Minimal .muse/ repo structure."""
    muse_dir = tmp_path / ".muse"
    (muse_dir / "commits").mkdir(parents=True)
    (muse_dir / "snapshots").mkdir(parents=True)
    (muse_dir / "objects").mkdir(parents=True)
    (muse_dir / "refs" / "heads").mkdir(parents=True)
    (muse_dir / "repo.json").write_text(json.dumps({"repo_id": "test-repo"}))
    (muse_dir / "HEAD").write_text("refs/heads/main\n")
    (muse_dir / "refs" / "heads" / "main").write_text("")
    return tmp_path


def _make_object(root: pathlib.Path, content: bytes) -> str:
    """Write raw bytes into the object store; return the object_id."""
    import hashlib
    oid = hashlib.sha256(content).hexdigest()
    write_object(root, oid, content)
    return oid


def _make_snapshot(
    root: pathlib.Path, snapshot_id: str, manifest: dict[str, str]
) -> SnapshotRecord:
    s = SnapshotRecord(snapshot_id=snapshot_id, manifest=manifest)
    write_snapshot(root, s)
    return s


def _make_commit(
    root: pathlib.Path,
    commit_id: str,
    snapshot_id: str,
    message: str = "test",
    parent: str | None = None,
) -> CommitRecord:
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


# ---------------------------------------------------------------------------
# build_pack tests
# ---------------------------------------------------------------------------


class TestBuildPack:
    def test_single_commit_no_history(self, repo: pathlib.Path) -> None:
        content = b"hello world"
        oid = _make_object(repo, content)
        _make_snapshot(repo, "snap1", {"file.txt": oid})
        _make_commit(repo, "commit1", "snap1")

        bundle = build_pack(repo, ["commit1"])

        assert len(bundle.get("commits") or []) == 1
        assert len(bundle.get("snapshots") or []) == 1
        assert len(bundle.get("objects") or []) == 1
        assert (bundle.get("objects") or [{}])[0]["object_id"] == oid

    def test_object_is_base64_encoded(self, repo: pathlib.Path) -> None:
        content = b"\x00\x01\x02\x03"
        oid = _make_object(repo, content)
        _make_snapshot(repo, "snap1", {"bin.dat": oid})
        _make_commit(repo, "c1", "snap1")

        bundle = build_pack(repo, ["c1"])

        objs = bundle.get("objects") or []
        assert len(objs) == 1
        decoded = base64.b64decode(objs[0]["content_b64"])
        assert decoded == content

    def test_multi_commit_chain(self, repo: pathlib.Path) -> None:
        oid1 = _make_object(repo, b"v1")
        oid2 = _make_object(repo, b"v2")
        _make_snapshot(repo, "snap1", {"f.txt": oid1})
        _make_snapshot(repo, "snap2", {"f.txt": oid2})
        _make_commit(repo, "c1", "snap1")
        _make_commit(repo, "c2", "snap2", parent="c1")

        bundle = build_pack(repo, ["c2"])

        assert len(bundle.get("commits") or []) == 2
        assert len(bundle.get("snapshots") or []) == 2
        assert len(bundle.get("objects") or []) == 2

    def test_have_excludes_ancestor_commits(self, repo: pathlib.Path) -> None:
        oid1 = _make_object(repo, b"v1")
        oid2 = _make_object(repo, b"v2")
        _make_snapshot(repo, "snap1", {"f.txt": oid1})
        _make_snapshot(repo, "snap2", {"f.txt": oid2})
        _make_commit(repo, "c1", "snap1")
        _make_commit(repo, "c2", "snap2", parent="c1")

        bundle = build_pack(repo, ["c2"], have=["c1"])

        # Only c2 should be in the bundle; c1 is in have.
        commit_ids = [c["commit_id"] for c in (bundle.get("commits") or [])]
        assert "c2" in commit_ids
        assert "c1" not in commit_ids

    def test_deduplicates_shared_objects(self, repo: pathlib.Path) -> None:
        shared_oid = _make_object(repo, b"shared")
        _make_snapshot(repo, "snap1", {"a.txt": shared_oid})
        _make_snapshot(repo, "snap2", {"b.txt": shared_oid})
        _make_commit(repo, "c1", "snap1")
        _make_commit(repo, "c2", "snap2", parent="c1")

        bundle = build_pack(repo, ["c2"])

        # Shared object should appear only once.
        object_ids = [o["object_id"] for o in (bundle.get("objects") or [])]
        assert object_ids.count(shared_oid) == 1

    def test_empty_commit_ids_returns_empty_bundle(self, repo: pathlib.Path) -> None:
        bundle = build_pack(repo, [])
        assert (bundle.get("commits") or []) == []
        assert (bundle.get("objects") or []) == []

    def test_missing_commit_skipped_gracefully(self, repo: pathlib.Path) -> None:
        # Should not raise even if a commit_id does not exist.
        bundle = build_pack(repo, ["nonexistent"])
        assert (bundle.get("commits") or []) == []

    def test_merge_commit_includes_both_parents(self, repo: pathlib.Path) -> None:
        oid_a = _make_object(repo, b"branch-a")
        oid_b = _make_object(repo, b"branch-b")
        _make_snapshot(repo, "snap_a", {"a.txt": oid_a})
        _make_snapshot(repo, "snap_b", {"b.txt": oid_b})
        _make_snapshot(repo, "snap_m", {"a.txt": oid_a, "b.txt": oid_b})
        _make_commit(repo, "c_a", "snap_a")
        _make_commit(repo, "c_b", "snap_b")
        # Merge commit with two parents
        c_merge = CommitRecord(
            commit_id="c_merge",
            repo_id="test-repo",
            branch="main",
            snapshot_id="snap_m",
            message="merge",
            committed_at=datetime.datetime.now(datetime.timezone.utc),
            parent_commit_id="c_a",
            parent2_commit_id="c_b",
        )
        write_commit(repo, c_merge)

        bundle = build_pack(repo, ["c_merge"])
        commit_ids = {c["commit_id"] for c in (bundle.get("commits") or [])}
        assert {"c_merge", "c_a", "c_b"}.issubset(commit_ids)


# ---------------------------------------------------------------------------
# apply_pack tests
# ---------------------------------------------------------------------------


class TestApplyPack:
    def test_round_trip(self, repo: pathlib.Path, tmp_path: pathlib.Path) -> None:
        """build_pack → apply_pack in a fresh repo produces identical data."""
        content = b"round trip"
        oid = _make_object(repo, content)
        _make_snapshot(repo, "snap1", {"f.txt": oid})
        _make_commit(repo, "c1", "snap1", message="initial")

        bundle = build_pack(repo, ["c1"])

        # Apply into a fresh repo.
        dest = tmp_path / "dest"
        muse_dir = dest / ".muse"
        (muse_dir / "commits").mkdir(parents=True)
        (muse_dir / "snapshots").mkdir(parents=True)
        (muse_dir / "objects").mkdir(parents=True)

        new_count = apply_pack(dest, bundle)

        assert new_count == 1
        assert has_object(dest, oid)
        assert read_object(dest, oid) == content
        assert read_snapshot(dest, "snap1") is not None
        assert read_commit(dest, "c1") is not None

    def test_idempotent_apply(self, repo: pathlib.Path) -> None:
        """Applying the same bundle twice does not raise and new_count = 0."""
        content = b"idempotent"
        oid = _make_object(repo, content)
        _make_snapshot(repo, "snap1", {"f.txt": oid})
        _make_commit(repo, "c1", "snap1")

        bundle = build_pack(repo, ["c1"])
        apply_pack(repo, bundle)
        new_count = apply_pack(repo, bundle)

        assert new_count == 0  # All already present.

    def test_malformed_object_skipped(self, repo: pathlib.Path) -> None:
        bundle: PackBundle = {
            "commits": [],
            "snapshots": [],
            "objects": [ObjectPayload(object_id="abc123", content_b64="NOT_VALID_BASE64!!!")],
        }
        new_count = apply_pack(repo, bundle)
        assert new_count == 0

    def test_empty_bundle_is_noop(self, repo: pathlib.Path) -> None:
        bundle: PackBundle = {}
        new_count = apply_pack(repo, bundle)
        assert new_count == 0

    def test_apply_preserves_commit_metadata(
        self, repo: pathlib.Path, tmp_path: pathlib.Path
    ) -> None:
        oid = _make_object(repo, b"data")
        _make_snapshot(repo, "s1", {"data.bin": oid})
        _make_commit(repo, "c1", "s1", message="preserve me")

        bundle = build_pack(repo, ["c1"])

        dest = tmp_path / "d"
        (dest / ".muse" / "commits").mkdir(parents=True)
        (dest / ".muse" / "snapshots").mkdir(parents=True)
        (dest / ".muse" / "objects").mkdir(parents=True)
        apply_pack(dest, bundle)

        commit = read_commit(dest, "c1")
        assert commit is not None
        assert commit.message == "preserve me"
        assert commit.snapshot_id == "s1"

    def test_apply_returns_new_object_count(
        self, repo: pathlib.Path, tmp_path: pathlib.Path
    ) -> None:
        oid1 = _make_object(repo, b"obj1")
        oid2 = _make_object(repo, b"obj2")
        _make_snapshot(repo, "s1", {"a": oid1, "b": oid2})
        _make_commit(repo, "c1", "s1")

        bundle = build_pack(repo, ["c1"])
        dest = tmp_path / "d"
        (dest / ".muse" / "commits").mkdir(parents=True)
        (dest / ".muse" / "snapshots").mkdir(parents=True)
        (dest / ".muse" / "objects").mkdir(parents=True)

        count = apply_pack(dest, bundle)
        assert count == 2

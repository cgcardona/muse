"""Tests for muse/core/gc.py — garbage collection."""

from __future__ import annotations

import json
import pathlib

import pytest

from muse.core.gc import GcResult, count_unreachable, run_gc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_repo(tmp_path: pathlib.Path) -> pathlib.Path:
    """Create a minimal .muse repo structure."""
    muse = tmp_path / ".muse"
    for d in ("objects", "commits", "snapshots", "refs/heads"):
        (muse / d).mkdir(parents=True, exist_ok=True)
    (muse / "repo.json").write_text(json.dumps({"repo_id": "test-repo"}))
    (muse / "HEAD").write_text("ref: refs/heads/main\n")
    return tmp_path


def _write_object(repo: pathlib.Path, content: bytes) -> str:
    import hashlib

    sha = hashlib.sha256(content).hexdigest()
    obj_dir = repo / ".muse" / "objects" / sha[:2]
    obj_dir.mkdir(parents=True, exist_ok=True)
    (obj_dir / sha[2:]).write_bytes(content)
    return sha


def _write_snapshot(repo: pathlib.Path, snapshot_id: str, manifest: dict[str, str]) -> None:
    snap_dir = repo / ".muse" / "snapshots"
    snap_dir.mkdir(parents=True, exist_ok=True)
    (snap_dir / f"{snapshot_id}.json").write_text(
        json.dumps({"snapshot_id": snapshot_id, "manifest": manifest})
    )


def _write_commit(repo: pathlib.Path, commit_id: str, snapshot_id: str) -> None:
    import datetime

    commit_dir = repo / ".muse" / "commits"
    commit_dir.mkdir(parents=True, exist_ok=True)
    (commit_dir / f"{commit_id}.json").write_text(json.dumps({
        "commit_id": commit_id,
        "repo_id": "test-repo",
        "branch": "main",
        "snapshot_id": snapshot_id,
        "message": "test",
        "committed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "parent_commit_id": None,
        "parent2_commit_id": None,
        "author": "",
        "metadata": {},
    }))
    # Advance branch HEAD.
    ref_path = repo / ".muse" / "refs" / "heads" / "main"
    ref_path.parent.mkdir(parents=True, exist_ok=True)
    ref_path.write_text(commit_id)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_gc_empty_repo(tmp_path: pathlib.Path) -> None:
    """GC on an empty repo should report 0 collected."""
    repo = _make_repo(tmp_path)
    result = run_gc(repo)
    assert isinstance(result, GcResult)
    assert result.collected_count == 0


def test_gc_removes_unreachable_object(tmp_path: pathlib.Path) -> None:
    repo = _make_repo(tmp_path)
    # Write an object but don't reference it in any commit.
    orphan_id = _write_object(repo, b"orphan data")
    obj_path = repo / ".muse" / "objects" / orphan_id[:2] / orphan_id[2:]
    assert obj_path.exists()

    result = run_gc(repo)
    assert result.collected_count == 1
    assert orphan_id in result.collected_ids
    assert not obj_path.exists()


def test_gc_preserves_reachable_object(tmp_path: pathlib.Path) -> None:
    repo = _make_repo(tmp_path)
    content = b"reachable file content"
    obj_id = _write_object(repo, content)
    snap_id = "s" * 64
    commit_id = "c" * 64
    _write_snapshot(repo, snap_id, {"file.txt": obj_id})
    _write_commit(repo, commit_id, snap_id)

    result = run_gc(repo)
    assert result.collected_count == 0
    obj_path = repo / ".muse" / "objects" / obj_id[:2] / obj_id[2:]
    assert obj_path.exists()


def test_gc_dry_run_does_not_delete(tmp_path: pathlib.Path) -> None:
    repo = _make_repo(tmp_path)
    orphan_id = _write_object(repo, b"orphan")
    obj_path = repo / ".muse" / "objects" / orphan_id[:2] / orphan_id[2:]

    result = run_gc(repo, dry_run=True)
    assert result.dry_run is True
    assert result.collected_count == 1
    # File should still exist.
    assert obj_path.exists()


def test_gc_collected_bytes(tmp_path: pathlib.Path) -> None:
    repo = _make_repo(tmp_path)
    content = b"x" * 1000
    _write_object(repo, content)
    result = run_gc(repo)
    assert result.collected_bytes >= 1000


def test_gc_multiple_orphans(tmp_path: pathlib.Path) -> None:
    repo = _make_repo(tmp_path)
    for i in range(5):
        _write_object(repo, f"orphan {i}".encode())
    result = run_gc(repo)
    assert result.collected_count == 5


def test_gc_mixed_reachable_and_orphans(tmp_path: pathlib.Path) -> None:
    repo = _make_repo(tmp_path)
    # One reachable object.
    reachable_id = _write_object(repo, b"reachable")
    snap_id = "s" * 64
    commit_id = "c" * 64
    _write_snapshot(repo, snap_id, {"file.txt": reachable_id})
    _write_commit(repo, commit_id, snap_id)
    # Two orphans.
    _write_object(repo, b"orphan A")
    _write_object(repo, b"orphan B")

    result = run_gc(repo)
    assert result.collected_count == 2
    assert result.reachable_count == 1


def test_count_unreachable(tmp_path: pathlib.Path) -> None:
    repo = _make_repo(tmp_path)
    _write_object(repo, b"orphan 1")
    _write_object(repo, b"orphan 2")
    assert count_unreachable(repo) == 2


def test_count_unreachable_empty(tmp_path: pathlib.Path) -> None:
    repo = _make_repo(tmp_path)
    assert count_unreachable(repo) == 0


def test_gc_elapsed_time_positive(tmp_path: pathlib.Path) -> None:
    repo = _make_repo(tmp_path)
    result = run_gc(repo)
    assert result.elapsed_seconds >= 0.0


# ---------------------------------------------------------------------------
# Stress test
# ---------------------------------------------------------------------------


def test_gc_stress_many_orphans(tmp_path: pathlib.Path) -> None:
    """GC should handle 200 orphaned objects efficiently."""
    repo = _make_repo(tmp_path)
    for i in range(200):
        _write_object(repo, f"orphan-{i:04d}".encode())
    result = run_gc(repo)
    assert result.collected_count == 200
    # Verify the objects directory is clean.
    obj_dir = repo / ".muse" / "objects"
    remaining = list(obj_dir.rglob("*"))
    remaining_files = [p for p in remaining if p.is_file()]
    assert remaining_files == []

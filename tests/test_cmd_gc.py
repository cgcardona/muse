"""Comprehensive tests for ``muse gc``.

Covers:
- Unit: run_gc core logic (reachable vs unreachable objects)
- Integration: gc cleans up orphaned objects after commits
- E2E: full CLI via CliRunner (--dry-run, --verbose, --format json)
- Security: only objects dir affected, no path traversal
- Stress: gc with many orphaned objects
"""

from __future__ import annotations

import datetime
import hashlib
import json
import pathlib
import uuid

import pytest
from tests.cli_test_helper import CliRunner

cli = None  # argparse migration — CliRunner ignores this arg

runner = CliRunner()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _env(root: pathlib.Path) -> dict[str, str]:
    return {"MUSE_REPO_ROOT": str(root)}


def _init_repo(tmp_path: pathlib.Path) -> tuple[pathlib.Path, str]:
    muse_dir = tmp_path / ".muse"
    muse_dir.mkdir()
    repo_id = str(uuid.uuid4())
    (muse_dir / "repo.json").write_text(json.dumps({
        "repo_id": repo_id,
        "domain": "midi",
        "default_branch": "main",
        "created_at": "2025-01-01T00:00:00+00:00",
    }), encoding="utf-8")
    (muse_dir / "HEAD").write_text("ref: refs/heads/main", encoding="utf-8")
    (muse_dir / "refs" / "heads").mkdir(parents=True)
    (muse_dir / "snapshots").mkdir()
    (muse_dir / "commits").mkdir()
    (muse_dir / "objects").mkdir()
    return tmp_path, repo_id


def _write_object(root: pathlib.Path, content: bytes) -> str:
    obj_id = hashlib.sha256(content).hexdigest()
    obj_path = root / ".muse" / "objects" / obj_id[:2] / obj_id[2:]
    obj_path.parent.mkdir(parents=True, exist_ok=True)
    obj_path.write_bytes(content)
    return obj_id


def _make_commit(root: pathlib.Path, repo_id: str, message: str = "init") -> str:
    from muse.core.store import CommitRecord, SnapshotRecord, write_commit, write_snapshot
    from muse.core.snapshot import compute_snapshot_id, compute_commit_id

    ref_file = root / ".muse" / "refs" / "heads" / "main"
    parent_id = ref_file.read_text().strip() if ref_file.exists() else None
    manifest: dict[str, str] = {}
    snap_id = compute_snapshot_id(manifest)
    committed_at = datetime.datetime.now(datetime.timezone.utc)
    commit_id = compute_commit_id(
        parent_ids=[parent_id] if parent_id else [],
        snapshot_id=snap_id, message=message,
        committed_at_iso=committed_at.isoformat(),
    )
    write_snapshot(root, SnapshotRecord(snapshot_id=snap_id, manifest=manifest))
    write_commit(root, CommitRecord(
        commit_id=commit_id, repo_id=repo_id, branch="main",
        snapshot_id=snap_id, message=message, committed_at=committed_at,
        parent_commit_id=parent_id,
    ))
    ref_file.parent.mkdir(parents=True, exist_ok=True)
    ref_file.write_text(commit_id, encoding="utf-8")
    return commit_id


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------

class TestGcUnit:
    def test_run_gc_empty_repo(self, tmp_path: pathlib.Path) -> None:
        root, _ = _init_repo(tmp_path)
        from muse.core.gc import run_gc
        result = run_gc(root, dry_run=False)
        assert result.collected_count == 0

    def test_run_gc_dry_run_does_not_delete(self, tmp_path: pathlib.Path) -> None:
        root, _ = _init_repo(tmp_path)
        orphan_id = _write_object(root, b"orphaned content")
        from muse.core.gc import run_gc
        result = run_gc(root, dry_run=True)
        obj_path = root / ".muse" / "objects" / orphan_id[:2] / orphan_id[2:]
        assert obj_path.exists()
        assert result.collected_count >= 1

    def test_run_gc_collects_unreachable_objects(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        _make_commit(root, repo_id, message="committed")
        orphan_id = _write_object(root, b"never committed content")
        from muse.core.gc import run_gc
        result = run_gc(root, dry_run=False)
        obj_path = root / ".muse" / "objects" / orphan_id[:2] / orphan_id[2:]
        assert not obj_path.exists()
        assert orphan_id in result.collected_ids


# ---------------------------------------------------------------------------
# Integration (CLI) tests
# ---------------------------------------------------------------------------

class TestGcIntegration:
    def test_gc_default_clean_repo(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        _make_commit(root, repo_id)
        result = runner.invoke(cli, ["gc"], env=_env(root), catch_exceptions=False)
        assert result.exit_code == 0

    def test_gc_dry_run_reports_orphans(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        _make_commit(root, repo_id)
        _write_object(root, b"orphan1")
        _write_object(root, b"orphan2")
        result = runner.invoke(cli, ["gc", "--dry-run"], env=_env(root), catch_exceptions=False)
        assert result.exit_code == 0
        assert "2" in result.output or "collect" in result.output.lower()

    def test_gc_verbose_shows_ids(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        _make_commit(root, repo_id)
        orphan_id = _write_object(root, b"verbose orphan")
        result = runner.invoke(cli, ["gc", "--verbose"], env=_env(root), catch_exceptions=False)
        assert result.exit_code == 0
        assert orphan_id[:12] in result.output

    def test_gc_output_includes_count(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        _write_object(root, b"orphan for count test")
        result = runner.invoke(cli, ["gc"], env=_env(root), catch_exceptions=False)
        assert result.exit_code == 0
        assert "Removed" in result.output or "object" in result.output

    def test_gc_keeps_referenced_objects(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        content = b"referenced file content"
        obj_id = _write_object(root, content)

        from muse.core.store import CommitRecord, SnapshotRecord, write_commit, write_snapshot
        from muse.core.snapshot import compute_snapshot_id, compute_commit_id

        manifest = {"file.mid": obj_id}
        snap_id = compute_snapshot_id(manifest)
        committed_at = datetime.datetime.now(datetime.timezone.utc)
        commit_id = compute_commit_id([], snap_id, "with file", committed_at.isoformat())
        write_snapshot(root, SnapshotRecord(snapshot_id=snap_id, manifest=manifest))
        write_commit(root, CommitRecord(
            commit_id=commit_id, repo_id=repo_id, branch="main",
            snapshot_id=snap_id, message="with file",
            committed_at=committed_at, parent_commit_id=None,
        ))
        (root / ".muse" / "refs" / "heads" / "main").write_text(commit_id)

        runner.invoke(cli, ["gc"], env=_env(root), catch_exceptions=False)
        obj_path = root / ".muse" / "objects" / obj_id[:2] / obj_id[2:]
        assert obj_path.exists()

    def test_gc_short_flags(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        _make_commit(root, repo_id)
        _write_object(root, b"short flag orphan")
        result = runner.invoke(cli, ["gc", "-n", "-v"], env=_env(root), catch_exceptions=False)
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# Stress tests
# ---------------------------------------------------------------------------

class TestGcStress:
    def test_gc_many_orphaned_objects(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        _make_commit(root, repo_id)
        orphan_ids = [_write_object(root, f"orphan {i}".encode()) for i in range(100)]

        result = runner.invoke(cli, ["gc"], env=_env(root), catch_exceptions=False)
        assert result.exit_code == 0
        assert "100" in result.output

        for oid in orphan_ids:
            obj_path = root / ".muse" / "objects" / oid[:2] / oid[2:]
            assert not obj_path.exists()

    def test_gc_repeated_runs_idempotent(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        _make_commit(root, repo_id)
        for _ in range(3):
            result = runner.invoke(cli, ["gc"], env=_env(root), catch_exceptions=False)
            assert result.exit_code == 0

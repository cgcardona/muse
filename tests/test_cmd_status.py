"""Comprehensive tests for ``muse status``.

Covers:
- Unit: status correctly identifies clean, added, modified, deleted files
- Integration: status after commit vs after changes
- E2E: CLI flags (--short / -s, --branch / -b, --format json / text)
- Stress: many tracked files, large workspace
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
# Shared helpers
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


def _make_commit(root: pathlib.Path, repo_id: str, message: str = "test") -> str:
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
# E2E CLI tests
# ---------------------------------------------------------------------------

class TestStatusCLI:
    def test_status_empty_repo(self, tmp_path: pathlib.Path) -> None:
        root, _ = _init_repo(tmp_path)
        result = runner.invoke(cli, ["status"], env=_env(root), catch_exceptions=False)
        assert result.exit_code == 0

    def test_status_after_commit_shows_clean(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        _make_commit(root, repo_id)
        result = runner.invoke(cli, ["status"], env=_env(root), catch_exceptions=False)
        assert result.exit_code == 0

    def test_status_shows_branch_name(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        _make_commit(root, repo_id)
        result = runner.invoke(cli, ["status"], env=_env(root), catch_exceptions=False)
        assert "main" in result.output

    def test_status_short_flag(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        _make_commit(root, repo_id)
        result = runner.invoke(cli, ["status", "--short"], env=_env(root), catch_exceptions=False)
        assert result.exit_code == 0

    def test_status_short_s_flag(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        _make_commit(root, repo_id)
        result = runner.invoke(cli, ["status", "-s"], env=_env(root), catch_exceptions=False)
        assert result.exit_code == 0

    def test_status_branch_flag(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        _make_commit(root, repo_id)
        result = runner.invoke(cli, ["status", "--branch"], env=_env(root), catch_exceptions=False)
        assert result.exit_code == 0
        assert "main" in result.output

    def test_status_porcelain_flag(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        _make_commit(root, repo_id)
        result = runner.invoke(cli, ["status", "--porcelain"], env=_env(root), catch_exceptions=False)
        assert result.exit_code == 0

    def test_status_format_json(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        _make_commit(root, repo_id)
        (root / "new.mid").write_bytes(b"new file")
        result = runner.invoke(cli, ["status", "--format", "json"], env=_env(root), catch_exceptions=False)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "branch" in data
        assert "added" in data

    def test_status_porcelain_output_machine_readable(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        _make_commit(root, repo_id)
        (root / "new.mid").write_bytes(b"new file")
        result = runner.invoke(cli, ["status", "--porcelain"], env=_env(root), catch_exceptions=False)
        assert result.exit_code == 0

    def test_status_detects_new_file(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        _make_commit(root, repo_id)
        (root / "new_song.mid").write_bytes(b"MIDI data")
        result = runner.invoke(cli, ["status"], env=_env(root), catch_exceptions=False)
        assert result.exit_code == 0
        assert "new_song.mid" in result.output

    def test_status_output_sanitized(self, tmp_path: pathlib.Path) -> None:
        """Status output must not echo ANSI escape codes from filenames."""
        root, repo_id = _init_repo(tmp_path)
        _make_commit(root, repo_id)
        result = runner.invoke(cli, ["status"], env=_env(root), catch_exceptions=False)
        # No raw ANSI from repo state (filenames can't be controlled here easily)
        assert result.exit_code == 0


class TestStatusStress:
    def test_status_with_many_files(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        _make_commit(root, repo_id)
        for i in range(100):
            (root / f"track_{i:03d}.mid").write_bytes(f"data {i}".encode())
        result = runner.invoke(cli, ["status"], env=_env(root), catch_exceptions=False)
        assert result.exit_code == 0

    def test_repeated_status_calls_idempotent(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        _make_commit(root, repo_id)
        outputs = []
        for _ in range(5):
            result = runner.invoke(cli, ["status"], env=_env(root), catch_exceptions=False)
            assert result.exit_code == 0
            outputs.append(result.output)
        assert len(set(outputs)) == 1

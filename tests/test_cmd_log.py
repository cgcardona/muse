"""Comprehensive tests for ``muse log``.

Covers:
- Unit: _parse_date helper
- Integration: log with history, filters, authors
- E2E: full CLI via CliRunner
- Security: no path traversal via --ref, sanitized output
- Stress: long commit history with limit
"""

from __future__ import annotations

import datetime
import json
import pathlib
import uuid

import pytest
from typer.testing import CliRunner

from muse.cli.app import cli

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


def _make_commit(
    root: pathlib.Path, repo_id: str, message: str = "commit",
    author: str = "Alice",
) -> str:
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
        parent_commit_id=parent_id, author=author,
    ))
    ref_file.parent.mkdir(parents=True, exist_ok=True)
    ref_file.write_text(commit_id, encoding="utf-8")
    return commit_id


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------

class TestLogUnit:
    def test_parse_date_iso(self) -> None:
        from muse.cli.commands.log import _parse_date
        dt = _parse_date("2025-01-15")
        assert dt.year == 2025
        assert dt.month == 1
        assert dt.day == 15

    def test_parse_date_relative_days(self) -> None:
        from muse.cli.commands.log import _parse_date
        dt = _parse_date("7 days ago")
        assert isinstance(dt, datetime.datetime)


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------

class TestLogIntegration:
    def test_log_empty_repo_shows_nothing(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        result = runner.invoke(cli, ["log"], env=_env(root), catch_exceptions=False)
        assert result.exit_code == 0

    def test_log_single_commit(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        _make_commit(root, repo_id, message="first commit")
        result = runner.invoke(cli, ["log"], env=_env(root), catch_exceptions=False)
        assert result.exit_code == 0
        assert "first commit" in result.output

    def test_log_multiple_commits_newest_first(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        _make_commit(root, repo_id, message="commit one")
        _make_commit(root, repo_id, message="commit two")
        _make_commit(root, repo_id, message="commit three")
        result = runner.invoke(cli, ["log"], env=_env(root), catch_exceptions=False)
        pos_one = result.output.find("commit one")
        pos_three = result.output.find("commit three")
        assert pos_three < pos_one  # newest appears first

    def test_log_oneline_format(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        _make_commit(root, repo_id, message="oneline test")
        result = runner.invoke(cli, ["log", "--oneline"], env=_env(root), catch_exceptions=False)
        assert result.exit_code == 0
        lines = [l for l in result.output.splitlines() if l.strip()]
        assert len(lines) == 1
        assert "oneline test" in lines[0]

    def test_log_max_count_limits_output(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        for i in range(5):
            _make_commit(root, repo_id, message=f"commit {i}")
        result = runner.invoke(cli, ["log", "-n", "2"], env=_env(root), catch_exceptions=False)
        assert result.exit_code == 0
        # Count distinct commit IDs in output (each is 64 chars long)
        import re
        commit_ids = re.findall(r"commit [0-9a-f]{64}", result.output)
        assert len(commit_ids) <= 2

    def test_log_filter_by_author(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        _make_commit(root, repo_id, message="by alice", author="Alice")
        _make_commit(root, repo_id, message="by bob", author="Bob")
        result = runner.invoke(cli, ["log", "--author", "Alice"], env=_env(root), catch_exceptions=False)
        assert "by alice" in result.output
        assert "by bob" not in result.output

    def test_log_stat_flag(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        _make_commit(root, repo_id, message="stat commit")
        result = runner.invoke(cli, ["log", "--stat"], env=_env(root), catch_exceptions=False)
        assert result.exit_code == 0

    def test_log_max_count_zero_rejected(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        result = runner.invoke(cli, ["log", "-n", "0"], env=_env(root))
        assert result.exit_code != 0

    def test_log_short_flags(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        _make_commit(root, repo_id, message="short flag")
        result = runner.invoke(cli, ["log", "-n", "1", "-p"], env=_env(root), catch_exceptions=False)
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# Security tests
# ---------------------------------------------------------------------------

class TestLogSecurity:
    def test_log_invalid_branch_name_handled(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        _make_commit(root, repo_id)
        result = runner.invoke(cli, ["log", "../../../etc/passwd"], env=_env(root))
        assert "\x1b" not in result.output


# ---------------------------------------------------------------------------
# Stress tests
# ---------------------------------------------------------------------------

class TestLogStress:
    def test_long_history_with_limit(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        for i in range(200):
            _make_commit(root, repo_id, message=f"commit {i:03d}")
        result = runner.invoke(cli, ["log", "-n", "10", "--oneline"], env=_env(root), catch_exceptions=False)
        assert result.exit_code == 0
        lines = [l for l in result.output.splitlines() if l.strip()]
        assert 1 <= len(lines) <= 10

    def test_many_commits_author_filter_performance(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        for i in range(100):
            author = "Alice" if i % 2 == 0 else "Bob"
            _make_commit(root, repo_id, message=f"msg {i}", author=author)
        result = runner.invoke(cli, ["log", "--author", "Alice", "-n", "50"], env=_env(root), catch_exceptions=False)
        assert result.exit_code == 0
        assert "Bob" not in result.output

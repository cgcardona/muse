"""Comprehensive tests for ``muse reflog``.

Covers:
- Unit: _fmt_entry sanitizes operation field
- Integration: reflog populated by commits, --all flag
- E2E: full CLI via CliRunner
- Security: branch name validated before use as path, operation sanitized
- Stress: large reflog with limit
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


def _make_commit_with_reflog(
    root: pathlib.Path, repo_id: str, message: str = "commit", branch: str = "main"
) -> str:
    from muse.core.store import CommitRecord, SnapshotRecord, write_commit, write_snapshot
    from muse.core.snapshot import compute_snapshot_id, compute_commit_id
    from muse.core.reflog import append_reflog

    ref_file = root / ".muse" / "refs" / "heads" / branch
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
        commit_id=commit_id, repo_id=repo_id, branch=branch,
        snapshot_id=snap_id, message=message, committed_at=committed_at,
        parent_commit_id=parent_id,
    ))
    ref_file.parent.mkdir(parents=True, exist_ok=True)
    ref_file.write_text(commit_id, encoding="utf-8")
    append_reflog(root, branch, old_id=parent_id or "0" * 64, new_id=commit_id,
                  author="user", operation=f"commit: {message}")
    return commit_id


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------

class TestReflogUnit:
    def test_fmt_entry_sanitizes_operation(self) -> None:
        from muse.cli.commands.reflog import _fmt_entry
        from muse.core.reflog import ReflogEntry

        entry = ReflogEntry(
            old_id="0" * 64, new_id="a" * 64, author="user",
            timestamp=datetime.datetime(2025, 1, 1, tzinfo=datetime.timezone.utc),
            operation="commit: Hello\x1b[31mRED\x1b[0m",
        )
        result = _fmt_entry(0, entry)
        assert "\x1b" not in result

    def test_fmt_entry_initial_shown_as_initial(self) -> None:
        from muse.cli.commands.reflog import _fmt_entry
        from muse.core.reflog import ReflogEntry

        entry = ReflogEntry(
            old_id="0" * 64, new_id="b" * 64, author="user",
            timestamp=datetime.datetime(2025, 1, 1, tzinfo=datetime.timezone.utc),
            operation="branch: created",
        )
        result = _fmt_entry(0, entry)
        assert "initial" in result


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------

class TestReflogIntegration:
    def test_reflog_empty_repo(self, tmp_path: pathlib.Path) -> None:
        root, _ = _init_repo(tmp_path)
        result = runner.invoke(cli, ["reflog"], env=_env(root), catch_exceptions=False)
        assert result.exit_code == 0
        assert "No reflog entries" in result.output

    def test_reflog_after_commit(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        _make_commit_with_reflog(root, repo_id, message="my first commit")
        result = runner.invoke(cli, ["reflog"], env=_env(root), catch_exceptions=False)
        assert result.exit_code == 0
        assert "@{0" in result.output
        assert "commit: my first commit" in result.output

    def test_reflog_limit(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        for i in range(10):
            _make_commit_with_reflog(root, repo_id, message=f"commit {i}")
        result = runner.invoke(cli, ["reflog", "--limit", "3"], env=_env(root), catch_exceptions=False)
        assert result.exit_code == 0
        lines = [l for l in result.output.splitlines() if "@{" in l]
        assert len(lines) <= 3

    def test_reflog_branch_flag(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        _make_commit_with_reflog(root, repo_id, message="on main")
        result = runner.invoke(cli, ["reflog", "--branch", "main"], env=_env(root), catch_exceptions=False)
        assert result.exit_code == 0
        assert "main" in result.output

    def test_reflog_short_flags(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        for i in range(5):
            _make_commit_with_reflog(root, repo_id, message=f"commit {i}")
        result = runner.invoke(cli, ["reflog", "-n", "2", "-b", "main"], env=_env(root), catch_exceptions=False)
        assert result.exit_code == 0
        lines = [l for l in result.output.splitlines() if "@{" in l]
        assert len(lines) <= 2

    def test_reflog_all_flag_lists_refs(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        _make_commit_with_reflog(root, repo_id, message="first")
        result = runner.invoke(cli, ["reflog", "--all"], env=_env(root), catch_exceptions=False)
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# Security tests
# ---------------------------------------------------------------------------

class TestReflogSecurity:
    def test_invalid_branch_name_rejected(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        _make_commit_with_reflog(root, repo_id)
        result = runner.invoke(cli, ["reflog", "--branch", "../../../etc/passwd"], env=_env(root))
        assert result.exit_code != 0

    def test_operation_with_control_chars_sanitized(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        from muse.core.reflog import append_reflog
        _make_commit_with_reflog(root, repo_id, message="clean")
        append_reflog(root, "main", old_id="0" * 64, new_id="a" * 64,
                      author="user", operation="evil\x1b[31mRED\x1b[0m op")
        result = runner.invoke(cli, ["reflog"], env=_env(root), catch_exceptions=False)
        assert result.exit_code == 0
        assert "\x1b" not in result.output


# ---------------------------------------------------------------------------
# Stress tests
# ---------------------------------------------------------------------------

class TestReflogStress:
    def test_large_reflog_with_limit(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        for i in range(50):
            _make_commit_with_reflog(root, repo_id, message=f"commit {i:03d}")
        result = runner.invoke(cli, ["reflog", "-n", "5"], env=_env(root), catch_exceptions=False)
        assert result.exit_code == 0
        lines = [l for l in result.output.splitlines() if "@{" in l]
        assert len(lines) <= 5

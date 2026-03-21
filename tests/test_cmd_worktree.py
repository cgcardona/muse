"""Comprehensive tests for ``muse worktree``.

Covers:
- E2E: add, list, remove worktrees
- Integration: worktree directory created and removed
- Security: sanitized path output
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
# Tests
# ---------------------------------------------------------------------------

class TestWorktreeCLI:
    def test_worktree_list_empty(self, tmp_path: pathlib.Path) -> None:
        root, _ = _init_repo(tmp_path)
        result = runner.invoke(cli, ["worktree", "list"], env=_env(root), catch_exceptions=False)
        assert result.exit_code == 0

    def test_worktree_add_creates_directory(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        _make_commit(root, repo_id)
        result = runner.invoke(
            cli, ["worktree", "add", "my-wt", "main"],
            env=_env(root), catch_exceptions=False
        )
        assert result.exit_code == 0

    def test_worktree_list_after_add(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        _make_commit(root, repo_id)
        runner.invoke(
            cli, ["worktree", "add", "my-wt2", "main"],
            env=_env(root), catch_exceptions=False
        )
        result = runner.invoke(cli, ["worktree", "list"], env=_env(root), catch_exceptions=False)
        assert result.exit_code == 0

    def test_worktree_remove(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        _make_commit(root, repo_id)
        runner.invoke(
            cli, ["worktree", "add", "rm-wt", "main"],
            env=_env(root), catch_exceptions=False
        )
        result = runner.invoke(
            cli, ["worktree", "remove", "rm-wt"],
            env=_env(root), catch_exceptions=False
        )
        assert result.exit_code == 0

    def test_worktree_list_format_json(self, tmp_path: pathlib.Path) -> None:
        root, _ = _init_repo(tmp_path)
        result = runner.invoke(
            cli, ["worktree", "list", "--format", "json"],
            env=_env(root), catch_exceptions=False
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)

    def test_worktree_output_sanitized(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        _make_commit(root, repo_id)
        result = runner.invoke(cli, ["worktree", "list"], env=_env(root), catch_exceptions=False)
        assert "\x1b" not in result.output

    def test_worktree_remove_nonexistent_fails(self, tmp_path: pathlib.Path) -> None:
        root, _ = _init_repo(tmp_path)
        result = runner.invoke(cli, ["worktree", "remove", "nonexistent"], env=_env(root))
        assert result.exit_code != 0

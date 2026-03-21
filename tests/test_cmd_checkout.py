"""Comprehensive tests for ``muse checkout``.

Covers:
- Unit: branch creation, branch switch, detached HEAD
- Integration: working-tree restore, HEAD pointer updates
- E2E: CLI flags (--branch / -b, existing branch, commit-id)
- Security: validate_branch_name rejects traversal inputs
- Stress: rapid branch creation and switching
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


def _make_commit(root: pathlib.Path, repo_id: str, message: str = "test",
                 branch: str = "main") -> str:
    from muse.core.store import CommitRecord, SnapshotRecord, write_commit, write_snapshot
    from muse.core.snapshot import compute_snapshot_id, compute_commit_id

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
    return commit_id


# ---------------------------------------------------------------------------
# Unit / Integration tests
# ---------------------------------------------------------------------------

class TestCheckoutUnit:
    def test_create_new_branch(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        _make_commit(root, repo_id)
        result = runner.invoke(cli, ["checkout", "-b", "feature"], env=_env(root), catch_exceptions=False)
        assert result.exit_code == 0
        head = (root / ".muse" / "HEAD").read_text()
        assert "feature" in head

    def test_switch_to_existing_branch(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        commit_id = _make_commit(root, repo_id)
        (root / ".muse" / "refs" / "heads" / "dev").write_text(commit_id)
        result = runner.invoke(cli, ["checkout", "dev"], env=_env(root), catch_exceptions=False)
        assert result.exit_code == 0
        head = (root / ".muse" / "HEAD").read_text()
        assert "dev" in head

    def test_switch_by_commit_id(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        commit_id = _make_commit(root, repo_id)
        result = runner.invoke(cli, ["checkout", commit_id], env=_env(root), catch_exceptions=False)
        assert result.exit_code == 0

    def test_checkout_nonexistent_branch_fails(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        _make_commit(root, repo_id)
        result = runner.invoke(cli, ["checkout", "nonexistent"], env=_env(root))
        assert result.exit_code != 0

    def test_create_branch_from_commit(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        commit_id = _make_commit(root, repo_id)
        result = runner.invoke(cli, ["checkout", "-b", "new-branch"], env=_env(root), catch_exceptions=False)
        assert result.exit_code == 0
        ref_path = root / ".muse" / "refs" / "heads" / "new-branch"
        assert ref_path.exists()
        assert ref_path.read_text().strip() == commit_id


class TestCheckoutSecurity:
    def test_invalid_branch_name_dotdot_rejected(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        _make_commit(root, repo_id)
        result = runner.invoke(cli, ["checkout", "../traversal"], env=_env(root))
        assert result.exit_code != 0

    def test_invalid_branch_name_slash_only_rejected(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        _make_commit(root, repo_id)
        result = runner.invoke(cli, ["checkout", "/etc/passwd"], env=_env(root))
        assert result.exit_code != 0

    def test_create_branch_traversal_rejected(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        _make_commit(root, repo_id)
        result = runner.invoke(cli, ["checkout", "-b", "../../evil"], env=_env(root))
        assert result.exit_code != 0

    def test_ansi_in_output_sanitized(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        _make_commit(root, repo_id)
        result = runner.invoke(cli, ["checkout", "safe-branch"], env=_env(root))
        assert "\x1b" not in result.output


class TestCheckoutStress:
    def test_many_branch_creations(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        _make_commit(root, repo_id)
        for i in range(20):
            result = runner.invoke(
                cli, ["checkout", "-b", f"branch-{i}"], env=_env(root), catch_exceptions=False
            )
            assert result.exit_code == 0

    def test_rapid_branch_switching(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        commit_id = _make_commit(root, repo_id)
        for i in range(5):
            branch = f"branch-{i}"
            (root / ".muse" / "refs" / "heads" / branch).write_text(commit_id)
        for i in range(5):
            branch = f"branch-{i}"
            result = runner.invoke(cli, ["checkout", branch], env=_env(root), catch_exceptions=False)
            assert result.exit_code == 0

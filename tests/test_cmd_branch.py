"""Comprehensive tests for ``muse branch``.

Covers:
- Unit: _list_branches helper behaviour
- Integration: create, delete, list with committed repo
- E2E: full CLI round-trips via CliRunner
- Security: invalid branch names rejected, no path traversal
- Stress: many branches listed and deleted efficiently
"""

from __future__ import annotations

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


def _init_repo(tmp_path: pathlib.Path) -> pathlib.Path:
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
    refs_dir = muse_dir / "refs" / "heads"
    refs_dir.mkdir(parents=True)
    (muse_dir / "snapshots").mkdir()
    (muse_dir / "commits").mkdir()
    (muse_dir / "objects").mkdir()
    return tmp_path


def _make_commit(root: pathlib.Path, branch: str = "main", message: str = "init") -> str:
    import datetime
    from muse.core.store import CommitRecord, SnapshotRecord, write_commit, write_snapshot
    from muse.core.snapshot import compute_snapshot_id, compute_commit_id

    repo_id = json.loads((root / ".muse" / "repo.json").read_text())["repo_id"]
    ref_file = root / ".muse" / "refs" / "heads" / branch
    parent_id = ref_file.read_text().strip() if ref_file.exists() else None
    manifest: dict[str, str] = {}
    snap_id = compute_snapshot_id(manifest)
    committed_at = datetime.datetime.now(datetime.timezone.utc)
    commit_id = compute_commit_id(
        parent_ids=[parent_id] if parent_id else [],
        snapshot_id=snap_id,
        message=message,
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
# Unit tests
# ---------------------------------------------------------------------------

class TestBranchUnit:
    def test_list_branches_empty_repo(self, tmp_path: pathlib.Path) -> None:
        root = _init_repo(tmp_path)
        from muse.cli.commands.branch import _list_branches
        assert _list_branches(root) == []

    def test_list_branches_after_create(self, tmp_path: pathlib.Path) -> None:
        root = _init_repo(tmp_path)
        refs_dir = root / ".muse" / "refs" / "heads"
        (refs_dir / "main").write_text("a" * 64, encoding="utf-8")
        (refs_dir / "dev").write_text("b" * 64, encoding="utf-8")
        from muse.cli.commands.branch import _list_branches
        assert _list_branches(root) == ["dev", "main"]


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------

class TestBranchIntegration:
    def test_create_and_list_branch(self, tmp_path: pathlib.Path) -> None:
        root = _init_repo(tmp_path)
        _make_commit(root, "main")
        result = runner.invoke(cli, ["branch", "feat/test"], env=_env(root), catch_exceptions=False)
        assert result.exit_code == 0
        assert "Created branch" in result.output

        result2 = runner.invoke(cli, ["branch"], env=_env(root), catch_exceptions=False)
        assert "feat/test" in result2.output
        assert "main" in result2.output

    def test_list_marks_current_branch(self, tmp_path: pathlib.Path) -> None:
        root = _init_repo(tmp_path)
        _make_commit(root, "main")
        result = runner.invoke(cli, ["branch"], env=_env(root), catch_exceptions=False)
        assert "* main" in result.output

    def test_verbose_shows_commit_sha(self, tmp_path: pathlib.Path) -> None:
        root = _init_repo(tmp_path)
        commit_id = _make_commit(root, "main")
        result = runner.invoke(cli, ["branch", "--verbose"], env=_env(root), catch_exceptions=False)
        assert commit_id[:8] in result.output

    def test_delete_branch(self, tmp_path: pathlib.Path) -> None:
        root = _init_repo(tmp_path)
        _make_commit(root, "main")
        runner.invoke(cli, ["branch", "to-delete"], env=_env(root), catch_exceptions=False)
        result = runner.invoke(cli, ["branch", "--delete", "to-delete"], env=_env(root), catch_exceptions=False)
        assert result.exit_code == 0
        assert "Deleted" in result.output

        result2 = runner.invoke(cli, ["branch"], env=_env(root), catch_exceptions=False)
        assert "to-delete" not in result2.output

    def test_delete_current_branch_rejected(self, tmp_path: pathlib.Path) -> None:
        root = _init_repo(tmp_path)
        _make_commit(root, "main")
        result = runner.invoke(cli, ["branch", "--delete", "main"], env=_env(root))
        assert result.exit_code != 0
        assert "Cannot delete" in result.output

    def test_duplicate_branch_rejected(self, tmp_path: pathlib.Path) -> None:
        root = _init_repo(tmp_path)
        _make_commit(root, "main")
        runner.invoke(cli, ["branch", "dup"], env=_env(root), catch_exceptions=False)
        result = runner.invoke(cli, ["branch", "dup"], env=_env(root))
        assert result.exit_code != 0
        assert "already exists" in result.output

    def test_short_flag_delete(self, tmp_path: pathlib.Path) -> None:
        root = _init_repo(tmp_path)
        _make_commit(root, "main")
        runner.invoke(cli, ["branch", "shortflag"], env=_env(root), catch_exceptions=False)
        result = runner.invoke(cli, ["branch", "-d", "shortflag"], env=_env(root), catch_exceptions=False)
        assert result.exit_code == 0

    def test_short_flag_verbose(self, tmp_path: pathlib.Path) -> None:
        root = _init_repo(tmp_path)
        commit_id = _make_commit(root, "main")
        result = runner.invoke(cli, ["branch", "-v"], env=_env(root), catch_exceptions=False)
        assert commit_id[:8] in result.output


# ---------------------------------------------------------------------------
# Security tests
# ---------------------------------------------------------------------------

class TestBranchSecurity:
    def test_invalid_branch_name_double_dot(self, tmp_path: pathlib.Path) -> None:
        root = _init_repo(tmp_path)
        result = runner.invoke(cli, ["branch", "../evil"], env=_env(root))
        assert result.exit_code != 0

    def test_invalid_branch_name_slash_prefix(self, tmp_path: pathlib.Path) -> None:
        root = _init_repo(tmp_path)
        result = runner.invoke(cli, ["branch", "/etc/passwd"], env=_env(root))
        assert result.exit_code != 0

    def test_invalid_delete_name(self, tmp_path: pathlib.Path) -> None:
        root = _init_repo(tmp_path)
        result = runner.invoke(cli, ["branch", "--delete", "../../../etc"], env=_env(root))
        assert result.exit_code != 0

    def test_delete_nonexistent_branch(self, tmp_path: pathlib.Path) -> None:
        root = _init_repo(tmp_path)
        result = runner.invoke(cli, ["branch", "--delete", "ghost"], env=_env(root))
        assert result.exit_code != 0
        assert "not found" in result.output


# ---------------------------------------------------------------------------
# Stress tests
# ---------------------------------------------------------------------------

class TestBranchStress:
    def test_many_branches_list_performance(self, tmp_path: pathlib.Path) -> None:
        root = _init_repo(tmp_path)
        _make_commit(root, "main")
        refs_dir = root / ".muse" / "refs" / "heads"
        commit_id = (refs_dir / "main").read_text().strip()

        for i in range(100):
            (refs_dir / f"feat-{i:03d}").write_text(commit_id, encoding="utf-8")

        result = runner.invoke(cli, ["branch"], env=_env(root), catch_exceptions=False)
        assert result.exit_code == 0
        assert "feat-000" in result.output
        assert "feat-099" in result.output

    def test_delete_many_branches(self, tmp_path: pathlib.Path) -> None:
        root = _init_repo(tmp_path)
        _make_commit(root, "main")
        refs_dir = root / ".muse" / "refs" / "heads"
        commit_id = (refs_dir / "main").read_text().strip()

        for i in range(20):
            (refs_dir / f"temp-{i}").write_text(commit_id, encoding="utf-8")

        for i in range(20):
            result = runner.invoke(cli, ["branch", "--delete", f"temp-{i}"], env=_env(root), catch_exceptions=False)
            assert result.exit_code == 0

        result = runner.invoke(cli, ["branch"], env=_env(root), catch_exceptions=False)
        assert "temp-" not in result.output

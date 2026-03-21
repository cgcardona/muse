"""Tests for muse plumbing symbolic-ref."""

from __future__ import annotations

import datetime
import hashlib
import json
import pathlib

import pytest
from typer.testing import CliRunner

from muse.cli.app import cli
from muse.core.store import CommitRecord, SnapshotRecord, write_commit, write_snapshot

runner = CliRunner()


def _sha(tag: str) -> str:
    return hashlib.sha256(tag.encode()).hexdigest()


def _init_repo(path: pathlib.Path, branch: str = "main") -> pathlib.Path:
    muse = path / ".muse"
    (muse / "commits").mkdir(parents=True)
    (muse / "snapshots").mkdir(parents=True)
    (muse / "objects").mkdir(parents=True)
    (muse / "refs" / "heads").mkdir(parents=True)
    (muse / "HEAD").write_text(f"ref: refs/heads/{branch}", encoding="utf-8")
    (muse / "repo.json").write_text(
        json.dumps({"repo_id": "test-repo", "domain": "midi"}), encoding="utf-8"
    )
    return path


def _env(repo: pathlib.Path) -> dict[str, str]:
    return {"MUSE_REPO_ROOT": str(repo)}


def _snap(repo: pathlib.Path, tag: str = "snap") -> str:
    sid = _sha(tag)
    write_snapshot(
        repo,
        SnapshotRecord(
            snapshot_id=sid,
            manifest={},
            created_at=datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc),
        ),
    )
    return sid


def _commit(
    repo: pathlib.Path,
    tag: str,
    snap_id: str,
    branch: str = "main",
    parent: str | None = None,
) -> str:
    cid = _sha(tag)
    write_commit(
        repo,
        CommitRecord(
            commit_id=cid,
            repo_id="test-repo",
            branch=branch,
            snapshot_id=snap_id,
            message=tag,
            committed_at=datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc),
            author="tester",
            parent_commit_id=parent,
            parent2_commit_id=None,
        ),
    )
    ref_path = repo / ".muse" / "refs" / "heads" / branch
    ref_path.parent.mkdir(parents=True, exist_ok=True)
    ref_path.write_text(cid, encoding="utf-8")
    return cid


class TestSymbolicRef:
    def test_read_json_default(self, tmp_path: pathlib.Path) -> None:
        _init_repo(tmp_path)
        result = runner.invoke(cli, ["plumbing", "symbolic-ref", "HEAD"], env=_env(tmp_path))
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["ref"] == "HEAD"
        assert data["branch"] == "main"
        assert data["symbolic_target"] == "refs/heads/main"

    def test_read_with_commit(self, tmp_path: pathlib.Path) -> None:
        _init_repo(tmp_path)
        sid = _snap(tmp_path)
        cid = _commit(tmp_path, "c1", sid)
        result = runner.invoke(cli, ["plumbing", "symbolic-ref", "HEAD"], env=_env(tmp_path))
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["commit_id"] == cid

    def test_read_text_format(self, tmp_path: pathlib.Path) -> None:
        _init_repo(tmp_path)
        result = runner.invoke(
            cli, ["plumbing", "symbolic-ref", "--format", "text", "HEAD"], env=_env(tmp_path)
        )
        assert result.exit_code == 0
        assert result.output.strip() == "refs/heads/main"

    def test_read_short_flag(self, tmp_path: pathlib.Path) -> None:
        _init_repo(tmp_path)
        result = runner.invoke(
            cli,
            ["plumbing", "symbolic-ref", "--format", "text", "--short", "HEAD"],
            env=_env(tmp_path),
        )
        assert result.exit_code == 0
        assert result.output.strip() == "main"

    def test_set_switches_branch(self, tmp_path: pathlib.Path) -> None:
        _init_repo(tmp_path)
        sid = _snap(tmp_path)
        _commit(tmp_path, "c1", sid, "main")
        _commit(tmp_path, "c2", sid, "other")
        result = runner.invoke(
            cli, ["plumbing", "symbolic-ref", "--set", "other", "HEAD"], env=_env(tmp_path)
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["branch"] == "other"

    def test_set_nonexistent_branch_errors(self, tmp_path: pathlib.Path) -> None:
        _init_repo(tmp_path)
        result = runner.invoke(
            cli, ["plumbing", "symbolic-ref", "--set", "nonexistent", "HEAD"], env=_env(tmp_path)
        )
        assert result.exit_code != 0
        data = json.loads(result.output)
        assert "error" in data

    def test_invalid_format_errors(self, tmp_path: pathlib.Path) -> None:
        _init_repo(tmp_path)
        result = runner.invoke(
            cli, ["plumbing", "symbolic-ref", "--format", "xml", "HEAD"], env=_env(tmp_path)
        )
        assert result.exit_code != 0

    def test_unsupported_ref_name_errors(self, tmp_path: pathlib.Path) -> None:
        _init_repo(tmp_path)
        result = runner.invoke(
            cli, ["plumbing", "symbolic-ref", "MERGE_HEAD"], env=_env(tmp_path)
        )
        assert result.exit_code != 0
        data = json.loads(result.output)
        assert "error" in data

    def test_no_commits_commit_id_none(self, tmp_path: pathlib.Path) -> None:
        """Fresh repo with no commits: commit_id should be null."""
        _init_repo(tmp_path)
        result = runner.invoke(cli, ["plumbing", "symbolic-ref", "HEAD"], env=_env(tmp_path))
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["commit_id"] is None

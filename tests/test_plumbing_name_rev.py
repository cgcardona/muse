"""Tests for muse plumbing name-rev."""

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


def _init_repo(path: pathlib.Path) -> pathlib.Path:
    muse = path / ".muse"
    (muse / "commits").mkdir(parents=True)
    (muse / "snapshots").mkdir(parents=True)
    (muse / "objects").mkdir(parents=True)
    (muse / "refs" / "heads").mkdir(parents=True)
    (muse / "HEAD").write_text("ref: refs/heads/main", encoding="utf-8")
    (muse / "repo.json").write_text(
        json.dumps({"repo_id": "test-repo", "domain": "midi"}), encoding="utf-8"
    )
    return path


def _env(repo: pathlib.Path) -> dict[str, str]:
    return {"MUSE_REPO_ROOT": str(repo)}


def _snap(repo: pathlib.Path, tag: str) -> str:
    sid = _sha(tag + ":snap")
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
    branch: str = "main",
    parent: str | None = None,
) -> str:
    sid = _snap(repo, tag)
    cid = _sha(tag)
    write_commit(
        repo,
        CommitRecord(
            commit_id=cid,
            repo_id="test-repo",
            branch=branch,
            snapshot_id=sid,
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


class TestNameRev:
    def test_tip_commit_is_branch_zero(self, tmp_path: pathlib.Path) -> None:
        _init_repo(tmp_path)
        cid = _commit(tmp_path, "c1")
        result = runner.invoke(cli, ["plumbing", "name-rev", cid], env=_env(tmp_path))
        assert result.exit_code == 0
        data = json.loads(result.output)
        r = data["results"][0]
        assert r["commit_id"] == cid
        assert r["undefined"] is False
        assert r["distance"] == 0

    def test_parent_commit_distance_1(self, tmp_path: pathlib.Path) -> None:
        _init_repo(tmp_path)
        c1 = _commit(tmp_path, "c1", parent=None)
        c2 = _commit(tmp_path, "c2", parent=c1)
        result = runner.invoke(cli, ["plumbing", "name-rev", c1], env=_env(tmp_path))
        assert result.exit_code == 0
        data = json.loads(result.output)
        r = data["results"][0]
        assert r["distance"] == 1
        assert r["name"] is not None
        assert "~1" in r["name"]

    def test_multiple_commit_ids(self, tmp_path: pathlib.Path) -> None:
        _init_repo(tmp_path)
        c1 = _commit(tmp_path, "c1", parent=None)
        c2 = _commit(tmp_path, "c2", parent=c1)
        result = runner.invoke(cli, ["plumbing", "name-rev", c1, c2], env=_env(tmp_path))
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data["results"]) == 2
        ids = {r["commit_id"] for r in data["results"]}
        assert c1 in ids
        assert c2 in ids

    def test_unknown_commit_is_undefined(self, tmp_path: pathlib.Path) -> None:
        _init_repo(tmp_path)
        _commit(tmp_path, "c1")
        fake_id = "a" * 64
        result = runner.invoke(cli, ["plumbing", "name-rev", fake_id], env=_env(tmp_path))
        assert result.exit_code == 0
        data = json.loads(result.output)
        r = data["results"][0]
        assert r["undefined"] is True
        assert r["name"] is None

    def test_text_format(self, tmp_path: pathlib.Path) -> None:
        _init_repo(tmp_path)
        cid = _commit(tmp_path, "c1")
        result = runner.invoke(
            cli, ["plumbing", "name-rev", "--format", "text", cid], env=_env(tmp_path)
        )
        assert result.exit_code == 0
        assert cid in result.output

    def test_name_only_flag(self, tmp_path: pathlib.Path) -> None:
        _init_repo(tmp_path)
        cid = _commit(tmp_path, "c1")
        result = runner.invoke(
            cli,
            ["plumbing", "name-rev", "--format", "text", "--name-only", cid],
            env=_env(tmp_path),
        )
        assert result.exit_code == 0
        assert cid not in result.output
        assert result.output.strip()

    def test_custom_undefined_string(self, tmp_path: pathlib.Path) -> None:
        _init_repo(tmp_path)
        _commit(tmp_path, "c1")
        fake_id = "b" * 64
        result = runner.invoke(
            cli,
            ["plumbing", "name-rev", "--format", "text", "--undefined", "UNKNOWN", fake_id],
            env=_env(tmp_path),
        )
        assert result.exit_code == 0
        assert "UNKNOWN" in result.output

    def test_no_args_errors(self, tmp_path: pathlib.Path) -> None:
        _init_repo(tmp_path)
        result = runner.invoke(cli, ["plumbing", "name-rev"], env=_env(tmp_path))
        assert result.exit_code != 0

    def test_invalid_format_errors(self, tmp_path: pathlib.Path) -> None:
        _init_repo(tmp_path)
        cid = _commit(tmp_path, "c1")
        result = runner.invoke(
            cli, ["plumbing", "name-rev", "--format", "xml", cid], env=_env(tmp_path)
        )
        assert result.exit_code != 0

    def test_depth_two_commit(self, tmp_path: pathlib.Path) -> None:
        """Grandparent commit should be named branch~2."""
        _init_repo(tmp_path)
        c1 = _commit(tmp_path, "grandparent", parent=None)
        c2 = _commit(tmp_path, "parent", parent=c1)
        _commit(tmp_path, "tip", parent=c2)
        result = runner.invoke(cli, ["plumbing", "name-rev", c1], env=_env(tmp_path))
        assert result.exit_code == 0
        data = json.loads(result.output)
        r = data["results"][0]
        assert r["distance"] == 2
        assert "~2" in r["name"]

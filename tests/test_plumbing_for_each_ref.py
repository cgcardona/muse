"""Tests for muse plumbing for-each-ref."""

from __future__ import annotations

import datetime
import hashlib
import json
import pathlib

import pytest
from tests.cli_test_helper import CliRunner

cli = None  # argparse migration — CliRunner ignores this arg
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
    branch: str = "main",
    parent: str | None = None,
    ts: datetime.datetime | None = None,
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
            committed_at=ts or datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc),
            author="tester",
            parent_commit_id=parent,
            parent2_commit_id=None,
        ),
    )
    ref_path = repo / ".muse" / "refs" / "heads" / branch
    ref_path.parent.mkdir(parents=True, exist_ok=True)
    ref_path.write_text(cid, encoding="utf-8")
    return cid


class TestForEachRef:
    def test_empty_repo_returns_empty_list(self, tmp_path: pathlib.Path) -> None:
        _init_repo(tmp_path)
        result = runner.invoke(cli, ["plumbing", "for-each-ref"], env=_env(tmp_path))
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["count"] == 0
        assert data["refs"] == []

    def test_after_commit_lists_ref(self, tmp_path: pathlib.Path) -> None:
        _init_repo(tmp_path)
        cid = _commit(tmp_path, "c1")
        result = runner.invoke(cli, ["plumbing", "for-each-ref"], env=_env(tmp_path))
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["count"] == 1
        assert data["refs"][0]["commit_id"] == cid

    def test_ref_detail_fields(self, tmp_path: pathlib.Path) -> None:
        _init_repo(tmp_path)
        _commit(tmp_path, "c1")
        result = runner.invoke(cli, ["plumbing", "for-each-ref"], env=_env(tmp_path))
        assert result.exit_code == 0
        data = json.loads(result.output)
        ref = data["refs"][0]
        for key in ("ref", "branch", "commit_id", "author", "message", "committed_at", "snapshot_id"):
            assert key in ref, f"missing field: {key}"

    def test_sort_by_ref(self, tmp_path: pathlib.Path) -> None:
        _init_repo(tmp_path)
        _commit(tmp_path, "c_dev", "dev")
        _commit(tmp_path, "c_feat", "feat")
        result = runner.invoke(
            cli, ["plumbing", "for-each-ref", "--sort", "ref"], env=_env(tmp_path)
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        refs = [r["ref"] for r in data["refs"]]
        assert refs == sorted(refs)

    def test_sort_desc(self, tmp_path: pathlib.Path) -> None:
        _init_repo(tmp_path)
        _commit(tmp_path, "c_dev", "dev")
        _commit(tmp_path, "c_feat", "feat")
        result = runner.invoke(
            cli, ["plumbing", "for-each-ref", "--sort", "ref", "--desc"], env=_env(tmp_path)
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        refs = [r["ref"] for r in data["refs"]]
        assert refs == sorted(refs, reverse=True)

    def test_count_limits_output(self, tmp_path: pathlib.Path) -> None:
        _init_repo(tmp_path)
        _commit(tmp_path, "c_dev", "dev")
        _commit(tmp_path, "c_feat", "feat")
        result = runner.invoke(
            cli, ["plumbing", "for-each-ref", "--count", "1"], env=_env(tmp_path)
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["count"] == 1
        assert len(data["refs"]) == 1

    def test_pattern_filter(self, tmp_path: pathlib.Path) -> None:
        _init_repo(tmp_path)
        _commit(tmp_path, "c_main", "main")
        _commit(tmp_path, "c_feat", "feat/new")
        result = runner.invoke(
            cli, ["plumbing", "for-each-ref", "--pattern", "refs/heads/feat/*"], env=_env(tmp_path)
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        for ref in data["refs"]:
            assert ref["ref"].startswith("refs/heads/feat/")

    def test_text_format(self, tmp_path: pathlib.Path) -> None:
        _init_repo(tmp_path)
        _commit(tmp_path, "c1")
        result = runner.invoke(
            cli, ["plumbing", "for-each-ref", "--format", "text"], env=_env(tmp_path)
        )
        assert result.exit_code == 0
        lines = [l for l in result.output.strip().splitlines() if l]
        assert len(lines) == 1
        assert "refs/heads/" in lines[0]

    def test_invalid_sort_field(self, tmp_path: pathlib.Path) -> None:
        _init_repo(tmp_path)
        result = runner.invoke(
            cli, ["plumbing", "for-each-ref", "--sort", "nonexistent"], env=_env(tmp_path)
        )
        assert result.exit_code != 0
        data = json.loads(result.output)
        assert "error" in data

    def test_invalid_format(self, tmp_path: pathlib.Path) -> None:
        _init_repo(tmp_path)
        result = runner.invoke(
            cli, ["plumbing", "for-each-ref", "--format", "yaml"], env=_env(tmp_path)
        )
        assert result.exit_code != 0

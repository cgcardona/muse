"""Tests for the new commit-graph flags: --count, --first-parent, --ancestry-path."""

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
    parent2: str | None = None,
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
            parent2_commit_id=parent2,
        ),
    )
    ref_path = repo / ".muse" / "refs" / "heads" / branch
    ref_path.parent.mkdir(parents=True, exist_ok=True)
    ref_path.write_text(cid, encoding="utf-8")
    return cid


class TestCommitGraphCount:
    def test_count_returns_integer(self, tmp_path: pathlib.Path) -> None:
        _init_repo(tmp_path)
        c1 = _commit(tmp_path, "a", parent=None)
        _commit(tmp_path, "b", parent=c1)
        result = runner.invoke(cli, ["plumbing", "commit-graph", "--count"], env=_env(tmp_path))
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "count" in data
        assert data["count"] == 2
        assert "commits" not in data  # full node list suppressed

    def test_count_no_commits_returns_error(self, tmp_path: pathlib.Path) -> None:
        _init_repo(tmp_path)
        result = runner.invoke(cli, ["plumbing", "commit-graph", "--count"], env=_env(tmp_path))
        assert result.exit_code != 0

    def test_count_with_stop_at(self, tmp_path: pathlib.Path) -> None:
        _init_repo(tmp_path)
        base = _commit(tmp_path, "base", parent=None)
        _commit(tmp_path, "feature", parent=base)
        result = runner.invoke(
            cli, ["plumbing", "commit-graph", "--stop-at", base, "--count"], env=_env(tmp_path)
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["count"] == 1  # only "feature", base excluded

    def test_count_short_flag(self, tmp_path: pathlib.Path) -> None:
        _init_repo(tmp_path)
        _commit(tmp_path, "a")
        result = runner.invoke(cli, ["plumbing", "commit-graph", "-c"], env=_env(tmp_path))
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "count" in data


class TestCommitGraphFirstParent:
    def test_first_parent_only(self, tmp_path: pathlib.Path) -> None:
        _init_repo(tmp_path)
        c1 = _commit(tmp_path, "c1", parent=None)
        c2 = _commit(tmp_path, "c2", parent=c1)
        result = runner.invoke(
            cli, ["plumbing", "commit-graph", "--first-parent", "--count"], env=_env(tmp_path)
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["count"] == 2

    def test_first_parent_excludes_merge_parent(self, tmp_path: pathlib.Path) -> None:
        """With --first-parent, second parents of merges are not followed."""
        _init_repo(tmp_path)
        c1 = _commit(tmp_path, "c1", parent=None)
        c2 = _commit(tmp_path, "branch_tip", "feat", parent=c1)
        # merge commit with c1 as first parent, c2 as second parent
        _commit(tmp_path, "merge", parent=c1, parent2=c2)
        result = runner.invoke(
            cli, ["plumbing", "commit-graph", "--first-parent", "--count"], env=_env(tmp_path)
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        # Should NOT follow c2 branch; only main chain: merge → c1
        assert data["count"] == 2  # merge + c1

    def test_first_parent_short_flag(self, tmp_path: pathlib.Path) -> None:
        _init_repo(tmp_path)
        _commit(tmp_path, "c1")
        result = runner.invoke(
            cli, ["plumbing", "commit-graph", "-1", "--count"], env=_env(tmp_path)
        )
        assert result.exit_code == 0


class TestCommitGraphAncestryPath:
    def test_ancestry_path_requires_stop_at(self, tmp_path: pathlib.Path) -> None:
        _init_repo(tmp_path)
        _commit(tmp_path, "c1")
        result = runner.invoke(
            cli, ["plumbing", "commit-graph", "--ancestry-path"], env=_env(tmp_path)
        )
        assert result.exit_code != 0
        data = json.loads(result.output)
        assert "error" in data

    def test_ancestry_path_with_stop_at_runs(self, tmp_path: pathlib.Path) -> None:
        _init_repo(tmp_path)
        base = _commit(tmp_path, "base", parent=None)
        _commit(tmp_path, "feature", parent=base)
        result = runner.invoke(
            cli,
            ["plumbing", "commit-graph", "--stop-at", base, "--ancestry-path"],
            env=_env(tmp_path),
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "commits" in data

    def test_ancestry_path_short_flag(self, tmp_path: pathlib.Path) -> None:
        _init_repo(tmp_path)
        base = _commit(tmp_path, "base", parent=None)
        _commit(tmp_path, "next", parent=base)
        result = runner.invoke(
            cli,
            ["plumbing", "commit-graph", "--stop-at", base, "-a"],
            env=_env(tmp_path),
        )
        assert result.exit_code == 0


class TestCommitGraphCombined:
    def test_first_parent_and_count(self, tmp_path: pathlib.Path) -> None:
        _init_repo(tmp_path)
        c1 = _commit(tmp_path, "c1", parent=None)
        _commit(tmp_path, "c2", parent=c1)
        result = runner.invoke(
            cli, ["plumbing", "commit-graph", "--first-parent", "--count"], env=_env(tmp_path)
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["count"] == 2

    def test_count_always_emits_json(self, tmp_path: pathlib.Path) -> None:
        """--count emits JSON even when --format text is specified."""
        _init_repo(tmp_path)
        _commit(tmp_path, "c1")
        result = runner.invoke(
            cli,
            ["plumbing", "commit-graph", "--count", "--format", "text"],
            env=_env(tmp_path),
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "count" in data

    def test_invalid_format_errors(self, tmp_path: pathlib.Path) -> None:
        _init_repo(tmp_path)
        _commit(tmp_path, "c1")
        result = runner.invoke(
            cli, ["plumbing", "commit-graph", "--format", "csv"], env=_env(tmp_path)
        )
        assert result.exit_code != 0
        data = json.loads(result.output)
        assert "error" in data

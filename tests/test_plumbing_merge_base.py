"""Tests for ``muse plumbing merge-base``.

Verifies commit-ID resolution, branch-name resolution, HEAD resolution,
text-format output, and error handling for unresolvable refs.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import pathlib

import pytest
from typer.testing import CliRunner

from muse.cli.app import cli
from muse.core.errors import ExitCode
from muse.core.store import CommitRecord, SnapshotRecord, write_commit, write_snapshot

runner = CliRunner()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sha(tag: str) -> str:
    return hashlib.sha256(tag.encode()).hexdigest()


def _init_repo(path: pathlib.Path, domain: str = "midi") -> pathlib.Path:
    muse = path / ".muse"
    (muse / "commits").mkdir(parents=True)
    (muse / "snapshots").mkdir(parents=True)
    (muse / "objects").mkdir(parents=True)
    (muse / "refs" / "heads").mkdir(parents=True)
    (muse / "HEAD").write_text("ref: refs/heads/main", encoding="utf-8")
    (muse / "repo.json").write_text(
        json.dumps({"repo_id": "test-repo", "domain": domain}), encoding="utf-8"
    )
    return path


def _env(repo: pathlib.Path) -> dict[str, str]:
    return {"MUSE_REPO_ROOT": str(repo)}


def _snap(repo: pathlib.Path) -> str:
    sid = _sha("snap")
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
        ),
    )
    return cid


def _set_branch(repo: pathlib.Path, branch: str, commit_id: str) -> None:
    ref = repo / ".muse" / "refs" / "heads" / branch
    ref.parent.mkdir(parents=True, exist_ok=True)
    ref.write_text(commit_id, encoding="utf-8")


def _linear_dag(repo: pathlib.Path) -> tuple[str, str, str]:
    """Build A → B (main) and A → C (feat). Returns (A, B, C)."""
    sid = _snap(repo)
    cid_a = _commit(repo, "base", sid)
    cid_b = _commit(repo, "main-tip", sid, branch="main", parent=cid_a)
    cid_c = _commit(repo, "feat-tip", sid, branch="feat", parent=cid_a)
    _set_branch(repo, "main", cid_b)
    _set_branch(repo, "feat", cid_c)
    (repo / ".muse" / "HEAD").write_text("ref: refs/heads/main", encoding="utf-8")
    return cid_a, cid_b, cid_c


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestMergeBase:
    def test_finds_common_ancestor_by_commit_id(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        cid_a, cid_b, cid_c = _linear_dag(repo)
        result = runner.invoke(cli, ["plumbing", "merge-base", cid_b, cid_c], env=_env(repo))
        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        assert data["merge_base"] == cid_a
        assert data["commit_a"] == cid_b
        assert data["commit_b"] == cid_c

    def test_branch_names_resolve_to_correct_base(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        cid_a, _b, _c = _linear_dag(repo)
        result = runner.invoke(cli, ["plumbing", "merge-base", "main", "feat"], env=_env(repo))
        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout)["merge_base"] == cid_a

    def test_head_resolves_to_current_branch(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        cid_a, _b, _c = _linear_dag(repo)
        result = runner.invoke(cli, ["plumbing", "merge-base", "HEAD", "feat"], env=_env(repo))
        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout)["merge_base"] == cid_a

    def test_same_commit_returns_itself(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        sid = _snap(repo)
        cid = _commit(repo, "solo", sid)
        _set_branch(repo, "main", cid)
        result = runner.invoke(cli, ["plumbing", "merge-base", cid, cid], env=_env(repo))
        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout)["merge_base"] == cid

    def test_text_format_emits_bare_commit_id(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        cid_a, cid_b, cid_c = _linear_dag(repo)
        result = runner.invoke(
            cli, ["plumbing", "merge-base", "--format", "text", cid_b, cid_c], env=_env(repo)
        )
        assert result.exit_code == 0, result.output
        assert cid_a in result.stdout

    def test_unresolvable_ref_a_exits_user_error(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        result = runner.invoke(
            cli, ["plumbing", "merge-base", "no-such-branch", "also-missing"], env=_env(repo)
        )
        assert result.exit_code == ExitCode.USER_ERROR
        assert "error" in json.loads(result.stdout)

    def test_bad_format_flag_exits_user_error(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        sid = _snap(repo)
        cid = _commit(repo, "c", sid)
        _set_branch(repo, "main", cid)
        result = runner.invoke(
            cli, ["plumbing", "merge-base", "--format", "yaml", cid, cid], env=_env(repo)
        )
        assert result.exit_code == ExitCode.USER_ERROR

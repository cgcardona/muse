"""Tests for ``muse plumbing update-ref``.

Covers: normal update, new-branch creation, previous-commit tracking,
``--delete`` mode, ``--no-verify`` bypass, commit-not-found error when
``--verify`` is active, bad-commit-ID format, and I/O error recovery.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import pathlib

from tests.cli_test_helper import CliRunner

cli = None  # argparse migration — CliRunner ignores this arg
from muse.core.errors import ExitCode
from muse.core.store import CommitRecord, SnapshotRecord, write_commit, write_snapshot

runner = CliRunner()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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
    repo: pathlib.Path, tag: str, sid: str, branch: str = "main", parent: str | None = None
) -> str:
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
        ),
    )
    return cid


def _set_branch(repo: pathlib.Path, branch: str, cid: str) -> None:
    ref = repo / ".muse" / "refs" / "heads" / branch
    ref.parent.mkdir(parents=True, exist_ok=True)
    ref.write_text(cid, encoding="utf-8")


# ---------------------------------------------------------------------------
# Unit: format validation
# ---------------------------------------------------------------------------


class TestUpdateRefUnit:
    def test_bad_commit_id_format_exits_user_error(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        result = runner.invoke(
            cli, ["plumbing", "update-ref", "main", "not-a-hex-id"], env=_env(repo)
        )
        assert result.exit_code == ExitCode.USER_ERROR

    def test_missing_commit_id_without_delete_exits_user_error(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        result = runner.invoke(cli, ["plumbing", "update-ref", "main"], env=_env(repo))
        assert result.exit_code == ExitCode.USER_ERROR

    def test_commit_not_in_store_exits_user_error_with_verify(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        phantom = _sha("phantom")
        result = runner.invoke(
            cli, ["plumbing", "update-ref", "main", phantom], env=_env(repo)
        )
        assert result.exit_code == ExitCode.USER_ERROR
        assert "error" in json.loads(result.stdout)


# ---------------------------------------------------------------------------
# Integration: normal update
# ---------------------------------------------------------------------------


class TestUpdateRefNormal:
    def test_update_creates_branch_ref_file(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        sid = _snap(repo)
        cid = _commit(repo, "c1", sid)
        result = runner.invoke(
            cli, ["plumbing", "update-ref", "main", cid], env=_env(repo)
        )
        assert result.exit_code == 0, result.output
        ref_file = repo / ".muse" / "refs" / "heads" / "main"
        assert ref_file.read_text(encoding="utf-8") == cid

    def test_output_contains_branch_and_commit_id(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        sid = _snap(repo)
        cid = _commit(repo, "c2", sid)
        result = runner.invoke(cli, ["plumbing", "update-ref", "main", cid], env=_env(repo))
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["branch"] == "main"
        assert data["commit_id"] == cid

    def test_previous_field_is_null_for_new_branch(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        sid = _snap(repo)
        cid = _commit(repo, "c3", sid)
        result = runner.invoke(cli, ["plumbing", "update-ref", "newbranch", cid], env=_env(repo))
        assert result.exit_code == 0
        assert json.loads(result.stdout)["previous"] is None

    def test_previous_field_reflects_old_commit(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        sid = _snap(repo)
        cid_old = _commit(repo, "old", sid)
        cid_new = _commit(repo, "new", sid, parent=cid_old)
        _set_branch(repo, "main", cid_old)
        result = runner.invoke(cli, ["plumbing", "update-ref", "main", cid_new], env=_env(repo))
        assert result.exit_code == 0
        assert json.loads(result.stdout)["previous"] == cid_old

    def test_update_creates_nested_branch_dir(self, tmp_path: pathlib.Path) -> None:
        """Branch names with slashes create subdirectories under refs/heads/."""
        repo = _init_repo(tmp_path)
        sid = _snap(repo)
        cid = _commit(repo, "feat-c", sid, branch="feat/new")
        result = runner.invoke(
            cli, ["plumbing", "update-ref", "feat/new", cid], env=_env(repo)
        )
        assert result.exit_code == 0
        ref_file = repo / ".muse" / "refs" / "heads" / "feat" / "new"
        assert ref_file.exists()
        assert ref_file.read_text(encoding="utf-8") == cid


# ---------------------------------------------------------------------------
# Integration: --delete mode
# ---------------------------------------------------------------------------


class TestUpdateRefDelete:
    def test_delete_removes_ref_file(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        sid = _snap(repo)
        cid = _commit(repo, "d1", sid)
        _set_branch(repo, "to-delete", cid)
        result = runner.invoke(
            cli, ["plumbing", "update-ref", "--delete", "to-delete"], env=_env(repo)
        )
        assert result.exit_code == 0
        assert not (repo / ".muse" / "refs" / "heads" / "to-delete").exists()

    def test_delete_output_has_deleted_true(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        sid = _snap(repo)
        cid = _commit(repo, "d2", sid)
        _set_branch(repo, "bye", cid)
        result = runner.invoke(cli, ["plumbing", "update-ref", "-d", "bye"], env=_env(repo))
        assert result.exit_code == 0
        assert json.loads(result.stdout)["deleted"] is True

    def test_delete_nonexistent_ref_exits_user_error(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        result = runner.invoke(
            cli, ["plumbing", "update-ref", "--delete", "does-not-exist"], env=_env(repo)
        )
        assert result.exit_code == ExitCode.USER_ERROR


# ---------------------------------------------------------------------------
# Integration: --no-verify bypass
# ---------------------------------------------------------------------------


class TestUpdateRefNoVerify:
    def test_no_verify_writes_without_commit_in_store(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        phantom = _sha("not-in-store")
        result = runner.invoke(
            cli, ["plumbing", "update-ref", "--no-verify", "main", phantom], env=_env(repo)
        )
        assert result.exit_code == 0
        ref_file = repo / ".muse" / "refs" / "heads" / "main"
        assert ref_file.read_text(encoding="utf-8") == phantom

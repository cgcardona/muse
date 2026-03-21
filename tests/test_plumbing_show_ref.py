"""Tests for ``muse plumbing show-ref``.

Verifies enumeration of branch refs, HEAD resolution, glob-pattern filtering,
verify mode (silent existence check), and text-format output.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import pathlib

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


def _make_branch(repo: pathlib.Path, branch: str, tag: str, snap_id: str) -> str:
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
            parent_commit_id=None,
        ),
    )
    ref = repo / ".muse" / "refs" / "heads" / branch
    ref.write_text(cid, encoding="utf-8")
    return cid


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestShowRef:
    def test_lists_all_branch_refs(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        sid = _snap(repo)
        _make_branch(repo, "main", "main-commit", sid)
        _make_branch(repo, "dev", "dev-commit", sid)
        result = runner.invoke(cli, ["plumbing", "show-ref"], env=_env(repo))
        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        assert data["count"] == 2
        ref_names = {r["ref"] for r in data["refs"]}
        assert "refs/heads/main" in ref_names
        assert "refs/heads/dev" in ref_names

    def test_commit_ids_match_branch_heads(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        sid = _snap(repo)
        cid_main = _make_branch(repo, "main", "main-c", sid)
        result = runner.invoke(cli, ["plumbing", "show-ref"], env=_env(repo))
        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        main_ref = next(r for r in data["refs"] if r["ref"] == "refs/heads/main")
        assert main_ref["commit_id"] == cid_main

    def test_head_info_included(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        sid = _snap(repo)
        cid = _make_branch(repo, "main", "main-c", sid)
        result = runner.invoke(cli, ["plumbing", "show-ref"], env=_env(repo))
        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        assert data["head"]["branch"] == "main"
        assert data["head"]["commit_id"] == cid

    def test_empty_repo_returns_empty_list(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        result = runner.invoke(cli, ["plumbing", "show-ref"], env=_env(repo))
        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        assert data["count"] == 0
        assert data["refs"] == []

    def test_pattern_filter_restricts_output(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        sid = _snap(repo)
        _make_branch(repo, "main", "main-c", sid)
        _make_branch(repo, "dev", "dev-c", sid)
        result = runner.invoke(
            cli, ["plumbing", "show-ref", "--pattern", "refs/heads/main"], env=_env(repo)
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        assert data["count"] == 1
        assert data["refs"][0]["ref"] == "refs/heads/main"

    def test_head_flag_returns_only_head(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        sid = _snap(repo)
        cid = _make_branch(repo, "main", "main-c", sid)
        _make_branch(repo, "dev", "dev-c", sid)
        result = runner.invoke(cli, ["plumbing", "show-ref", "--head"], env=_env(repo))
        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        assert data["head"]["commit_id"] == cid

    def test_verify_existing_ref_exits_zero(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        sid = _snap(repo)
        _make_branch(repo, "main", "main-c", sid)
        result = runner.invoke(
            cli, ["plumbing", "show-ref", "--verify", "refs/heads/main"], env=_env(repo)
        )
        assert result.exit_code == 0

    def test_verify_missing_ref_exits_user_error(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        result = runner.invoke(
            cli, ["plumbing", "show-ref", "--verify", "refs/heads/nonexistent"], env=_env(repo)
        )
        assert result.exit_code == ExitCode.USER_ERROR

    def test_verify_produces_no_stdout(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        sid = _snap(repo)
        _make_branch(repo, "main", "main-c", sid)
        result = runner.invoke(
            cli, ["plumbing", "show-ref", "--verify", "refs/heads/main"], env=_env(repo)
        )
        assert result.stdout.strip() == ""

    def test_text_format_output(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        sid = _snap(repo)
        _make_branch(repo, "main", "main-c", sid)
        result = runner.invoke(
            cli, ["plumbing", "show-ref", "--format", "text"], env=_env(repo)
        )
        assert result.exit_code == 0, result.output
        assert "refs/heads/main" in result.stdout

    def test_refs_sorted_lexicographically(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        sid = _snap(repo)
        _make_branch(repo, "zzz", "z-c", sid)
        _make_branch(repo, "aaa", "a-c", sid)
        _make_branch(repo, "mmm", "m-c", sid)
        result = runner.invoke(cli, ["plumbing", "show-ref"], env=_env(repo))
        assert result.exit_code == 0, result.output
        ref_names = [r["ref"] for r in json.loads(result.stdout)["refs"]]
        assert ref_names == sorted(ref_names)

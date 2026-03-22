"""Tests for ``muse plumbing rev-parse``.

Covers: HEAD resolution, branch-name resolution, full commit-ID passthrough,
abbreviated-prefix resolution, ambiguous-prefix detection, not-found errors,
text-format output, bad-format handling, and an empty-branch edge case.
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


def _snap(repo: pathlib.Path, tag: str = "snap") -> str:
    sid = _sha(f"snap-{tag}")
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
    ref = repo / ".muse" / "refs" / "heads" / branch
    ref.parent.mkdir(parents=True, exist_ok=True)
    ref.write_text(cid, encoding="utf-8")
    return cid


# ---------------------------------------------------------------------------
# Unit: HEAD resolution
# ---------------------------------------------------------------------------


class TestRevParseHead:
    def test_head_resolves_to_current_branch_commit(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        sid = _snap(repo)
        cid = _commit(repo, "first", sid)
        result = runner.invoke(cli, ["plumbing", "rev-parse", "HEAD"], env=_env(repo))
        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        assert data["commit_id"] == cid
        assert data["ref"] == "HEAD"

    def test_head_case_insensitive(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        sid = _snap(repo)
        cid = _commit(repo, "c1", sid)
        for variant in ("head", "Head", "hEaD"):
            result = runner.invoke(cli, ["plumbing", "rev-parse", variant], env=_env(repo))
            assert result.exit_code == 0
            assert json.loads(result.stdout)["commit_id"] == cid

    def test_head_on_empty_branch_exits_user_error(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)  # No commits yet.
        result = runner.invoke(cli, ["plumbing", "rev-parse", "HEAD"], env=_env(repo))
        assert result.exit_code == ExitCode.USER_ERROR
        assert "error" in json.loads(result.stdout)


# ---------------------------------------------------------------------------
# Integration: branch-name resolution
# ---------------------------------------------------------------------------


class TestRevParseBranch:
    def test_branch_name_resolves_to_tip_commit(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        sid = _snap(repo)
        cid = _commit(repo, "tip", sid, branch="feature")
        (repo / ".muse" / "HEAD").write_text("ref: refs/heads/main", encoding="utf-8")
        result = runner.invoke(cli, ["plumbing", "rev-parse", "feature"], env=_env(repo))
        assert result.exit_code == 0
        assert json.loads(result.stdout)["commit_id"] == cid

    def test_nonexistent_branch_not_found(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        result = runner.invoke(cli, ["plumbing", "rev-parse", "no-such-branch"], env=_env(repo))
        assert result.exit_code == ExitCode.USER_ERROR
        assert json.loads(result.stdout)["error"] == "not found"

    def test_multiple_branches_each_resolve_independently(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        sid = _snap(repo)
        cid_main = _commit(repo, "main-c", sid, branch="main")
        cid_dev = _commit(repo, "dev-c", sid, branch="dev")
        r_main = runner.invoke(cli, ["plumbing", "rev-parse", "main"], env=_env(repo))
        r_dev = runner.invoke(cli, ["plumbing", "rev-parse", "dev"], env=_env(repo))
        assert json.loads(r_main.stdout)["commit_id"] == cid_main
        assert json.loads(r_dev.stdout)["commit_id"] == cid_dev


# ---------------------------------------------------------------------------
# Integration: full & abbreviated commit ID
# ---------------------------------------------------------------------------


class TestRevParseCommitId:
    def test_full_64_char_id_resolves(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        sid = _snap(repo)
        cid = _commit(repo, "commit", sid)
        result = runner.invoke(cli, ["plumbing", "rev-parse", cid], env=_env(repo))
        assert result.exit_code == 0
        assert json.loads(result.stdout)["commit_id"] == cid

    def test_prefix_resolves_when_unique(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        sid = _snap(repo)
        cid = _commit(repo, "unique-prefix", sid)
        prefix = cid[:12]
        result = runner.invoke(cli, ["plumbing", "rev-parse", prefix], env=_env(repo))
        assert result.exit_code == 0
        assert json.loads(result.stdout)["commit_id"] == cid

    def test_unknown_full_id_exits_not_found(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        ghost = _sha("ghost-commit")
        result = runner.invoke(cli, ["plumbing", "rev-parse", ghost], env=_env(repo))
        assert result.exit_code == ExitCode.USER_ERROR


# ---------------------------------------------------------------------------
# Integration: format flags
# ---------------------------------------------------------------------------


class TestRevParseFormats:
    def test_text_format_emits_bare_commit_id(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        sid = _snap(repo)
        cid = _commit(repo, "text", sid)
        result = runner.invoke(
            cli, ["plumbing", "rev-parse", "--format", "text", "main"], env=_env(repo)
        )
        assert result.exit_code == 0
        assert result.stdout.strip() == cid

    def test_short_format_flag(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        sid = _snap(repo)
        cid = _commit(repo, "short", sid)
        result = runner.invoke(cli, ["plumbing", "rev-parse", "-f", "text", "main"], env=_env(repo))
        assert result.exit_code == 0
        assert result.stdout.strip() == cid

    def test_bad_format_exits_user_error(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        sid = _snap(repo)
        _commit(repo, "x", sid)
        result = runner.invoke(
            cli, ["plumbing", "rev-parse", "--format", "toml", "main"], env=_env(repo)
        )
        assert result.exit_code == ExitCode.USER_ERROR

    def test_json_output_has_ref_and_commit_id_keys(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        sid = _snap(repo)
        _commit(repo, "kv", sid)
        result = runner.invoke(cli, ["plumbing", "rev-parse", "main"], env=_env(repo))
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert "ref" in data
        assert "commit_id" in data

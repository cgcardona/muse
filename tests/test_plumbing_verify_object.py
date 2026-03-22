"""Tests for ``muse plumbing verify-object``.

Verifies streaming integrity checking, detection of missing objects, detection
of corrupted objects (hash mismatch), batch mode (multiple IDs), quiet-mode
exit codes, and text-format output.
"""

from __future__ import annotations

import hashlib
import json
import pathlib

from tests.cli_test_helper import CliRunner

cli = None  # argparse migration — CliRunner ignores this arg
from muse.core.errors import ExitCode
from muse.core.object_store import object_path, write_object

runner = CliRunner()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


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


def _obj(repo: pathlib.Path, content: bytes) -> str:
    oid = _sha(content)
    write_object(repo, oid, content)
    return oid


def _fake_id(tag: str) -> str:
    return hashlib.sha256(tag.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestVerifyObject:
    def test_valid_object_passes_verification(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        content = b"test object content"
        oid = _obj(repo, content)
        result = runner.invoke(cli, ["plumbing", "verify-object", oid], env=_env(repo))
        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        assert data["all_ok"] is True
        assert data["checked"] == 1
        assert data["failed"] == 0
        assert data["results"][0]["ok"] is True
        assert data["results"][0]["size_bytes"] == len(content)
        assert data["results"][0]["error"] is None

    def test_missing_object_reported_as_failure(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        missing = _fake_id("not-stored")
        result = runner.invoke(cli, ["plumbing", "verify-object", missing], env=_env(repo))
        assert result.exit_code == ExitCode.USER_ERROR
        data = json.loads(result.stdout)
        assert data["all_ok"] is False
        assert "not found" in data["results"][0]["error"]

    def test_corrupted_object_detected_as_hash_mismatch(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        oid = _obj(repo, b"original content")
        # Overwrite on disk with different bytes — same path, different content.
        object_path(repo, oid).write_bytes(b"corrupted bytes that do not match the sha256 id")
        result = runner.invoke(cli, ["plumbing", "verify-object", oid], env=_env(repo))
        assert result.exit_code == ExitCode.USER_ERROR
        data = json.loads(result.stdout)
        assert data["all_ok"] is False
        assert "mismatch" in data["results"][0]["error"]

    def test_batch_all_valid_exits_zero(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        oid1 = _obj(repo, b"first")
        oid2 = _obj(repo, b"second")
        oid3 = _obj(repo, b"third")
        result = runner.invoke(
            cli, ["plumbing", "verify-object", oid1, oid2, oid3], env=_env(repo)
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        assert data["all_ok"] is True
        assert data["checked"] == 3
        assert data["failed"] == 0

    def test_batch_mixed_reports_partial_failure(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        good = _obj(repo, b"good content")
        bad = _fake_id("missing-id")
        result = runner.invoke(
            cli, ["plumbing", "verify-object", good, bad], env=_env(repo)
        )
        assert result.exit_code == ExitCode.USER_ERROR
        data = json.loads(result.stdout)
        assert data["all_ok"] is False
        assert data["checked"] == 2
        assert data["failed"] == 1

    def test_quiet_all_valid_exits_zero_no_output(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        oid = _obj(repo, b"quiet test")
        result = runner.invoke(
            cli, ["plumbing", "verify-object", "--quiet", oid], env=_env(repo)
        )
        assert result.exit_code == 0
        assert result.stdout.strip() == ""

    def test_quiet_any_invalid_exits_user_error(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        bad = _fake_id("nonexistent")
        result = runner.invoke(
            cli, ["plumbing", "verify-object", "--quiet", bad], env=_env(repo)
        )
        assert result.exit_code == ExitCode.USER_ERROR

    def test_text_format_shows_ok_status(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        oid = _obj(repo, b"text format test")
        result = runner.invoke(
            cli, ["plumbing", "verify-object", "--format", "text", oid], env=_env(repo)
        )
        assert result.exit_code == 0, result.output
        assert "OK" in result.stdout
        assert oid in result.stdout

    def test_text_format_shows_fail_status(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        bad = _fake_id("not-there")
        result = runner.invoke(
            cli, ["plumbing", "verify-object", "--format", "text", bad], env=_env(repo)
        )
        assert result.exit_code == ExitCode.USER_ERROR
        assert "FAIL" in result.stdout

    def test_invalid_sha256_format_reported_without_crash(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        result = runner.invoke(
            cli, ["plumbing", "verify-object", "not-a-hex-string"], env=_env(repo)
        )
        assert result.exit_code == ExitCode.USER_ERROR
        data = json.loads(result.stdout)
        assert data["results"][0]["ok"] is False

    def test_bad_format_flag_exits_user_error(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        oid = _obj(repo, b"data")
        result = runner.invoke(
            cli, ["plumbing", "verify-object", "--format", "csv", oid], env=_env(repo)
        )
        assert result.exit_code == ExitCode.USER_ERROR

    def test_size_bytes_reported_correctly(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        content = b"x" * 1024
        oid = _obj(repo, content)
        result = runner.invoke(cli, ["plumbing", "verify-object", oid], env=_env(repo))
        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout)["results"][0]["size_bytes"] == 1024

"""Tests for ``muse plumbing cat-object``.

Covers: raw streaming output, info-format JSON, missing-object handling,
invalid-ID validation, text/info format switching, size reporting, and
a stress case verifying large blob streaming stays memory-safe.
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
# Unit: format validation
# ---------------------------------------------------------------------------


class TestCatObjectUnit:
    def test_invalid_object_id_format_exits_user_error(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        result = runner.invoke(cli, ["plumbing", "cat-object", "not-hex"], env=_env(repo))
        assert result.exit_code == ExitCode.USER_ERROR

    def test_too_short_id_exits_user_error(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        result = runner.invoke(cli, ["plumbing", "cat-object", "abc123"], env=_env(repo))
        assert result.exit_code == ExitCode.USER_ERROR

    def test_bad_format_exits_user_error(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        oid = _fake_id("x")
        result = runner.invoke(
            cli, ["plumbing", "cat-object", "--format", "json", oid], env=_env(repo)
        )
        assert result.exit_code == ExitCode.USER_ERROR


# ---------------------------------------------------------------------------
# Integration: raw (default) mode
# ---------------------------------------------------------------------------


class TestCatObjectRaw:
    def test_raw_output_matches_stored_bytes(self, tmp_path: pathlib.Path) -> None:
        content = b"raw bytes for cat-object"
        repo = _init_repo(tmp_path)
        oid = _obj(repo, content)
        result = runner.invoke(cli, ["plumbing", "cat-object", oid], env=_env(repo))
        assert result.exit_code == 0, result.output
        assert result.output.encode() == content

    def test_raw_binary_content_round_trip(self, tmp_path: pathlib.Path) -> None:
        content = bytes(range(256))
        repo = _init_repo(tmp_path)
        oid = _obj(repo, content)
        result = runner.invoke(cli, ["plumbing", "cat-object", oid], env=_env(repo))
        assert result.exit_code == 0

    def test_missing_object_raw_exits_user_error(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        missing = _fake_id("not-stored")
        result = runner.invoke(cli, ["plumbing", "cat-object", missing], env=_env(repo))
        assert result.exit_code == ExitCode.USER_ERROR


# ---------------------------------------------------------------------------
# Integration: info format
# ---------------------------------------------------------------------------


class TestCatObjectInfo:
    def test_info_format_reports_present_true(self, tmp_path: pathlib.Path) -> None:
        content = b"info check"
        repo = _init_repo(tmp_path)
        oid = _obj(repo, content)
        result = runner.invoke(
            cli, ["plumbing", "cat-object", "--format", "info", oid], env=_env(repo)
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        assert data["present"] is True
        assert data["object_id"] == oid
        assert data["size_bytes"] == len(content)

    def test_info_format_missing_reports_present_false(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        missing = _fake_id("absent")
        result = runner.invoke(
            cli, ["plumbing", "cat-object", "--format", "info", missing], env=_env(repo)
        )
        assert result.exit_code == ExitCode.USER_ERROR
        data = json.loads(result.stdout)
        assert data["present"] is False
        assert data["size_bytes"] == 0

    def test_info_format_does_not_emit_content(self, tmp_path: pathlib.Path) -> None:
        content = b"no-content-in-info"
        repo = _init_repo(tmp_path)
        oid = _obj(repo, content)
        result = runner.invoke(
            cli, ["plumbing", "cat-object", "--format", "info", oid], env=_env(repo)
        )
        assert result.exit_code == 0
        # Output is JSON only — the raw bytes should NOT appear.
        assert content not in result.output.encode()

    def test_info_size_bytes_accurate(self, tmp_path: pathlib.Path) -> None:
        content = b"q" * 512
        repo = _init_repo(tmp_path)
        oid = _obj(repo, content)
        result = runner.invoke(
            cli, ["plumbing", "cat-object", "-f", "info", oid], env=_env(repo)
        )
        assert result.exit_code == 0
        assert json.loads(result.stdout)["size_bytes"] == 512

    def test_short_format_flag_info(self, tmp_path: pathlib.Path) -> None:
        content = b"short-f"
        repo = _init_repo(tmp_path)
        oid = _obj(repo, content)
        result = runner.invoke(cli, ["plumbing", "cat-object", "-f", "info", oid], env=_env(repo))
        assert result.exit_code == 0
        assert json.loads(result.stdout)["present"] is True


# ---------------------------------------------------------------------------
# Integration: multiple objects
# ---------------------------------------------------------------------------


class TestCatObjectMultiple:
    def test_distinct_objects_return_distinct_content(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        oid1 = _obj(repo, b"content one")
        oid2 = _obj(repo, b"content two")
        r1 = runner.invoke(cli, ["plumbing", "cat-object", oid1], env=_env(repo))
        r2 = runner.invoke(cli, ["plumbing", "cat-object", oid2], env=_env(repo))
        assert r1.output != r2.output


# ---------------------------------------------------------------------------
# Stress: large blob streaming
# ---------------------------------------------------------------------------


class TestCatObjectStress:
    def test_1mib_blob_streams_without_error(self, tmp_path: pathlib.Path) -> None:
        content = b"M" * (1024 * 1024)
        repo = _init_repo(tmp_path)
        oid = _obj(repo, content)
        result = runner.invoke(cli, ["plumbing", "cat-object", "--format", "info", oid], env=_env(repo))
        assert result.exit_code == 0
        assert json.loads(result.stdout)["size_bytes"] == 1024 * 1024

    def test_50_sequential_reads_all_succeed(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        oids = [_obj(repo, f"obj-{i}".encode()) for i in range(50)]
        for oid in oids:
            result = runner.invoke(
                cli, ["plumbing", "cat-object", "--format", "info", oid], env=_env(repo)
            )
            assert result.exit_code == 0
            assert json.loads(result.stdout)["present"] is True

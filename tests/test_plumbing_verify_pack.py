"""Tests for muse plumbing verify-pack."""

from __future__ import annotations

import datetime
import hashlib
import json
import pathlib

import msgpack
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


def _make_bundle(objects: list[dict[str, bytes | str]] | None = None) -> bytes:
    """Build a minimal PackBundle msgpack binary for testing."""
    bundle: dict[str, list[dict[str, bytes | str]]] = {
        "objects": objects or [],
        "commits": [],
        "snapshots": [],
    }
    return msgpack.packb(bundle, use_bin_type=True)


def _good_object() -> dict[str, bytes | str]:
    """Return an ObjectPayload dict with a valid hash and raw bytes content."""
    data = b"hello world"
    oid = hashlib.sha256(data).hexdigest()
    return {"object_id": oid, "content": data}


def _bad_hash_object() -> dict[str, bytes | str]:
    """Return an ObjectPayload dict where the hash does NOT match the content."""
    data = b"hello world"
    wrong_oid = "a" * 64
    return {"object_id": wrong_oid, "content": data}


class TestVerifyPack:
    def test_empty_bundle_passes(self, tmp_path: pathlib.Path) -> None:
        _init_repo(tmp_path)
        bundle_file = tmp_path / "bundle.muse"
        bundle_file.write_bytes(_make_bundle())
        result = runner.invoke(
            cli, ["plumbing", "verify-pack", "--file", str(bundle_file)], env=_env(tmp_path)
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["all_ok"] is True
        assert data["failures"] == []

    def test_good_objects_pass(self, tmp_path: pathlib.Path) -> None:
        _init_repo(tmp_path)
        bundle_file = tmp_path / "bundle.muse"
        bundle_file.write_bytes(_make_bundle([_good_object()]))
        result = runner.invoke(
            cli,
            ["plumbing", "verify-pack", "--file", str(bundle_file), "--no-local"],
            env=_env(tmp_path),
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["all_ok"] is True
        assert data["objects_checked"] == 1

    def test_bad_hash_detected(self, tmp_path: pathlib.Path) -> None:
        _init_repo(tmp_path)
        bundle_file = tmp_path / "bundle.muse"
        bundle_file.write_bytes(_make_bundle([_bad_hash_object()]))
        result = runner.invoke(
            cli,
            ["plumbing", "verify-pack", "--file", str(bundle_file), "--no-local"],
            env=_env(tmp_path),
        )
        assert result.exit_code != 0
        data = json.loads(result.output)
        assert data["all_ok"] is False
        assert len(data["failures"]) == 1
        assert data["failures"][0]["kind"] == "object"
        assert "hash mismatch" in data["failures"][0]["error"]

    def test_text_format_clean(self, tmp_path: pathlib.Path) -> None:
        _init_repo(tmp_path)
        bundle_file = tmp_path / "bundle.muse"
        bundle_file.write_bytes(_make_bundle())
        result = runner.invoke(
            cli,
            ["plumbing", "verify-pack", "--file", str(bundle_file), "--format", "text", "--no-local"],
            env=_env(tmp_path),
        )
        assert result.exit_code == 0
        assert "all_ok=True" in result.output

    def test_text_format_failure(self, tmp_path: pathlib.Path) -> None:
        _init_repo(tmp_path)
        bundle_file = tmp_path / "bundle.muse"
        bundle_file.write_bytes(_make_bundle([_bad_hash_object()]))
        result = runner.invoke(
            cli,
            ["plumbing", "verify-pack", "--file", str(bundle_file), "--format", "text", "--no-local"],
            env=_env(tmp_path),
        )
        assert result.exit_code != 0
        assert "FAIL" in result.output

    def test_quiet_mode_clean_exits_zero(self, tmp_path: pathlib.Path) -> None:
        _init_repo(tmp_path)
        bundle_file = tmp_path / "bundle.muse"
        bundle_file.write_bytes(_make_bundle())
        result = runner.invoke(
            cli,
            ["plumbing", "verify-pack", "--file", str(bundle_file), "--quiet", "--no-local"],
            env=_env(tmp_path),
        )
        assert result.exit_code == 0
        assert result.output.strip() == ""

    def test_quiet_mode_failure_exits_nonzero(self, tmp_path: pathlib.Path) -> None:
        _init_repo(tmp_path)
        bundle_file = tmp_path / "bundle.muse"
        bundle_file.write_bytes(_make_bundle([_bad_hash_object()]))
        result = runner.invoke(
            cli,
            ["plumbing", "verify-pack", "--file", str(bundle_file), "-q", "--no-local"],
            env=_env(tmp_path),
        )
        assert result.exit_code != 0
        assert result.output.strip() == ""

    def test_malformed_msgpack_errors(self, tmp_path: pathlib.Path) -> None:
        _init_repo(tmp_path)
        bundle_file = tmp_path / "bundle.muse"
        bundle_file.write_bytes(b"\xff\xff NOT VALID MSGPACK")
        result = runner.invoke(
            cli, ["plumbing", "verify-pack", "--file", str(bundle_file)], env=_env(tmp_path)
        )
        assert result.exit_code != 0

    def test_multiple_objects_one_bad(self, tmp_path: pathlib.Path) -> None:
        """Mix of good and bad objects — all_ok should be False."""
        _init_repo(tmp_path)
        objs = [_good_object(), _bad_hash_object()]
        bundle_file = tmp_path / "bundle.muse"
        bundle_file.write_bytes(_make_bundle(objs))
        result = runner.invoke(
            cli,
            ["plumbing", "verify-pack", "--file", str(bundle_file), "--no-local"],
            env=_env(tmp_path),
        )
        assert result.exit_code != 0
        data = json.loads(result.output)
        assert data["all_ok"] is False
        assert data["objects_checked"] == 2
        assert len(data["failures"]) == 1

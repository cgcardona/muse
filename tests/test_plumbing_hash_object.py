"""Tests for ``muse plumbing hash-object``.

Covers: SHA-256 correctness, ``--write`` flag storage, streaming safety,
idempotent writes, error cases (missing path, directory path, bad format,
no-repo write), text-format output, and a stress case with a 2 MiB file.
"""

from __future__ import annotations

import hashlib
import json
import pathlib

import pytest
from tests.cli_test_helper import CliRunner

cli = None  # argparse migration — CliRunner ignores this arg
from muse.core.errors import ExitCode
from muse.core.object_store import has_object, object_path

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


def _file(tmp: pathlib.Path, name: str, content: bytes) -> pathlib.Path:
    p = tmp / name
    p.write_bytes(content)
    return p


# ---------------------------------------------------------------------------
# Unit: SHA-256 correctness
# ---------------------------------------------------------------------------


class TestHashObjectUnit:
    def test_known_sha256_matches(self, tmp_path: pathlib.Path) -> None:
        content = b"hello muse"
        expected = _sha(content)
        f = _file(tmp_path, "sample.mid", content)
        repo = _init_repo(tmp_path / "repo")
        result = runner.invoke(
            cli, ["plumbing", "hash-object", "--format", "text", str(f)], env=_env(repo)
        )
        assert result.exit_code == 0, result.output
        assert result.stdout.strip() == expected

    def test_json_output_has_object_id_and_stored_false(self, tmp_path: pathlib.Path) -> None:
        content = b"json output"
        f = _file(tmp_path, "x.mid", content)
        repo = _init_repo(tmp_path / "repo")
        result = runner.invoke(cli, ["plumbing", "hash-object", str(f)], env=_env(repo))
        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        assert data["object_id"] == _sha(content)
        assert data["stored"] is False

    def test_different_content_yields_different_id(self, tmp_path: pathlib.Path) -> None:
        f1 = _file(tmp_path, "a.mid", b"aaa")
        f2 = _file(tmp_path, "b.mid", b"bbb")
        repo = _init_repo(tmp_path / "repo")
        r1 = runner.invoke(cli, ["plumbing", "hash-object", "--format", "text", str(f1)], env=_env(repo))
        r2 = runner.invoke(cli, ["plumbing", "hash-object", "--format", "text", str(f2)], env=_env(repo))
        assert r1.stdout.strip() != r2.stdout.strip()

    def test_empty_file_has_deterministic_sha256(self, tmp_path: pathlib.Path) -> None:
        f = _file(tmp_path, "empty.mid", b"")
        expected = _sha(b"")
        repo = _init_repo(tmp_path / "repo")
        result = runner.invoke(cli, ["plumbing", "hash-object", "--format", "text", str(f)], env=_env(repo))
        assert result.exit_code == 0
        assert result.stdout.strip() == expected


# ---------------------------------------------------------------------------
# Integration: --write flag
# ---------------------------------------------------------------------------


class TestHashObjectWrite:
    def test_write_stores_object_in_object_store(self, tmp_path: pathlib.Path) -> None:
        content = b"write me"
        f = _file(tmp_path, "w.mid", content)
        repo = _init_repo(tmp_path / "repo")
        result = runner.invoke(cli, ["plumbing", "hash-object", "--write", str(f)], env=_env(repo))
        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        assert data["stored"] is True
        assert has_object(repo, data["object_id"])

    def test_write_stores_correct_bytes(self, tmp_path: pathlib.Path) -> None:
        content = b"check round-trip"
        f = _file(tmp_path, "rt.mid", content)
        repo = _init_repo(tmp_path / "repo")
        result = runner.invoke(cli, ["plumbing", "hash-object", "--write", str(f)], env=_env(repo))
        assert result.exit_code == 0
        oid = json.loads(result.stdout)["object_id"]
        stored = object_path(repo, oid).read_bytes()
        assert stored == content

    def test_write_idempotent_second_call(self, tmp_path: pathlib.Path) -> None:
        content = b"idempotent"
        f = _file(tmp_path, "i.mid", content)
        repo = _init_repo(tmp_path / "repo")
        args = ["plumbing", "hash-object", "--write", str(f)]
        r1 = runner.invoke(cli, args, env=_env(repo))
        r2 = runner.invoke(cli, args, env=_env(repo))
        assert r1.exit_code == 0 and r2.exit_code == 0
        assert json.loads(r1.stdout)["object_id"] == json.loads(r2.stdout)["object_id"]

    def test_write_without_repo_exits_user_error(self, tmp_path: pathlib.Path) -> None:
        f = _file(tmp_path, "norepo.mid", b"data")
        # No MUSE_REPO_ROOT, no .muse directory — should fail.
        result = runner.invoke(
            cli, ["plumbing", "hash-object", "--write", str(f)],
            env={"MUSE_REPO_ROOT": str(tmp_path / "nonexistent")}
        )
        assert result.exit_code != 0

    def test_short_write_flag_works(self, tmp_path: pathlib.Path) -> None:
        content = b"short flag"
        f = _file(tmp_path, "sf.mid", content)
        repo = _init_repo(tmp_path / "repo")
        result = runner.invoke(cli, ["plumbing", "hash-object", "-w", str(f)], env=_env(repo))
        assert result.exit_code == 0
        assert json.loads(result.stdout)["stored"] is True


# ---------------------------------------------------------------------------
# Integration: error cases
# ---------------------------------------------------------------------------


class TestHashObjectErrors:
    def test_nonexistent_path_exits_user_error(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path / "repo")
        result = runner.invoke(
            cli, ["plumbing", "hash-object", str(tmp_path / "missing.mid")], env=_env(repo)
        )
        assert result.exit_code == ExitCode.USER_ERROR

    def test_directory_path_exits_user_error(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path / "repo")
        d = tmp_path / "subdir"
        d.mkdir()
        result = runner.invoke(cli, ["plumbing", "hash-object", str(d)], env=_env(repo))
        assert result.exit_code == ExitCode.USER_ERROR

    def test_bad_format_exits_user_error(self, tmp_path: pathlib.Path) -> None:
        f = _file(tmp_path, "f.mid", b"data")
        repo = _init_repo(tmp_path / "repo")
        result = runner.invoke(
            cli, ["plumbing", "hash-object", "--format", "yaml", str(f)], env=_env(repo)
        )
        assert result.exit_code == ExitCode.USER_ERROR

    def test_short_format_flag_works(self, tmp_path: pathlib.Path) -> None:
        f = _file(tmp_path, "g.mid", b"data")
        repo = _init_repo(tmp_path / "repo")
        result = runner.invoke(cli, ["plumbing", "hash-object", "-f", "text", str(f)], env=_env(repo))
        assert result.exit_code == 0
        assert len(result.stdout.strip()) == 64


# ---------------------------------------------------------------------------
# Stress: 2 MiB file handled without heap explosion
# ---------------------------------------------------------------------------


class TestHashObjectStress:
    def test_large_file_hashes_correctly(self, tmp_path: pathlib.Path) -> None:
        content = b"X" * (2 * 1024 * 1024)  # 2 MiB
        expected = _sha(content)
        f = _file(tmp_path, "big.bin", content)
        repo = _init_repo(tmp_path / "repo")
        result = runner.invoke(
            cli, ["plumbing", "hash-object", "--format", "text", str(f)], env=_env(repo)
        )
        assert result.exit_code == 0
        assert result.stdout.strip() == expected

    def test_large_file_write_round_trip(self, tmp_path: pathlib.Path) -> None:
        content = bytes(range(256)) * 4096  # 1 MiB of varied bytes
        f = _file(tmp_path, "varied.bin", content)
        repo = _init_repo(tmp_path / "repo")
        result = runner.invoke(
            cli, ["plumbing", "hash-object", "--write", str(f)], env=_env(repo)
        )
        assert result.exit_code == 0
        oid = json.loads(result.stdout)["object_id"]
        assert object_path(repo, oid).read_bytes() == content

    def test_100_distinct_files_all_unique_ids(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path / "repo")
        ids: set[str] = set()
        for i in range(100):
            content = f"file-content-{i}".encode()
            f = _file(tmp_path, f"f{i}.mid", content)
            result = runner.invoke(
                cli, ["plumbing", "hash-object", "--format", "text", str(f)], env=_env(repo)
            )
            assert result.exit_code == 0
            ids.add(result.stdout.strip())
        assert len(ids) == 100

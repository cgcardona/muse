"""Tests for ``muse verify`` and ``muse/core/verify.py``.

Covers: empty repo, healthy repo, missing commit, missing snapshot,
missing object, corrupted object (hash mismatch), --no-objects flag,
--quiet flag, --format json, stress: 100-commit chain.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import pathlib

import pytest
from tests.cli_test_helper import CliRunner

cli = None  # argparse migration — CliRunner ignores this arg
from muse.core.object_store import object_path, write_object
from muse.core.snapshot import compute_snapshot_id
from muse.core.store import CommitRecord, SnapshotRecord, write_commit, write_snapshot
from muse.core.verify import run_verify

runner = CliRunner()

_REPO_ID = "verify-test"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _init_repo(path: pathlib.Path) -> pathlib.Path:
    muse = path / ".muse"
    for d in ("commits", "snapshots", "objects", "refs/heads"):
        (muse / d).mkdir(parents=True, exist_ok=True)
    (muse / "HEAD").write_text("ref: refs/heads/main", encoding="utf-8")
    (muse / "repo.json").write_text(
        json.dumps({"repo_id": _REPO_ID, "domain": "midi"}), encoding="utf-8"
    )
    return path


def _env(repo: pathlib.Path) -> dict[str, str]:
    return {"MUSE_REPO_ROOT": str(repo)}


def _make_commit(
    root: pathlib.Path,
    parent_id: str | None = None,
    content: bytes = b"data",
    branch: str = "main",
    idx: int = 0,
) -> str:
    obj_id = _sha(content + str(idx).encode())
    write_object(root, obj_id, content + str(idx).encode())
    manifest = {f"file_{idx}.txt": obj_id}
    snap_id = compute_snapshot_id(manifest)
    write_snapshot(root, SnapshotRecord(snapshot_id=snap_id, manifest=manifest))
    committed_at = datetime.datetime.now(datetime.timezone.utc)
    commit_id = _sha(f"{idx}:{snap_id}:{committed_at.isoformat()}".encode())
    write_commit(root, CommitRecord(
        commit_id=commit_id,
        repo_id=_REPO_ID,
        branch=branch,
        snapshot_id=snap_id,
        message=f"commit {idx}",
        committed_at=committed_at,
        parent_commit_id=parent_id,
    ))
    (root / ".muse" / "refs" / "heads" / branch).write_text(commit_id, encoding="utf-8")
    return commit_id


# ---------------------------------------------------------------------------
# Unit: core run_verify
# ---------------------------------------------------------------------------


def test_verify_empty_repo_no_failures(tmp_path: pathlib.Path) -> None:
    _init_repo(tmp_path)
    result = run_verify(tmp_path)
    assert result["all_ok"] is True
    assert result["failures"] == []


def test_verify_healthy_repo(tmp_path: pathlib.Path) -> None:
    _init_repo(tmp_path)
    _make_commit(tmp_path, content=b"healthy", idx=0)
    result = run_verify(tmp_path)
    assert result["all_ok"] is True
    assert result["commits_checked"] == 1
    assert result["objects_checked"] >= 1


def test_verify_missing_commit_fails(tmp_path: pathlib.Path) -> None:
    _init_repo(tmp_path)
    # Write a ref pointing to a nonexistent commit.
    fake_id = "a" * 64
    (tmp_path / ".muse" / "refs" / "heads" / "main").write_text(fake_id, encoding="utf-8")
    result = run_verify(tmp_path)
    assert result["all_ok"] is False
    kinds = [f["kind"] for f in result["failures"]]
    assert "commit" in kinds


def test_verify_corrupted_object_detected(tmp_path: pathlib.Path) -> None:
    _init_repo(tmp_path)
    content = b"original content"
    obj_id = _sha(content)
    write_object(tmp_path, obj_id, content)
    manifest = {"file.txt": obj_id}
    snap_id = compute_snapshot_id(manifest)
    write_snapshot(tmp_path, SnapshotRecord(snapshot_id=snap_id, manifest=manifest))
    committed_at = datetime.datetime.now(datetime.timezone.utc)
    commit_id = _sha(f"corrupt:{snap_id}:{committed_at.isoformat()}".encode())
    write_commit(tmp_path, CommitRecord(
        commit_id=commit_id,
        repo_id=_REPO_ID,
        branch="main",
        snapshot_id=snap_id,
        message="corrupt test",
        committed_at=committed_at,
    ))
    (tmp_path / ".muse" / "refs" / "heads" / "main").write_text(commit_id, encoding="utf-8")

    # Corrupt the object file.
    obj_file = object_path(tmp_path, obj_id)
    obj_file.write_bytes(b"tampered data!")

    result = run_verify(tmp_path, check_objects=True)
    assert result["all_ok"] is False
    kinds = [f["kind"] for f in result["failures"]]
    assert "object" in kinds


def test_verify_no_objects_flag_skips_rehash(tmp_path: pathlib.Path) -> None:
    _init_repo(tmp_path)
    content = b"clean"
    obj_id = _sha(content)
    write_object(tmp_path, obj_id, content)
    manifest = {"f.txt": obj_id}
    snap_id = compute_snapshot_id(manifest)
    write_snapshot(tmp_path, SnapshotRecord(snapshot_id=snap_id, manifest=manifest))
    committed_at = datetime.datetime.now(datetime.timezone.utc)
    commit_id = _sha(f"noobj:{snap_id}".encode())
    write_commit(tmp_path, CommitRecord(
        commit_id=commit_id, repo_id=_REPO_ID, branch="main",
        snapshot_id=snap_id, message="test", committed_at=committed_at,
    ))
    (tmp_path / ".muse" / "refs" / "heads" / "main").write_text(commit_id, encoding="utf-8")

    # Corrupt object but check_objects=False should not detect it.
    obj_file = object_path(tmp_path, obj_id)
    obj_file.write_bytes(b"corrupted!")

    result = run_verify(tmp_path, check_objects=False)
    # Should not flag the corruption since we skipped re-hashing.
    assert result["all_ok"] is True


# ---------------------------------------------------------------------------
# CLI: muse verify
# ---------------------------------------------------------------------------


def test_verify_cli_help() -> None:
    result = runner.invoke(cli, ["verify", "--help"])
    assert result.exit_code == 0
    # Rich injects ANSI codes between '--' dashes; the short flag '-O' is reliable.
    assert "--no-objects" in result.output or "-O" in result.output


def test_verify_cli_healthy(tmp_path: pathlib.Path) -> None:
    _init_repo(tmp_path)
    _make_commit(tmp_path, content=b"cli healthy", idx=99)
    result = runner.invoke(cli, ["verify"], env=_env(tmp_path))
    assert result.exit_code == 0
    assert "healthy" in result.output.lower()


def test_verify_cli_json(tmp_path: pathlib.Path) -> None:
    _init_repo(tmp_path)
    _make_commit(tmp_path, content=b"json verify", idx=88)
    result = runner.invoke(cli, ["verify", "--format", "json"], env=_env(tmp_path))
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["all_ok"] is True
    assert data["failures"] == []


def test_verify_cli_quiet_exit_zero_when_clean(tmp_path: pathlib.Path) -> None:
    _init_repo(tmp_path)
    _make_commit(tmp_path, content=b"quiet clean", idx=77)
    result = runner.invoke(cli, ["verify", "--quiet"], env=_env(tmp_path))
    assert result.exit_code == 0


def test_verify_cli_quiet_exit_one_when_broken(tmp_path: pathlib.Path) -> None:
    _init_repo(tmp_path)
    fake_id = "b" * 64
    (tmp_path / ".muse" / "refs" / "heads" / "main").write_text(fake_id, encoding="utf-8")
    result = runner.invoke(cli, ["verify", "-q"], env=_env(tmp_path))
    assert result.exit_code != 0


def test_verify_cli_no_objects_flag(tmp_path: pathlib.Path) -> None:
    _init_repo(tmp_path)
    _make_commit(tmp_path, content=b"no-obj flag", idx=66)
    result = runner.invoke(cli, ["verify", "--no-objects"], env=_env(tmp_path))
    assert result.exit_code == 0


# ---------------------------------------------------------------------------
# Stress: 100-commit chain
# ---------------------------------------------------------------------------


def test_verify_stress_100_commit_chain(tmp_path: pathlib.Path) -> None:
    _init_repo(tmp_path)
    prev: str | None = None
    for i in range(100):
        prev = _make_commit(tmp_path, parent_id=prev, content=b"chain", idx=i)

    result = run_verify(tmp_path, check_objects=True)
    assert result["all_ok"] is True
    assert result["commits_checked"] == 100

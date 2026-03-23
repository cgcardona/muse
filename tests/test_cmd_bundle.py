"""Tests for ``muse bundle`` subcommands.

Covers: create (default/have prune), unbundle (ref update), verify (clean/corrupt),
list-heads, round-trip, stress: 50-commit bundle.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import pathlib

import msgpack
import pytest
from tests.cli_test_helper import CliRunner

cli = None  # argparse migration — CliRunner ignores this arg
from muse.core.object_store import write_object
from muse.core.snapshot import compute_snapshot_id
from muse.core.store import CommitRecord, SnapshotRecord, write_commit, write_snapshot

runner = CliRunner()

_REPO_ID = "bundle-test"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _init_repo(path: pathlib.Path, repo_id: str = _REPO_ID) -> pathlib.Path:
    muse = path / ".muse"
    for d in ("commits", "snapshots", "objects", "refs/heads"):
        (muse / d).mkdir(parents=True, exist_ok=True)
    (muse / "HEAD").write_text("ref: refs/heads/main", encoding="utf-8")
    (muse / "repo.json").write_text(
        json.dumps({"repo_id": repo_id, "domain": "midi"}), encoding="utf-8"
    )
    return path


def _env(repo: pathlib.Path) -> dict[str, str]:
    return {"MUSE_REPO_ROOT": str(repo)}


_counter = 0


def _make_commit(
    root: pathlib.Path,
    parent_id: str | None = None,
    content: bytes = b"data",
    branch: str = "main",
) -> str:
    global _counter
    _counter += 1
    c = content + str(_counter).encode()
    obj_id = _sha(c)
    write_object(root, obj_id, c)
    manifest = {f"f_{_counter}.txt": obj_id}
    snap_id = compute_snapshot_id(manifest)
    write_snapshot(root, SnapshotRecord(snapshot_id=snap_id, manifest=manifest))
    committed_at = datetime.datetime.now(datetime.timezone.utc)
    commit_id = _sha(f"{_counter}:{snap_id}:{committed_at.isoformat()}".encode())
    write_commit(root, CommitRecord(
        commit_id=commit_id,
        repo_id=_REPO_ID,
        branch=branch,
        snapshot_id=snap_id,
        message=f"commit {_counter}",
        committed_at=committed_at,
        parent_commit_id=parent_id,
    ))
    (root / ".muse" / "refs" / "heads" / branch).write_text(commit_id, encoding="utf-8")
    return commit_id


# ---------------------------------------------------------------------------
# Unit: help
# ---------------------------------------------------------------------------


def test_bundle_help() -> None:
    result = runner.invoke(cli, ["bundle", "--help"])
    assert result.exit_code == 0


def test_bundle_create_help() -> None:
    result = runner.invoke(cli, ["bundle", "create", "--help"])
    assert result.exit_code == 0


# ---------------------------------------------------------------------------
# Unit: create
# ---------------------------------------------------------------------------


def test_bundle_create_basic(tmp_path: pathlib.Path) -> None:
    _init_repo(tmp_path)
    _make_commit(tmp_path, content=b"first")
    out = tmp_path / "out.bundle"
    result = runner.invoke(cli, ["bundle", "create", str(out)], env=_env(tmp_path))
    assert result.exit_code == 0
    assert out.exists()
    data = msgpack.unpackb(out.read_bytes(), raw=False)
    assert "commits" in data
    assert len(data["commits"]) >= 1


def test_bundle_create_no_commits(tmp_path: pathlib.Path) -> None:
    _init_repo(tmp_path)
    out = tmp_path / "empty.bundle"
    result = runner.invoke(cli, ["bundle", "create", str(out)], env=_env(tmp_path))
    assert result.exit_code != 0  # no commits to bundle


# ---------------------------------------------------------------------------
# Unit: verify clean
# ---------------------------------------------------------------------------


def test_bundle_verify_clean(tmp_path: pathlib.Path) -> None:
    _init_repo(tmp_path)
    _make_commit(tmp_path, content=b"verify me")
    out = tmp_path / "clean.bundle"
    runner.invoke(cli, ["bundle", "create", str(out)], env=_env(tmp_path))
    result = runner.invoke(cli, ["bundle", "verify", str(out)], env=_env(tmp_path))
    assert result.exit_code == 0
    assert "clean" in result.output.lower()


def test_bundle_verify_corrupt(tmp_path: pathlib.Path) -> None:
    _init_repo(tmp_path)
    _make_commit(tmp_path, content=b"to corrupt")
    out = tmp_path / "corrupt.bundle"
    runner.invoke(cli, ["bundle", "create", str(out)], env=_env(tmp_path))

    # Tamper with an object's content bytes.
    raw = msgpack.unpackb(out.read_bytes(), raw=False)
    if raw.get("objects"):
        raw["objects"][0]["content"] = b"tampered!"
    out.write_bytes(msgpack.packb(raw, use_bin_type=True))

    result = runner.invoke(cli, ["bundle", "verify", str(out)], env=_env(tmp_path))
    assert result.exit_code != 0
    assert "mismatch" in result.output.lower() or "failure" in result.output.lower()


def test_bundle_verify_json(tmp_path: pathlib.Path) -> None:
    _init_repo(tmp_path)
    _make_commit(tmp_path, content=b"json verify")
    out = tmp_path / "jv.bundle"
    runner.invoke(cli, ["bundle", "create", str(out)], env=_env(tmp_path))
    result = runner.invoke(cli, ["bundle", "verify", str(out), "--format", "json"], env=_env(tmp_path))
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["all_ok"] is True


def test_bundle_verify_quiet_clean(tmp_path: pathlib.Path) -> None:
    _init_repo(tmp_path)
    _make_commit(tmp_path, content=b"quiet clean")
    out = tmp_path / "q.bundle"
    runner.invoke(cli, ["bundle", "create", str(out)], env=_env(tmp_path))
    result = runner.invoke(cli, ["bundle", "verify", str(out), "-q"], env=_env(tmp_path))
    assert result.exit_code == 0


# ---------------------------------------------------------------------------
# Unit: unbundle
# ---------------------------------------------------------------------------


def test_bundle_unbundle_writes_objects(tmp_path: pathlib.Path) -> None:
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    dst.mkdir()
    _init_repo(src)
    _init_repo(dst, repo_id="dst-repo")
    _make_commit(src, content=b"unbundle me")

    out = tmp_path / "unbundle_test.bundle"
    runner.invoke(cli, ["bundle", "create", str(out)], env=_env(src))

    result = runner.invoke(cli, ["bundle", "unbundle", str(out)], env=_env(dst))
    assert result.exit_code == 0
    assert "unpacked" in result.output.lower()


# ---------------------------------------------------------------------------
# Unit: list-heads
# ---------------------------------------------------------------------------


def test_bundle_list_heads_text(tmp_path: pathlib.Path) -> None:
    _init_repo(tmp_path)
    _make_commit(tmp_path, content=b"heads test")
    out = tmp_path / "heads.bundle"
    runner.invoke(cli, ["bundle", "create", str(out)], env=_env(tmp_path))
    result = runner.invoke(cli, ["bundle", "list-heads", str(out)], env=_env(tmp_path))
    assert result.exit_code == 0


def test_bundle_list_heads_json(tmp_path: pathlib.Path) -> None:
    _init_repo(tmp_path)
    _make_commit(tmp_path, content=b"json heads")
    out = tmp_path / "jheads.bundle"
    runner.invoke(cli, ["bundle", "create", str(out)], env=_env(tmp_path))
    result = runner.invoke(cli, ["bundle", "list-heads", str(out), "--format", "json"], env=_env(tmp_path))
    assert result.exit_code == 0
    json.loads(result.output)  # valid JSON


# ---------------------------------------------------------------------------
# Integration: full round-trip
# ---------------------------------------------------------------------------


def test_bundle_round_trip(tmp_path: pathlib.Path) -> None:
    """Create a bundle from a source repo, unbundle into a clean target."""
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    dst.mkdir()
    _init_repo(src)
    _init_repo(dst, repo_id="dst-rt")

    prev: str | None = None
    for i in range(5):
        prev = _make_commit(src, parent_id=prev, content=f"rt-{i}".encode())

    out = tmp_path / "rt.bundle"
    create_result = runner.invoke(cli, ["bundle", "create", str(out)], env=_env(src))
    assert create_result.exit_code == 0

    unbundle_result = runner.invoke(cli, ["bundle", "unbundle", str(out)], env=_env(dst))
    assert unbundle_result.exit_code == 0


# ---------------------------------------------------------------------------
# Stress: 50-commit bundle
# ---------------------------------------------------------------------------


def test_bundle_stress_50_commits(tmp_path: pathlib.Path) -> None:
    _init_repo(tmp_path)
    prev: str | None = None
    for i in range(50):
        prev = _make_commit(tmp_path, parent_id=prev, content=f"stress-{i}".encode())

    out = tmp_path / "stress.bundle"
    result = runner.invoke(cli, ["bundle", "create", str(out)], env=_env(tmp_path))
    assert result.exit_code == 0

    raw = msgpack.unpackb(out.read_bytes(), raw=False)
    assert len(raw.get("commits", [])) == 50

    verify_result = runner.invoke(cli, ["bundle", "verify", str(out), "-q"], env=_env(tmp_path))
    assert verify_result.exit_code == 0

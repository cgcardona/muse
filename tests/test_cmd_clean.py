"""Tests for ``muse clean``.

Covers: --dry-run preview, --force delete, --directories, no-force error,
already-clean repo, multiple untracked files, stress: 500 untracked files.
"""

from __future__ import annotations

import json
import pathlib

import pytest
from typer.testing import CliRunner

from muse.cli.app import cli
from muse.core.object_store import write_object
from muse.core.snapshot import compute_snapshot_id
from muse.core.store import CommitRecord, SnapshotRecord, write_commit, write_snapshot

import datetime
import hashlib

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
        json.dumps({"repo_id": "clean-test", "domain": "midi"}), encoding="utf-8"
    )
    return path


def _env(repo: pathlib.Path) -> dict[str, str]:
    return {"MUSE_REPO_ROOT": str(repo)}


def _commit_file(root: pathlib.Path, rel_path: str, content: bytes) -> str:
    """Write a file, store its object, and commit it. Returns commit_id."""
    obj_id = _sha(content)
    write_object(root, obj_id, content)
    (root / rel_path).write_bytes(content)
    manifest = {rel_path: obj_id}
    snap_id = compute_snapshot_id(manifest)
    snap = SnapshotRecord(snapshot_id=snap_id, manifest=manifest)
    write_snapshot(root, snap)
    committed_at = datetime.datetime.now(datetime.timezone.utc)
    commit_id = _sha(f"commit:{snap_id}:{committed_at.isoformat()}".encode())
    write_commit(root, CommitRecord(
        commit_id=commit_id,
        repo_id="clean-test",
        branch="main",
        snapshot_id=snap_id,
        message="initial",
        committed_at=committed_at,
    ))
    (root / ".muse" / "refs" / "heads" / "main").write_text(commit_id, encoding="utf-8")
    return commit_id


# ---------------------------------------------------------------------------
# Unit: safety guard — no flags
# ---------------------------------------------------------------------------


def test_clean_no_force_exits_with_error(tmp_path: pathlib.Path) -> None:
    _init_repo(tmp_path)
    (tmp_path / "untracked.txt").write_text("hello", encoding="utf-8")
    result = runner.invoke(cli, ["clean"], env=_env(tmp_path))
    assert result.exit_code != 0


def test_clean_help() -> None:
    result = runner.invoke(cli, ["clean", "--help"])
    assert result.exit_code == 0
    # Rich injects ANSI codes between '--' dashes; the short flag '-f' is reliable.
    assert "--force" in result.output or "-f" in result.output


# ---------------------------------------------------------------------------
# Unit: dry-run shows but does not delete
# ---------------------------------------------------------------------------


def test_clean_dry_run_shows_untracked(tmp_path: pathlib.Path) -> None:
    _init_repo(tmp_path)
    _commit_file(tmp_path, "tracked.txt", b"I am tracked")
    untracked = tmp_path / "ghost.txt"
    untracked.write_text("untracked", encoding="utf-8")

    result = runner.invoke(cli, ["clean", "-n"], env=_env(tmp_path))
    assert result.exit_code == 0
    assert "ghost.txt" in result.output
    assert untracked.exists()  # not deleted


def test_clean_dry_run_short_flag(tmp_path: pathlib.Path) -> None:
    _init_repo(tmp_path)
    (tmp_path / "junk.txt").write_text("junk", encoding="utf-8")
    result = runner.invoke(cli, ["clean", "-n"], env=_env(tmp_path))
    assert result.exit_code == 0


# ---------------------------------------------------------------------------
# Unit: --force deletes untracked files
# ---------------------------------------------------------------------------


def test_clean_force_deletes_untracked(tmp_path: pathlib.Path) -> None:
    _init_repo(tmp_path)
    _commit_file(tmp_path, "kept.txt", b"keep me")
    untracked = tmp_path / "delete_me.txt"
    untracked.write_text("bye", encoding="utf-8")

    result = runner.invoke(cli, ["clean", "-f"], env=_env(tmp_path))
    assert result.exit_code == 0
    assert not untracked.exists()
    assert (tmp_path / "kept.txt").exists()


def test_clean_force_nothing_to_clean(tmp_path: pathlib.Path) -> None:
    _init_repo(tmp_path)
    _commit_file(tmp_path, "tracked.txt", b"tracked")

    result = runner.invoke(cli, ["clean", "-f"], env=_env(tmp_path))
    assert result.exit_code == 0
    assert "nothing" in result.output.lower()


# ---------------------------------------------------------------------------
# Unit: --directories removes empty dirs
# ---------------------------------------------------------------------------


def test_clean_directories_removes_empty_dir(tmp_path: pathlib.Path) -> None:
    _init_repo(tmp_path)
    _commit_file(tmp_path, "kept.txt", b"kept")
    empty_dir = tmp_path / "empty_dir"
    empty_dir.mkdir()
    (empty_dir / "junk.txt").write_text("junk", encoding="utf-8")

    result = runner.invoke(cli, ["clean", "-f", "-d"], env=_env(tmp_path))
    assert result.exit_code == 0
    assert not (empty_dir / "junk.txt").exists()


# ---------------------------------------------------------------------------
# Integration: multiple untracked files
# ---------------------------------------------------------------------------


def test_clean_multiple_untracked(tmp_path: pathlib.Path) -> None:
    _init_repo(tmp_path)
    for i in range(10):
        (tmp_path / f"untracked_{i}.txt").write_text(f"data {i}", encoding="utf-8")

    result = runner.invoke(cli, ["clean", "-f"], env=_env(tmp_path))
    assert result.exit_code == 0
    remaining = [f for f in tmp_path.iterdir() if f.name.startswith("untracked")]
    assert len(remaining) == 0


# ---------------------------------------------------------------------------
# Stress: 500 untracked files
# ---------------------------------------------------------------------------


def test_clean_stress_500_untracked(tmp_path: pathlib.Path) -> None:
    _init_repo(tmp_path)
    for i in range(500):
        (tmp_path / f"stress_{i}.dat").write_bytes(b"x" * 100)

    result = runner.invoke(cli, ["clean", "-f"], env=_env(tmp_path))
    assert result.exit_code == 0
    remaining = list(tmp_path.glob("stress_*.dat"))
    assert len(remaining) == 0

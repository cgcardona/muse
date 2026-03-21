"""Tests for ``muse snapshot`` subcommands.

Covers: create (text/json), list (empty/populated/limit), show (text/json/prefix),
export (tar.gz/zip), short flags, stress: 50 files.
"""

from __future__ import annotations

import json
import pathlib
import tarfile
import zipfile

import pytest
from typer.testing import CliRunner

from muse.cli.app import cli

runner = CliRunner()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _init_repo(path: pathlib.Path) -> pathlib.Path:
    muse = path / ".muse"
    for d in ("commits", "snapshots", "objects", "refs/heads"):
        (muse / d).mkdir(parents=True, exist_ok=True)
    (muse / "HEAD").write_text("ref: refs/heads/main", encoding="utf-8")
    (muse / "repo.json").write_text(
        json.dumps({"repo_id": "snap-test", "domain": "midi"}), encoding="utf-8"
    )
    return path


def _env(repo: pathlib.Path) -> dict[str, str]:
    return {"MUSE_REPO_ROOT": str(repo)}


def _create_files(root: pathlib.Path, count: int = 3) -> list[str]:
    names: list[str] = []
    for i in range(count):
        name = f"file_{i}.txt"
        (root / name).write_text(f"content {i}", encoding="utf-8")
        names.append(name)
    return names


# ---------------------------------------------------------------------------
# Unit: snapshot create
# ---------------------------------------------------------------------------


def test_snapshot_create_help() -> None:
    result = runner.invoke(cli, ["snapshot", "--help"])
    assert result.exit_code == 0


def test_snapshot_create_text(tmp_path: pathlib.Path) -> None:
    _init_repo(tmp_path)
    _create_files(tmp_path, 3)
    result = runner.invoke(cli, ["snapshot", "create"], env=_env(tmp_path))
    assert result.exit_code == 0
    assert "file(s)" in result.output


def test_snapshot_create_json(tmp_path: pathlib.Path) -> None:
    _init_repo(tmp_path)
    _create_files(tmp_path, 2)
    result = runner.invoke(cli, ["snapshot", "create", "-f", "json"], env=_env(tmp_path))
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert "snapshot_id" in data
    assert data["file_count"] >= 1


def test_snapshot_create_with_note(tmp_path: pathlib.Path) -> None:
    _init_repo(tmp_path)
    _create_files(tmp_path, 1)
    result = runner.invoke(cli, ["snapshot", "create", "-m", "WIP note"], env=_env(tmp_path))
    assert result.exit_code == 0
    assert "WIP note" in result.output


def test_snapshot_create_short_flags(tmp_path: pathlib.Path) -> None:
    _init_repo(tmp_path)
    _create_files(tmp_path, 1)
    result = runner.invoke(cli, ["snapshot", "create", "-m", "test", "-f", "json"], env=_env(tmp_path))
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert "snapshot_id" in data


# ---------------------------------------------------------------------------
# Unit: snapshot list
# ---------------------------------------------------------------------------


def test_snapshot_list_empty(tmp_path: pathlib.Path) -> None:
    _init_repo(tmp_path)
    result = runner.invoke(cli, ["snapshot", "list"], env=_env(tmp_path))
    assert result.exit_code == 0
    assert "no snapshots" in result.output.lower()


def test_snapshot_list_after_create(tmp_path: pathlib.Path) -> None:
    _init_repo(tmp_path)
    _create_files(tmp_path, 2)
    runner.invoke(cli, ["snapshot", "create"], env=_env(tmp_path))
    result = runner.invoke(cli, ["snapshot", "list"], env=_env(tmp_path))
    assert result.exit_code == 0
    lines = [l for l in result.output.strip().split("\n") if l.strip()]
    assert len(lines) >= 1


def test_snapshot_list_json(tmp_path: pathlib.Path) -> None:
    _init_repo(tmp_path)
    _create_files(tmp_path, 2)
    runner.invoke(cli, ["snapshot", "create"], env=_env(tmp_path))
    result = runner.invoke(cli, ["snapshot", "list", "-f", "json"], env=_env(tmp_path))
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert isinstance(data, list)
    assert len(data) >= 1
    assert "snapshot_id" in data[0]


def test_snapshot_list_limit(tmp_path: pathlib.Path) -> None:
    _init_repo(tmp_path)
    _create_files(tmp_path, 1)
    for _ in range(5):
        runner.invoke(cli, ["snapshot", "create"], env=_env(tmp_path))
    result = runner.invoke(cli, ["snapshot", "list", "-n", "3", "-f", "json"], env=_env(tmp_path))
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert len(data) <= 3


# ---------------------------------------------------------------------------
# Unit: snapshot show
# ---------------------------------------------------------------------------


def test_snapshot_show_json(tmp_path: pathlib.Path) -> None:
    _init_repo(tmp_path)
    _create_files(tmp_path, 2)
    create_result = runner.invoke(cli, ["snapshot", "create", "-f", "json"], env=_env(tmp_path))
    snap_id = json.loads(create_result.output)["snapshot_id"]
    result = runner.invoke(cli, ["snapshot", "show", snap_id], env=_env(tmp_path))
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["snapshot_id"] == snap_id
    assert "manifest" in data


def test_snapshot_show_prefix(tmp_path: pathlib.Path) -> None:
    _init_repo(tmp_path)
    _create_files(tmp_path, 1)
    create_result = runner.invoke(cli, ["snapshot", "create", "-f", "json"], env=_env(tmp_path))
    snap_id = json.loads(create_result.output)["snapshot_id"]
    # Use first 12 chars as prefix.
    result = runner.invoke(cli, ["snapshot", "show", snap_id[:12]], env=_env(tmp_path))
    assert result.exit_code == 0


def test_snapshot_show_not_found(tmp_path: pathlib.Path) -> None:
    _init_repo(tmp_path)
    result = runner.invoke(cli, ["snapshot", "show", "nonexistent"], env=_env(tmp_path))
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Unit: snapshot export
# ---------------------------------------------------------------------------


def test_snapshot_export_tar_gz(tmp_path: pathlib.Path) -> None:
    _init_repo(tmp_path)
    _create_files(tmp_path, 2)
    create_result = runner.invoke(cli, ["snapshot", "create", "-f", "json"], env=_env(tmp_path))
    snap_id = json.loads(create_result.output)["snapshot_id"]
    out_file = tmp_path / "snap.tar.gz"
    result = runner.invoke(
        cli,
        ["snapshot", "export", snap_id, "--output", str(out_file)],
        env=_env(tmp_path),
    )
    assert result.exit_code == 0
    assert out_file.exists()
    assert tarfile.is_tarfile(str(out_file))


def test_snapshot_export_zip(tmp_path: pathlib.Path) -> None:
    _init_repo(tmp_path)
    _create_files(tmp_path, 2)
    create_result = runner.invoke(cli, ["snapshot", "create", "-f", "json"], env=_env(tmp_path))
    snap_id = json.loads(create_result.output)["snapshot_id"]
    out_file = tmp_path / "snap.zip"
    result = runner.invoke(
        cli,
        ["snapshot", "export", snap_id, "--format", "zip", "--output", str(out_file)],
        env=_env(tmp_path),
    )
    assert result.exit_code == 0
    assert out_file.exists()
    assert zipfile.is_zipfile(str(out_file))


# ---------------------------------------------------------------------------
# Stress: 50 files
# ---------------------------------------------------------------------------


def test_snapshot_create_stress_50_files(tmp_path: pathlib.Path) -> None:
    _init_repo(tmp_path)
    for i in range(50):
        (tmp_path / f"stress_{i}.txt").write_bytes(b"x" * 1024)
    result = runner.invoke(cli, ["snapshot", "create", "-f", "json"], env=_env(tmp_path))
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["file_count"] >= 50

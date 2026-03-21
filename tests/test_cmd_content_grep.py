"""Tests for ``muse content-grep``.

Covers: no match exit-1, pattern found, --files-only, --count, --ignore-case,
--format json, binary skip, multi-file, stress: 100 files.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import pathlib

import pytest
from typer.testing import CliRunner

from muse.cli.app import cli
from muse.core.object_store import write_object
from muse.core.snapshot import compute_snapshot_id
from muse.core.store import CommitRecord, SnapshotRecord, write_commit, write_snapshot

runner = CliRunner()

_REPO_ID = "cgrep-test"


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


_counter = 0


def _commit_files(root: pathlib.Path, files: dict[str, bytes]) -> str:
    global _counter
    _counter += 1
    manifest: dict[str, str] = {}
    for rel_path, content in files.items():
        obj_id = _sha(content)
        write_object(root, obj_id, content)
        manifest[rel_path] = obj_id
    snap_id = compute_snapshot_id(manifest)
    write_snapshot(root, SnapshotRecord(snapshot_id=snap_id, manifest=manifest))
    committed_at = datetime.datetime.now(datetime.timezone.utc)
    commit_id = _sha(f"{_counter}:{snap_id}:{committed_at.isoformat()}".encode())
    write_commit(root, CommitRecord(
        commit_id=commit_id,
        repo_id=_REPO_ID,
        branch="main",
        snapshot_id=snap_id,
        message=f"commit {_counter}",
        committed_at=committed_at,
    ))
    (root / ".muse" / "refs" / "heads" / "main").write_text(commit_id, encoding="utf-8")
    return commit_id


# ---------------------------------------------------------------------------
# Unit: help
# ---------------------------------------------------------------------------


def test_content_grep_help() -> None:
    result = runner.invoke(cli, ["content-grep", "--help"])
    assert result.exit_code == 0
    assert "--pattern" in result.output or "-p" in result.output


# ---------------------------------------------------------------------------
# Unit: no match → exit 1
# ---------------------------------------------------------------------------


def test_content_grep_no_match(tmp_path: pathlib.Path) -> None:
    _init_repo(tmp_path)
    _commit_files(tmp_path, {"song.txt": b"chord: Am\ntempo: 120\n"})
    result = runner.invoke(cli, ["content-grep", "--pattern", "ZZZNOMATCH"], env=_env(tmp_path))
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Unit: match found → exit 0
# ---------------------------------------------------------------------------


def test_content_grep_match_found(tmp_path: pathlib.Path) -> None:
    _init_repo(tmp_path)
    _commit_files(tmp_path, {"song.txt": b"chord: Cm7\ntempo: 120\n"})
    result = runner.invoke(cli, ["content-grep", "--pattern", "Cm7"], env=_env(tmp_path))
    assert result.exit_code == 0
    assert "song.txt" in result.output


# ---------------------------------------------------------------------------
# Unit: --ignore-case
# ---------------------------------------------------------------------------


def test_content_grep_ignore_case(tmp_path: pathlib.Path) -> None:
    _init_repo(tmp_path)
    _commit_files(tmp_path, {"notes.txt": b"VERSE: intro melody\n"})
    result = runner.invoke(
        cli, ["content-grep", "--pattern", "verse", "--ignore-case"], env=_env(tmp_path)
    )
    assert result.exit_code == 0
    assert "notes.txt" in result.output


def test_content_grep_case_sensitive_no_match(tmp_path: pathlib.Path) -> None:
    _init_repo(tmp_path)
    _commit_files(tmp_path, {"notes.txt": b"VERSE: intro melody\n"})
    result = runner.invoke(
        cli, ["content-grep", "--pattern", "verse"], env=_env(tmp_path)
    )
    # Case-sensitive: "verse" ≠ "VERSE" → no match.
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Unit: --files-only
# ---------------------------------------------------------------------------


def test_content_grep_files_only(tmp_path: pathlib.Path) -> None:
    _init_repo(tmp_path)
    _commit_files(tmp_path, {
        "a.txt": b"match here\n",
        "b.txt": b"match here too\n",
    })
    result = runner.invoke(
        cli, ["content-grep", "--pattern", "match", "--files-only"], env=_env(tmp_path)
    )
    assert result.exit_code == 0
    lines = [l.strip() for l in result.output.strip().split("\n") if l.strip()]
    for line in lines:
        assert ":" not in line or line.startswith("a.txt") or line.startswith("b.txt")


# ---------------------------------------------------------------------------
# Unit: --count
# ---------------------------------------------------------------------------


def test_content_grep_count(tmp_path: pathlib.Path) -> None:
    _init_repo(tmp_path)
    _commit_files(tmp_path, {"multi.txt": b"hit\nhit\nhit\nmiss\n"})
    result = runner.invoke(
        cli, ["content-grep", "--pattern", "hit", "--count"], env=_env(tmp_path)
    )
    assert result.exit_code == 0
    assert "3" in result.output


# ---------------------------------------------------------------------------
# Unit: --format json
# ---------------------------------------------------------------------------


def test_content_grep_json_output(tmp_path: pathlib.Path) -> None:
    _init_repo(tmp_path)
    _commit_files(tmp_path, {"song.midi.txt": b"note: C4\nnote: D4\n"})
    result = runner.invoke(
        cli, ["content-grep", "--pattern", "note", "--format", "json"], env=_env(tmp_path)
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert isinstance(data, list)
    assert len(data) >= 1
    assert data[0]["match_count"] >= 2


# ---------------------------------------------------------------------------
# Unit: binary file skipped silently
# ---------------------------------------------------------------------------


def test_content_grep_binary_skipped(tmp_path: pathlib.Path) -> None:
    _init_repo(tmp_path)
    binary_content = b"\x00\x01\x02\x03" * 100
    text_content = b"searchable text here\n"
    _commit_files(tmp_path, {
        "binary.bin": binary_content,
        "text.txt": text_content,
    })
    result = runner.invoke(
        cli, ["content-grep", "--pattern", "searchable"], env=_env(tmp_path)
    )
    assert result.exit_code == 0
    assert "text.txt" in result.output


# ---------------------------------------------------------------------------
# Unit: short flags work
# ---------------------------------------------------------------------------


def test_content_grep_short_flags(tmp_path: pathlib.Path) -> None:
    _init_repo(tmp_path)
    _commit_files(tmp_path, {"f.txt": b"hello world\n"})
    result = runner.invoke(
        cli, ["content-grep", "-p", "hello", "-i", "-f", "json"], env=_env(tmp_path)
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert len(data) >= 1


# ---------------------------------------------------------------------------
# Stress: 100 files, pattern matches 50
# ---------------------------------------------------------------------------


def test_content_grep_stress_100_files(tmp_path: pathlib.Path) -> None:
    _init_repo(tmp_path)
    files: dict[str, bytes] = {}
    for i in range(100):
        content = b"TARGET_LINE\n" if i % 2 == 0 else b"other content\n"
        files[f"file_{i:04d}.txt"] = content
    _commit_files(tmp_path, files)
    result = runner.invoke(
        cli, ["content-grep", "--pattern", "TARGET_LINE", "--format", "json"], env=_env(tmp_path)
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert len(data) == 50

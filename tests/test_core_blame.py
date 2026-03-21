"""Tests for muse/core/blame.py — line-level text attribution."""

from __future__ import annotations

import hashlib
import json
import pathlib

import pytest

from muse.core.blame import BlameLine, blame_file


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _write_object(repo: pathlib.Path, content: bytes) -> str:
    sha = _sha256(content)
    obj_dir = repo / ".muse" / "objects" / sha[:2]
    obj_dir.mkdir(parents=True, exist_ok=True)
    (obj_dir / sha[2:]).write_bytes(content)
    return sha


def _write_snapshot(repo: pathlib.Path, snap_id: str, manifest: dict[str, str]) -> None:
    snap_dir = repo / ".muse" / "snapshots"
    snap_dir.mkdir(parents=True, exist_ok=True)
    (snap_dir / f"{snap_id}.json").write_text(
        json.dumps({"snapshot_id": snap_id, "manifest": manifest})
    )


def _write_commit(
    repo: pathlib.Path,
    commit_id: str,
    snap_id: str,
    message: str = "test",
    parent: str | None = None,
    author: str = "Author",
) -> None:
    import datetime

    commit_dir = repo / ".muse" / "commits"
    commit_dir.mkdir(parents=True, exist_ok=True)
    rec = {
        "commit_id": commit_id,
        "repo_id": "test-repo",
        "branch": "main",
        "snapshot_id": snap_id,
        "message": message,
        "committed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "parent_commit_id": parent,
        "parent2_commit_id": None,
        "author": author,
        "metadata": {},
    }
    (commit_dir / f"{commit_id}.json").write_text(json.dumps(rec))


def _make_repo(tmp_path: pathlib.Path) -> pathlib.Path:
    muse = tmp_path / ".muse"
    for d in ("objects", "commits", "snapshots", "refs/heads"):
        (muse / d).mkdir(parents=True, exist_ok=True)
    (muse / "repo.json").write_text(json.dumps({"repo_id": "test-repo"}))
    (muse / "HEAD").write_text("ref: refs/heads/main\n")
    return tmp_path


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_blame_returns_none_for_missing_file(tmp_path: pathlib.Path) -> None:
    repo = _make_repo(tmp_path)
    snap_id = "s" * 64
    commit_id = "c" * 64
    _write_snapshot(repo, snap_id, {})  # empty manifest
    _write_commit(repo, commit_id, snap_id)

    result = blame_file(repo, "nonexistent.txt", commit_id)
    assert result is None


def test_blame_single_commit_all_lines_attributed(tmp_path: pathlib.Path) -> None:
    repo = _make_repo(tmp_path)
    content = b"line one\nline two\nline three\n"
    obj_id = _write_object(repo, content)
    snap_id = "s" * 64
    commit_id = "c" * 64
    _write_snapshot(repo, snap_id, {"readme.txt": obj_id})
    _write_commit(repo, commit_id, snap_id, message="initial commit", author="Alice")

    result = blame_file(repo, "readme.txt", commit_id)
    assert result is not None
    assert len(result) == 3
    for line in result:
        assert isinstance(line, BlameLine)
        assert line.commit_id == commit_id


def test_blame_line_numbers_are_1_indexed(tmp_path: pathlib.Path) -> None:
    repo = _make_repo(tmp_path)
    content = b"a\nb\nc\n"
    obj_id = _write_object(repo, content)
    snap_id = "s" * 64
    commit_id = "c" * 64
    _write_snapshot(repo, snap_id, {"f.txt": obj_id})
    _write_commit(repo, commit_id, snap_id)

    result = blame_file(repo, "f.txt", commit_id)
    assert result is not None
    assert [bl.lineno for bl in result] == [1, 2, 3]


def test_blame_content_matches_file(tmp_path: pathlib.Path) -> None:
    repo = _make_repo(tmp_path)
    content = b"hello\nworld\n"
    obj_id = _write_object(repo, content)
    snap_id = "s" * 64
    commit_id = "c" * 64
    _write_snapshot(repo, snap_id, {"f.txt": obj_id})
    _write_commit(repo, commit_id, snap_id)

    result = blame_file(repo, "f.txt", commit_id)
    assert result is not None
    assert result[0].content == "hello"
    assert result[1].content == "world"


def test_blame_empty_file_returns_empty_list(tmp_path: pathlib.Path) -> None:
    repo = _make_repo(tmp_path)
    content = b""
    obj_id = _write_object(repo, content)
    snap_id = "s" * 64
    commit_id = "c" * 64
    _write_snapshot(repo, snap_id, {"empty.txt": obj_id})
    _write_commit(repo, commit_id, snap_id)

    result = blame_file(repo, "empty.txt", commit_id)
    assert result == []


def test_blame_two_commits_attributes_older_lines_correctly(tmp_path: pathlib.Path) -> None:
    """Lines present in both commits should be attributed to the older commit."""
    repo = _make_repo(tmp_path)

    # Commit 1: file with two lines.
    content1 = b"original line 1\noriginal line 2\n"
    obj1 = _write_object(repo, content1)
    snap1 = "1" * 64
    commit1 = "a" * 64
    _write_snapshot(repo, snap1, {"f.txt": obj1})
    _write_commit(repo, commit1, snap1, message="initial", author="Alice")

    # Commit 2: same two lines + one new line.
    content2 = b"original line 1\noriginal line 2\nnew line 3\n"
    obj2 = _write_object(repo, content2)
    snap2 = "2" * 64
    commit2 = "b" * 64
    _write_snapshot(repo, snap2, {"f.txt": obj2})
    _write_commit(repo, commit2, snap2, message="add line 3", parent=commit1, author="Bob")

    result = blame_file(repo, "f.txt", commit2)
    assert result is not None
    assert len(result) == 3
    # Lines 1 and 2 should be attributed to commit1 (they existed before commit2).
    assert result[0].commit_id == commit1
    assert result[1].commit_id == commit1
    # Line 3 was added by commit2.
    assert result[2].commit_id == commit2


def test_blame_author_populated(tmp_path: pathlib.Path) -> None:
    repo = _make_repo(tmp_path)
    obj_id = _write_object(repo, b"line\n")
    snap_id = "s" * 64
    commit_id = "c" * 64
    _write_snapshot(repo, snap_id, {"f.txt": obj_id})
    _write_commit(repo, commit_id, snap_id, author="Carol")

    result = blame_file(repo, "f.txt", commit_id)
    assert result is not None
    assert result[0].author == "Carol"


def test_blame_message_is_first_line_of_commit_message(tmp_path: pathlib.Path) -> None:
    repo = _make_repo(tmp_path)
    obj_id = _write_object(repo, b"line\n")
    snap_id = "s" * 64
    commit_id = "c" * 64
    _write_snapshot(repo, snap_id, {"f.txt": obj_id})
    _write_commit(repo, commit_id, snap_id, message="feat: add feature\n\nLong body here.")

    result = blame_file(repo, "f.txt", commit_id)
    assert result is not None
    assert result[0].message == "feat: add feature"


# ---------------------------------------------------------------------------
# Stress
# ---------------------------------------------------------------------------


def test_blame_stress_100_line_file(tmp_path: pathlib.Path) -> None:
    """Blame should handle a 100-line file without errors."""
    repo = _make_repo(tmp_path)
    content = "\n".join(f"line {i}" for i in range(100)).encode() + b"\n"
    obj_id = _write_object(repo, content)
    snap_id = "s" * 64
    commit_id = "c" * 64
    _write_snapshot(repo, snap_id, {"big.txt": obj_id})
    _write_commit(repo, commit_id, snap_id)

    result = blame_file(repo, "big.txt", commit_id)
    assert result is not None
    assert len(result) == 100
    assert all(bl.commit_id == commit_id for bl in result)

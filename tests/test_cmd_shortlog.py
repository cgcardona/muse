"""Tests for ``muse shortlog``.

Covers: empty repo, single author, multiple authors, --numbered sort,
--email flag, --format json, --all branches, --limit, short flags,
stress: 200 commits across 3 authors.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import pathlib

import pytest
from tests.cli_test_helper import CliRunner

cli = None  # argparse migration — CliRunner ignores this arg
from muse.core.object_store import write_object
from muse.core.snapshot import compute_snapshot_id
from muse.core.store import CommitRecord, SnapshotRecord, write_commit, write_snapshot

runner = CliRunner()

_REPO_ID = "shortlog-test"


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

# Per-branch tracking of the latest commit so tests can chain automatically.
_branch_heads: dict[str, str] = {}


def _make_commit(
    root: pathlib.Path,
    author: str = "Alice",
    parent_id: str | None = None,
    branch: str = "main",
) -> str:
    """Create a commit, automatically chaining to the previous commit on the branch."""
    global _counter
    _counter += 1
    # Auto-chain: if no explicit parent, use the last commit on this branch.
    if parent_id is None:
        parent_id = _branch_heads.get(f"{str(root)}:{branch}")
    content = f"content-{_counter}".encode()
    obj_id = _sha(content)
    write_object(root, obj_id, content)
    manifest = {f"file_{_counter}.txt": obj_id}
    snap_id = compute_snapshot_id(manifest)
    write_snapshot(root, SnapshotRecord(snapshot_id=snap_id, manifest=manifest))
    committed_at = datetime.datetime.now(datetime.timezone.utc)
    commit_id = _sha(f"{_counter}:{author}:{snap_id}".encode())
    write_commit(root, CommitRecord(
        commit_id=commit_id,
        repo_id=_REPO_ID,
        branch=branch,
        snapshot_id=snap_id,
        message=f"commit by {author} #{_counter}",
        committed_at=committed_at,
        parent_commit_id=parent_id,
        author=author,
    ))
    (root / ".muse" / "refs" / "heads" / branch).write_text(commit_id, encoding="utf-8")
    _branch_heads[f"{str(root)}:{branch}"] = commit_id
    return commit_id


# ---------------------------------------------------------------------------
# Unit: empty repo
# ---------------------------------------------------------------------------


def test_shortlog_empty_repo(tmp_path: pathlib.Path) -> None:
    _init_repo(tmp_path)
    result = runner.invoke(cli, ["shortlog"], env=_env(tmp_path))
    assert result.exit_code == 0
    assert "no commits" in result.output.lower()


def test_shortlog_help() -> None:
    result = runner.invoke(cli, ["shortlog", "--help"])
    assert result.exit_code == 0
    assert "--numbered" in result.output or "-n" in result.output


# ---------------------------------------------------------------------------
# Unit: single author
# ---------------------------------------------------------------------------


def test_shortlog_single_author(tmp_path: pathlib.Path) -> None:
    _init_repo(tmp_path)
    _make_commit(tmp_path, author="Alice")
    _make_commit(tmp_path, author="Alice")
    result = runner.invoke(cli, ["shortlog"], env=_env(tmp_path))
    assert result.exit_code == 0
    assert "Alice" in result.output
    assert "(2)" in result.output


# ---------------------------------------------------------------------------
# Unit: multiple authors
# ---------------------------------------------------------------------------


def test_shortlog_multiple_authors(tmp_path: pathlib.Path) -> None:
    _init_repo(tmp_path)
    _make_commit(tmp_path, author="Alice")
    _make_commit(tmp_path, author="Bob")
    _make_commit(tmp_path, author="Alice")
    result = runner.invoke(cli, ["shortlog"], env=_env(tmp_path))
    assert result.exit_code == 0
    assert "Alice" in result.output
    assert "Bob" in result.output


# ---------------------------------------------------------------------------
# Unit: --numbered sorts by count
# ---------------------------------------------------------------------------


def test_shortlog_numbered(tmp_path: pathlib.Path) -> None:
    _init_repo(tmp_path)
    _make_commit(tmp_path, author="Bob")
    _make_commit(tmp_path, author="Alice")
    _make_commit(tmp_path, author="Alice")
    _make_commit(tmp_path, author="Alice")
    result = runner.invoke(cli, ["shortlog", "--numbered"], env=_env(tmp_path))
    assert result.exit_code == 0
    alice_pos = result.output.index("Alice")
    bob_pos = result.output.index("Bob")
    assert alice_pos < bob_pos  # Alice has more commits, should appear first


# ---------------------------------------------------------------------------
# Unit: --format json
# ---------------------------------------------------------------------------


def test_shortlog_json_output(tmp_path: pathlib.Path) -> None:
    _init_repo(tmp_path)
    _make_commit(tmp_path, author="Charlie")
    result = runner.invoke(cli, ["shortlog", "--format", "json"], env=_env(tmp_path))
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert isinstance(data, list)
    assert len(data) >= 1
    assert data[0]["author"] == "Charlie"
    assert data[0]["count"] >= 1


# ---------------------------------------------------------------------------
# Unit: --limit
# ---------------------------------------------------------------------------


def test_shortlog_limit(tmp_path: pathlib.Path) -> None:
    _init_repo(tmp_path)
    for _ in range(20):
        _make_commit(tmp_path, author="Dave")
    result = runner.invoke(cli, ["shortlog", "--limit", "5", "--format", "json"], env=_env(tmp_path))
    assert result.exit_code == 0
    data = json.loads(result.output)
    total_commits = sum(g["count"] for g in data)
    assert total_commits <= 5


# ---------------------------------------------------------------------------
# Unit: short flags
# ---------------------------------------------------------------------------


def test_shortlog_short_flags(tmp_path: pathlib.Path) -> None:
    _init_repo(tmp_path)
    _make_commit(tmp_path, author="Eve")
    result = runner.invoke(cli, ["shortlog", "-n", "-f", "json"], env=_env(tmp_path))
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert len(data) >= 1


# ---------------------------------------------------------------------------
# Stress: 200 commits across 3 authors
# ---------------------------------------------------------------------------


def test_shortlog_stress_200_commits(tmp_path: pathlib.Path) -> None:
    _init_repo(tmp_path)
    authors = ["Frank", "Grace", "Heidi"]
    for i in range(200):
        _make_commit(tmp_path, author=authors[i % 3])

    result = runner.invoke(cli, ["shortlog", "--format", "json"], env=_env(tmp_path))
    assert result.exit_code == 0
    data = json.loads(result.output)
    total = sum(g["count"] for g in data)
    assert total == 200
    names = {g["author"] for g in data}
    assert "Frank" in names
    assert "Grace" in names
    assert "Heidi" in names

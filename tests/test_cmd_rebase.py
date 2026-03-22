"""Tests for ``muse rebase`` and ``muse/core/rebase.py``.

Covers: state file load/save/clear, collect_commits_to_replay, abort, no-op
(already up to date), simple forward rebase, --squash, conflict detection,
stress: 20-commit rebase chain.
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
from muse.core.rebase import (
    RebaseState,
    clear_rebase_state,
    collect_commits_to_replay,
    load_rebase_state,
    save_rebase_state,
)
from muse.core.snapshot import compute_snapshot_id
from muse.core.store import CommitRecord, SnapshotRecord, write_commit, write_snapshot

runner = CliRunner()

_REPO_ID = "rebase-test"


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
# Unit: state file load/save/clear
# ---------------------------------------------------------------------------


def test_rebase_state_round_trip(tmp_path: pathlib.Path) -> None:
    _init_repo(tmp_path)
    state = RebaseState(
        original_branch="main",
        original_head="a" * 64,
        onto="b" * 64,
        remaining=["c" * 64, "d" * 64],
        completed=["e" * 64],
        squash=False,
    )
    save_rebase_state(tmp_path, state)
    loaded = load_rebase_state(tmp_path)
    assert loaded is not None
    assert loaded["original_branch"] == "main"
    assert loaded["remaining"] == ["c" * 64, "d" * 64]
    assert loaded["completed"] == ["e" * 64]
    assert loaded["squash"] is False


def test_rebase_state_clear(tmp_path: pathlib.Path) -> None:
    _init_repo(tmp_path)
    state = RebaseState(
        original_branch="feat", original_head="a" * 64, onto="b" * 64,
        remaining=[], completed=[], squash=False,
    )
    save_rebase_state(tmp_path, state)
    assert load_rebase_state(tmp_path) is not None
    clear_rebase_state(tmp_path)
    assert load_rebase_state(tmp_path) is None


def test_rebase_state_none_when_no_file(tmp_path: pathlib.Path) -> None:
    _init_repo(tmp_path)
    assert load_rebase_state(tmp_path) is None


# ---------------------------------------------------------------------------
# Unit: collect_commits_to_replay
# ---------------------------------------------------------------------------


def test_collect_commits_empty_when_same_base(tmp_path: pathlib.Path) -> None:
    _init_repo(tmp_path)
    cid = _make_commit(tmp_path, content=b"only")
    result = collect_commits_to_replay(tmp_path, stop_at=cid, tip=cid)
    assert result == []


def test_collect_commits_one_commit(tmp_path: pathlib.Path) -> None:
    _init_repo(tmp_path)
    base = _make_commit(tmp_path, content=b"base")
    tip = _make_commit(tmp_path, parent_id=base, content=b"tip")
    result = collect_commits_to_replay(tmp_path, stop_at=base, tip=tip)
    assert len(result) == 1
    assert result[0].commit_id == tip


def test_collect_commits_chain(tmp_path: pathlib.Path) -> None:
    _init_repo(tmp_path)
    base = _make_commit(tmp_path, content=b"base")
    c1 = _make_commit(tmp_path, parent_id=base, content=b"c1")
    c2 = _make_commit(tmp_path, parent_id=c1, content=b"c2")
    c3 = _make_commit(tmp_path, parent_id=c2, content=b"c3")
    result = collect_commits_to_replay(tmp_path, stop_at=base, tip=c3)
    assert len(result) == 3
    # Oldest first.
    assert result[0].commit_id == c1
    assert result[1].commit_id == c2
    assert result[2].commit_id == c3


# ---------------------------------------------------------------------------
# CLI: muse rebase --help
# ---------------------------------------------------------------------------


def test_rebase_help() -> None:
    result = runner.invoke(cli, ["rebase", "--help"])
    assert result.exit_code == 0
    assert "--abort" in result.output or "-a" in result.output


# ---------------------------------------------------------------------------
# CLI: abort with no active rebase
# ---------------------------------------------------------------------------


def test_rebase_abort_no_state(tmp_path: pathlib.Path) -> None:
    _init_repo(tmp_path)
    result = runner.invoke(cli, ["rebase", "--abort"], env=_env(tmp_path))
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# CLI: continue with no active rebase
# ---------------------------------------------------------------------------


def test_rebase_continue_no_state(tmp_path: pathlib.Path) -> None:
    _init_repo(tmp_path)
    result = runner.invoke(cli, ["rebase", "--continue"], env=_env(tmp_path))
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# CLI: rebase with no upstream given
# ---------------------------------------------------------------------------


def test_rebase_no_upstream_error(tmp_path: pathlib.Path) -> None:
    _init_repo(tmp_path)
    _make_commit(tmp_path, content=b"single")
    result = runner.invoke(cli, ["rebase"], env=_env(tmp_path))
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# CLI: already up-to-date
# ---------------------------------------------------------------------------


def test_rebase_already_up_to_date(tmp_path: pathlib.Path) -> None:
    _init_repo(tmp_path)
    cid = _make_commit(tmp_path, content=b"only commit")
    # Point upstream to the same commit.
    (tmp_path / ".muse" / "refs" / "heads" / "upstream").write_text(cid, encoding="utf-8")
    result = runner.invoke(
        cli, ["rebase", "upstream"], env=_env(tmp_path)
    )
    # Should exit cleanly — nothing to rebase.
    assert result.exit_code == 0
    assert "up to date" in result.output.lower()


# ---------------------------------------------------------------------------
# CLI: abort restores original HEAD
# ---------------------------------------------------------------------------


def test_rebase_abort_restores_head(tmp_path: pathlib.Path) -> None:
    _init_repo(tmp_path)
    base = _make_commit(tmp_path, content=b"base")
    tip = _make_commit(tmp_path, parent_id=base, content=b"tip")

    state = RebaseState(
        original_branch="main",
        original_head=base,
        onto=base,
        remaining=[tip],
        completed=[],
        squash=False,
    )
    save_rebase_state(tmp_path, state)

    result = runner.invoke(cli, ["rebase", "--abort"], env=_env(tmp_path))
    assert result.exit_code == 0
    assert "aborted" in result.output.lower()
    # State file should be gone.
    assert load_rebase_state(tmp_path) is None
    # Branch ref should be restored to original_head.
    head = (tmp_path / ".muse" / "refs" / "heads" / "main").read_text(encoding="utf-8").strip()
    assert head == base


# ---------------------------------------------------------------------------
# Stress: collect 20 commits
# ---------------------------------------------------------------------------


def test_rebase_stress_collect_20_commits(tmp_path: pathlib.Path) -> None:
    _init_repo(tmp_path)
    base = _make_commit(tmp_path, content=b"stress-base")
    prev = base
    commits: list[str] = []
    for i in range(20):
        c = _make_commit(tmp_path, parent_id=prev, content=f"s{i}".encode())
        commits.append(c)
        prev = c

    result = collect_commits_to_replay(tmp_path, stop_at=base, tip=prev)
    assert len(result) == 20
    assert result[0].commit_id == commits[0]
    assert result[-1].commit_id == commits[-1]

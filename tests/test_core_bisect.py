"""Tests for muse/core/bisect.py — binary search regression hunting."""

from __future__ import annotations

import json
import pathlib

import pytest

from muse.core.bisect import (
    BisectResult,
    get_bisect_log,
    is_bisect_active,
    mark_bad,
    mark_good,
    reset_bisect,
    skip_commit,
    start_bisect,
)


# ---------------------------------------------------------------------------
# Repo fixture
# ---------------------------------------------------------------------------


def _make_linear_repo(tmp_path: pathlib.Path, n: int = 8) -> list[str]:
    """Create n commits in a linear chain; return commit IDs oldest-first."""
    import datetime

    muse = tmp_path / ".muse"
    for d in ("objects", "commits", "snapshots", "refs/heads"):
        (muse / d).mkdir(parents=True, exist_ok=True)
    (muse / "repo.json").write_text(json.dumps({"repo_id": "test"}))
    (muse / "HEAD").write_text("refs/heads/main\n")

    commit_ids: list[str] = []
    parent: str | None = None
    for i in range(n):
        commit_id = format(i + 1, "064x")
        snap_id = format(100 + i, "064x")
        rec: dict[str, str | None | dict[str, str]] = {
            "commit_id": commit_id,
            "repo_id": "test",
            "branch": "main",
            "snapshot_id": snap_id,
            "message": f"commit {i + 1}",
            "committed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "parent_commit_id": parent,
            "parent2_commit_id": None,
            "author": "Test",
            "metadata": {},
        }
        (muse / "commits" / f"{commit_id}.json").write_text(json.dumps(rec))
        snap: dict[str, str | dict[str, str]] = {"snapshot_id": snap_id, "manifest": {}}
        (muse / "snapshots" / f"{snap_id}.json").write_text(json.dumps(snap))
        commit_ids.append(commit_id)
        parent = commit_id

    (muse / "refs" / "heads" / "main").write_text(commit_ids[-1])
    return commit_ids


# ---------------------------------------------------------------------------
# start_bisect
# ---------------------------------------------------------------------------


def test_start_bisect_creates_state(tmp_path: pathlib.Path) -> None:
    commits = _make_linear_repo(tmp_path)
    bad_id = commits[-1]
    good_id = commits[0]
    result = start_bisect(tmp_path, bad_id, [good_id])
    assert is_bisect_active(tmp_path)
    assert isinstance(result, BisectResult)


def test_start_bisect_suggests_midpoint(tmp_path: pathlib.Path) -> None:
    commits = _make_linear_repo(tmp_path, n=8)
    result = start_bisect(tmp_path, commits[-1], [commits[0]])
    assert result.next_to_test is not None
    assert not result.done


def test_start_bisect_steps_remaining_positive(tmp_path: pathlib.Path) -> None:
    commits = _make_linear_repo(tmp_path, n=16)
    result = start_bisect(tmp_path, commits[-1], [commits[0]])
    assert result.steps_remaining > 0


def test_start_bisect_with_multiple_good(tmp_path: pathlib.Path) -> None:
    commits = _make_linear_repo(tmp_path, n=10)
    result = start_bisect(tmp_path, commits[-1], [commits[0], commits[2]])
    assert result.next_to_test is not None


# ---------------------------------------------------------------------------
# mark_good / mark_bad
# ---------------------------------------------------------------------------


def test_mark_good_advances_bisect(tmp_path: pathlib.Path) -> None:
    commits = _make_linear_repo(tmp_path, n=8)
    start_bisect(tmp_path, commits[-1], [commits[0]])
    # Read the midpoint from state.
    from muse.core.bisect import _load_state
    state = _load_state(tmp_path)
    assert state is not None
    remaining = state.get("remaining", [])
    mid = remaining[len(remaining) // 2]
    result = mark_good(tmp_path, mid)
    assert isinstance(result, BisectResult)
    assert result.verdict == "good"


def test_mark_bad_advances_bisect(tmp_path: pathlib.Path) -> None:
    commits = _make_linear_repo(tmp_path, n=8)
    start_bisect(tmp_path, commits[-1], [commits[0]])
    from muse.core.bisect import _load_state
    state = _load_state(tmp_path)
    assert state is not None
    remaining = state.get("remaining", [])
    mid = remaining[len(remaining) // 2]
    result = mark_bad(tmp_path, mid)
    assert result.verdict == "bad"


def test_mark_good_reduces_remaining(tmp_path: pathlib.Path) -> None:
    commits = _make_linear_repo(tmp_path, n=16)
    start_bisect(tmp_path, commits[-1], [commits[0]])
    from muse.core.bisect import _load_state
    state = _load_state(tmp_path)
    assert state is not None
    remaining_before = len(state.get("remaining", []))
    mid = state["remaining"][len(state["remaining"]) // 2]
    result = mark_good(tmp_path, mid)
    assert result.remaining_count < remaining_before


# ---------------------------------------------------------------------------
# skip_commit
# ---------------------------------------------------------------------------


def test_skip_commit(tmp_path: pathlib.Path) -> None:
    commits = _make_linear_repo(tmp_path, n=8)
    start_bisect(tmp_path, commits[-1], [commits[0]])
    from muse.core.bisect import _load_state
    state = _load_state(tmp_path)
    assert state is not None
    remaining = state.get("remaining", [])
    mid = remaining[len(remaining) // 2]
    result = skip_commit(tmp_path, mid)
    assert result.verdict == "skip"


# ---------------------------------------------------------------------------
# reset_bisect
# ---------------------------------------------------------------------------


def test_reset_bisect_removes_state(tmp_path: pathlib.Path) -> None:
    commits = _make_linear_repo(tmp_path)
    start_bisect(tmp_path, commits[-1], [commits[0]])
    assert is_bisect_active(tmp_path)
    reset_bisect(tmp_path)
    assert not is_bisect_active(tmp_path)


def test_reset_idempotent(tmp_path: pathlib.Path) -> None:
    reset_bisect(tmp_path)  # Should not raise even with no active session.


# ---------------------------------------------------------------------------
# bisect log
# ---------------------------------------------------------------------------


def test_bisect_log_records_start(tmp_path: pathlib.Path) -> None:
    commits = _make_linear_repo(tmp_path)
    start_bisect(tmp_path, commits[-1], [commits[0]])
    log = get_bisect_log(tmp_path)
    assert len(log) >= 2  # bad + at least one good


def test_bisect_log_records_verdicts(tmp_path: pathlib.Path) -> None:
    commits = _make_linear_repo(tmp_path, n=8)
    start_bisect(tmp_path, commits[-1], [commits[0]])
    from muse.core.bisect import _load_state
    state = _load_state(tmp_path)
    assert state is not None
    remaining = state.get("remaining", [])
    mark_good(tmp_path, remaining[len(remaining) // 2])
    log = get_bisect_log(tmp_path)
    assert any("good" in entry for entry in log)


def test_bisect_log_empty_when_inactive(tmp_path: pathlib.Path) -> None:
    assert get_bisect_log(tmp_path) == []


# ---------------------------------------------------------------------------
# is_bisect_active
# ---------------------------------------------------------------------------


def test_is_bisect_active_false_initially(tmp_path: pathlib.Path) -> None:
    _make_linear_repo(tmp_path)
    assert not is_bisect_active(tmp_path)


def test_is_bisect_active_true_after_start(tmp_path: pathlib.Path) -> None:
    commits = _make_linear_repo(tmp_path)
    start_bisect(tmp_path, commits[-1], [commits[0]])
    assert is_bisect_active(tmp_path)


# ---------------------------------------------------------------------------
# Full convergence test
# ---------------------------------------------------------------------------


def test_bisect_converges_to_first_bad(tmp_path: pathlib.Path) -> None:
    """Bisect should isolate commit 6 (0-indexed 5) as first bad in 8-commit chain."""
    commits = _make_linear_repo(tmp_path, n=8)
    # Commits 0..4 are good; commit 5 is the first bad.
    bad_idx = 5

    start_bisect(tmp_path, commits[-1], [commits[0]])

    steps = 0
    max_steps = 20  # guard against infinite loop in tests
    while steps < max_steps:
        from muse.core.bisect import _load_state
        state = _load_state(tmp_path)
        assert state is not None
        remaining = state.get("remaining", [])
        if not remaining:
            break
        mid = remaining[len(remaining) // 2]
        mid_idx = commits.index(mid)
        if mid_idx < bad_idx:
            mark_good(tmp_path, mid)
        else:
            mark_bad(tmp_path, mid)
        steps += 1

    from muse.core.bisect import _load_state
    final = _load_state(tmp_path)
    assert final is not None
    first_bad = final.get("bad_id", "")
    # The first bad commit should be at or near index bad_idx.
    assert first_bad in commits[bad_idx:]


# ---------------------------------------------------------------------------
# Stress: many commits
# ---------------------------------------------------------------------------


def test_bisect_stress_100_commits(tmp_path: pathlib.Path) -> None:
    """Bisect should converge in at most log2(100) ≈ 7 steps for 100 commits."""
    import math

    commits = _make_linear_repo(tmp_path, n=100)
    bad_idx = 60
    start_bisect(tmp_path, commits[-1], [commits[0]])

    steps = 0
    max_steps = int(math.log2(100)) + 5
    from muse.core.bisect import _load_state
    while steps < max_steps:
        state = _load_state(tmp_path)
        if state is None:
            break
        remaining = state.get("remaining", [])
        if not remaining:
            break
        mid = remaining[len(remaining) // 2]
        mid_idx = commits.index(mid)
        if mid_idx < bad_idx:
            mark_good(tmp_path, mid)
        else:
            mark_bad(tmp_path, mid)
        steps += 1

    assert steps <= max_steps

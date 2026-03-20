"""Tests for muse/core/worktree.py — multiple simultaneous branch checkouts."""

from __future__ import annotations

import json
import pathlib

import pytest

from muse.core.worktree import (
    WorktreeInfo,
    add_worktree,
    list_worktrees,
    prune_worktrees,
    remove_worktree,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_repo(tmp_path: pathlib.Path, branch: str = "main") -> pathlib.Path:
    """Create a minimal Muse repo inside a named subdirectory."""
    repo = tmp_path / "myproject"
    muse = repo / ".muse"
    for d in ("objects", "commits", "snapshots", "refs/heads"):
        (muse / d).mkdir(parents=True, exist_ok=True)
    (muse / "repo.json").write_text(json.dumps({"repo_id": "test-repo"}))
    (muse / "HEAD").write_text(f"refs/heads/{branch}\n")
    (muse / "refs" / "heads" / branch).write_text("0" * 64)
    return repo


def _add_branch(repo: pathlib.Path, branch: str) -> None:
    ref = repo / ".muse" / "refs" / "heads" / branch
    ref.parent.mkdir(parents=True, exist_ok=True)
    ref.write_text("0" * 64)


# ---------------------------------------------------------------------------
# add_worktree
# ---------------------------------------------------------------------------


def test_add_worktree_creates_directory(tmp_path: pathlib.Path) -> None:
    repo = _make_repo(tmp_path)
    _add_branch(repo, "feat/audio")
    wt_path = add_worktree(repo, "feat-audio", "feat/audio")
    assert wt_path.exists()
    assert (wt_path / "state").exists()


def test_add_worktree_creates_metadata(tmp_path: pathlib.Path) -> None:
    repo = _make_repo(tmp_path)
    _add_branch(repo, "dev")
    add_worktree(repo, "mydev", "dev")
    meta = repo / ".muse" / "worktrees" / "mydev.json"
    assert meta.exists()
    data = json.loads(meta.read_text())
    assert data["name"] == "mydev"
    assert data["branch"] == "dev"


def test_add_worktree_creates_head_file(tmp_path: pathlib.Path) -> None:
    repo = _make_repo(tmp_path)
    _add_branch(repo, "dev")
    add_worktree(repo, "mydev", "dev")
    head = repo / ".muse" / "worktrees" / "mydev.HEAD"
    assert head.exists()
    assert "refs/heads/dev" in head.read_text()


def test_add_worktree_rejects_unknown_branch(tmp_path: pathlib.Path) -> None:
    repo = _make_repo(tmp_path)
    with pytest.raises(ValueError, match="does not exist"):
        add_worktree(repo, "bad", "no-such-branch")


def test_add_worktree_rejects_duplicate_name(tmp_path: pathlib.Path) -> None:
    repo = _make_repo(tmp_path)
    _add_branch(repo, "dev")
    add_worktree(repo, "mydev", "dev")
    with pytest.raises(ValueError, match="already exists"):
        add_worktree(repo, "mydev", "dev")


def test_add_worktree_rejects_invalid_name(tmp_path: pathlib.Path) -> None:
    repo = _make_repo(tmp_path)
    _add_branch(repo, "dev")
    with pytest.raises(ValueError):
        add_worktree(repo, "..", "dev")


def test_add_worktree_directory_name_includes_repo_name(tmp_path: pathlib.Path) -> None:
    repo = _make_repo(tmp_path)
    _add_branch(repo, "dev")
    wt_path = add_worktree(repo, "dev", "dev")
    assert "myproject" in wt_path.name


# ---------------------------------------------------------------------------
# list_worktrees
# ---------------------------------------------------------------------------


def test_list_worktrees_includes_main(tmp_path: pathlib.Path) -> None:
    repo = _make_repo(tmp_path)
    worktrees = list_worktrees(repo)
    assert any(wt.is_main for wt in worktrees)


def test_list_worktrees_includes_linked(tmp_path: pathlib.Path) -> None:
    repo = _make_repo(tmp_path)
    _add_branch(repo, "dev")
    add_worktree(repo, "dev", "dev")
    worktrees = list_worktrees(repo)
    names = [wt.name for wt in worktrees]
    assert "dev" in names


def test_list_worktrees_empty_repo(tmp_path: pathlib.Path) -> None:
    repo = _make_repo(tmp_path)
    worktrees = list_worktrees(repo)
    assert len(worktrees) == 1  # only main
    assert worktrees[0].is_main


# ---------------------------------------------------------------------------
# remove_worktree
# ---------------------------------------------------------------------------


def test_remove_worktree_removes_directory(tmp_path: pathlib.Path) -> None:
    repo = _make_repo(tmp_path)
    _add_branch(repo, "dev")
    wt_path = add_worktree(repo, "dev", "dev")
    assert wt_path.exists()
    remove_worktree(repo, "dev")
    assert not wt_path.exists()


def test_remove_worktree_removes_metadata(tmp_path: pathlib.Path) -> None:
    repo = _make_repo(tmp_path)
    _add_branch(repo, "dev")
    add_worktree(repo, "dev", "dev")
    remove_worktree(repo, "dev")
    meta = repo / ".muse" / "worktrees" / "dev.json"
    assert not meta.exists()


def test_remove_worktree_not_found_raises(tmp_path: pathlib.Path) -> None:
    repo = _make_repo(tmp_path)
    with pytest.raises(ValueError, match="does not exist"):
        remove_worktree(repo, "nonexistent")


def test_remove_worktree_not_in_list_after_removal(tmp_path: pathlib.Path) -> None:
    repo = _make_repo(tmp_path)
    _add_branch(repo, "dev")
    add_worktree(repo, "dev", "dev")
    remove_worktree(repo, "dev")
    worktrees = list_worktrees(repo)
    names = [wt.name for wt in worktrees]
    assert "dev" not in names


# ---------------------------------------------------------------------------
# prune_worktrees
# ---------------------------------------------------------------------------


def test_prune_removes_stale_metadata(tmp_path: pathlib.Path) -> None:
    repo = _make_repo(tmp_path)
    _add_branch(repo, "dev")
    wt_path = add_worktree(repo, "dev", "dev")
    # Manually delete the worktree directory to simulate external removal.
    import shutil
    shutil.rmtree(wt_path)
    pruned = prune_worktrees(repo)
    assert "dev" in pruned


def test_prune_does_nothing_when_all_present(tmp_path: pathlib.Path) -> None:
    repo = _make_repo(tmp_path)
    _add_branch(repo, "dev")
    add_worktree(repo, "dev", "dev")
    pruned = prune_worktrees(repo)
    assert pruned == []


def test_prune_empty_repo(tmp_path: pathlib.Path) -> None:
    repo = _make_repo(tmp_path)
    assert prune_worktrees(repo) == []


# ---------------------------------------------------------------------------
# Stress: multiple worktrees
# ---------------------------------------------------------------------------


def test_stress_many_worktrees(tmp_path: pathlib.Path) -> None:
    """Creating 10 worktrees should all succeed and be listed."""
    repo = _make_repo(tmp_path)
    for i in range(10):
        branch = f"feat-{i}"
        _add_branch(repo, branch)
        add_worktree(repo, f"wt{i}", branch)

    worktrees = list_worktrees(repo)
    linked = [wt for wt in worktrees if not wt.is_main]
    assert len(linked) == 10

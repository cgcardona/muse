"""Tests for muse/core/workspace.py — multi-repository workspace management."""

from __future__ import annotations

import json
import pathlib

import pytest

from muse.core.workspace import (
    WorkspaceMemberStatus,
    add_workspace_member,
    list_workspace_members,
    remove_workspace_member,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_repo(tmp_path: pathlib.Path) -> pathlib.Path:
    muse = tmp_path / ".muse"
    for d in ("objects", "commits", "snapshots", "refs/heads"):
        (muse / d).mkdir(parents=True, exist_ok=True)
    (muse / "repo.json").write_text(json.dumps({"repo_id": "test-repo"}))
    (muse / "HEAD").write_text("ref: refs/heads/main\n")
    (muse / "refs" / "heads" / "main").write_text("0" * 64)
    return tmp_path


# ---------------------------------------------------------------------------
# add_workspace_member
# ---------------------------------------------------------------------------


def test_add_member_creates_manifest(tmp_path: pathlib.Path) -> None:
    repo = _make_repo(tmp_path)
    add_workspace_member(repo, "core", "https://musehub.ai/acme/core")
    manifest_path = repo / ".muse" / "workspace.toml"
    assert manifest_path.exists()


def test_add_member_stores_name_and_url(tmp_path: pathlib.Path) -> None:
    repo = _make_repo(tmp_path)
    add_workspace_member(repo, "sounds", "https://musehub.ai/acme/sounds")
    members = list_workspace_members(repo)
    assert len(members) == 1
    assert members[0].name == "sounds"
    assert members[0].url == "https://musehub.ai/acme/sounds"


def test_add_member_default_path(tmp_path: pathlib.Path) -> None:
    repo = _make_repo(tmp_path)
    add_workspace_member(repo, "core", "https://example.com/core")
    members = list_workspace_members(repo)
    assert "repos/core" in str(members[0].path)


def test_add_member_custom_path(tmp_path: pathlib.Path) -> None:
    repo = _make_repo(tmp_path)
    add_workspace_member(repo, "core", "https://example.com/core", path="vendor/core")
    members = list_workspace_members(repo)
    assert "vendor/core" in str(members[0].path)


def test_add_member_default_branch_is_main(tmp_path: pathlib.Path) -> None:
    repo = _make_repo(tmp_path)
    add_workspace_member(repo, "core", "https://example.com/core")
    members = list_workspace_members(repo)
    assert members[0].branch == "main"


def test_add_member_custom_branch(tmp_path: pathlib.Path) -> None:
    repo = _make_repo(tmp_path)
    add_workspace_member(repo, "data", "https://example.com/data", branch="v2")
    members = list_workspace_members(repo)
    assert members[0].branch == "v2"


def test_add_duplicate_member_raises(tmp_path: pathlib.Path) -> None:
    repo = _make_repo(tmp_path)
    add_workspace_member(repo, "core", "https://example.com/core")
    with pytest.raises(ValueError, match="already exists"):
        add_workspace_member(repo, "core", "https://example.com/other")


def test_add_multiple_members(tmp_path: pathlib.Path) -> None:
    repo = _make_repo(tmp_path)
    add_workspace_member(repo, "core", "https://example.com/core")
    add_workspace_member(repo, "sounds", "https://example.com/sounds")
    add_workspace_member(repo, "docs", "https://example.com/docs")
    members = list_workspace_members(repo)
    assert len(members) == 3


# ---------------------------------------------------------------------------
# remove_workspace_member
# ---------------------------------------------------------------------------


def test_remove_member_removes_from_manifest(tmp_path: pathlib.Path) -> None:
    repo = _make_repo(tmp_path)
    add_workspace_member(repo, "core", "https://example.com/core")
    remove_workspace_member(repo, "core")
    members = list_workspace_members(repo)
    assert len(members) == 0


def test_remove_nonexistent_member_raises(tmp_path: pathlib.Path) -> None:
    repo = _make_repo(tmp_path)
    # First add a member so the manifest exists, then try to remove a nonexistent one.
    add_workspace_member(repo, "core", "https://example.com/core")
    with pytest.raises(ValueError, match="not found"):
        remove_workspace_member(repo, "nonexistent")


def test_remove_no_manifest_raises(tmp_path: pathlib.Path) -> None:
    repo = _make_repo(tmp_path)
    with pytest.raises(ValueError, match="No workspace manifest"):
        remove_workspace_member(repo, "anything")


def test_remove_only_removes_named_member(tmp_path: pathlib.Path) -> None:
    repo = _make_repo(tmp_path)
    add_workspace_member(repo, "core", "https://example.com/core")
    add_workspace_member(repo, "sounds", "https://example.com/sounds")
    remove_workspace_member(repo, "core")
    members = list_workspace_members(repo)
    assert len(members) == 1
    assert members[0].name == "sounds"


# ---------------------------------------------------------------------------
# list_workspace_members
# ---------------------------------------------------------------------------


def test_list_returns_empty_when_no_manifest(tmp_path: pathlib.Path) -> None:
    repo = _make_repo(tmp_path)
    assert list_workspace_members(repo) == []


def test_list_present_false_when_not_cloned(tmp_path: pathlib.Path) -> None:
    repo = _make_repo(tmp_path)
    add_workspace_member(repo, "core", "https://example.com/core")
    members = list_workspace_members(repo)
    assert members[0].present is False


def test_list_present_true_when_cloned(tmp_path: pathlib.Path) -> None:
    repo = _make_repo(tmp_path)
    add_workspace_member(repo, "local", str(tmp_path / "local_clone"), path="local_clone")
    # Simulate a cloned repo at the expected path.
    clone_path = tmp_path / "local_clone"
    (clone_path / ".muse").mkdir(parents=True, exist_ok=True)
    members = list_workspace_members(repo)
    assert members[0].present is True


def test_list_head_none_when_not_cloned(tmp_path: pathlib.Path) -> None:
    repo = _make_repo(tmp_path)
    add_workspace_member(repo, "core", "https://example.com/core")
    members = list_workspace_members(repo)
    assert members[0].head_commit is None


def test_list_returns_workspace_member_status(tmp_path: pathlib.Path) -> None:
    repo = _make_repo(tmp_path)
    add_workspace_member(repo, "core", "https://example.com/core")
    members = list_workspace_members(repo)
    assert isinstance(members[0], WorkspaceMemberStatus)


# ---------------------------------------------------------------------------
# Stress
# ---------------------------------------------------------------------------


def test_stress_50_members(tmp_path: pathlib.Path) -> None:
    """Adding 50 members should all be preserved and listed correctly."""
    repo = _make_repo(tmp_path)
    for i in range(50):
        add_workspace_member(repo, f"svc{i}", f"https://example.com/svc{i}")
    members = list_workspace_members(repo)
    assert len(members) == 50
    names = {m.name for m in members}
    for i in range(50):
        assert f"svc{i}" in names


def test_stress_add_remove_cycle(tmp_path: pathlib.Path) -> None:
    """Add and remove 20 members; manifest should be empty at the end."""
    repo = _make_repo(tmp_path)
    for i in range(20):
        add_workspace_member(repo, f"repo{i}", f"https://example.com/repo{i}")
    for i in range(20):
        remove_workspace_member(repo, f"repo{i}")
    assert list_workspace_members(repo) == []

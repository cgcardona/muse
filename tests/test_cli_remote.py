"""Tests for muse remote — add, remove, rename, list, get-url, set-url."""

from __future__ import annotations

import json
import os
import pathlib

import pytest
from typer.testing import CliRunner

from muse.cli.app import cli
from muse.cli.config import get_remote, list_remotes


# ---------------------------------------------------------------------------
# Fixture — initialised repo
# ---------------------------------------------------------------------------


@pytest.fixture
def repo(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    """Minimal .muse/ repo; cwd and MUSE_REPO_ROOT set to tmp_path."""
    muse_dir = tmp_path / ".muse"
    (muse_dir / "refs" / "heads").mkdir(parents=True)
    (muse_dir / "objects").mkdir()
    (muse_dir / "commits").mkdir()
    (muse_dir / "snapshots").mkdir()
    (muse_dir / "repo.json").write_text(
        json.dumps({"repo_id": "test-repo", "schema_version": "2", "domain": "midi"})
    )
    (muse_dir / "HEAD").write_text("ref: refs/heads/main\n")
    (muse_dir / "refs" / "heads" / "main").write_text("")
    (muse_dir / "config.toml").write_text("")
    monkeypatch.setenv("MUSE_REPO_ROOT", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    return tmp_path


runner = CliRunner()


# ---------------------------------------------------------------------------
# remote add
# ---------------------------------------------------------------------------


class TestRemoteAdd:
    def test_add_new_remote(self, repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["remote", "add", "origin", "https://hub.muse.io/repos/r1"])
        assert result.exit_code == 0
        assert "origin" in result.output
        assert get_remote("origin", repo) == "https://hub.muse.io/repos/r1"

    def test_add_duplicate_remote_fails(self, repo: pathlib.Path) -> None:
        runner.invoke(cli, ["remote", "add", "origin", "https://hub.muse.io/repos/r1"])
        result = runner.invoke(cli, ["remote", "add", "origin", "https://other.com/r"])
        assert result.exit_code != 0
        assert "already exists" in result.output

    def test_add_multiple_remotes(self, repo: pathlib.Path) -> None:
        runner.invoke(cli, ["remote", "add", "origin", "https://hub.muse.io/repos/r1"])
        runner.invoke(cli, ["remote", "add", "upstream", "https://hub.muse.io/repos/r2"])
        remotes = {r["name"] for r in list_remotes(repo)}
        assert remotes == {"origin", "upstream"}


# ---------------------------------------------------------------------------
# remote remove
# ---------------------------------------------------------------------------


class TestRemoteRemove:
    def test_remove_existing_remote(self, repo: pathlib.Path) -> None:
        runner.invoke(cli, ["remote", "add", "origin", "https://hub.muse.io/repos/r1"])
        result = runner.invoke(cli, ["remote", "remove", "origin"])
        assert result.exit_code == 0
        assert get_remote("origin", repo) is None

    def test_remove_nonexistent_remote_fails(self, repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["remote", "remove", "ghost"])
        assert result.exit_code != 0
        assert "does not exist" in result.output

    def test_remove_cleans_tracking_refs(self, repo: pathlib.Path) -> None:
        runner.invoke(cli, ["remote", "add", "origin", "https://hub.muse.io/repos/r1"])
        refs_dir = repo / ".muse" / "remotes" / "origin"
        refs_dir.mkdir(parents=True)
        (refs_dir / "main").write_text("abc123")
        runner.invoke(cli, ["remote", "remove", "origin"])
        assert not refs_dir.exists()


# ---------------------------------------------------------------------------
# remote rename
# ---------------------------------------------------------------------------


class TestRemoteRename:
    def test_rename_existing_remote(self, repo: pathlib.Path) -> None:
        runner.invoke(cli, ["remote", "add", "origin", "https://hub.muse.io/repos/r1"])
        result = runner.invoke(cli, ["remote", "rename", "origin", "upstream"])
        assert result.exit_code == 0
        assert get_remote("upstream", repo) == "https://hub.muse.io/repos/r1"
        assert get_remote("origin", repo) is None

    def test_rename_nonexistent_fails(self, repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["remote", "rename", "ghost", "new"])
        assert result.exit_code != 0
        assert "does not exist" in result.output

    def test_rename_to_existing_name_fails(self, repo: pathlib.Path) -> None:
        runner.invoke(cli, ["remote", "add", "origin", "https://hub.muse.io/repos/r1"])
        runner.invoke(cli, ["remote", "add", "upstream", "https://hub.muse.io/repos/r2"])
        result = runner.invoke(cli, ["remote", "rename", "origin", "upstream"])
        assert result.exit_code != 0
        assert "already exists" in result.output


# ---------------------------------------------------------------------------
# muse remote (implied list)
# ---------------------------------------------------------------------------


class TestRemoteList:
    def test_list_empty(self, repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["remote"])
        assert result.exit_code == 0
        assert "No remotes" in result.output

    def test_list_shows_names(self, repo: pathlib.Path) -> None:
        runner.invoke(cli, ["remote", "add", "origin", "https://hub.muse.io/repos/r1"])
        runner.invoke(cli, ["remote", "add", "upstream", "https://hub.muse.io/repos/r2"])
        result = runner.invoke(cli, ["remote"])
        assert result.exit_code == 0
        assert "origin" in result.output
        assert "upstream" in result.output

    def test_list_verbose_shows_url(self, repo: pathlib.Path) -> None:
        runner.invoke(cli, ["remote", "add", "origin", "https://hub.muse.io/repos/r1"])
        result = runner.invoke(cli, ["remote", "-v"])
        assert result.exit_code == 0
        assert "https://hub.muse.io/repos/r1" in result.output


# ---------------------------------------------------------------------------
# remote get-url
# ---------------------------------------------------------------------------


class TestRemoteGetUrl:
    def test_get_url_existing(self, repo: pathlib.Path) -> None:
        runner.invoke(cli, ["remote", "add", "origin", "https://hub.muse.io/repos/r1"])
        result = runner.invoke(cli, ["remote", "get-url", "origin"])
        assert result.exit_code == 0
        assert "https://hub.muse.io/repos/r1" in result.output

    def test_get_url_nonexistent_fails(self, repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["remote", "get-url", "ghost"])
        assert result.exit_code != 0
        assert "does not exist" in result.output


# ---------------------------------------------------------------------------
# remote set-url
# ---------------------------------------------------------------------------


class TestRemoteSetUrl:
    def test_set_url_updates_existing(self, repo: pathlib.Path) -> None:
        runner.invoke(cli, ["remote", "add", "origin", "https://hub.muse.io/repos/r1"])
        result = runner.invoke(
            cli, ["remote", "set-url", "origin", "https://hub.muse.io/repos/r2"]
        )
        assert result.exit_code == 0
        assert get_remote("origin", repo) == "https://hub.muse.io/repos/r2"

    def test_set_url_nonexistent_fails(self, repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["remote", "set-url", "ghost", "https://example.com"])
        assert result.exit_code != 0
        assert "does not exist" in result.output

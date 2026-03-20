"""Tests for muse reset and muse revert."""

import pathlib

import pytest
from typer.testing import CliRunner

from muse.cli.app import cli
from muse.core.store import get_head_commit_id

runner = CliRunner()


@pytest.fixture
def repo(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MUSE_REPO_ROOT", str(tmp_path))
    result = runner.invoke(cli, ["init"])
    assert result.exit_code == 0, result.output
    return tmp_path


def _write(repo: pathlib.Path, filename: str, content: str = "data") -> None:
    (repo / "state" / filename).write_text(content)


def _commit(msg: str = "initial") -> None:
    result = runner.invoke(cli, ["commit", "-m", msg])
    assert result.exit_code == 0, result.output


def _head_id(repo: pathlib.Path) -> str | None:
    return get_head_commit_id(repo, "main")


# ---------------------------------------------------------------------------
# reset
# ---------------------------------------------------------------------------


class TestResetSoft:
    def test_moves_branch_pointer(self, repo: pathlib.Path) -> None:
        _write(repo, "beat.mid", "v1")
        _commit("first")
        first_id = _head_id(repo)

        _write(repo, "beat.mid", "v2")
        _commit("second")
        assert _head_id(repo) != first_id

        result = runner.invoke(cli, ["reset", first_id])
        assert result.exit_code == 0, result.output
        assert _head_id(repo) == first_id

    def test_soft_preserves_workdir(self, repo: pathlib.Path) -> None:
        _write(repo, "beat.mid", "v1")
        _commit("first")
        first_id = _head_id(repo)

        _write(repo, "lead.mid", "new")
        _commit("second")

        runner.invoke(cli, ["reset", first_id])
        # workdir still has lead.mid from second commit (soft = no restore)
        assert (repo / "state" / "lead.mid").exists()

    def test_soft_output_message(self, repo: pathlib.Path) -> None:
        _write(repo, "beat.mid")
        _commit("first")
        first_id = _head_id(repo)
        _write(repo, "lead.mid")
        _commit("second")

        result = runner.invoke(cli, ["reset", first_id])
        assert "Moved" in result.output or first_id[:8] in result.output

    def test_reset_unknown_ref_errors(self, repo: pathlib.Path) -> None:
        _write(repo, "beat.mid")
        _commit("only")
        result = runner.invoke(cli, ["reset", "deadbeef"])
        assert result.exit_code != 0
        assert "not found" in result.output.lower() or "deadbeef" in result.output


class TestResetHard:
    def test_moves_branch_pointer(self, repo: pathlib.Path) -> None:
        _write(repo, "beat.mid", "v1")
        _commit("first")
        first_id = _head_id(repo)

        _write(repo, "beat.mid", "v2")
        _commit("second")

        result = runner.invoke(cli, ["reset", "--hard", first_id])
        assert result.exit_code == 0, result.output
        assert _head_id(repo) == first_id

    def test_restores_workdir(self, repo: pathlib.Path) -> None:
        _write(repo, "beat.mid", "v1")
        _commit("first")
        first_id = _head_id(repo)

        _write(repo, "lead.mid", "new")
        _commit("second")

        runner.invoke(cli, ["reset", "--hard", first_id])
        # After hard reset, workdir should reflect first commit (no lead.mid)
        assert not (repo / "state" / "lead.mid").exists()
        assert (repo / "state" / "beat.mid").exists()

    def test_restores_file_content(self, repo: pathlib.Path) -> None:
        _write(repo, "beat.mid", "original")
        _commit("first")
        first_id = _head_id(repo)

        _write(repo, "beat.mid", "modified")
        _commit("second")

        runner.invoke(cli, ["reset", "--hard", first_id])
        assert (repo / "state" / "beat.mid").read_text() == "original"

    def test_hard_output_shows_commit(self, repo: pathlib.Path) -> None:
        _write(repo, "beat.mid")
        _commit("the target")
        first_id = _head_id(repo)
        _write(repo, "lead.mid")
        _commit("second")

        result = runner.invoke(cli, ["reset", "--hard", first_id])
        assert result.exit_code == 0
        assert "HEAD is now at" in result.output


# ---------------------------------------------------------------------------
# revert
# ---------------------------------------------------------------------------


class TestRevert:
    def test_creates_new_commit(self, repo: pathlib.Path) -> None:
        _write(repo, "beat.mid")
        _commit("add beat")
        before_id = _head_id(repo)

        _write(repo, "lead.mid")
        _commit("add lead")
        after_id = _head_id(repo)

        result = runner.invoke(cli, ["revert", after_id])
        assert result.exit_code == 0, result.output
        new_id = _head_id(repo)
        assert new_id not in (before_id, after_id)

    def test_revert_restores_parent_state(self, repo: pathlib.Path) -> None:
        _write(repo, "beat.mid", "original")
        _commit("first")

        _write(repo, "beat.mid", "changed")
        _commit("second")
        second_id = _head_id(repo)

        runner.invoke(cli, ["revert", second_id])
        assert (repo / "state" / "beat.mid").read_text() == "original"

    def test_revert_default_message_includes_original(self, repo: pathlib.Path) -> None:
        # Need a base commit first so "my change" is not the root
        _write(repo, "base.mid", "base")
        _commit("base")

        _write(repo, "beat.mid")
        _commit("my change")
        commit_id = _head_id(repo)

        _write(repo, "lead.mid")
        _commit("third")

        result = runner.invoke(cli, ["revert", commit_id])
        assert result.exit_code == 0
        assert "my change" in result.output or "Revert" in result.output

    def test_revert_custom_message(self, repo: pathlib.Path) -> None:
        _write(repo, "base.mid", "base")
        _commit("base")
        _write(repo, "beat.mid")
        _commit("to revert")
        commit_id = _head_id(repo)
        _write(repo, "lead.mid")
        _commit("third")

        result = runner.invoke(cli, ["revert", "-m", "undo that change", commit_id])
        assert result.exit_code == 0, result.output
        assert "undo that change" in result.output

    def test_revert_no_commit_flag(self, repo: pathlib.Path) -> None:
        _write(repo, "base.mid", "base")
        _commit("base")

        _write(repo, "beat.mid")
        _commit("second")
        second_id = _head_id(repo)

        _write(repo, "lead.mid")
        _commit("third")

        result = runner.invoke(cli, ["revert", "--no-commit", second_id])
        assert result.exit_code == 0, result.output
        assert "state" in result.output or "applied" in result.output.lower()
        # HEAD should not have advanced
        assert _head_id(repo) != second_id  # third is still HEAD

    def test_revert_unknown_ref_errors(self, repo: pathlib.Path) -> None:
        _write(repo, "beat.mid")
        _commit("only")
        result = runner.invoke(cli, ["revert", "deadbeef"])
        assert result.exit_code != 0

    def test_revert_root_commit_errors(self, repo: pathlib.Path) -> None:
        _write(repo, "beat.mid")
        _commit("root")
        root_id = _head_id(repo)

        result = runner.invoke(cli, ["revert", root_id])
        assert result.exit_code != 0
        assert "root" in result.output.lower() or "parent" in result.output.lower()

    def test_revert_removes_added_file(self, repo: pathlib.Path) -> None:
        _write(repo, "beat.mid", "base")
        _commit("base")

        _write(repo, "lead.mid", "added")
        _commit("add lead")
        lead_commit = _head_id(repo)

        runner.invoke(cli, ["revert", lead_commit])
        assert not (repo / "state" / "lead.mid").exists()

"""End-to-end CLI workflow tests — init, commit, log, status, branch, merge."""
from __future__ import annotations

import pathlib

import pytest
from typer.testing import CliRunner

from muse.cli.app import cli

runner = CliRunner()


@pytest.fixture
def repo(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    """Initialise a fresh Muse repo in tmp_path and set it as cwd."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MUSE_REPO_ROOT", str(tmp_path))
    result = runner.invoke(cli, ["init"])
    assert result.exit_code == 0, result.output
    return tmp_path


def _write(repo: pathlib.Path, filename: str, content: str = "data") -> None:
    (repo / "muse-work" / filename).write_text(content)


class TestInit:
    def test_creates_muse_dir(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(cli, ["init"])
        assert result.exit_code == 0
        assert (tmp_path / ".muse").is_dir()
        assert (tmp_path / ".muse" / "HEAD").exists()
        assert (tmp_path / ".muse" / "repo.json").exists()
        assert (tmp_path / "muse-work").is_dir()

    def test_reinit_requires_force(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        runner.invoke(cli, ["init"])
        result = runner.invoke(cli, ["init"])
        assert result.exit_code != 0
        assert "force" in result.output.lower()

    def test_bare_repo(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(cli, ["init", "--bare"])
        assert result.exit_code == 0
        assert not (tmp_path / "muse-work").exists()


class TestCommit:
    def test_commit_with_message(self, repo: pathlib.Path) -> None:
        _write(repo, "beat.mid")
        result = runner.invoke(cli, ["commit", "-m", "Initial commit"])
        assert result.exit_code == 0
        assert "Initial commit" in result.output

    def test_nothing_to_commit(self, repo: pathlib.Path) -> None:
        _write(repo, "beat.mid")
        runner.invoke(cli, ["commit", "-m", "First"])
        result = runner.invoke(cli, ["commit", "-m", "Second"])
        assert result.exit_code == 0
        assert "Nothing to commit" in result.output

    def test_allow_empty(self, repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["commit", "-m", "Empty", "--allow-empty"])
        assert result.exit_code == 0

    def test_message_required(self, repo: pathlib.Path) -> None:
        _write(repo, "beat.mid")
        result = runner.invoke(cli, ["commit"])
        assert result.exit_code != 0

    def test_section_metadata(self, repo: pathlib.Path) -> None:
        _write(repo, "beat.mid")
        result = runner.invoke(cli, ["commit", "-m", "Chorus take", "--section", "chorus"])
        assert result.exit_code == 0

        from muse.core.store import get_head_commit_id, read_commit
        import json
        repo_id = json.loads((repo / ".muse" / "repo.json").read_text())["repo_id"]
        commit_id = get_head_commit_id(repo, "main")
        commit = read_commit(repo, commit_id)
        assert commit is not None
        assert commit.metadata.get("section") == "chorus"


class TestStatus:
    def test_clean_after_commit(self, repo: pathlib.Path) -> None:
        _write(repo, "beat.mid")
        runner.invoke(cli, ["commit", "-m", "First"])
        result = runner.invoke(cli, ["status"])
        assert result.exit_code == 0
        assert "Nothing to commit" in result.output

    def test_shows_new_file(self, repo: pathlib.Path) -> None:
        _write(repo, "beat.mid")
        result = runner.invoke(cli, ["status"])
        assert result.exit_code == 0
        assert "beat.mid" in result.output

    def test_short_flag(self, repo: pathlib.Path) -> None:
        _write(repo, "beat.mid")
        result = runner.invoke(cli, ["status", "--short"])
        assert result.exit_code == 0
        assert "A " in result.output

    def test_porcelain_flag(self, repo: pathlib.Path) -> None:
        _write(repo, "beat.mid")
        result = runner.invoke(cli, ["status", "--porcelain"])
        assert result.exit_code == 0
        assert "## main" in result.output


class TestLog:
    def test_empty_log(self, repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["log"])
        assert result.exit_code == 0
        assert "no commits" in result.output

    def test_shows_commit(self, repo: pathlib.Path) -> None:
        _write(repo, "beat.mid")
        runner.invoke(cli, ["commit", "-m", "First take"])
        result = runner.invoke(cli, ["log"])
        assert result.exit_code == 0
        assert "First take" in result.output

    def test_oneline(self, repo: pathlib.Path) -> None:
        _write(repo, "beat.mid")
        runner.invoke(cli, ["commit", "-m", "First take"])
        result = runner.invoke(cli, ["log", "--oneline"])
        assert result.exit_code == 0
        assert "First take" in result.output
        assert "Author:" not in result.output

    def test_multiple_commits_newest_first(self, repo: pathlib.Path) -> None:
        _write(repo, "a.mid")
        runner.invoke(cli, ["commit", "-m", "First"])
        _write(repo, "b.mid")
        runner.invoke(cli, ["commit", "-m", "Second"])
        result = runner.invoke(cli, ["log", "--oneline"])
        lines = [l for l in result.output.strip().splitlines() if l.strip()]
        assert "Second" in lines[0]
        assert "First" in lines[1]


class TestBranch:
    def test_list_shows_main(self, repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["branch"])
        assert result.exit_code == 0
        assert "main" in result.output
        assert "* " in result.output

    def test_create_branch(self, repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["branch", "feature/chorus"])
        assert result.exit_code == 0
        result = runner.invoke(cli, ["branch"])
        assert "feature/chorus" in result.output

    def test_delete_branch(self, repo: pathlib.Path) -> None:
        runner.invoke(cli, ["branch", "feature/x"])
        result = runner.invoke(cli, ["branch", "--delete", "feature/x"])
        assert result.exit_code == 0
        result = runner.invoke(cli, ["branch"])
        assert "feature/x" not in result.output


class TestCheckout:
    def test_create_and_switch(self, repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["checkout", "-b", "feature/chorus"])
        assert result.exit_code == 0
        assert "feature/chorus" in result.output
        status = runner.invoke(cli, ["status"])
        assert "feature/chorus" in status.output

    def test_switch_existing_branch(self, repo: pathlib.Path) -> None:
        runner.invoke(cli, ["checkout", "-b", "feature/chorus"])
        runner.invoke(cli, ["checkout", "main"])
        result = runner.invoke(cli, ["status"])
        assert "main" in result.output

    def test_already_on_branch(self, repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["checkout", "main"])
        assert result.exit_code == 0
        assert "Already on" in result.output


class TestMerge:
    def test_fast_forward(self, repo: pathlib.Path) -> None:
        _write(repo, "verse.mid")
        runner.invoke(cli, ["commit", "-m", "Verse"])
        runner.invoke(cli, ["checkout", "-b", "feature/chorus"])
        _write(repo, "chorus.mid")
        runner.invoke(cli, ["commit", "-m", "Add chorus"])
        runner.invoke(cli, ["checkout", "main"])
        result = runner.invoke(cli, ["merge", "feature/chorus"])
        assert result.exit_code == 0
        assert "Fast-forward" in result.output

    def test_clean_three_way_merge(self, repo: pathlib.Path) -> None:
        _write(repo, "base.mid")
        runner.invoke(cli, ["commit", "-m", "Base"])
        runner.invoke(cli, ["checkout", "-b", "branch-a"])
        _write(repo, "a.mid")
        runner.invoke(cli, ["commit", "-m", "Add A"])
        runner.invoke(cli, ["checkout", "main"])
        runner.invoke(cli, ["checkout", "-b", "branch-b"])
        _write(repo, "b.mid")
        runner.invoke(cli, ["commit", "-m", "Add B"])
        runner.invoke(cli, ["checkout", "main"])
        result = runner.invoke(cli, ["merge", "branch-a"])
        assert result.exit_code == 0

    def test_cannot_merge_self(self, repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["merge", "main"])
        assert result.exit_code != 0


class TestDiff:
    def test_no_diff_clean(self, repo: pathlib.Path) -> None:
        _write(repo, "beat.mid")
        runner.invoke(cli, ["commit", "-m", "First"])
        result = runner.invoke(cli, ["diff"])
        assert result.exit_code == 0
        assert "No differences" in result.output

    def test_shows_new_file(self, repo: pathlib.Path) -> None:
        _write(repo, "beat.mid")
        runner.invoke(cli, ["commit", "-m", "First"])
        _write(repo, "lead.mid")
        result = runner.invoke(cli, ["diff"])
        assert result.exit_code == 0
        assert "lead.mid" in result.output


class TestTag:
    def test_add_and_list(self, repo: pathlib.Path) -> None:
        _write(repo, "beat.mid")
        runner.invoke(cli, ["commit", "-m", "Tagged take"])
        result = runner.invoke(cli, ["tag", "add", "emotion:joyful"])
        assert result.exit_code == 0
        result = runner.invoke(cli, ["tag", "list"])
        assert "emotion:joyful" in result.output


class TestStash:
    def test_stash_and_pop(self, repo: pathlib.Path) -> None:
        _write(repo, "beat.mid")
        runner.invoke(cli, ["commit", "-m", "First"])
        _write(repo, "lead.mid")
        result = runner.invoke(cli, ["stash"])
        assert result.exit_code == 0
        assert not (repo / "muse-work" / "lead.mid").exists()
        result = runner.invoke(cli, ["stash", "pop"])
        assert result.exit_code == 0
        assert (repo / "muse-work" / "lead.mid").exists()

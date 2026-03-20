"""Tests targeting specific coverage gaps in checkout, tag, commit, diff, stash, branch."""

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


def _commit(msg: str = "commit") -> str | None:
    result = runner.invoke(cli, ["commit", "-m", msg])
    assert result.exit_code == 0, result.output
    return get_head_commit_id(pathlib.Path("."), "main")


# ---------------------------------------------------------------------------
# checkout gaps
# ---------------------------------------------------------------------------


class TestCheckoutGaps:
    def test_create_branch_already_exists_errors(self, repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["checkout", "-b", "main"])
        assert result.exit_code != 0
        assert "already exists" in result.output

    def test_unknown_ref_errors(self, repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["checkout", "no-such-branch-or-commit"])
        assert result.exit_code != 0
        assert "not a branch" in result.output.lower() or "not found" in result.output.lower() or "no-such" in result.output

    def test_checkout_by_commit_id_detaches_head(self, repo: pathlib.Path) -> None:
        _write(repo, "beat.mid")
        result = runner.invoke(cli, ["commit", "-m", "root"])
        assert result.exit_code == 0
        commit_id = get_head_commit_id(repo, "main")
        assert commit_id is not None

        _write(repo, "lead.mid")
        runner.invoke(cli, ["commit", "-m", "second"])

        result = runner.invoke(cli, ["checkout", commit_id])
        assert result.exit_code == 0
        assert "HEAD is now at" in result.output

    def test_checkout_restores_workdir_to_target_snapshot(self, repo: pathlib.Path) -> None:
        _write(repo, "beat.mid", "v1")
        result = runner.invoke(cli, ["commit", "-m", "first"])
        assert result.exit_code == 0
        first_id = get_head_commit_id(repo, "main")
        assert first_id is not None

        _write(repo, "lead.mid", "new")
        runner.invoke(cli, ["commit", "-m", "second"])

        runner.invoke(cli, ["checkout", first_id])
        assert (repo / "state" / "beat.mid").exists()
        assert not (repo / "state" / "lead.mid").exists()


# ---------------------------------------------------------------------------
# tag gaps
# ---------------------------------------------------------------------------


class TestTagGaps:
    def test_tag_add_unknown_ref_errors(self, repo: pathlib.Path) -> None:
        _write(repo, "beat.mid")
        runner.invoke(cli, ["commit", "-m", "base"])
        result = runner.invoke(cli, ["tag", "add", "emotion:joyful", "deadbeef"])
        assert result.exit_code != 0

    def test_tag_list_for_specific_commit(self, repo: pathlib.Path) -> None:
        _write(repo, "beat.mid")
        runner.invoke(cli, ["commit", "-m", "base"])
        commit_id = get_head_commit_id(repo, "main")
        assert commit_id is not None

        runner.invoke(cli, ["tag", "add", "emotion:joyful", commit_id[:8]])
        result = runner.invoke(cli, ["tag", "list", commit_id[:8]])
        assert result.exit_code == 0
        assert "joyful" in result.output

    def test_tag_list_for_unknown_ref_errors(self, repo: pathlib.Path) -> None:
        _write(repo, "beat.mid")
        runner.invoke(cli, ["commit", "-m", "base"])
        result = runner.invoke(cli, ["tag", "list", "deadbeef"])
        assert result.exit_code != 0

    def test_tag_list_all_shows_all_tags(self, repo: pathlib.Path) -> None:
        _write(repo, "beat.mid")
        runner.invoke(cli, ["commit", "-m", "base"])
        commit_id = get_head_commit_id(repo, "main")
        assert commit_id is not None

        runner.invoke(cli, ["tag", "add", "section:verse", commit_id[:8]])
        runner.invoke(cli, ["tag", "add", "emotion:joyful", commit_id[:8]])
        result = runner.invoke(cli, ["tag", "list"])
        assert result.exit_code == 0
        assert "section:verse" in result.output
        assert "emotion:joyful" in result.output


# ---------------------------------------------------------------------------
# commit gaps
# ---------------------------------------------------------------------------


class TestCommitGaps:
    def test_no_message_without_allow_empty_errors(self, repo: pathlib.Path) -> None:
        _write(repo, "beat.mid")
        result = runner.invoke(cli, ["commit"])
        assert result.exit_code != 0

    def test_no_muse_work_dir_errors(self, repo: pathlib.Path) -> None:
        import shutil
        shutil.rmtree(repo / "state")
        result = runner.invoke(cli, ["commit", "-m", "no workdir"])
        assert result.exit_code != 0
        assert "state" in result.output

    def test_empty_workdir_without_allow_empty_errors(self, repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["commit", "-m", "empty"])
        assert result.exit_code != 0

    def test_nothing_to_commit_clean_tree(self, repo: pathlib.Path) -> None:
        _write(repo, "beat.mid")
        runner.invoke(cli, ["commit", "-m", "first"])
        result = runner.invoke(cli, ["commit", "-m", "second"])
        assert result.exit_code == 0
        assert "Nothing to commit" in result.output or "clean" in result.output

    def test_commit_with_pending_conflicts_errors(self, repo: pathlib.Path) -> None:
        _write(repo, "beat.mid", "base")
        runner.invoke(cli, ["commit", "-m", "base"])

        runner.invoke(cli, ["branch", "feature"])
        runner.invoke(cli, ["checkout", "feature"])
        _write(repo, "beat.mid", "feature-v")
        runner.invoke(cli, ["commit", "-m", "feature changes"])

        runner.invoke(cli, ["checkout", "main"])
        _write(repo, "beat.mid", "main-v")
        runner.invoke(cli, ["commit", "-m", "main changes"])

        runner.invoke(cli, ["merge", "feature"])

        # Now try to commit — should fail because of unresolved conflicts
        _write(repo, "new.mid")
        result = runner.invoke(cli, ["commit", "-m", "during conflict"])
        assert result.exit_code != 0
        assert "conflict" in result.output.lower()


# ---------------------------------------------------------------------------
# diff gaps
# ---------------------------------------------------------------------------


class TestDiffGaps:
    def test_diff_two_commits(self, repo: pathlib.Path) -> None:
        _write(repo, "a.mid", "v1")
        runner.invoke(cli, ["commit", "-m", "first"])
        first_id = get_head_commit_id(repo, "main")

        _write(repo, "a.mid", "v2")
        runner.invoke(cli, ["commit", "-m", "second"])
        second_id = get_head_commit_id(repo, "main")

        assert first_id is not None
        assert second_id is not None
        result = runner.invoke(cli, ["diff", first_id, second_id])
        assert result.exit_code == 0
        assert "a.mid" in result.output

    def test_diff_commit_vs_head(self, repo: pathlib.Path) -> None:
        _write(repo, "a.mid", "v1")
        runner.invoke(cli, ["commit", "-m", "first"])
        first_id = get_head_commit_id(repo, "main")

        _write(repo, "a.mid", "v2")
        runner.invoke(cli, ["commit", "-m", "second"])
        assert first_id is not None

        result = runner.invoke(cli, ["diff", first_id])
        assert result.exit_code == 0
        assert "a.mid" in result.output

    def test_diff_stat_flag(self, repo: pathlib.Path) -> None:
        _write(repo, "a.mid")
        runner.invoke(cli, ["commit", "-m", "base"])
        _write(repo, "b.mid")
        result = runner.invoke(cli, ["diff", "--stat"])
        assert result.exit_code == 0
        # --stat prints the structured delta summary line.
        assert "added" in result.output or "No differences" in result.output


# ---------------------------------------------------------------------------
# stash gaps
# ---------------------------------------------------------------------------


class TestStashGaps:
    def test_stash_pop_restores_files(self, repo: pathlib.Path) -> None:
        _write(repo, "beat.mid")
        runner.invoke(cli, ["commit", "-m", "base"])
        _write(repo, "unsaved.mid", "wip")

        runner.invoke(cli, ["stash"])
        assert not (repo / "state" / "unsaved.mid").exists()

        runner.invoke(cli, ["stash", "pop"])
        assert (repo / "state" / "unsaved.mid").exists()

    def test_stash_drop_removes_entry(self, repo: pathlib.Path) -> None:
        _write(repo, "beat.mid")
        runner.invoke(cli, ["commit", "-m", "base"])
        _write(repo, "unsaved.mid", "wip")

        runner.invoke(cli, ["stash"])
        result = runner.invoke(cli, ["stash", "drop"])
        assert result.exit_code == 0

        result = runner.invoke(cli, ["stash", "list"])
        assert "No stash entries" in result.output

    def test_stash_pop_empty_errors(self, repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["stash", "pop"])
        assert result.exit_code != 0
        assert "No stash" in result.output

    def test_stash_drop_empty_errors(self, repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["stash", "drop"])
        assert result.exit_code != 0

    def test_stash_nothing_to_stash(self, repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["stash"])
        assert result.exit_code == 0
        assert "Nothing to stash" in result.output


# ---------------------------------------------------------------------------
# branch gaps
# ---------------------------------------------------------------------------


class TestBranchGaps:
    def test_delete_branch_with_d_flag(self, repo: pathlib.Path) -> None:
        runner.invoke(cli, ["branch", "to-delete"])
        result = runner.invoke(cli, ["branch", "-d", "to-delete"])
        assert result.exit_code == 0

    def test_delete_current_branch_errors(self, repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["branch", "-d", "main"])
        assert result.exit_code != 0
        assert "current" in result.output.lower() or "main" in result.output

    def test_delete_nonexistent_branch_errors(self, repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["branch", "-d", "no-such-branch"])
        assert result.exit_code != 0

    def test_create_branch_with_b_flag(self, repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["branch", "new-feature"])
        assert result.exit_code == 0

    def test_branch_with_slash_shown_in_list(self, repo: pathlib.Path) -> None:
        runner.invoke(cli, ["branch", "feature/my-thing"])
        result = runner.invoke(cli, ["branch"])
        assert "feature/my-thing" in result.output

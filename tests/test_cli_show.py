"""Tests for muse show — inspect a commit's metadata, diff, and files."""

import json
import pathlib

import pytest
from typer.testing import CliRunner

from muse.cli.app import cli

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


def _commit(msg: str = "initial", **flags: str) -> str:
    args = ["commit", "-m", msg]
    for k, v in flags.items():
        args += [f"--{k}", v]
    result = runner.invoke(cli, args)
    assert result.exit_code == 0, result.output
    # output: "[main abcd1234] msg" → strip trailing ] from token
    return result.output.split()[1].rstrip("]")


class TestShowHead:
    def test_shows_commit_id(self, repo: pathlib.Path) -> None:
        _write(repo, "beat.mid")
        _commit("initial commit")
        result = runner.invoke(cli, ["show"])
        assert result.exit_code == 0, result.output
        assert "commit" in result.output

    def test_shows_message(self, repo: pathlib.Path) -> None:
        _write(repo, "beat.mid")
        _commit("my special message")
        result = runner.invoke(cli, ["show"])
        assert result.exit_code == 0
        assert "my special message" in result.output

    def test_shows_date(self, repo: pathlib.Path) -> None:
        _write(repo, "beat.mid")
        _commit("dated commit")
        result = runner.invoke(cli, ["show"])
        assert result.exit_code == 0
        assert "Date:" in result.output

    def test_shows_author(self, repo: pathlib.Path) -> None:
        _write(repo, "beat.mid")
        runner.invoke(cli, ["commit", "-m", "authored", "--author", "Gabriel"])
        result = runner.invoke(cli, ["show"])
        assert result.exit_code == 0
        assert "Gabriel" in result.output

    def test_no_author_line_when_empty(self, repo: pathlib.Path) -> None:
        _write(repo, "beat.mid")
        _commit("no author")
        result = runner.invoke(cli, ["show"])
        assert result.exit_code == 0
        assert "Author:" not in result.output


class TestShowStat:
    def test_shows_added_file_by_default(self, repo: pathlib.Path) -> None:
        _write(repo, "beat.mid")
        _commit("add beat")
        result = runner.invoke(cli, ["show"])
        assert result.exit_code == 0
        assert "beat.mid" in result.output
        assert "+" in result.output

    def test_no_stat_flag_hides_files(self, repo: pathlib.Path) -> None:
        _write(repo, "beat.mid")
        _commit("add beat")
        result = runner.invoke(cli, ["show", "--no-stat"])
        assert result.exit_code == 0
        assert "beat.mid" not in result.output

    def test_shows_modified_file(self, repo: pathlib.Path) -> None:
        _write(repo, "beat.mid", "v1")
        _commit("v1")
        _write(repo, "beat.mid", "v2")
        _commit("v2")
        result = runner.invoke(cli, ["show"])
        assert result.exit_code == 0
        assert "beat.mid" in result.output

    def test_file_change_count(self, repo: pathlib.Path) -> None:
        _write(repo, "a.mid")
        _write(repo, "b.mid")
        _commit("two files")
        result = runner.invoke(cli, ["show"])
        assert result.exit_code == 0
        assert "file(s) changed" in result.output

    def test_no_files_changed_no_count_line(self, repo: pathlib.Path) -> None:
        _write(repo, "beat.mid", "v1")
        _commit("v1")
        _write(repo, "beat.mid", "v1")
        result = runner.invoke(cli, ["commit", "--allow-empty"])
        # empty commit — stat block should show no files changed
        result2 = runner.invoke(cli, ["show"])
        assert result2.exit_code == 0


class TestShowMetadata:
    def test_shows_section_metadata(self, repo: pathlib.Path) -> None:
        _write(repo, "beat.mid")
        runner.invoke(cli, ["commit", "-m", "verse", "--section", "verse"])
        result = runner.invoke(cli, ["show"])
        assert result.exit_code == 0
        assert "section" in result.output
        assert "verse" in result.output

    def test_shows_track_and_emotion(self, repo: pathlib.Path) -> None:
        _write(repo, "beat.mid")
        runner.invoke(cli, ["commit", "-m", "drums", "--track", "drums", "--emotion", "joyful"])
        result = runner.invoke(cli, ["show"])
        assert result.exit_code == 0
        assert "track" in result.output
        assert "emotion" in result.output


class TestShowRef:
    def test_show_specific_commit(self, repo: pathlib.Path) -> None:
        _write(repo, "beat.mid")
        short = _commit("first")
        _write(repo, "lead.mid")
        _commit("second")
        # show the first commit by prefix
        result = runner.invoke(cli, ["show", short])
        assert result.exit_code == 0
        assert "first" in result.output

    def test_show_unknown_ref_errors(self, repo: pathlib.Path) -> None:
        _write(repo, "beat.mid")
        _commit("only")
        result = runner.invoke(cli, ["show", "deadbeef"])
        assert result.exit_code != 0
        assert "not found" in result.output.lower() or "deadbeef" in result.output

    def test_show_no_commits_errors(self, repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["show"])
        assert result.exit_code != 0


class TestShowParent:
    def test_shows_parent_after_second_commit(self, repo: pathlib.Path) -> None:
        _write(repo, "beat.mid")
        _commit("first")
        _write(repo, "lead.mid")
        _commit("second")
        result = runner.invoke(cli, ["show"])
        assert result.exit_code == 0
        assert "Parent:" in result.output

    def test_root_commit_has_no_parent_line(self, repo: pathlib.Path) -> None:
        _write(repo, "beat.mid")
        short = _commit("root commit")
        result = runner.invoke(cli, ["show", short])
        assert result.exit_code == 0
        assert "Parent:" not in result.output


class TestShowJson:
    def test_json_output_is_valid(self, repo: pathlib.Path) -> None:
        _write(repo, "beat.mid")
        _commit("json test")
        result = runner.invoke(cli, ["show", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "commit_id" in data
        assert "message" in data

    def test_json_contains_message(self, repo: pathlib.Path) -> None:
        _write(repo, "beat.mid")
        _commit("the message")
        result = runner.invoke(cli, ["show", "--json"])
        data = json.loads(result.output)
        assert data["message"] == "the message"

    def test_json_with_stat_includes_file_lists(self, repo: pathlib.Path) -> None:
        _write(repo, "beat.mid")
        _commit("add beat")
        result = runner.invoke(cli, ["show", "--json", "--stat"])
        data = json.loads(result.output)
        assert "files_added" in data
        assert "beat.mid" in data["files_added"]

    def test_json_no_stat_excludes_file_lists(self, repo: pathlib.Path) -> None:
        _write(repo, "beat.mid")
        _commit("add beat")
        result = runner.invoke(cli, ["show", "--json", "--no-stat"])
        data = json.loads(result.output)
        assert "files_added" not in data

    def test_json_stat_shows_removed_file(self, repo: pathlib.Path) -> None:
        _write(repo, "beat.mid", "v1")
        _commit("add")
        (repo / "state" / "beat.mid").unlink()
        _write(repo, "lead.mid", "new")
        _commit("swap")
        result = runner.invoke(cli, ["show", "--json", "--stat"])
        data = json.loads(result.output)
        assert "beat.mid" in data["files_removed"]

    def test_json_stat_shows_modified_file(self, repo: pathlib.Path) -> None:
        _write(repo, "beat.mid", "v1")
        _commit("v1")
        _write(repo, "beat.mid", "v2")
        _commit("v2")
        result = runner.invoke(cli, ["show", "--json", "--stat"])
        data = json.loads(result.output)
        assert "beat.mid" in data["files_modified"]

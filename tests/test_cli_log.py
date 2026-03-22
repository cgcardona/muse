"""Tests for muse log — commit history display and filters."""

import pathlib

import pytest
from tests.cli_test_helper import CliRunner

cli = None  # argparse migration — CliRunner ignores this arg
from muse.cli.commands.log import _parse_date

runner = CliRunner()


@pytest.fixture
def repo(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MUSE_REPO_ROOT", str(tmp_path))
    result = runner.invoke(cli, ["init"])
    assert result.exit_code == 0, result.output
    return tmp_path


def _write(repo: pathlib.Path, filename: str, content: str = "data") -> None:
    (repo / filename).write_text(content)


def _commit(msg: str, **flags: str) -> None:
    args = ["commit", "-m", msg]
    for k, v in flags.items():
        args += [f"--{k}", v]
    result = runner.invoke(cli, args)
    assert result.exit_code == 0, result.output


# ---------------------------------------------------------------------------
# _parse_date unit tests
# ---------------------------------------------------------------------------


class TestParseDate:
    def test_today(self) -> None:
        from datetime import datetime, timezone
        dt = _parse_date("today")
        assert dt.tzinfo == timezone.utc
        now = datetime.now(timezone.utc)
        assert dt.date() == now.date()

    def test_yesterday(self) -> None:
        from datetime import datetime, timedelta, timezone
        dt = _parse_date("yesterday")
        expected = (datetime.now(timezone.utc) - timedelta(days=1)).date()
        assert dt.date() == expected

    def test_n_days_ago(self) -> None:
        from datetime import datetime, timedelta, timezone
        dt = _parse_date("3 days ago")
        expected = (datetime.now(timezone.utc) - timedelta(days=3)).date()
        assert dt.date() == expected

    def test_n_weeks_ago(self) -> None:
        from datetime import datetime, timedelta, timezone
        dt = _parse_date("2 weeks ago")
        expected = (datetime.now(timezone.utc) - timedelta(weeks=2)).date()
        assert dt.date() == expected

    def test_n_months_ago(self) -> None:
        from datetime import datetime, timedelta, timezone
        dt = _parse_date("1 month ago")
        expected = (datetime.now(timezone.utc) - timedelta(days=30)).date()
        assert dt.date() == expected

    def test_n_years_ago(self) -> None:
        from datetime import datetime, timedelta, timezone
        dt = _parse_date("1 year ago")
        expected = (datetime.now(timezone.utc) - timedelta(days=365)).date()
        assert dt.date() == expected

    def test_iso_date(self) -> None:
        dt = _parse_date("2025-01-15")
        assert dt.year == 2025
        assert dt.month == 1
        assert dt.day == 15

    def test_iso_datetime(self) -> None:
        dt = _parse_date("2025-01-15T10:30:00")
        assert dt.hour == 10
        assert dt.minute == 30

    def test_iso_datetime_space(self) -> None:
        dt = _parse_date("2025-01-15 10:30:00")
        assert dt.hour == 10

    def test_invalid_raises(self) -> None:
        with pytest.raises(ValueError, match="Cannot parse date"):
            _parse_date("not-a-date")


# ---------------------------------------------------------------------------
# Log output modes
# ---------------------------------------------------------------------------


class TestLogEmpty:
    def test_empty_repo_shows_no_commits(self, repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["log"])
        assert result.exit_code == 0
        assert "no commits" in result.output.lower() or "(no commits)" in result.output


class TestLogDefault:
    def test_shows_commit_line(self, repo: pathlib.Path) -> None:
        _write(repo, "beat.mid")
        _commit("first commit")
        result = runner.invoke(cli, ["log"])
        assert result.exit_code == 0
        assert "commit" in result.output
        assert "first commit" in result.output

    def test_shows_date(self, repo: pathlib.Path) -> None:
        _write(repo, "beat.mid")
        _commit("dated")
        result = runner.invoke(cli, ["log"])
        assert "Date:" in result.output

    def test_shows_author_when_set(self, repo: pathlib.Path) -> None:
        _write(repo, "beat.mid")
        _commit("authored", author="Gabriel")
        result = runner.invoke(cli, ["log"])
        assert "Gabriel" in result.output

    def test_multiple_commits_newest_first(self, repo: pathlib.Path) -> None:
        _write(repo, "a.mid")
        _commit("first")
        _write(repo, "b.mid")
        _commit("second")
        result = runner.invoke(cli, ["log"])
        assert result.output.index("second") < result.output.index("first")

    def test_shows_head_label(self, repo: pathlib.Path) -> None:
        _write(repo, "beat.mid")
        _commit("only")
        result = runner.invoke(cli, ["log"])
        assert "HEAD" in result.output

    def test_shows_metadata(self, repo: pathlib.Path) -> None:
        _write(repo, "beat.mid")
        _commit("versed", section="verse")
        result = runner.invoke(cli, ["log"])
        assert "verse" in result.output
        assert "Meta:" in result.output


class TestLogOneline:
    def test_one_line_per_commit(self, repo: pathlib.Path) -> None:
        _write(repo, "a.mid")
        _commit("first")
        _write(repo, "b.mid")
        _commit("second")
        result = runner.invoke(cli, ["log", "--oneline"])
        assert result.exit_code == 0
        lines = [l for l in result.output.strip().splitlines() if l.strip()]
        assert len(lines) == 2

    def test_oneline_format(self, repo: pathlib.Path) -> None:
        _write(repo, "beat.mid")
        _commit("a message")
        result = runner.invoke(cli, ["log", "--oneline"])
        # short id + message on one line
        assert "a message" in result.output
        lines = [l for l in result.output.strip().splitlines() if l]
        assert len(lines) == 1

    def test_oneline_shows_head_label(self, repo: pathlib.Path) -> None:
        _write(repo, "beat.mid")
        _commit("only")
        result = runner.invoke(cli, ["log", "--oneline"])
        assert "HEAD" in result.output


class TestLogGraph:
    def test_graph_prefix(self, repo: pathlib.Path) -> None:
        _write(repo, "beat.mid")
        _commit("graphed")
        result = runner.invoke(cli, ["log", "--graph"])
        assert result.exit_code == 0
        assert "* " in result.output

    def test_graph_shows_message(self, repo: pathlib.Path) -> None:
        _write(repo, "beat.mid")
        _commit("graph msg")
        result = runner.invoke(cli, ["log", "--graph"])
        assert "graph msg" in result.output


class TestLogStat:
    def test_stat_shows_added_files(self, repo: pathlib.Path) -> None:
        _write(repo, "beat.mid")
        _commit("add beat")
        result = runner.invoke(cli, ["log", "--stat"])
        assert result.exit_code == 0
        assert "beat.mid" in result.output
        assert "+" in result.output

    def test_stat_shows_summary_line(self, repo: pathlib.Path) -> None:
        _write(repo, "beat.mid")
        _commit("add")
        result = runner.invoke(cli, ["log", "--stat"])
        assert "added" in result.output

    def test_patch_shows_files(self, repo: pathlib.Path) -> None:
        _write(repo, "beat.mid")
        _commit("patched")
        result = runner.invoke(cli, ["log", "--patch"])
        assert result.exit_code == 0
        assert "beat.mid" in result.output


class TestLogFilters:
    def test_limit_n(self, repo: pathlib.Path) -> None:
        for i in range(5):
            _write(repo, f"f{i}.mid", str(i))
            _commit(f"msg-{i}")
        result = runner.invoke(cli, ["log", "-n", "2"])
        assert result.exit_code == 0
        # With limit 2, we should see the 2 newest but not the oldest
        assert "msg-0" not in result.output
        assert "msg-1" not in result.output
        assert "msg-2" not in result.output

    def test_filter_author(self, repo: pathlib.Path) -> None:
        _write(repo, "a.mid")
        _commit("by gabriel", author="Gabriel")
        _write(repo, "b.mid")
        _commit("by alice", author="Alice")
        result = runner.invoke(cli, ["log", "--author", "Gabriel"])
        assert result.exit_code == 0
        assert "by gabriel" in result.output
        assert "by alice" not in result.output

    def test_filter_author_case_insensitive(self, repo: pathlib.Path) -> None:
        _write(repo, "a.mid")
        _commit("authored", author="Gabriel")
        result = runner.invoke(cli, ["log", "--author", "gabriel"])
        assert "authored" in result.output

    def test_filter_section(self, repo: pathlib.Path) -> None:
        _write(repo, "a.mid")
        _commit("verse part", section="verse")
        _write(repo, "b.mid")
        _commit("chorus part", section="chorus")
        result = runner.invoke(cli, ["log", "--section", "verse"])
        assert result.exit_code == 0
        assert "verse part" in result.output
        assert "chorus part" not in result.output

    def test_filter_track(self, repo: pathlib.Path) -> None:
        _write(repo, "a.mid")
        _commit("drums commit", track="drums")
        _write(repo, "b.mid")
        _commit("bass commit", track="bass")
        result = runner.invoke(cli, ["log", "--track", "drums"])
        assert "drums commit" in result.output
        assert "bass commit" not in result.output

    def test_filter_emotion(self, repo: pathlib.Path) -> None:
        _write(repo, "a.mid")
        _commit("happy commit", emotion="joyful")
        _write(repo, "b.mid")
        _commit("sad commit", emotion="melancholic")
        result = runner.invoke(cli, ["log", "--emotion", "joyful"])
        assert "happy commit" in result.output
        assert "sad commit" not in result.output

    def test_filter_since_future_returns_nothing(self, repo: pathlib.Path) -> None:
        _write(repo, "a.mid")
        _commit("old commit")
        result = runner.invoke(cli, ["log", "--since", "2099-01-01"])
        assert result.exit_code == 0
        assert "no commits" in result.output.lower() or "(no commits)" in result.output

    def test_filter_until_past_returns_nothing(self, repo: pathlib.Path) -> None:
        _write(repo, "a.mid")
        _commit("recent commit")
        result = runner.invoke(cli, ["log", "--until", "2000-01-01"])
        assert result.exit_code == 0
        assert "no commits" in result.output.lower() or "(no commits)" in result.output

    def test_no_matches_shows_no_commits(self, repo: pathlib.Path) -> None:
        _write(repo, "a.mid")
        _commit("only commit", author="Gabriel")
        result = runner.invoke(cli, ["log", "--author", "nobody"])
        assert "(no commits)" in result.output

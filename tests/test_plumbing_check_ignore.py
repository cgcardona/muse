"""Tests for ``muse plumbing check-ignore``.

Verifies pattern evaluation (global + domain sections), last-match-wins
semantics, negation rules, quiet mode exit codes, matching-pattern reporting,
and text-format output.
"""

from __future__ import annotations

import json
import pathlib

from tests.cli_test_helper import CliRunner

cli = None  # argparse migration — CliRunner ignores this arg
from muse.core.errors import ExitCode

runner = CliRunner()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _init_repo(path: pathlib.Path, domain: str = "midi") -> pathlib.Path:
    muse = path / ".muse"
    muse.mkdir(parents=True, exist_ok=True)
    (muse / "commits").mkdir(exist_ok=True)
    (muse / "snapshots").mkdir(exist_ok=True)
    (muse / "objects").mkdir(exist_ok=True)
    (muse / "refs" / "heads").mkdir(parents=True, exist_ok=True)
    (muse / "HEAD").write_text("ref: refs/heads/main", encoding="utf-8")
    (muse / "repo.json").write_text(
        json.dumps({"repo_id": "test-repo", "domain": domain}), encoding="utf-8"
    )
    return path


def _env(repo: pathlib.Path) -> dict[str, str]:
    return {"MUSE_REPO_ROOT": str(repo)}


def _write_ignore(repo: pathlib.Path, content: str) -> None:
    (repo / ".museignore").write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCheckIgnore:
    def test_ignored_path_reports_true(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        _write_ignore(repo, '[global]\npatterns = ["build/"]\n')
        result = runner.invoke(
            cli, ["plumbing", "check-ignore", "build/output.bin"], env=_env(repo)
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        assert data["results"][0]["ignored"] is True

    def test_non_ignored_path_reports_false(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        _write_ignore(repo, '[global]\npatterns = ["build/"]\n')
        result = runner.invoke(
            cli, ["plumbing", "check-ignore", "tracks/drums.mid"], env=_env(repo)
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        assert data["results"][0]["ignored"] is False

    def test_multiple_paths_evaluated_independently(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        _write_ignore(repo, '[global]\npatterns = ["*.bin"]\n')
        result = runner.invoke(
            cli, ["plumbing", "check-ignore", "a.bin", "b.mid", "c.bin"], env=_env(repo)
        )
        assert result.exit_code == 0, result.output
        results = {r["path"]: r["ignored"] for r in json.loads(result.stdout)["results"]}
        assert results["a.bin"] is True
        assert results["b.mid"] is False
        assert results["c.bin"] is True

    def test_no_museignore_means_nothing_is_ignored(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        result = runner.invoke(
            cli, ["plumbing", "check-ignore", "tracks/drums.mid"], env=_env(repo)
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        assert data["results"][0]["ignored"] is False
        assert data["patterns_loaded"] == 0

    def test_negation_rule_unignores_previously_matched_path(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        _write_ignore(repo, '[global]\npatterns = ["*.bin", "!important.bin"]\n')
        result = runner.invoke(
            cli, ["plumbing", "check-ignore", "other.bin", "important.bin"], env=_env(repo)
        )
        assert result.exit_code == 0, result.output
        results = {r["path"]: r["ignored"] for r in json.loads(result.stdout)["results"]}
        assert results["other.bin"] is True
        assert results["important.bin"] is False

    def test_matching_pattern_reported_in_result(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        _write_ignore(repo, '[global]\npatterns = ["build/"]\n')
        result = runner.invoke(
            cli, ["plumbing", "check-ignore", "build/artifact.bin"], env=_env(repo)
        )
        assert result.exit_code == 0, result.output
        r = json.loads(result.stdout)["results"][0]
        assert r["matching_pattern"] == "build/"

    def test_non_matching_path_has_null_pattern(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        _write_ignore(repo, '[global]\npatterns = ["build/"]\n')
        result = runner.invoke(
            cli, ["plumbing", "check-ignore", "tracks/drums.mid"], env=_env(repo)
        )
        assert result.exit_code == 0, result.output
        r = json.loads(result.stdout)["results"][0]
        assert r["matching_pattern"] is None

    def test_domain_specific_patterns_applied(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path, domain="midi")
        _write_ignore(repo, '[global]\npatterns = []\n[domain.midi]\npatterns = ["*.mid"]\n')
        result = runner.invoke(
            cli, ["plumbing", "check-ignore", "track.mid"], env=_env(repo)
        )
        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout)["results"][0]["ignored"] is True

    def test_domain_patterns_not_applied_to_other_domains(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path, domain="code")
        _write_ignore(repo, '[domain.midi]\npatterns = ["*.mid"]\n')
        result = runner.invoke(
            cli, ["plumbing", "check-ignore", "track.mid"], env=_env(repo)
        )
        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout)["results"][0]["ignored"] is False

    def test_quiet_all_ignored_exits_zero(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        _write_ignore(repo, '[global]\npatterns = ["*.bin"]\n')
        result = runner.invoke(
            cli, ["plumbing", "check-ignore", "--quiet", "a.bin", "b.bin"], env=_env(repo)
        )
        assert result.exit_code == 0
        assert result.stdout.strip() == ""

    def test_quiet_any_not_ignored_exits_user_error(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        _write_ignore(repo, '[global]\npatterns = ["*.bin"]\n')
        result = runner.invoke(
            cli, ["plumbing", "check-ignore", "--quiet", "a.bin", "keep.mid"], env=_env(repo)
        )
        assert result.exit_code == ExitCode.USER_ERROR

    def test_text_format_output(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        _write_ignore(repo, '[global]\npatterns = ["*.bin"]\n')
        result = runner.invoke(
            cli, ["plumbing", "check-ignore", "--format", "text", "a.bin", "b.mid"], env=_env(repo)
        )
        assert result.exit_code == 0, result.output
        assert "ignored" in result.stdout
        assert "ok" in result.stdout

    def test_verbose_text_shows_matching_pattern(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        _write_ignore(repo, '[global]\npatterns = ["build/"]\n')
        result = runner.invoke(
            cli,
            ["plumbing", "check-ignore", "--format", "text", "--verbose", "build/x.bin"],
            env=_env(repo),
        )
        assert result.exit_code == 0, result.output
        assert "build/" in result.stdout

    def test_patterns_loaded_count_correct(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        _write_ignore(repo, '[global]\npatterns = ["*.bin", "build/"]\n')
        result = runner.invoke(
            cli, ["plumbing", "check-ignore", "x.bin"], env=_env(repo)
        )
        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout)["patterns_loaded"] == 2

"""Tests for ``muse plumbing check-attr``.

Verifies first-match-wins strategy resolution, priority ordering, dimension
filtering, ``--all-rules`` enumeration, text-format output, and fallback to
``"auto"`` when no rule matches.
"""

from __future__ import annotations

import json
import pathlib

from typer.testing import CliRunner

from muse.cli.app import cli
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


def _write_attrs(repo: pathlib.Path, content: str) -> None:
    (repo / ".museattributes").write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCheckAttr:
    def test_matching_rule_strategy_returned(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        _write_attrs(repo, '[[rules]]\npath = "drums/*"\ndimension = "*"\nstrategy = "ours"\n')
        result = runner.invoke(
            cli, ["plumbing", "check-attr", "drums/kit.mid"], env=_env(repo)
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        assert data["results"][0]["strategy"] == "ours"
        assert data["results"][0]["rule"]["path_pattern"] == "drums/*"

    def test_no_match_returns_auto(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        _write_attrs(repo, '[[rules]]\npath = "drums/*"\ndimension = "*"\nstrategy = "ours"\n')
        result = runner.invoke(
            cli, ["plumbing", "check-attr", "keys/melody.mid"], env=_env(repo)
        )
        assert result.exit_code == 0, result.output
        r = json.loads(result.stdout)["results"][0]
        assert r["strategy"] == "auto"
        assert r["rule"] is None

    def test_no_museattributes_returns_auto_with_zero_rules(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        result = runner.invoke(
            cli, ["plumbing", "check-attr", "any/path.mid"], env=_env(repo)
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        assert data["results"][0]["strategy"] == "auto"
        assert data["rules_loaded"] == 0

    def test_multiple_paths_resolved_independently(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        _write_attrs(
            repo,
            '[[rules]]\npath = "drums/*"\ndimension = "*"\nstrategy = "ours"\n'
            '[[rules]]\npath = "keys/*"\ndimension = "*"\nstrategy = "theirs"\n',
        )
        result = runner.invoke(
            cli, ["plumbing", "check-attr", "drums/kit.mid", "keys/piano.mid", "misc/x.mid"],
            env=_env(repo),
        )
        assert result.exit_code == 0, result.output
        strategies = {r["path"]: r["strategy"] for r in json.loads(result.stdout)["results"]}
        assert strategies["drums/kit.mid"] == "ours"
        assert strategies["keys/piano.mid"] == "theirs"
        assert strategies["misc/x.mid"] == "auto"

    def test_priority_higher_wins_over_lower(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        _write_attrs(
            repo,
            '[[rules]]\npath = "*"\ndimension = "*"\nstrategy = "auto"\npriority = 0\n'
            '[[rules]]\npath = "drums/*"\ndimension = "*"\nstrategy = "ours"\npriority = 10\n',
        )
        result = runner.invoke(
            cli, ["plumbing", "check-attr", "drums/kit.mid"], env=_env(repo)
        )
        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout)["results"][0]["strategy"] == "ours"

    def test_dimension_flag_filters_to_specific_axis(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        _write_attrs(
            repo,
            '[[rules]]\npath = "*"\ndimension = "notes"\nstrategy = "union"\n'
            '[[rules]]\npath = "*"\ndimension = "tempo"\nstrategy = "ours"\n',
        )
        result = runner.invoke(
            cli, ["plumbing", "check-attr", "--dimension", "tempo", "track.mid"],
            env=_env(repo),
        )
        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout)["results"][0]["strategy"] == "ours"

    def test_all_rules_flag_returns_every_match(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        _write_attrs(
            repo,
            '[[rules]]\npath = "*"\ndimension = "*"\nstrategy = "auto"\n'
            '[[rules]]\npath = "drums/*"\ndimension = "*"\nstrategy = "ours"\n',
        )
        result = runner.invoke(
            cli, ["plumbing", "check-attr", "--all-rules", "drums/kit.mid"], env=_env(repo)
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        assert len(data["results"][0]["matching_rules"]) == 2

    def test_all_rules_no_match_returns_empty_list(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        _write_attrs(repo, '[[rules]]\npath = "drums/*"\ndimension = "*"\nstrategy = "ours"\n')
        result = runner.invoke(
            cli, ["plumbing", "check-attr", "--all-rules", "keys/x.mid"], env=_env(repo)
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        assert data["results"][0]["matching_rules"] == []

    def test_text_format_output(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        _write_attrs(repo, '[[rules]]\npath = "drums/*"\ndimension = "*"\nstrategy = "ours"\n')
        result = runner.invoke(
            cli, ["plumbing", "check-attr", "--format", "text", "drums/kit.mid"], env=_env(repo)
        )
        assert result.exit_code == 0, result.output
        assert "strategy=ours" in result.stdout

    def test_source_index_matches_rule_position(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        _write_attrs(
            repo,
            '[[rules]]\npath = "a/*"\ndimension = "*"\nstrategy = "ours"\n'
            '[[rules]]\npath = "b/*"\ndimension = "*"\nstrategy = "theirs"\n',
        )
        result = runner.invoke(
            cli, ["plumbing", "check-attr", "b/x.mid"], env=_env(repo)
        )
        assert result.exit_code == 0, result.output
        r = json.loads(result.stdout)["results"][0]
        assert r["rule"]["source_index"] == 1

    def test_bad_format_exits_user_error(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        result = runner.invoke(
            cli, ["plumbing", "check-attr", "--format", "xml", "any.mid"], env=_env(repo)
        )
        assert result.exit_code == ExitCode.USER_ERROR

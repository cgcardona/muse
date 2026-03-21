"""Tests for muse plumbing check-ref-format."""

from __future__ import annotations

import json
import pathlib

import pytest
from typer.testing import CliRunner

from muse.cli.app import cli

runner = CliRunner()


def _env(repo: pathlib.Path) -> dict[str, str]:
    return {"MUSE_REPO_ROOT": str(repo)}


def _init_repo(repo: pathlib.Path) -> None:
    muse = repo / ".muse"
    (muse / "commits").mkdir(parents=True)
    (muse / "snapshots").mkdir(parents=True)
    (muse / "objects").mkdir(parents=True)
    (muse / "refs" / "heads").mkdir(parents=True)
    (muse / "HEAD").write_text("ref: refs/heads/main", encoding="utf-8")
    (muse / "repo.json").write_text(
        json.dumps({"repo_id": "test-repo", "domain": "midi"}), encoding="utf-8"
    )


class TestCheckRefFormat:
    def test_valid_simple_name(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        _init_repo(tmp_path)
        result = runner.invoke(
            cli, ["plumbing", "check-ref-format", "main"], env=_env(tmp_path)
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["all_valid"] is True
        assert data["results"][0]["valid"] is True

    def test_valid_namespaced_name(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        _init_repo(tmp_path)
        result = runner.invoke(
            cli, ["plumbing", "check-ref-format", "feat/my-branch"], env=_env(tmp_path)
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["all_valid"] is True

    def test_consecutive_dots_invalid(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        _init_repo(tmp_path)
        result = runner.invoke(
            cli, ["plumbing", "check-ref-format", "bad..name"], env=_env(tmp_path)
        )
        assert result.exit_code != 0
        data = json.loads(result.output)
        assert data["all_valid"] is False
        assert data["results"][0]["valid"] is False
        assert data["results"][0]["error"] is not None

    def test_leading_dot_invalid(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        _init_repo(tmp_path)
        result = runner.invoke(
            cli, ["plumbing", "check-ref-format", ".hidden"], env=_env(tmp_path)
        )
        assert result.exit_code != 0
        data = json.loads(result.output)
        assert data["all_valid"] is False

    def test_leading_slash_invalid(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        _init_repo(tmp_path)
        result = runner.invoke(
            cli, ["plumbing", "check-ref-format", "/bad"], env=_env(tmp_path)
        )
        assert result.exit_code != 0

    def test_multiple_names_mixed_validity(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        _init_repo(tmp_path)
        result = runner.invoke(
            cli, ["plumbing", "check-ref-format", "good-name", "bad..name"], env=_env(tmp_path)
        )
        assert result.exit_code != 0
        data = json.loads(result.output)
        assert data["all_valid"] is False
        valid_map = {r["name"]: r["valid"] for r in data["results"]}
        assert valid_map["good-name"] is True
        assert valid_map["bad..name"] is False

    def test_all_valid_exits_zero(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        _init_repo(tmp_path)
        result = runner.invoke(
            cli, ["plumbing", "check-ref-format", "feat/a", "fix/b", "dev"], env=_env(tmp_path)
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["all_valid"] is True

    def test_quiet_mode_exit_zero(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        _init_repo(tmp_path)
        result = runner.invoke(
            cli, ["plumbing", "check-ref-format", "--quiet", "main"], env=_env(tmp_path)
        )
        assert result.exit_code == 0
        assert result.output.strip() == ""

    def test_quiet_mode_exit_one_on_invalid(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        _init_repo(tmp_path)
        result = runner.invoke(
            cli, ["plumbing", "check-ref-format", "-q", "bad..name"], env=_env(tmp_path)
        )
        assert result.exit_code != 0
        assert result.output.strip() == ""

    def test_text_format(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        _init_repo(tmp_path)
        result = runner.invoke(
            cli,
            ["plumbing", "check-ref-format", "--format", "text", "good", "bad..name"],
            env=_env(tmp_path),
        )
        assert result.exit_code != 0
        assert "ok" in result.output
        assert "FAIL" in result.output

    def test_no_args_errors(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        _init_repo(tmp_path)
        result = runner.invoke(cli, ["plumbing", "check-ref-format"], env=_env(tmp_path))
        assert result.exit_code != 0

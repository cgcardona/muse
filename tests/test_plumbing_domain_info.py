"""Tests for ``muse plumbing domain-info``.

Verifies that the command correctly reports the active domain, plugin class,
capability flags, structural schema, and registered domain enumeration.
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


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDomainInfo:
    def test_reports_domain_and_plugin_class(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path, domain="midi")
        result = runner.invoke(cli, ["plumbing", "domain-info"], env=_env(repo))
        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        assert data["domain"] == "midi"
        assert "Plugin" in data["plugin_class"]

    def test_capabilities_dict_has_three_keys(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path, domain="midi")
        result = runner.invoke(cli, ["plumbing", "domain-info"], env=_env(repo))
        assert result.exit_code == 0, result.output
        caps = json.loads(result.stdout)["capabilities"]
        assert "structured_merge" in caps
        assert "crdt" in caps
        assert "rerere" in caps
        assert all(isinstance(v, bool) for v in caps.values())

    def test_schema_has_required_fields(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path, domain="midi")
        result = runner.invoke(cli, ["plumbing", "domain-info"], env=_env(repo))
        assert result.exit_code == 0, result.output
        schema = json.loads(result.stdout)["schema"]
        assert "domain" in schema
        assert "merge_mode" in schema
        assert schema["merge_mode"] in ("three_way", "crdt")
        assert "dimensions" in schema

    def test_registered_domains_list_present(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path, domain="midi")
        result = runner.invoke(cli, ["plumbing", "domain-info"], env=_env(repo))
        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        assert "registered_domains" in data
        assert "midi" in data["registered_domains"]

    def test_code_domain_reports_correctly(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path, domain="code")
        result = runner.invoke(cli, ["plumbing", "domain-info"], env=_env(repo))
        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout)["domain"] == "code"

    def test_text_format_output(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path, domain="midi")
        result = runner.invoke(
            cli, ["plumbing", "domain-info", "--format", "text"], env=_env(repo)
        )
        assert result.exit_code == 0, result.output
        assert "Domain:" in result.stdout
        assert "midi" in result.stdout
        assert "Plugin:" in result.stdout
        assert "Merge mode:" in result.stdout

    def test_all_domains_flag_json(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path, domain="midi")
        result = runner.invoke(
            cli, ["plumbing", "domain-info", "--all-domains"], env=_env(repo)
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        assert "registered_domains" in data
        assert isinstance(data["registered_domains"], list)
        assert len(data["registered_domains"]) >= 1

    def test_all_domains_flag_text(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path, domain="midi")
        result = runner.invoke(
            cli,
            ["plumbing", "domain-info", "--all-domains", "--format", "text"],
            env=_env(repo),
        )
        assert result.exit_code == 0, result.output
        assert "midi" in result.stdout

    def test_bad_format_exits_user_error(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path, domain="midi")
        result = runner.invoke(
            cli, ["plumbing", "domain-info", "--format", "toml"], env=_env(repo)
        )
        assert result.exit_code == ExitCode.USER_ERROR

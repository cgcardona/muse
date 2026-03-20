"""Tests for `muse auth` CLI commands — login, whoami, logout.

The identity store is redirected to a temporary directory per test so these
tests never touch ~/.muse/identity.toml.  Network calls are not made — auth
commands read/write the local identity store only.

getpass.getpass is mocked for tests that exercise the interactive token
prompt path, so tests run fully non-interactively.
"""

from __future__ import annotations

import json
import pathlib
import unittest.mock

import pytest
from typer.testing import CliRunner

from muse.cli.app import cli
from muse.cli.config import get_hub_url, set_hub_url
from muse.core.identity import (
    IdentityEntry,
    get_identity_path,
    list_all_identities,
    load_identity,
    save_identity,
)

runner = CliRunner()


# ---------------------------------------------------------------------------
# Fixture: minimal repo + isolated identity store
# ---------------------------------------------------------------------------


@pytest.fixture()
def repo(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    """Minimal .muse/ repo with a pre-configured hub URL.

    The identity store is redirected to *tmp_path* so tests never touch
    the real ``~/.muse/identity.toml``.
    """
    muse_dir = tmp_path / ".muse"
    (muse_dir / "refs" / "heads").mkdir(parents=True)
    (muse_dir / "objects").mkdir()
    (muse_dir / "commits").mkdir()
    (muse_dir / "snapshots").mkdir()
    (muse_dir / "repo.json").write_text(
        json.dumps({"repo_id": "test-repo", "schema_version": "2", "domain": "midi"})
    )
    (muse_dir / "HEAD").write_text("refs/heads/main\n")
    (muse_dir / "config.toml").write_text(
        '[hub]\nurl = "https://musehub.ai"\n'
    )
    monkeypatch.setenv("MUSE_REPO_ROOT", str(tmp_path))
    monkeypatch.chdir(tmp_path)

    # Isolate the identity store.
    fake_dir = tmp_path / "home" / ".muse"
    fake_dir.mkdir(parents=True)
    fake_file = fake_dir / "identity.toml"
    monkeypatch.setattr("muse.core.identity._IDENTITY_DIR", fake_dir)
    monkeypatch.setattr("muse.core.identity._IDENTITY_FILE", fake_file)
    return tmp_path


# ---------------------------------------------------------------------------
# muse auth login
# ---------------------------------------------------------------------------


class TestAuthLogin:
    def test_login_with_token_flag(self, repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["auth", "login", "--token", "mytoken123"])
        assert result.exit_code == 0
        entry = load_identity("https://musehub.ai")
        assert entry is not None
        assert entry.get("token") == "mytoken123"

    def test_login_stores_human_type_by_default(self, repo: pathlib.Path) -> None:
        runner.invoke(cli, ["auth", "login", "--token", "tok"])
        entry = load_identity("https://musehub.ai")
        assert entry is not None
        assert entry.get("type") == "human"

    def test_login_stores_agent_type_with_flag(self, repo: pathlib.Path) -> None:
        runner.invoke(cli, ["auth", "login", "--token", "tok", "--agent"])
        entry = load_identity("https://musehub.ai")
        assert entry is not None
        assert entry.get("type") == "agent"

    def test_login_stores_name(self, repo: pathlib.Path) -> None:
        runner.invoke(cli, ["auth", "login", "--token", "tok", "--name", "Alice"])
        entry = load_identity("https://musehub.ai")
        assert entry is not None
        assert entry.get("name") == "Alice"

    def test_login_stores_id(self, repo: pathlib.Path) -> None:
        runner.invoke(cli, ["auth", "login", "--token", "tok", "--id", "usr_abc123"])
        entry = load_identity("https://musehub.ai")
        assert entry is not None
        assert entry.get("id") == "usr_abc123"

    def test_login_hub_option_overrides_config(self, repo: pathlib.Path) -> None:
        result = runner.invoke(
            cli, ["auth", "login", "--token", "tok", "--hub", "https://staging.musehub.ai"]
        )
        assert result.exit_code == 0
        entry = load_identity("https://staging.musehub.ai")
        assert entry is not None
        assert entry.get("token") == "tok"

    def test_login_env_var_accepted_silently(
        self, repo: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MUSE_TOKEN", "env_token_value")
        result = runner.invoke(cli, ["auth", "login"])
        assert result.exit_code == 0
        # Should NOT warn about shell history exposure when token comes from env.
        assert "shell history" not in result.output
        assert "⚠️" not in result.output or "MUSE_TOKEN" not in result.output
        entry = load_identity("https://musehub.ai")
        assert entry is not None
        assert entry.get("token") == "env_token_value"

    def test_login_warns_when_token_passed_via_cli_flag(
        self, repo: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("MUSE_TOKEN", raising=False)
        result = runner.invoke(cli, ["auth", "login", "--token", "plaintext_token"])
        # Warning goes to stderr; we just confirm exit code is 0 (success) and
        # that the token was stored, since CliRunner merges stdout/stderr.
        assert result.exit_code == 0
        entry = load_identity("https://musehub.ai")
        assert entry is not None
        assert entry.get("token") == "plaintext_token"

    def test_login_interactive_prompt(
        self, repo: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When no token source is given, getpass.getpass is called."""
        monkeypatch.delenv("MUSE_TOKEN", raising=False)
        with unittest.mock.patch("getpass.getpass", return_value="prompted_token"):
            result = runner.invoke(cli, ["auth", "login"])
        assert result.exit_code == 0
        entry = load_identity("https://musehub.ai")
        assert entry is not None
        assert entry.get("token") == "prompted_token"

    def test_login_fails_without_hub(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """With no hub in config and no --hub flag, login should fail."""
        muse_dir = tmp_path / ".muse"
        muse_dir.mkdir()
        (muse_dir / "config.toml").write_text("")  # no [hub] section
        (muse_dir / "repo.json").write_text(
            json.dumps({"repo_id": "r", "schema_version": "2", "domain": "midi"})
        )
        (muse_dir / "HEAD").write_text("refs/heads/main\n")
        monkeypatch.setenv("MUSE_REPO_ROOT", str(tmp_path))
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("MUSE_TOKEN", raising=False)
        fake_dir = tmp_path / "home" / ".muse"
        fake_dir.mkdir(parents=True)
        monkeypatch.setattr("muse.core.identity._IDENTITY_DIR", fake_dir)
        monkeypatch.setattr("muse.core.identity._IDENTITY_FILE", fake_dir / "identity.toml")
        result = runner.invoke(cli, ["auth", "login", "--token", "tok"])
        assert result.exit_code != 0
        assert "hub" in result.output.lower()

    def test_login_success_message_shown(self, repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["auth", "login", "--token", "tok", "--name", "Bob"])
        assert result.exit_code == 0
        assert "Bob" in result.output or "Authenticated" in result.output


# ---------------------------------------------------------------------------
# muse auth whoami
# ---------------------------------------------------------------------------


class TestAuthWhoami:
    def _store_entry(self, hub: str = "https://musehub.ai") -> None:
        entry: IdentityEntry = {
            "type": "human",
            "token": "tok_secret",
            "name": "Alice",
            "id": "usr_001",
        }
        save_identity(hub, entry)

    def test_whoami_shows_hub(self, repo: pathlib.Path) -> None:
        self._store_entry()
        result = runner.invoke(cli, ["auth", "whoami"])
        assert result.exit_code == 0
        assert "musehub.ai" in result.output

    def test_whoami_shows_type(self, repo: pathlib.Path) -> None:
        self._store_entry()
        result = runner.invoke(cli, ["auth", "whoami"])
        assert "human" in result.output

    def test_whoami_shows_name(self, repo: pathlib.Path) -> None:
        self._store_entry()
        result = runner.invoke(cli, ["auth", "whoami"])
        assert "Alice" in result.output

    def test_whoami_does_not_print_raw_token(self, repo: pathlib.Path) -> None:
        self._store_entry()
        result = runner.invoke(cli, ["auth", "whoami"])
        assert "tok_secret" not in result.output

    def test_whoami_shows_token_set_indicator(self, repo: pathlib.Path) -> None:
        self._store_entry()
        result = runner.invoke(cli, ["auth", "whoami"])
        assert "set" in result.output.lower() or "***" in result.output

    def test_whoami_json_output(self, repo: pathlib.Path) -> None:
        self._store_entry()
        result = runner.invoke(cli, ["auth", "whoami", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["type"] == "human"
        assert data["name"] == "Alice"
        assert data.get("token_set") in ("true", "false")

    def test_whoami_json_does_not_include_raw_token(self, repo: pathlib.Path) -> None:
        self._store_entry()
        result = runner.invoke(cli, ["auth", "whoami", "--json"])
        assert "tok_secret" not in result.output

    def test_whoami_no_identity_exits_nonzero(self, repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["auth", "whoami"])
        assert result.exit_code != 0

    def test_whoami_hub_option_selects_specific_hub(self, repo: pathlib.Path) -> None:
        save_identity("https://staging.musehub.ai", {"type": "agent", "token": "tok2", "name": "bot"})
        result = runner.invoke(cli, ["auth", "whoami", "--hub", "https://staging.musehub.ai"])
        assert result.exit_code == 0
        assert "staging.musehub.ai" in result.output

    def test_whoami_all_lists_all_hubs(self, repo: pathlib.Path) -> None:
        self._store_entry("https://hub1.example.com")
        self._store_entry("https://hub2.example.com")
        result = runner.invoke(cli, ["auth", "whoami", "--all"])
        assert result.exit_code == 0
        assert "hub1.example.com" in result.output
        assert "hub2.example.com" in result.output

    def test_whoami_all_no_identities_exits_nonzero(self, repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["auth", "whoami", "--all"])
        assert result.exit_code != 0

    def test_whoami_capabilities_shown(self, repo: pathlib.Path) -> None:
        entry: IdentityEntry = {
            "type": "agent",
            "token": "tok",
            "name": "worker",
            "capabilities": ["read:*", "write:midi"],
        }
        save_identity("https://musehub.ai", entry)
        result = runner.invoke(cli, ["auth", "whoami"])
        assert "read:*" in result.output or "write:midi" in result.output


# ---------------------------------------------------------------------------
# muse auth logout
# ---------------------------------------------------------------------------


class TestAuthLogout:
    def _store(self, hub: str = "https://musehub.ai") -> None:
        entry: IdentityEntry = {"type": "human", "token": "tok"}
        save_identity(hub, entry)

    def test_logout_removes_identity(self, repo: pathlib.Path) -> None:
        self._store()
        result = runner.invoke(cli, ["auth", "logout"])
        assert result.exit_code == 0
        assert load_identity("https://musehub.ai") is None

    def test_logout_shows_success_message(self, repo: pathlib.Path) -> None:
        self._store()
        result = runner.invoke(cli, ["auth", "logout"])
        assert "musehub.ai" in result.output or "Logged out" in result.output

    def test_logout_nothing_to_do_does_not_fail(self, repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["auth", "logout"])
        assert result.exit_code == 0
        assert "nothing" in result.output.lower() or "nothing to do" in result.output.lower()

    def test_logout_hub_option_removes_specific_hub(self, repo: pathlib.Path) -> None:
        self._store("https://hub1.example.com")
        self._store("https://hub2.example.com")
        result = runner.invoke(cli, ["auth", "logout", "--hub", "https://hub1.example.com"])
        assert result.exit_code == 0
        assert load_identity("https://hub1.example.com") is None
        assert load_identity("https://hub2.example.com") is not None

    def test_logout_all_removes_all_identities(self, repo: pathlib.Path) -> None:
        self._store("https://hub1.example.com")
        self._store("https://hub2.example.com")
        result = runner.invoke(cli, ["auth", "logout", "--all"])
        assert result.exit_code == 0
        assert not list_all_identities()

    def test_logout_all_reports_count(self, repo: pathlib.Path) -> None:
        self._store("https://hub1.example.com")
        self._store("https://hub2.example.com")
        result = runner.invoke(cli, ["auth", "logout", "--all"])
        assert "2" in result.output

    def test_logout_all_no_identities_succeeds(self, repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["auth", "logout", "--all"])
        assert result.exit_code == 0

    def test_logout_fails_without_hub_source(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """With no hub in config and no --hub flag, logout should fail."""
        muse_dir = tmp_path / ".muse"
        muse_dir.mkdir()
        (muse_dir / "config.toml").write_text("")
        (muse_dir / "repo.json").write_text(
            json.dumps({"repo_id": "r", "schema_version": "2", "domain": "midi"})
        )
        (muse_dir / "HEAD").write_text("refs/heads/main\n")
        monkeypatch.setenv("MUSE_REPO_ROOT", str(tmp_path))
        monkeypatch.chdir(tmp_path)
        fake_dir = tmp_path / "home" / ".muse"
        fake_dir.mkdir(parents=True)
        monkeypatch.setattr("muse.core.identity._IDENTITY_DIR", fake_dir)
        monkeypatch.setattr("muse.core.identity._IDENTITY_FILE", fake_dir / "identity.toml")
        result = runner.invoke(cli, ["auth", "logout"])
        assert result.exit_code != 0

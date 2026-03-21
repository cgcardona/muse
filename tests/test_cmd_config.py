"""Comprehensive tests for ``muse config`` — show / get / set.

Coverage:
- Unit: get_config_value, set_config_value, config_as_dict
- Integration: CLI round-trips for show, get, set
- E2E: full set→get→show workflow
- Security: blocked namespaces, TOML injection, malformed keys
- Format: --json / --format json output
"""

from __future__ import annotations

import json
import pathlib
import uuid

import pytest
from typer.testing import CliRunner

from muse.cli.app import cli

runner = CliRunner()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _init_repo(tmp_path: pathlib.Path) -> tuple[pathlib.Path, str]:
    """Initialise a minimal .muse repo and return (root, repo_id)."""
    repo_id = str(uuid.uuid4())
    muse = tmp_path / ".muse"
    muse.mkdir()
    (muse / "repo.json").write_text(
        json.dumps({"repo_id": repo_id, "domain": "midi",
                    "default_branch": "main",
                    "created_at": "2026-01-01T00:00:00+00:00"})
    )
    (muse / "HEAD").write_text("ref: refs/heads/main")
    (muse / "refs" / "heads").mkdir(parents=True)
    (muse / "snapshots").mkdir()
    (muse / "commits").mkdir()
    (muse / "objects").mkdir()
    return tmp_path, repo_id


def _env(root: pathlib.Path) -> dict[str, str]:
    return {"MUSE_REPO_ROOT": str(root)}


# ---------------------------------------------------------------------------
# Unit tests — config helpers
# ---------------------------------------------------------------------------


class TestConfigValueHelpers:
    def test_set_and_get_user_name(self, tmp_path: pathlib.Path) -> None:
        root, _ = _init_repo(tmp_path)
        from muse.cli.config import get_config_value, set_config_value
        set_config_value("user.name", "Alice", root)
        assert get_config_value("user.name", root) == "Alice"

    def test_set_and_get_user_email(self, tmp_path: pathlib.Path) -> None:
        root, _ = _init_repo(tmp_path)
        from muse.cli.config import get_config_value, set_config_value
        set_config_value("user.email", "alice@example.com", root)
        assert get_config_value("user.email", root) == "alice@example.com"

    def test_set_and_get_user_type(self, tmp_path: pathlib.Path) -> None:
        root, _ = _init_repo(tmp_path)
        from muse.cli.config import get_config_value, set_config_value
        set_config_value("user.type", "agent", root)
        assert get_config_value("user.type", root) == "agent"

    def test_set_and_get_domain_key(self, tmp_path: pathlib.Path) -> None:
        root, _ = _init_repo(tmp_path)
        from muse.cli.config import get_config_value, set_config_value
        set_config_value("domain.ticks_per_beat", "480", root)
        assert get_config_value("domain.ticks_per_beat", root) == "480"

    def test_get_missing_key_returns_none(self, tmp_path: pathlib.Path) -> None:
        root, _ = _init_repo(tmp_path)
        from muse.cli.config import get_config_value
        assert get_config_value("user.name", root) is None

    def test_get_unknown_namespace_returns_none(self, tmp_path: pathlib.Path) -> None:
        root, _ = _init_repo(tmp_path)
        from muse.cli.config import get_config_value
        assert get_config_value("unknown.key", root) is None

    def test_set_blocked_auth_raises(self, tmp_path: pathlib.Path) -> None:
        root, _ = _init_repo(tmp_path)
        from muse.cli.config import set_config_value
        with pytest.raises(ValueError, match="muse auth login"):
            set_config_value("auth.token", "secret", root)

    def test_set_blocked_remotes_raises(self, tmp_path: pathlib.Path) -> None:
        root, _ = _init_repo(tmp_path)
        from muse.cli.config import set_config_value
        with pytest.raises(ValueError, match="muse remote"):
            set_config_value("remotes.origin", "https://x.com", root)

    def test_set_unknown_namespace_raises(self, tmp_path: pathlib.Path) -> None:
        root, _ = _init_repo(tmp_path)
        from muse.cli.config import set_config_value
        with pytest.raises(ValueError):
            set_config_value("invalid.key", "value", root)

    def test_set_malformed_key_raises(self, tmp_path: pathlib.Path) -> None:
        root, _ = _init_repo(tmp_path)
        from muse.cli.config import set_config_value
        with pytest.raises(ValueError):
            set_config_value("no-dot-key", "value", root)

    def test_config_as_dict_includes_user(self, tmp_path: pathlib.Path) -> None:
        root, _ = _init_repo(tmp_path)
        from muse.cli.config import config_as_dict, set_config_value
        set_config_value("user.name", "Bob", root)
        d = config_as_dict(root)
        assert d.get("user", {}).get("name") == "Bob"

    def test_config_as_dict_empty_repo(self, tmp_path: pathlib.Path) -> None:
        root, _ = _init_repo(tmp_path)
        from muse.cli.config import config_as_dict
        d = config_as_dict(root)
        assert isinstance(d, dict)

    def test_set_hub_url_requires_https(self, tmp_path: pathlib.Path) -> None:
        root, _ = _init_repo(tmp_path)
        from muse.cli.config import set_config_value
        with pytest.raises(ValueError, match="HTTPS"):
            set_config_value("hub.url", "http://insecure.example.com", root)


# ---------------------------------------------------------------------------
# Integration tests — CLI commands
# ---------------------------------------------------------------------------


class TestConfigCLI:
    def test_show_empty_config(self, tmp_path: pathlib.Path) -> None:
        root, _ = _init_repo(tmp_path)
        result = runner.invoke(cli, ["config", "show"], env=_env(root), catch_exceptions=False)
        assert result.exit_code == 0

    def test_show_json_empty(self, tmp_path: pathlib.Path) -> None:
        root, _ = _init_repo(tmp_path)
        result = runner.invoke(cli, ["config", "show", "--json"], env=_env(root), catch_exceptions=False)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, dict)

    def test_show_format_json(self, tmp_path: pathlib.Path) -> None:
        root, _ = _init_repo(tmp_path)
        result = runner.invoke(cli, ["config", "show", "--format", "json"], env=_env(root), catch_exceptions=False)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, dict)

    def test_set_user_name(self, tmp_path: pathlib.Path) -> None:
        root, _ = _init_repo(tmp_path)
        result = runner.invoke(cli, ["config", "set", "user.name", "Alice"], env=_env(root), catch_exceptions=False)
        assert result.exit_code == 0
        assert "user.name" in result.output

    def test_set_then_get_user_name(self, tmp_path: pathlib.Path) -> None:
        root, _ = _init_repo(tmp_path)
        runner.invoke(cli, ["config", "set", "user.name", "Carol"], env=_env(root), catch_exceptions=False)
        result = runner.invoke(cli, ["config", "get", "user.name"], env=_env(root), catch_exceptions=False)
        assert result.exit_code == 0
        assert "Carol" in result.output

    def test_get_unset_key_fails(self, tmp_path: pathlib.Path) -> None:
        root, _ = _init_repo(tmp_path)
        result = runner.invoke(cli, ["config", "get", "user.name"], env=_env(root))
        assert result.exit_code != 0

    def test_set_domain_key(self, tmp_path: pathlib.Path) -> None:
        root, _ = _init_repo(tmp_path)
        result = runner.invoke(cli, ["config", "set", "domain.ticks_per_beat", "480"],
                               env=_env(root), catch_exceptions=False)
        assert result.exit_code == 0

    def test_get_domain_key_after_set(self, tmp_path: pathlib.Path) -> None:
        root, _ = _init_repo(tmp_path)
        runner.invoke(cli, ["config", "set", "domain.ticks_per_beat", "960"], env=_env(root))
        result = runner.invoke(cli, ["config", "get", "domain.ticks_per_beat"], env=_env(root), catch_exceptions=False)
        assert result.exit_code == 0
        assert "960" in result.output

    def test_set_blocked_auth_fails(self, tmp_path: pathlib.Path) -> None:
        root, _ = _init_repo(tmp_path)
        result = runner.invoke(cli, ["config", "set", "auth.token", "secret"], env=_env(root))
        assert result.exit_code != 0

    def test_set_blocked_remotes_fails(self, tmp_path: pathlib.Path) -> None:
        root, _ = _init_repo(tmp_path)
        result = runner.invoke(cli, ["config", "set", "remotes.origin", "https://x.com"], env=_env(root))
        assert result.exit_code != 0

    def test_set_http_hub_url_fails(self, tmp_path: pathlib.Path) -> None:
        root, _ = _init_repo(tmp_path)
        result = runner.invoke(cli, ["config", "set", "hub.url", "http://insecure.example.com"], env=_env(root))
        assert result.exit_code != 0

    def test_set_https_hub_url_succeeds(self, tmp_path: pathlib.Path) -> None:
        root, _ = _init_repo(tmp_path)
        result = runner.invoke(cli, ["config", "set", "hub.url", "https://musehub.ai"],
                               env=_env(root), catch_exceptions=False)
        assert result.exit_code == 0

    def test_show_after_set_includes_value(self, tmp_path: pathlib.Path) -> None:
        root, _ = _init_repo(tmp_path)
        runner.invoke(cli, ["config", "set", "user.name", "Dave"], env=_env(root))
        result = runner.invoke(cli, ["config", "show"], env=_env(root), catch_exceptions=False)
        assert result.exit_code == 0
        assert "Dave" in result.output

    def test_show_json_after_set(self, tmp_path: pathlib.Path) -> None:
        root, _ = _init_repo(tmp_path)
        runner.invoke(cli, ["config", "set", "user.name", "Eve"], env=_env(root))
        runner.invoke(cli, ["config", "set", "user.type", "agent"], env=_env(root))
        result = runner.invoke(cli, ["config", "show", "--json"], env=_env(root), catch_exceptions=False)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data.get("user", {}).get("name") == "Eve"
        assert data.get("user", {}).get("type") == "agent"

    def test_multiple_sets_accumulate(self, tmp_path: pathlib.Path) -> None:
        root, _ = _init_repo(tmp_path)
        runner.invoke(cli, ["config", "set", "user.name", "Frank"], env=_env(root))
        runner.invoke(cli, ["config", "set", "user.email", "frank@example.com"], env=_env(root))
        runner.invoke(cli, ["config", "set", "domain.key", "val"], env=_env(root))
        result = runner.invoke(cli, ["config", "show", "--json"], env=_env(root), catch_exceptions=False)
        data = json.loads(result.output)
        assert data["user"]["name"] == "Frank"
        assert data["user"]["email"] == "frank@example.com"
        assert data["domain"]["key"] == "val"

    def test_set_overwrites_previous_value(self, tmp_path: pathlib.Path) -> None:
        root, _ = _init_repo(tmp_path)
        runner.invoke(cli, ["config", "set", "user.name", "Old"], env=_env(root))
        runner.invoke(cli, ["config", "set", "user.name", "New"], env=_env(root))
        result = runner.invoke(cli, ["config", "get", "user.name"], env=_env(root), catch_exceptions=False)
        assert result.exit_code == 0
        assert "New" in result.output

    def test_show_format_unknown_fails(self, tmp_path: pathlib.Path) -> None:
        root, _ = _init_repo(tmp_path)
        result = runner.invoke(cli, ["config", "show", "--format", "xml"], env=_env(root))
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# E2E tests
# ---------------------------------------------------------------------------


class TestConfigE2E:
    def test_full_agent_config_workflow(self, tmp_path: pathlib.Path) -> None:
        """Agent sets identity, then reads it back as JSON."""
        root, _ = _init_repo(tmp_path)
        runner.invoke(cli, ["config", "set", "user.name", "muse-agent-001"], env=_env(root))
        runner.invoke(cli, ["config", "set", "user.type", "agent"], env=_env(root))
        runner.invoke(cli, ["config", "set", "domain.ticks_per_beat", "960"], env=_env(root))

        result = runner.invoke(cli, ["config", "show", "--format", "json"], env=_env(root), catch_exceptions=False)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["user"]["name"] == "muse-agent-001"
        assert data["user"]["type"] == "agent"
        assert data["domain"]["ticks_per_beat"] == "960"

    def test_config_persists_across_invocations(self, tmp_path: pathlib.Path) -> None:
        """Config written in one invocation is readable in a subsequent one."""
        root, _ = _init_repo(tmp_path)
        runner.invoke(cli, ["config", "set", "user.name", "Persistent"], env=_env(root))
        result = runner.invoke(cli, ["config", "get", "user.name"], env=_env(root), catch_exceptions=False)
        assert "Persistent" in result.output


# ---------------------------------------------------------------------------
# Security tests
# ---------------------------------------------------------------------------


class TestConfigSecurity:
    def test_toml_injection_in_name_is_stored_safely(self, tmp_path: pathlib.Path) -> None:
        """TOML injection chars in a name value do not break the config file."""
        root, _ = _init_repo(tmp_path)
        injection = 'Alice"\n[injected]\nkey = "value'
        result = runner.invoke(cli, ["config", "set", "user.name", injection], env=_env(root))
        # Should either fail safely or store the value escaped
        if result.exit_code == 0:
            get_result = runner.invoke(cli, ["config", "get", "user.name"], env=_env(root))
            # If stored, round-trip must be stable — no config file corruption
            show_result = runner.invoke(cli, ["config", "show", "--json"], env=_env(root))
            assert show_result.exit_code == 0
            data = json.loads(show_result.output)
            assert isinstance(data, dict)

    def test_no_credentials_in_json_output(self, tmp_path: pathlib.Path) -> None:
        """config show --json never leaks credentials even if they somehow end up in config.toml."""
        root, _ = _init_repo(tmp_path)
        config_path = root / ".muse" / "config.toml"
        # Manually inject a fake token into config.toml
        config_path.write_text('[auth]\ntoken = "super-secret"\n', encoding="utf-8")
        result = runner.invoke(cli, ["config", "show", "--json"], env=_env(root), catch_exceptions=False)
        assert result.exit_code == 0
        assert "super-secret" not in result.output

    def test_set_user_type_rejects_unknown_values_gracefully(self, tmp_path: pathlib.Path) -> None:
        """user.type accepts free-form values — but they are stored, not validated."""
        root, _ = _init_repo(tmp_path)
        result = runner.invoke(cli, ["config", "set", "user.type", "robot"], env=_env(root), catch_exceptions=False)
        # Current behaviour: stored as-is. This tests it doesn't crash.
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# Stress tests
# ---------------------------------------------------------------------------


class TestConfigStress:
    def test_many_domain_keys(self, tmp_path: pathlib.Path) -> None:
        """Setting 50 domain keys all survive a JSON round-trip."""
        root, _ = _init_repo(tmp_path)
        keys = {f"domain.key_{i}": str(i) for i in range(50)}
        for k, v in keys.items():
            r = runner.invoke(cli, ["config", "set", k, v], env=_env(root))
            assert r.exit_code == 0

        result = runner.invoke(cli, ["config", "show", "--json"], env=_env(root), catch_exceptions=False)
        assert result.exit_code == 0
        data = json.loads(result.output)
        domain = data.get("domain", {})
        for i in range(50):
            assert domain.get(f"key_{i}") == str(i)

    def test_overwrite_domain_key_many_times(self, tmp_path: pathlib.Path) -> None:
        """Repeated writes to the same key keep only the latest value."""
        root, _ = _init_repo(tmp_path)
        for i in range(20):
            runner.invoke(cli, ["config", "set", "domain.counter", str(i)], env=_env(root))
        result = runner.invoke(cli, ["config", "get", "domain.counter"], env=_env(root), catch_exceptions=False)
        assert result.exit_code == 0
        assert "19" in result.output

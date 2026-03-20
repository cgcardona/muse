"""Tests for `muse hub` CLI commands — connect, status, disconnect, ping.

All network calls are mocked — no real HTTP traffic occurs.  The identity
store is isolated per test using a tmp_path override.
"""

from __future__ import annotations

import io
import json
import pathlib
import unittest.mock
import urllib.error
import urllib.request
import urllib.response

import pytest
from typer.testing import CliRunner

from muse.cli.app import cli
from muse.cli.commands.hub import _hub_hostname, _normalise_url, _ping_hub
from muse.cli.config import get_hub_url, set_hub_url
from muse.core.identity import IdentityEntry, save_identity

runner = CliRunner()


# ---------------------------------------------------------------------------
# Fixture: minimal Muse repo
# ---------------------------------------------------------------------------


@pytest.fixture()
def repo(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    """Minimal .muse/ repo; hub tests don't need commits."""
    muse_dir = tmp_path / ".muse"
    (muse_dir / "refs" / "heads").mkdir(parents=True)
    (muse_dir / "objects").mkdir()
    (muse_dir / "commits").mkdir()
    (muse_dir / "snapshots").mkdir()
    (muse_dir / "repo.json").write_text(
        json.dumps({"repo_id": "test-repo", "schema_version": "2", "domain": "midi"})
    )
    (muse_dir / "HEAD").write_text("refs/heads/main\n")
    monkeypatch.setenv("MUSE_REPO_ROOT", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    # Redirect the identity store to tmp_path so tests never touch ~/.muse/
    fake_identity_dir = tmp_path / "fake_home" / ".muse"
    fake_identity_dir.mkdir(parents=True)
    fake_identity_file = fake_identity_dir / "identity.toml"
    monkeypatch.setattr("muse.core.identity._IDENTITY_DIR", fake_identity_dir)
    monkeypatch.setattr("muse.core.identity._IDENTITY_FILE", fake_identity_file)
    return tmp_path


# ---------------------------------------------------------------------------
# Unit tests for pure helper functions
# ---------------------------------------------------------------------------


class TestNormaliseUrl:
    def test_bare_hostname_gets_https(self) -> None:
        assert _normalise_url("musehub.ai") == "https://musehub.ai"

    def test_https_url_unchanged(self) -> None:
        assert _normalise_url("https://musehub.ai") == "https://musehub.ai"

    def test_trailing_slash_stripped(self) -> None:
        assert _normalise_url("https://musehub.ai/") == "https://musehub.ai"

    def test_http_url_raises(self) -> None:
        with pytest.raises(ValueError, match="Insecure"):
            _normalise_url("http://musehub.ai")

    def test_http_suggests_https(self) -> None:
        with pytest.raises(ValueError, match="https://"):
            _normalise_url("http://musehub.ai")

    def test_whitespace_stripped(self) -> None:
        assert _normalise_url("  https://musehub.ai  ") == "https://musehub.ai"


class TestHubHostname:
    def test_extracts_hostname_from_https_url(self) -> None:
        assert _hub_hostname("https://musehub.ai/repos/r1") == "musehub.ai"

    def test_bare_hostname(self) -> None:
        assert _hub_hostname("musehub.ai") == "musehub.ai"

    def test_strips_path(self) -> None:
        assert _hub_hostname("https://musehub.ai/deep/path") == "musehub.ai"

    def test_preserves_port(self) -> None:
        assert _hub_hostname("https://musehub.ai:8443") == "musehub.ai:8443"


class TestPingHub:
    def test_2xx_returns_true(self) -> None:
        mock_resp = unittest.mock.MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = unittest.mock.MagicMock(return_value=False)
        with unittest.mock.patch("muse.cli.commands.hub._PING_OPENER.open", return_value=mock_resp):
            ok, msg = _ping_hub("https://musehub.ai")
        assert ok is True
        assert "200" in msg

    def test_5xx_returns_false(self) -> None:
        mock_resp = unittest.mock.MagicMock()
        mock_resp.status = 503
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = unittest.mock.MagicMock(return_value=False)
        with unittest.mock.patch("muse.cli.commands.hub._PING_OPENER.open", return_value=mock_resp):
            ok, msg = _ping_hub("https://musehub.ai")
        assert ok is False

    def test_http_error_returns_false(self) -> None:
        err = urllib.error.HTTPError("https://musehub.ai/health", 401, "Unauthorized", {}, io.BytesIO(b"Unauthorized"))
        with unittest.mock.patch("muse.cli.commands.hub._PING_OPENER.open", side_effect=err):
            ok, msg = _ping_hub("https://musehub.ai")
        assert ok is False
        assert "401" in msg

    def test_url_error_returns_false(self) -> None:
        err = urllib.error.URLError("name resolution failure")
        with unittest.mock.patch("muse.cli.commands.hub._PING_OPENER.open", side_effect=err):
            ok, msg = _ping_hub("https://musehub.ai")
        assert ok is False

    def test_timeout_error_returns_false(self) -> None:
        with unittest.mock.patch("muse.cli.commands.hub._PING_OPENER.open", side_effect=TimeoutError()):
            ok, msg = _ping_hub("https://musehub.ai")
        assert ok is False
        assert "timed out" in msg

    def test_os_error_returns_false(self) -> None:
        with unittest.mock.patch("muse.cli.commands.hub._PING_OPENER.open", side_effect=OSError("network down")):
            ok, msg = _ping_hub("https://musehub.ai")
        assert ok is False

    def test_health_endpoint_used(self) -> None:
        calls: list[str] = []

        def _fake_open(req: urllib.request.Request, timeout: int = 0) -> urllib.response.addinfourl:
            calls.append(req.full_url)
            raise urllib.error.URLError("stop")

        with unittest.mock.patch("muse.cli.commands.hub._PING_OPENER.open", side_effect=_fake_open):
            _ping_hub("https://musehub.ai")
        assert calls and calls[0] == "https://musehub.ai/health"


# ---------------------------------------------------------------------------
# hub connect
# ---------------------------------------------------------------------------


class TestHubConnect:
    def test_connect_bare_hostname(self, repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["hub", "connect", "musehub.ai"])
        assert result.exit_code == 0
        assert "Connected" in result.output

    def test_connect_stores_https_url(self, repo: pathlib.Path) -> None:
        runner.invoke(cli, ["hub", "connect", "musehub.ai"])
        stored = get_hub_url(repo)
        assert stored == "https://musehub.ai"

    def test_connect_https_url_directly(self, repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["hub", "connect", "https://musehub.ai"])
        assert result.exit_code == 0
        assert get_hub_url(repo) == "https://musehub.ai"

    def test_connect_http_rejected(self, repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["hub", "connect", "http://musehub.ai"])
        assert result.exit_code != 0
        assert "Insecure" in result.output or "rejected" in result.output

    def test_connect_warns_on_hub_switch(self, repo: pathlib.Path) -> None:
        runner.invoke(cli, ["hub", "connect", "https://hub1.example.com"])
        result = runner.invoke(cli, ["hub", "connect", "https://hub2.example.com"])
        assert result.exit_code == 0
        assert "hub1.example.com" in result.output or "Switching" in result.output

    def test_connect_shows_identity_if_already_logged_in(self, repo: pathlib.Path) -> None:
        entry: IdentityEntry = {"type": "human", "token": "tok123", "name": "Alice"}
        save_identity("https://musehub.ai", entry)
        result = runner.invoke(cli, ["hub", "connect", "https://musehub.ai"])
        assert result.exit_code == 0
        assert "Alice" in result.output or "human" in result.output

    def test_connect_prompts_login_when_no_identity(self, repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["hub", "connect", "https://musehub.ai"])
        assert result.exit_code == 0
        assert "muse auth login" in result.output

    def test_connect_fails_outside_repo(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("MUSE_REPO_ROOT", raising=False)
        result = runner.invoke(cli, ["hub", "connect", "https://musehub.ai"])
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# hub status
# ---------------------------------------------------------------------------


class TestHubStatus:
    def _setup_hub(self, repo: pathlib.Path) -> None:
        set_hub_url("https://musehub.ai", repo)

    def test_no_hub_exits_nonzero(self, repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["hub", "status"])
        assert result.exit_code != 0

    def test_hub_url_shown(self, repo: pathlib.Path) -> None:
        self._setup_hub(repo)
        result = runner.invoke(cli, ["hub", "status"])
        assert result.exit_code == 0
        assert "musehub.ai" in result.output

    def test_not_authenticated_shown(self, repo: pathlib.Path) -> None:
        self._setup_hub(repo)
        result = runner.invoke(cli, ["hub", "status"])
        assert "not authenticated" in result.output or "auth login" in result.output

    def test_identity_fields_shown_when_logged_in(self, repo: pathlib.Path) -> None:
        self._setup_hub(repo)
        entry: IdentityEntry = {"type": "agent", "token": "tok", "name": "bot", "id": "agt_001"}
        save_identity("https://musehub.ai", entry)
        result = runner.invoke(cli, ["hub", "status"])
        assert "agent" in result.output
        assert "bot" in result.output

    def test_json_output_structure(self, repo: pathlib.Path) -> None:
        self._setup_hub(repo)
        result = runner.invoke(cli, ["hub", "status", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "hub_url" in data
        assert "hostname" in data
        assert "authenticated" in data

    def test_json_output_with_identity(self, repo: pathlib.Path) -> None:
        self._setup_hub(repo)
        entry: IdentityEntry = {"type": "human", "token": "t", "name": "Alice", "id": "usr_1"}
        save_identity("https://musehub.ai", entry)
        result = runner.invoke(cli, ["hub", "status", "--json"])
        data = json.loads(result.output)
        assert data["authenticated"] is True
        assert data["identity_type"] == "human"
        assert data["identity_name"] == "Alice"


# ---------------------------------------------------------------------------
# hub disconnect
# ---------------------------------------------------------------------------


class TestHubDisconnect:
    def test_disconnect_clears_hub_url(self, repo: pathlib.Path) -> None:
        set_hub_url("https://musehub.ai", repo)
        result = runner.invoke(cli, ["hub", "disconnect"])
        assert result.exit_code == 0
        assert get_hub_url(repo) is None

    def test_disconnect_shows_hostname(self, repo: pathlib.Path) -> None:
        set_hub_url("https://musehub.ai", repo)
        result = runner.invoke(cli, ["hub", "disconnect"])
        assert "musehub.ai" in result.output

    def test_disconnect_nothing_to_do(self, repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["hub", "disconnect"])
        assert result.exit_code == 0
        assert "nothing" in result.output.lower() or "No hub" in result.output

    def test_disconnect_preserves_identity(self, repo: pathlib.Path) -> None:
        """Credentials in identity.toml must survive hub disconnect."""
        set_hub_url("https://musehub.ai", repo)
        entry: IdentityEntry = {"type": "human", "token": "secret"}
        save_identity("https://musehub.ai", entry)
        runner.invoke(cli, ["hub", "disconnect"])
        from muse.core.identity import load_identity
        assert load_identity("https://musehub.ai") is not None

    def test_disconnect_fails_outside_repo(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("MUSE_REPO_ROOT", raising=False)
        result = runner.invoke(cli, ["hub", "disconnect"])
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# hub ping
# ---------------------------------------------------------------------------


class TestHubPing:
    def _setup_hub(self, repo: pathlib.Path) -> None:
        set_hub_url("https://musehub.ai", repo)

    def test_ping_success(self, repo: pathlib.Path) -> None:
        self._setup_hub(repo)
        mock_resp = unittest.mock.MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = unittest.mock.MagicMock(return_value=False)
        with unittest.mock.patch("muse.cli.commands.hub._PING_OPENER.open", return_value=mock_resp):
            result = runner.invoke(cli, ["hub", "ping"])
        assert result.exit_code == 0
        assert "200" in result.output or "OK" in result.output.upper()

    def test_ping_failure_exits_nonzero(self, repo: pathlib.Path) -> None:
        self._setup_hub(repo)
        err = urllib.error.URLError("no route to host")
        with unittest.mock.patch("muse.cli.commands.hub._PING_OPENER.open", side_effect=err):
            result = runner.invoke(cli, ["hub", "ping"])
        assert result.exit_code != 0

    def test_ping_no_hub_exits_nonzero(self, repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["hub", "ping"])
        assert result.exit_code != 0

    def test_ping_fails_outside_repo(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("MUSE_REPO_ROOT", raising=False)
        result = runner.invoke(cli, ["hub", "ping"])
        assert result.exit_code != 0

"""Tests for ``muse domains publish`` — the MuseHub marketplace publisher.

Covers:
  Unit tests for ``_post_json`` helper:
    - Sends correct HTTP method, URL, headers, and JSON body.
    - Returns a typed _PublishResponse on 200.
    - Raises HTTPError on non-2xx.
    - Raises ValueError on non-object JSON.

  CLI integration tests (via Typer CliRunner, no real HTTP):
    - Successful publish with --capabilities JSON emits domain_id / scoped_id.
    - Successful publish with --json emits machine-readable JSON.
    - Missing required args → UsageError (non-zero exit).
    - No auth token → exit 1 with clear message.
    - HTTP 409 conflict → exit 1 with "already registered" message.
    - HTTP 401 → exit 1 with "Authentication failed" message.
    - Network error (URLError) → exit 1 with "Could not reach" message.
    - Non-JSON response body → exit 1 with "Unexpected response" message.
    - Capabilities derived from plugin schema when --capabilities is omitted.
"""
from __future__ import annotations

import http.client
import io
import json
import pathlib
import urllib.error
import urllib.request
import urllib.response
import unittest.mock
from typing import TYPE_CHECKING

import pytest
from typer.testing import CliRunner

from muse._version import __version__
from muse.cli.app import cli
from muse.cli.commands.domains import _post_json, _PublishPayload, _Capabilities, _DimensionDef

if TYPE_CHECKING:
    pass

runner = CliRunner()

# ---------------------------------------------------------------------------
# Fixture: minimal Muse repo with auth token
# ---------------------------------------------------------------------------

_BASE_CAPS_JSON = json.dumps({
    "dimensions": [{"name": "notes", "description": "Note events"}],
    "artifact_types": ["mid"],
    "merge_semantics": "three_way",
    "supported_commands": ["commit", "diff"],
})

_REQUIRED_ARGS = [
    "domains", "publish",
    "--author", "testuser",
    "--slug", "genomics",
    "--name", "Genomics",
    "--description", "Version DNA sequences",
    "--viewer-type", "genome",
    "--capabilities", _BASE_CAPS_JSON,
    "--hub", "https://hub.test",
]


@pytest.fixture()
def repo(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    """Minimal .muse/ repo; auth token is mocked via get_auth_token."""
    muse_dir = tmp_path / ".muse"
    (muse_dir / "refs" / "heads").mkdir(parents=True)
    (muse_dir / "objects").mkdir()
    (muse_dir / "commits").mkdir()
    (muse_dir / "snapshots").mkdir()
    (muse_dir / "repo.json").write_text(
        json.dumps({"repo_id": "test-repo", "schema_version": __version__, "domain": "midi"})
    )
    (muse_dir / "HEAD").write_text("ref: refs/heads/main\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("muse.cli.commands.domains.get_auth_token", lambda *a, **kw: "test-token-abc")
    return tmp_path


@pytest.fixture()
def repo_no_token(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    """Minimal .muse/ repo where get_auth_token returns None (no token)."""
    muse_dir = tmp_path / ".muse"
    (muse_dir / "refs" / "heads").mkdir(parents=True)
    (muse_dir / "objects").mkdir()
    (muse_dir / "commits").mkdir()
    (muse_dir / "snapshots").mkdir()
    (muse_dir / "repo.json").write_text(
        json.dumps({"repo_id": "test-repo", "schema_version": __version__, "domain": "midi"})
    )
    (muse_dir / "HEAD").write_text("ref: refs/heads/main\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("muse.cli.commands.domains.get_auth_token", lambda *a, **kw: None)
    return tmp_path


# ---------------------------------------------------------------------------
# Helper: build a mock urllib response
# ---------------------------------------------------------------------------


def _mock_urlopen(response_body: str | bytes, status: int = 200) -> unittest.mock.MagicMock:
    """Return a context-manager mock that yields a fake HTTP response."""
    if isinstance(response_body, str):
        response_body = response_body.encode()
    mock_resp = unittest.mock.MagicMock()
    mock_resp.read.return_value = response_body
    mock_resp.__enter__ = unittest.mock.MagicMock(return_value=mock_resp)
    mock_resp.__exit__ = unittest.mock.MagicMock(return_value=False)
    return mock_resp


# ---------------------------------------------------------------------------
# Unit tests for _post_json
# ---------------------------------------------------------------------------


def test_post_json_sends_correct_request() -> None:
    """_post_json should POST JSON to the given URL with Auth header."""
    payload = _PublishPayload(
        author_slug="alice",
        slug="spatial",
        display_name="Spatial 3D",
        description="Version 3D scenes",
        capabilities=_Capabilities(
            dimensions=[_DimensionDef(name="geometry", description="Mesh data")],
            artifact_types=["glb"],
            merge_semantics="three_way",
            supported_commands=["commit"],
        ),
        viewer_type="spatial",
        version="0.1.0",
    )

    captured: list[urllib.request.Request] = []

    def _fake_urlopen(
        req: urllib.request.Request, timeout: float | None = None
    ) -> unittest.mock.MagicMock:
        captured.append(req)
        mock_resp = _mock_urlopen(json.dumps({"domain_id": "d1", "scoped_id": "@alice/spatial", "manifest_hash": "abc"}))
        return mock_resp

    with unittest.mock.patch("urllib.request.urlopen", side_effect=_fake_urlopen):
        result = _post_json("https://hub.test/api/v1/domains", payload, "tok-123")

    assert len(captured) == 1
    req = captured[0]
    assert req.get_method() == "POST"
    assert req.full_url == "https://hub.test/api/v1/domains"
    assert req.get_header("Authorization") == "Bearer tok-123"
    assert req.get_header("Content-type") == "application/json"
    assert req.data is not None
    body = json.loads(req.data.decode())
    assert body["author_slug"] == "alice"
    assert body["slug"] == "spatial"

    assert result["scoped_id"] == "@alice/spatial"


def test_post_json_raises_on_non_object_response() -> None:
    """_post_json should raise ValueError when server returns a JSON array."""
    payload = _PublishPayload(
        author_slug="bob", slug="s", display_name="S", description="d",
        capabilities=_Capabilities(), viewer_type="v", version="0.1.0",
    )
    mock_resp = _mock_urlopen(json.dumps(["not", "an", "object"]))
    with unittest.mock.patch("urllib.request.urlopen", return_value=mock_resp):
        with pytest.raises(ValueError, match="Expected JSON object"):
            _post_json("https://hub.test/api/v1/domains", payload, "tok")


def test_post_json_raises_http_error() -> None:
    """_post_json should propagate HTTPError from urlopen."""
    payload = _PublishPayload(
        author_slug="bob", slug="s", display_name="S", description="d",
        capabilities=_Capabilities(), viewer_type="v", version="0.1.0",
    )
    err = urllib.error.HTTPError(
        url="https://hub.test/api/v1/domains",
        code=409,
        msg="Conflict",
        hdrs=http.client.HTTPMessage(),
        fp=io.BytesIO(b'{"error": "already_exists"}'),
    )
    with unittest.mock.patch("urllib.request.urlopen", side_effect=err):
        with pytest.raises(urllib.error.HTTPError):
            _post_json("https://hub.test/api/v1/domains", payload, "tok")


# ---------------------------------------------------------------------------
# CLI integration tests
# ---------------------------------------------------------------------------


def test_publish_success(repo: pathlib.Path) -> None:
    """Successful publish prints domain scoped_id and manifest_hash."""
    server_resp = json.dumps({
        "domain_id": "dom-001",
        "scoped_id": "@testuser/genomics",
        "manifest_hash": "sha256:abc123",
    })
    mock_resp = _mock_urlopen(server_resp)

    with unittest.mock.patch("urllib.request.urlopen", return_value=mock_resp):
        result = runner.invoke(cli, _REQUIRED_ARGS)

    assert result.exit_code == 0, result.output
    assert "@testuser/genomics" in result.output
    assert "sha256:abc123" in result.output


def test_publish_json_flag(repo: pathlib.Path) -> None:
    """--json flag emits machine-readable JSON."""
    server_resp = json.dumps({
        "domain_id": "dom-002",
        "scoped_id": "@testuser/genomics",
        "manifest_hash": "sha256:def456",
    })
    mock_resp = _mock_urlopen(server_resp)

    with unittest.mock.patch("urllib.request.urlopen", return_value=mock_resp):
        result = runner.invoke(cli, _REQUIRED_ARGS + ["--json"])

    assert result.exit_code == 0, result.output
    data = json.loads(result.output.strip())
    assert data["scoped_id"] == "@testuser/genomics"


def test_publish_no_token(repo_no_token: pathlib.Path) -> None:
    """Missing auth token should exit 1 with clear message."""
    result = runner.invoke(cli, _REQUIRED_ARGS)
    assert result.exit_code != 0
    assert "token" in result.output.lower() or "auth" in result.output.lower()


def test_publish_http_409_conflict(repo: pathlib.Path) -> None:
    """HTTP 409 should exit 1 with 'already registered' message."""
    err = urllib.error.HTTPError(
        url="https://hub.test/api/v1/domains",
        code=409,
        msg="Conflict",
        hdrs=http.client.HTTPMessage(),
        fp=io.BytesIO(b'{"error": "already_exists"}'),
    )
    with unittest.mock.patch("urllib.request.urlopen", side_effect=err):
        result = runner.invoke(cli, _REQUIRED_ARGS)

    assert result.exit_code != 0
    assert "already registered" in result.output.lower() or "409" in result.output


def test_publish_http_401_unauthorized(repo: pathlib.Path) -> None:
    """HTTP 401 should exit 1 with authentication message."""
    err = urllib.error.HTTPError(
        url="https://hub.test/api/v1/domains",
        code=401,
        msg="Unauthorized",
        hdrs=http.client.HTTPMessage(),
        fp=io.BytesIO(b'{"error": "unauthorized"}'),
    )
    with unittest.mock.patch("urllib.request.urlopen", side_effect=err):
        result = runner.invoke(cli, _REQUIRED_ARGS)

    assert result.exit_code != 0
    assert "authentication" in result.output.lower() or "token" in result.output.lower()


def test_publish_network_error(repo: pathlib.Path) -> None:
    """URLError (network failure) should exit 1 with 'Could not reach' message."""
    err = urllib.error.URLError(reason="Connection refused")
    with unittest.mock.patch("urllib.request.urlopen", side_effect=err):
        result = runner.invoke(cli, _REQUIRED_ARGS)

    assert result.exit_code != 0
    assert "could not reach" in result.output.lower()


def test_publish_bad_json_response(repo: pathlib.Path) -> None:
    """Non-JSON server response should exit 1 with 'Unexpected response' message."""
    mock_resp = _mock_urlopen(b"not json at all")
    with unittest.mock.patch("urllib.request.urlopen", return_value=mock_resp):
        result = runner.invoke(cli, _REQUIRED_ARGS)

    assert result.exit_code != 0
    assert "unexpected" in result.output.lower()


def test_publish_missing_required_author(repo: pathlib.Path) -> None:
    """Omitting --author should produce a non-zero exit and usage message."""
    args = [a for a in _REQUIRED_ARGS if a != "--author" and a != "testuser"]
    result = runner.invoke(cli, args)
    assert result.exit_code != 0


def test_publish_missing_required_slug(repo: pathlib.Path) -> None:
    """Omitting --slug should produce a non-zero exit."""
    args = [a for a in _REQUIRED_ARGS if a != "--slug" and a != "genomics"]
    result = runner.invoke(cli, args)
    assert result.exit_code != 0


def test_publish_capabilities_from_plugin_schema(repo: pathlib.Path) -> None:
    """When --capabilities is omitted, schema is derived from active domain plugin."""
    # Remove --capabilities from args
    args_no_caps = [
        a for i, a in enumerate(_REQUIRED_ARGS)
        if a not in ("--capabilities", _BASE_CAPS_JSON)
        and not (i > 0 and _REQUIRED_ARGS[i - 1] == "--capabilities")
    ]

    server_resp = json.dumps({
        "domain_id": "dom-plugin",
        "scoped_id": "@testuser/genomics",
        "manifest_hash": "sha256:plugin",
    })
    mock_resp = _mock_urlopen(server_resp)

    with unittest.mock.patch("urllib.request.urlopen", return_value=mock_resp):
        result = runner.invoke(cli, args_no_caps)

    # Should succeed (midi plugin schema is available)
    assert result.exit_code == 0, result.output
    assert "@testuser/genomics" in result.output


def test_publish_invalid_capabilities_json(repo: pathlib.Path) -> None:
    """--capabilities with invalid JSON should exit 1."""
    bad_caps_args = [
        "domains", "publish",
        "--author", "testuser",
        "--slug", "genomics",
        "--name", "Genomics",
        "--description", "Version DNA sequences",
        "--viewer-type", "genome",
        "--capabilities", "{not valid json",
        "--hub", "https://hub.test",
    ]
    result = runner.invoke(cli, bad_caps_args)
    assert result.exit_code != 0
    assert "json" in result.output.lower()


def test_publish_http_500_server_error(repo: pathlib.Path) -> None:
    """HTTP 5xx should exit 1 with HTTP error code shown."""
    err = urllib.error.HTTPError(
        url="https://hub.test/api/v1/domains",
        code=500,
        msg="Internal Server Error",
        hdrs=http.client.HTTPMessage(),
        fp=io.BytesIO(b'{"error": "server_error"}'),
    )
    with unittest.mock.patch("urllib.request.urlopen", side_effect=err):
        result = runner.invoke(cli, _REQUIRED_ARGS)

    assert result.exit_code != 0
    assert "500" in result.output


def test_publish_custom_version(repo: pathlib.Path) -> None:
    """--version flag is passed to the server payload."""
    captured_bodies: list[bytes] = []

    def _capture(req: urllib.request.Request, timeout: float | None = None) -> unittest.mock.MagicMock:
        raw = req.data
        captured_bodies.append(raw if raw is not None else b"")
        return _mock_urlopen(json.dumps({"domain_id": "d", "scoped_id": "@testuser/genomics", "manifest_hash": "h"}))

    with unittest.mock.patch("urllib.request.urlopen", side_effect=_capture):
        result = runner.invoke(cli, _REQUIRED_ARGS + ["--version", "1.2.3"])

    assert result.exit_code == 0, result.output
    body = json.loads(captured_bodies[0])
    assert body["version"] == "1.2.3"

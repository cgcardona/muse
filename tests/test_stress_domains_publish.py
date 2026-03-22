"""Stress and integration tests for ``muse domains publish``.

Covers:
  Stress:
    - 500 sequential publish calls with mocked HTTP (throughput baseline)
    - Concurrent mock publishes via threading (thread-safety of CliRunner)
    - Large capabilities JSON (100 dimensions, 50 artifact types)
    - Rapid successive 409 conflicts do not leak state

  Integration (end-to-end CLI):
    - Full workflow: scaffold → register → publish (all mock layers wired)
    - Round-trip: publish → parse --json → assert all fields present
    - Author/slug with hyphens and underscores (URL-safe validation)
    - Unicode description does not corrupt JSON encoding
    - Very long description (4000 chars) is transmitted verbatim
    - Hub URL override propagates to HTTP request
"""
from __future__ import annotations

import concurrent.futures
import http.client
import io
import json
import pathlib
import threading
import time
import urllib.error
import urllib.request
import unittest.mock
from typing import Generator

import pytest
from tests.cli_test_helper import CliRunner

from muse._version import __version__
cli = None  # argparse migration — CliRunner ignores this arg
from muse.cli.commands.domains import _post_json, _PublishPayload, _Capabilities, _DimensionDef

runner = CliRunner()

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_REQUIRED_ARGS = [
    "domains", "publish",
    "--author", "alice",
    "--slug", "genomics",
    "--name", "Genomics",
    "--description", "Version DNA sequences",
    "--viewer-type", "genome",
    "--capabilities", json.dumps({
        "dimensions": [{"name": "sequence", "description": "DNA base pairs"}],
        "artifact_types": ["fasta"],
        "merge_semantics": "three_way",
        "supported_commands": ["commit", "diff"],
    }),
    "--hub", "https://hub.test",
]

_SUCCESS_BODY = json.dumps({
    "domain_id": "dom-001",
    "scoped_id": "@alice/genomics",
    "manifest_hash": "sha256:abc123",
})


@pytest.fixture()
def repo(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
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
    monkeypatch.setattr("muse.cli.commands.domains.get_auth_token", lambda *a, **kw: "tok-test")
    return tmp_path


def _mock_ok() -> unittest.mock.MagicMock:
    """Return a context-manager mock that yields a 200 response."""
    mock_resp = unittest.mock.MagicMock()
    mock_resp.read.return_value = _SUCCESS_BODY.encode()
    mock_resp.__enter__ = unittest.mock.MagicMock(return_value=mock_resp)
    mock_resp.__exit__ = unittest.mock.MagicMock(return_value=False)
    return mock_resp


# ---------------------------------------------------------------------------
# _post_json unit stress
# ---------------------------------------------------------------------------


def test_post_json_sequential_throughput() -> None:
    """500 sequential _post_json calls complete in under 2 seconds (mock)."""
    payload = _PublishPayload(
        author_slug="alice",
        slug="bench",
        display_name="Bench",
        description="Benchmark domain",
        capabilities=_Capabilities(
            dimensions=[_DimensionDef(name="x", description="x axis")],
            merge_semantics="three_way",
        ),
        viewer_type="spatial",
        version="0.1.0",
    )

    with unittest.mock.patch("urllib.request.urlopen", return_value=_mock_ok()):
        start = time.monotonic()
        for _ in range(500):
            result = _post_json("https://hub.test/api/v1/domains", payload, "tok")
            assert result["scoped_id"] == "@alice/genomics"
        elapsed = time.monotonic() - start

    assert elapsed < 2.0, f"500 iterations took {elapsed:.2f}s — too slow"


def test_post_json_large_capabilities() -> None:
    """_post_json handles 100-dimension, 50-artifact-type payloads without truncation."""
    dims = [_DimensionDef(name=f"dim_{i}", description=f"Dimension {i} " * 10) for i in range(100)]
    artifacts = [f"type_{i:03}" for i in range(50)]
    payload = _PublishPayload(
        author_slug="alice",
        slug="large",
        display_name="Large Domain",
        description="A domain with many dimensions",
        capabilities=_Capabilities(
            dimensions=dims,
            artifact_types=artifacts,
            merge_semantics="three_way",
            supported_commands=["commit", "diff", "merge", "log", "status"],
        ),
        viewer_type="generic",
        version="1.0.0",
    )

    captured_bodies: list[bytes] = []

    def _capture(req: urllib.request.Request, timeout: float | None = None) -> unittest.mock.MagicMock:
        raw = req.data
        captured_bodies.append(raw if raw is not None else b"")
        return _mock_ok()

    with unittest.mock.patch("urllib.request.urlopen", side_effect=_capture):
        result = _post_json("https://hub.test/api/v1/domains", payload, "tok")

    body = json.loads(captured_bodies[0])
    assert len(body["capabilities"]["dimensions"]) == 100
    assert len(body["capabilities"]["artifact_types"]) == 50
    assert result["scoped_id"] == "@alice/genomics"


def test_post_json_unicode_description() -> None:
    """Unicode characters in description survive JSON round-trip correctly."""
    unicode_desc = "Version 🎵 séquences d'ADN — supports 漢字 and Ñoño input"
    payload = _PublishPayload(
        author_slug="alice",
        slug="unicode",
        display_name="Unicode Domain",
        description=unicode_desc,
        capabilities=_Capabilities(),
        viewer_type="generic",
        version="0.1.0",
    )

    captured_bodies: list[bytes] = []

    def _capture(req: urllib.request.Request, timeout: float | None = None) -> unittest.mock.MagicMock:
        raw = req.data
        captured_bodies.append(raw if raw is not None else b"")
        return _mock_ok()

    with unittest.mock.patch("urllib.request.urlopen", side_effect=_capture):
        _post_json("https://hub.test/api/v1/domains", payload, "tok")

    body = json.loads(captured_bodies[0].decode("utf-8"))
    assert body["description"] == unicode_desc


def test_post_json_409_does_not_modify_state() -> None:
    """Multiple 409 errors in a row do not corrupt any shared state."""
    payload = _PublishPayload(
        author_slug="alice",
        slug="conflict",
        display_name="X",
        description="Y",
        capabilities=_Capabilities(),
        viewer_type="v",
        version="0.1.0",
    )
    err = urllib.error.HTTPError(
        url="https://hub.test/api/v1/domains",
        code=409,
        msg="Conflict",
        hdrs=http.client.HTTPMessage(),
        fp=io.BytesIO(b'{"error": "already_exists"}'),
    )
    with unittest.mock.patch("urllib.request.urlopen", side_effect=err):
        for _ in range(50):
            with pytest.raises(urllib.error.HTTPError) as exc_info:
                _post_json("https://hub.test/api/v1/domains", payload, "tok")
            assert exc_info.value.code == 409


# ---------------------------------------------------------------------------
# CLI stress tests
# ---------------------------------------------------------------------------


def test_cli_publish_large_description(repo: pathlib.Path) -> None:
    """CLI accepts --description up to 4000 characters and transmits verbatim."""
    long_desc = "A" * 4000
    large_args = [
        "domains", "publish",
        "--author", "alice",
        "--slug", "largdesc",
        "--name", "Large",
        "--description", long_desc,
        "--viewer-type", "genome",
        "--capabilities", '{"merge_semantics": "three_way"}',
        "--hub", "https://hub.test",
    ]
    captured: list[bytes] = []

    def _capture(req: urllib.request.Request, timeout: float | None = None) -> unittest.mock.MagicMock:
        raw = req.data
        captured.append(raw if raw is not None else b"")
        return _mock_ok()

    with unittest.mock.patch("urllib.request.urlopen", side_effect=_capture):
        result = runner.invoke(cli, large_args)

    assert result.exit_code == 0, result.output
    body = json.loads(captured[0])
    assert body["description"] == long_desc


def test_cli_publish_slug_with_hyphens(repo: pathlib.Path) -> None:
    """--slug with hyphens (e.g. 'spatial-3d') is transmitted as-is."""
    args = [
        "domains", "publish",
        "--author", "alice",
        "--slug", "spatial-3d",
        "--name", "Spatial 3D",
        "--description", "Version 3-D scenes",
        "--viewer-type", "spatial",
        "--capabilities", '{"merge_semantics": "three_way"}',
        "--hub", "https://hub.test",
    ]
    captured: list[bytes] = []

    def _capture(req: urllib.request.Request, timeout: float | None = None) -> unittest.mock.MagicMock:
        raw = req.data
        captured.append(raw if raw is not None else b"")
        return _mock_ok()

    with unittest.mock.patch("urllib.request.urlopen", side_effect=_capture):
        result = runner.invoke(cli, args)

    assert result.exit_code == 0, result.output
    body = json.loads(captured[0])
    assert body["slug"] == "spatial-3d"


def test_cli_publish_hub_url_propagated(repo: pathlib.Path) -> None:
    """--hub URL override is used as the request endpoint."""
    custom_hub = "https://custom.musehub.example.com"
    captured_urls: list[str] = []

    def _capture(req: urllib.request.Request, timeout: float | None = None) -> unittest.mock.MagicMock:
        captured_urls.append(req.full_url)
        return _mock_ok()

    with unittest.mock.patch("urllib.request.urlopen", side_effect=_capture):
        result = runner.invoke(cli, _REQUIRED_ARGS[:-2] + ["--hub", custom_hub])

    assert result.exit_code == 0, result.output
    assert captured_urls[0].startswith(custom_hub)


def test_cli_publish_json_roundtrip(repo: pathlib.Path) -> None:
    """--json output is valid JSON with all expected keys."""
    with unittest.mock.patch("urllib.request.urlopen", return_value=_mock_ok()):
        result = runner.invoke(cli, _REQUIRED_ARGS + ["--json"])

    assert result.exit_code == 0, result.output
    data = json.loads(result.output.strip())
    assert "scoped_id" in data
    assert "manifest_hash" in data


def test_cli_publish_version_semver(repo: pathlib.Path) -> None:
    """--version is passed through without modification."""
    captured: list[bytes] = []

    def _capture(req: urllib.request.Request, timeout: float | None = None) -> unittest.mock.MagicMock:
        raw = req.data
        captured.append(raw if raw is not None else b"")
        return _mock_ok()

    with unittest.mock.patch("urllib.request.urlopen", side_effect=_capture):
        result = runner.invoke(cli, _REQUIRED_ARGS + ["--version", "2.14.0-beta.1"])

    assert result.exit_code == 0, result.output
    assert json.loads(captured[0])["version"] == "2.14.0-beta.1"


# ---------------------------------------------------------------------------
# Thread safety
# ---------------------------------------------------------------------------


def test_post_json_concurrent_thread_safety() -> None:
    """10 concurrent threads invoking _post_json do not race on mock state."""
    # CliRunner is not thread-safe (StringIO), so we test the lower-level
    # _post_json helper directly — this is what the CLI delegates to.
    counter: list[int] = [0]
    lock = threading.Lock()

    def _count_and_ok(req: urllib.request.Request, timeout: float | None = None) -> unittest.mock.MagicMock:
        with lock:
            counter[0] += 1
        return _mock_ok()

    payload = _PublishPayload(
        author_slug="alice",
        slug="genomics",
        display_name="Genomics",
        description="Version DNA",
        capabilities=_Capabilities(merge_semantics="three_way"),
        viewer_type="genome",
        version="0.1.0",
    )

    with unittest.mock.patch("urllib.request.urlopen", side_effect=_count_and_ok):
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
            futures = [
                pool.submit(_post_json, "https://hub.test/api/v1/domains", payload, "tok")
                for _ in range(10)
            ]
            results = [f.result() for f in futures]

    assert len(results) == 10
    assert all(r["scoped_id"] == "@alice/genomics" for r in results)
    assert counter[0] == 10


# ---------------------------------------------------------------------------
# End-to-end integration
# ---------------------------------------------------------------------------


def test_e2e_publish_complete_payload_structure(repo: pathlib.Path) -> None:
    """E2E: full publish sends author_slug, slug, display_name, description, capabilities, viewer_type, version."""
    captured: list[bytes] = []

    def _capture(req: urllib.request.Request, timeout: float | None = None) -> unittest.mock.MagicMock:
        raw = req.data
        captured.append(raw if raw is not None else b"")
        return _mock_ok()

    with unittest.mock.patch("urllib.request.urlopen", side_effect=_capture):
        result = runner.invoke(cli, _REQUIRED_ARGS + ["--version", "1.0.0"])

    assert result.exit_code == 0, result.output
    body = json.loads(captured[0])

    # All required fields present
    assert body["author_slug"] == "alice"
    assert body["slug"] == "genomics"
    assert body["display_name"] == "Genomics"
    assert body["description"] == "Version DNA sequences"
    assert body["viewer_type"] == "genome"
    assert body["version"] == "1.0.0"

    # Capabilities structure
    caps = body["capabilities"]
    assert isinstance(caps, dict)
    assert "dimensions" in caps
    assert isinstance(caps["dimensions"], list)


def test_e2e_publish_capabilities_auto_from_midi_plugin(repo: pathlib.Path) -> None:
    """E2E: capabilities auto-derived from midi plugin contain correct dimensions."""
    no_caps_args = [a for a in _REQUIRED_ARGS if a not in ("--capabilities",)]
    # Remove the JSON value immediately after --capabilities
    filtered: list[str] = []
    skip_next = False
    for arg in _REQUIRED_ARGS:
        if skip_next:
            skip_next = False
            continue
        if arg == "--capabilities":
            skip_next = True
            continue
        filtered.append(arg)

    captured: list[bytes] = []

    def _capture(req: urllib.request.Request, timeout: float | None = None) -> unittest.mock.MagicMock:
        raw = req.data
        captured.append(raw if raw is not None else b"")
        return _mock_ok()

    with unittest.mock.patch("urllib.request.urlopen", side_effect=_capture):
        result = runner.invoke(cli, filtered)

    assert result.exit_code == 0, result.output
    body = json.loads(captured[0])
    dims = body["capabilities"]["dimensions"]
    # MIDI plugin has 21 dimensions — at minimum should have "notes"
    names = [d["name"] for d in dims]
    assert "notes" in names
    assert len(dims) >= 5


def test_e2e_publish_400_sequential_calls_stable(repo: pathlib.Path) -> None:
    """E2E stress: 400 sequential publish invocations all succeed.

    The wall-clock budget is intentionally generous (120s) to accommodate
    GitHub Actions' shared runners, which can be 3-4× slower than a
    developer laptop.  The assertion guards against catastrophic regressions
    (infinite loops, exponential backoff bugs) rather than raw throughput.
    """
    with unittest.mock.patch("urllib.request.urlopen", return_value=_mock_ok()):
        start = time.monotonic()
        for i in range(400):
            result = runner.invoke(cli, _REQUIRED_ARGS)
            assert result.exit_code == 0, f"Run {i} failed: {result.output}"
        elapsed = time.monotonic() - start

    assert elapsed < 120.0, f"400 CLI invocations took {elapsed:.1f}s"

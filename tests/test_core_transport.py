"""Tests for muse.core.transport — HttpTransport and response parsers."""

from __future__ import annotations

import json
import unittest.mock
import urllib.error
import urllib.request
from io import BytesIO

import msgpack
import pytest

from muse.core.pack import PackBundle, RemoteInfo
from muse.core.transport import (
    HttpTransport,
    TransportError,
    _parse_bundle,
    _parse_push_result,
    _parse_remote_info,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_response(body: bytes, status: int = 200, content_type: str = "application/x-msgpack") -> unittest.mock.MagicMock:
    """Return a mock urllib response context manager."""
    resp = unittest.mock.MagicMock()
    resp.read.return_value = body
    resp.headers = {"Content-Type": content_type}
    resp.__enter__ = lambda s: s
    resp.__exit__ = unittest.mock.MagicMock(return_value=False)
    return resp


def _mp(data: dict[str, str | int | float | bool | bytes | None | list[str | dict[str, str | None | bool]] | dict[str, str]]) -> bytes:
    """Encode data as msgpack."""
    return msgpack.packb(data, use_bin_type=True)


def _http_error(code: int, body: bytes = b"") -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        url="https://example.com",
        code=code,
        msg=str(code),
        hdrs=None,
        fp=BytesIO(body),
    )


# ---------------------------------------------------------------------------
# _parse_remote_info
# ---------------------------------------------------------------------------


class TestParseRemoteInfo:
    def test_valid_response(self) -> None:
        raw = _mp(
            {
                "repo_id": "r123",
                "domain": "midi",
                "default_branch": "main",
                "branch_heads": {"main": "abc123", "dev": "def456"},
            }
        )
        info = _parse_remote_info(raw)
        assert info["repo_id"] == "r123"
        assert info["domain"] == "midi"
        assert info["default_branch"] == "main"
        assert info["branch_heads"] == {"main": "abc123", "dev": "def456"}

    def test_invalid_msgpack_raises_transport_error(self) -> None:
        with pytest.raises(TransportError):
            _parse_remote_info(b"\xff\xff\xff\xff\xff invalid")

    def test_non_dict_response_returns_defaults(self) -> None:
        raw = _mp([1, 2, 3])
        info = _parse_remote_info(raw)
        assert info["repo_id"] == ""
        assert info["branch_heads"] == {}

    def test_missing_fields_get_defaults(self) -> None:
        raw = _mp({"repo_id": "x"})
        info = _parse_remote_info(raw)
        assert info["repo_id"] == "x"
        assert info["domain"] == "midi"
        assert info["default_branch"] == "main"
        assert info["branch_heads"] == {}

    def test_non_string_branch_heads_excluded(self) -> None:
        raw = _mp({"branch_heads": {"main": "abc", "bad": 123}})
        info = _parse_remote_info(raw)
        assert "main" in info["branch_heads"]
        assert "bad" not in info["branch_heads"]


# ---------------------------------------------------------------------------
# _parse_bundle
# ---------------------------------------------------------------------------


class TestParseBundle:
    def test_empty_msgpack_object_returns_empty_bundle(self) -> None:
        bundle = _parse_bundle(_mp({}))
        assert bundle == {}

    def test_non_dict_returns_empty_bundle(self) -> None:
        bundle = _parse_bundle(_mp([]))
        assert bundle == {}

    def test_commits_extracted(self) -> None:
        raw = _mp(
            {
                "commits": [
                    {
                        "commit_id": "c1",
                        "repo_id": "r1",
                        "branch": "main",
                        "snapshot_id": "1" * 64,
                        "message": "test",
                        "committed_at": "2026-01-01T00:00:00+00:00",
                        "parent_commit_id": None,
                        "parent2_commit_id": None,
                        "author": "bob",
                        "metadata": {},
                    }
                ]
            }
        )
        bundle = _parse_bundle(raw)
        commits = bundle.get("commits") or []
        assert len(commits) == 1
        assert commits[0]["commit_id"] == "c1"

    def test_objects_extracted(self) -> None:
        raw = _mp(
            {
                "objects": [
                    {
                        "object_id": "abc123",
                        "content": b"hello",
                    }
                ]
            }
        )
        bundle = _parse_bundle(raw)
        objs = bundle.get("objects") or []
        assert len(objs) == 1
        assert objs[0]["object_id"] == "abc123"
        assert objs[0]["content"] == b"hello"

    def test_object_missing_content_excluded(self) -> None:
        raw = _mp({"objects": [{"object_id": "abc"}]})
        bundle = _parse_bundle(raw)
        assert (bundle.get("objects") or []) == []

    def test_branch_heads_extracted(self) -> None:
        raw = _mp({"branch_heads": {"main": "abc123"}})
        bundle = _parse_bundle(raw)
        assert bundle.get("branch_heads") == {"main": "abc123"}


# ---------------------------------------------------------------------------
# _parse_push_result
# ---------------------------------------------------------------------------


class TestParsePushResult:
    def test_success_response(self) -> None:
        raw = _mp({"ok": True, "message": "pushed", "branch_heads": {"main": "abc"}})
        result = _parse_push_result(raw)
        assert result["ok"] is True
        assert result["message"] == "pushed"
        assert result["branch_heads"] == {"main": "abc"}

    def test_failure_response(self) -> None:
        raw = _mp({"ok": False, "message": "rejected", "branch_heads": {}})
        result = _parse_push_result(raw)
        assert result["ok"] is False
        assert result["message"] == "rejected"

    def test_non_msgpack_raises_transport_error(self) -> None:
        with pytest.raises(TransportError):
            _parse_push_result(b"\xff\xff invalid msgpack")

    def test_missing_ok_defaults_false(self) -> None:
        raw = _mp({"message": "hm", "branch_heads": {}})
        result = _parse_push_result(raw)
        assert result["ok"] is False


# ---------------------------------------------------------------------------
# HttpTransport — mocked urlopen
# ---------------------------------------------------------------------------


class TestHttpTransportFetchRemoteInfo:
    def test_calls_correct_endpoint(self) -> None:
        body = _mp(
            {
                "repo_id": "r1",
                "domain": "midi",
                "default_branch": "main",
                "branch_heads": {"main": "abc"},
            }
        )
        mock_resp = _mock_response(body)
        with unittest.mock.patch("muse.core.transport._open_url", return_value=mock_resp) as m:
            transport = HttpTransport()
            info = transport.fetch_remote_info("https://hub.example.com/repos/r1", None)
        req = m.call_args[0][0]
        assert req.full_url == "https://hub.example.com/repos/r1/refs"
        assert info["repo_id"] == "r1"

    def test_bearer_token_sent(self) -> None:
        body = _mp(
            {"repo_id": "r1", "domain": "midi", "default_branch": "main", "branch_heads": {}}
        )
        mock_resp = _mock_response(body)
        with unittest.mock.patch("muse.core.transport._open_url", return_value=mock_resp) as m:
            HttpTransport().fetch_remote_info("https://hub.example.com/repos/r1", "my-token")
        req = m.call_args[0][0]
        assert req.get_header("Authorization") == "Bearer my-token"

    def test_no_token_no_auth_header(self) -> None:
        body = _mp(
            {"repo_id": "r1", "domain": "midi", "default_branch": "main", "branch_heads": {}}
        )
        mock_resp = _mock_response(body)
        with unittest.mock.patch("muse.core.transport._open_url", return_value=mock_resp) as m:
            HttpTransport().fetch_remote_info("https://hub.example.com/repos/r1", None)
        req = m.call_args[0][0]
        assert req.get_header("Authorization") is None

    def test_http_401_raises_transport_error(self) -> None:
        with unittest.mock.patch(
            "muse.core.transport._open_url", side_effect=_http_error(401, b"Unauthorized")
        ):
            with pytest.raises(TransportError) as exc_info:
                HttpTransport().fetch_remote_info("https://hub.example.com/repos/r1", None)
        assert exc_info.value.status_code == 401

    def test_http_404_raises_transport_error(self) -> None:
        with unittest.mock.patch(
            "muse.core.transport._open_url", side_effect=_http_error(404)
        ):
            with pytest.raises(TransportError) as exc_info:
                HttpTransport().fetch_remote_info("https://hub.example.com/repos/r1", None)
        assert exc_info.value.status_code == 404

    def test_http_500_raises_transport_error(self) -> None:
        with unittest.mock.patch(
            "muse.core.transport._open_url", side_effect=_http_error(500, b"Internal Error")
        ):
            with pytest.raises(TransportError) as exc_info:
                HttpTransport().fetch_remote_info("https://hub.example.com/repos/r1", None)
        assert exc_info.value.status_code == 500

    def test_url_error_raises_transport_error_with_code_0(self) -> None:
        with unittest.mock.patch(
            "muse.core.transport._open_url",
            side_effect=urllib.error.URLError("Name or service not known"),
        ):
            with pytest.raises(TransportError) as exc_info:
                HttpTransport().fetch_remote_info("https://bad.host/r", None)
        assert exc_info.value.status_code == 0

    def test_trailing_slash_stripped_from_url(self) -> None:
        body = _mp(
            {"repo_id": "r", "domain": "midi", "default_branch": "main", "branch_heads": {}}
        )
        mock_resp = _mock_response(body)
        with unittest.mock.patch("muse.core.transport._open_url", return_value=mock_resp) as m:
            HttpTransport().fetch_remote_info("https://hub.example.com/repos/r1/", None)
        req = m.call_args[0][0]
        assert req.full_url == "https://hub.example.com/repos/r1/refs"


class TestHttpTransportFetchPack:
    def test_posts_to_fetch_endpoint(self) -> None:
        bundle_body = _mp(
            {
                "commits": [],
                "snapshots": [],
                "objects": [],
                "branch_heads": {"main": "abc"},
            }
        )
        mock_resp = _mock_response(bundle_body)
        with unittest.mock.patch("muse.core.transport._open_url", return_value=mock_resp) as m:
            transport = HttpTransport()
            bundle = transport.fetch_pack(
                "https://hub.example.com/repos/r1",
                "tok",
                want=["abc"],
                have=["def"],
            )
        req = m.call_args[0][0]
        assert req.full_url == "https://hub.example.com/repos/r1/fetch"
        sent = msgpack.unpackb(req.data, raw=False)
        assert sent["want"] == ["abc"]
        assert sent["have"] == ["def"]
        assert bundle.get("branch_heads") == {"main": "abc"}

    def test_http_409_raises_transport_error(self) -> None:
        with unittest.mock.patch(
            "muse.core.transport._open_url", side_effect=_http_error(409)
        ):
            with pytest.raises(TransportError) as exc_info:
                HttpTransport().fetch_pack("https://hub.example.com/r", None, [], [])
        assert exc_info.value.status_code == 409


class TestHttpTransportPushPack:
    def test_posts_to_push_endpoint(self) -> None:
        push_body = _mp({"ok": True, "message": "ok", "branch_heads": {"main": "new"}})
        mock_resp = _mock_response(push_body)
        bundle: PackBundle = {"commits": [], "snapshots": [], "objects": []}
        with unittest.mock.patch("muse.core.transport._open_url", return_value=mock_resp) as m:
            result = HttpTransport().push_pack(
                "https://hub.example.com/repos/r1", "tok", bundle, "main", False
            )
        req = m.call_args[0][0]
        assert req.full_url == "https://hub.example.com/repos/r1/push"
        sent = msgpack.unpackb(req.data, raw=False)
        assert sent["branch"] == "main"
        assert sent["force"] is False
        assert result["ok"] is True

    def test_force_flag_sent(self) -> None:
        push_body = _mp({"ok": True, "message": "", "branch_heads": {}})
        mock_resp = _mock_response(push_body)
        bundle: PackBundle = {}
        with unittest.mock.patch("muse.core.transport._open_url", return_value=mock_resp) as m:
            HttpTransport().push_pack("https://hub.example.com/r", None, bundle, "main", True)
        req = m.call_args[0][0]
        sent = msgpack.unpackb(req.data, raw=False)
        assert sent["force"] is True

    def test_push_rejected_raises_transport_error(self) -> None:
        with unittest.mock.patch(
            "muse.core.transport._open_url", side_effect=_http_error(409, b"non-fast-forward")
        ):
            with pytest.raises(TransportError) as exc_info:
                HttpTransport().push_pack(
                    "https://hub.example.com/r", None, {}, "main", False
                )
        assert exc_info.value.status_code == 409

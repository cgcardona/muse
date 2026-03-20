"""Muse transport layer — typed HTTP client for MuseHub communication.

The :class:`MuseTransport` Protocol defines the interface between the Muse CLI
and a remote host (e.g. MuseHub).  The CLI calls this Protocol; MuseHub
implements the server side.

:class:`HttpTransport` is the stdlib implementation using ``urllib.request``
(synchronous, HTTP/1.1 + TLS).  The :class:`MuseTransport` Protocol seam
means MuseHub can upgrade to HTTP/2 or gRPC on the server side without
touching any CLI command code — only the ``HttpTransport`` class changes.

MuseHub API contract
--------------------

All endpoints live under the remote repository URL
(e.g. ``https://hub.muse.io/repos/{repo_id}``).

    GET  {url}/refs
        Response: JSON :class:`~muse.core.pack.RemoteInfo`

    POST {url}/fetch
        Body:     JSON :class:`~muse.core.pack.FetchRequest`
        Response: JSON :class:`~muse.core.pack.PackBundle`

    POST {url}/push
        Body:     JSON ``{"bundle": PackBundle, "branch": str, "force": bool}``
        Response: JSON :class:`~muse.core.pack.PushResult`

Authentication
--------------

All endpoints accept an ``Authorization: Bearer <token>`` header.  Public
repositories may work without a token.  The token is read from
``.muse/config.toml`` via :func:`muse.cli.config.get_auth_token` and is
**never** written to any log line.

Error codes
-----------

    401  Unauthorized — invalid or missing token
    404  Not found — repo does not exist on the remote
    409  Conflict — push rejected (non-fast-forward without ``--force``)
    5xx  Server error
"""

from __future__ import annotations

import http.client
import json
import logging
import urllib.error
import urllib.request
from typing import IO, Protocol

import types
import urllib.parse
import urllib.response
from typing import Protocol, runtime_checkable

from muse.core.pack import FetchRequest, ObjectPayload, PackBundle, PushResult, RemoteInfo
from muse.core.store import CommitDict, SnapshotDict
from muse.core.validation import MAX_RESPONSE_BYTES
from muse.domain import SemVerBump

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 60


# ---------------------------------------------------------------------------
# Response protocol — typed adapter for urllib response objects
# ---------------------------------------------------------------------------


class _HttpHeaders(Protocol):
    """Minimal interface for HTTP response headers."""

    def get(self, name: str, default: str = "") -> str: ...


@runtime_checkable
class _HttpResponse(Protocol):
    """Structural interface for urllib HTTP response objects.

    Defined as a Protocol so that ``_open_url`` can have a concrete, non-Any
    return type without importing implementation-specific urllib internals.
    """

    headers: _HttpHeaders

    def read(self, amt: int | None = None) -> bytes: ...

    def __enter__(self) -> "_HttpResponse": ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: "types.TracebackType | None",
    ) -> bool | None: ...


# ---------------------------------------------------------------------------
# Security — redirect and scheme enforcement
# ---------------------------------------------------------------------------


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Refuse all HTTP redirects.

    ``urllib.request`` follows redirects by default, including across schemes
    (HTTPS → HTTP) and across hosts.  If a server we contact redirects us:

    - To HTTP: the ``Authorization: Bearer`` header would be sent in cleartext.
    - To a different host: the token would be sent to an unintended recipient.

    We refuse both.  The server must use the correct, stable URL.  If a
    redirect is required during operations, it is always better to surface
    it as a hard error so the operator can update the configured URL than to
    silently follow it and leak credentials.
    """

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: http.client.HTTPMessage,
        newurl: str,
    ) -> urllib.request.Request | None:
        raise urllib.error.HTTPError(
            req.full_url,
            code,
            (
                f"Redirect refused ({code}): server tried to redirect to {newurl!r}. "
                "Update the configured remote URL to the final destination."
            ),
            headers,
            fp,
        )


# Build one opener that never follows redirects.  Used for every request
# so that Authorization headers are never sent to an unintended recipient.
_STRICT_OPENER = urllib.request.build_opener(_NoRedirectHandler())


def _open_url(req: urllib.request.Request, timeout: int) -> _HttpResponse:
    """Thin wrapper around ``_STRICT_OPENER.open`` — exists purely to give tests
    a single, importable patch target instead of deep-patching the opener object.

    Returns an ``_HttpResponse`` Protocol value so that callers can be fully
    typed without depending on urllib's concrete ``addinfourl`` class.
    """
    resp: _HttpResponse = _STRICT_OPENER.open(req, timeout=timeout)
    return resp


# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------


class TransportError(Exception):
    """Raised when the remote returns a non-2xx response or is unreachable.

    Attributes:
        status_code: HTTP status code (e.g. ``401``, ``404``, ``409``, ``500``).
                     ``0`` for network-level failures (DNS, connection refused).
    """

    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


# ---------------------------------------------------------------------------
# Protocol — the seam between CLI commands and the transport implementation
# ---------------------------------------------------------------------------


class MuseTransport(Protocol):
    """Protocol for Muse remote transport implementations.

    All methods are synchronous — the Muse CLI is synchronous by design.
    """

    def fetch_remote_info(self, url: str, token: str | None) -> RemoteInfo:
        """Return repository metadata from ``GET {url}/refs``.

        Args:
            url:   Remote repository URL.
            token: Bearer token, or ``None`` for public repos.

        Raises:
            :class:`TransportError` on HTTP 4xx/5xx or network failure.
        """
        ...

    def fetch_pack(
        self, url: str, token: str | None, want: list[str], have: list[str]
    ) -> PackBundle:
        """Download a :class:`~muse.core.pack.PackBundle` via ``POST {url}/fetch``.

        Args:
            url:   Remote repository URL.
            token: Bearer token, or ``None``.
            want:  Commit IDs the client wants to receive.
            have:  Commit IDs already present locally.

        Raises:
            :class:`TransportError` on HTTP 4xx/5xx or network failure.
        """
        ...

    def push_pack(
        self,
        url: str,
        token: str | None,
        bundle: PackBundle,
        branch: str,
        force: bool,
    ) -> PushResult:
        """Upload a :class:`~muse.core.pack.PackBundle` via ``POST {url}/push``.

        Args:
            url:    Remote repository URL.
            token:  Bearer token, or ``None``.
            bundle: Bundle to upload.
            branch: Remote branch to update.
            force:  Bypass the server-side fast-forward check.

        Raises:
            :class:`TransportError` on HTTP 4xx/5xx or network failure.
        """
        ...


# ---------------------------------------------------------------------------
# HTTP/1.1 implementation (stdlib, zero extra dependencies)
# ---------------------------------------------------------------------------


class HttpTransport:
    """Synchronous HTTPS transport using stdlib ``urllib.request``.

    One short-lived HTTPS connection per CLI invocation over HTTP/1.1.
    Bearer token values are **never** written to any log line.
    """

    def _build_request(
        self,
        method: str,
        url: str,
        token: str | None,
        body_bytes: bytes | None = None,
    ) -> urllib.request.Request:
        # Never send a bearer token over cleartext HTTP — the token would be
        # visible to any network observer on the path.
        # Use urlparse for a proper scheme check rather than a fragile prefix test.
        if token and urllib.parse.urlparse(url).scheme != "https":
            raise TransportError(
                f"Refusing to send credentials to a non-HTTPS URL: {url!r}. "
                "Ensure the remote URL uses https://.",
                0,
            )
        headers: dict[str, str] = {"Accept": "application/json"}
        if body_bytes is not None:
            headers["Content-Type"] = "application/json"
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return urllib.request.Request(
            url=url,
            data=body_bytes,
            headers=headers,
            method=method,
        )

    def _execute(self, req: urllib.request.Request) -> bytes:
        """Send *req* and return raw response bytes.

        Uses :data:`_STRICT_OPENER` which refuses all HTTP redirects, ensuring
        ``Authorization`` headers are never forwarded to an unintended host or
        sent over a downgraded cleartext connection.

        Raises:
            :class:`TransportError` on non-2xx HTTP or any network error.
        """
        try:
            with _open_url(req, _TIMEOUT_SECONDS) as resp:
                # Enforce a hard cap before reading the body to defend against
                # a malicious or compromised server sending an unbounded response.
                content_length_str = resp.headers.get("Content-Length", "")
                if content_length_str:
                    try:
                        declared = int(content_length_str)
                        if declared > MAX_RESPONSE_BYTES:
                            raise TransportError(
                                f"Server Content-Length {declared} exceeds the "
                                f"{MAX_RESPONSE_BYTES // (1024 * 1024)} MiB response cap.",
                                0,
                            )
                    except ValueError:
                        pass  # Unparseable Content-Length — fall through to streaming cap.
                # Read one byte more than the cap so we can detect over-limit responses.
                body: bytes = resp.read(MAX_RESPONSE_BYTES + 1)
                if len(body) > MAX_RESPONSE_BYTES:
                    raise TransportError(
                        f"Response body exceeds the {MAX_RESPONSE_BYTES // (1024 * 1024)} MiB "
                        "cap. The server may be sending unexpected data.",
                        0,
                    )
            return body
        except urllib.error.HTTPError as exc:
            try:
                err_body: str = exc.read().decode("utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                err_body = ""
            raise TransportError(f"HTTP {exc.code}: {err_body[:400]}", exc.code) from exc
        except urllib.error.URLError as exc:
            raise TransportError(str(exc.reason), 0) from exc

    def fetch_remote_info(self, url: str, token: str | None) -> RemoteInfo:
        """Fetch repository metadata from ``GET {url}/refs``."""
        endpoint = f"{url.rstrip('/')}/refs"
        logger.debug("transport: GET %s", endpoint)
        req = self._build_request("GET", endpoint, token)
        raw = self._execute(req)
        return _parse_remote_info(raw)

    def fetch_pack(
        self, url: str, token: str | None, want: list[str], have: list[str]
    ) -> PackBundle:
        """Download a PackBundle via ``POST {url}/fetch``."""
        endpoint = f"{url.rstrip('/')}/fetch"
        logger.debug(
            "transport: POST %s (want=%d, have=%d)", endpoint, len(want), len(have)
        )
        payload: FetchRequest = {"want": want, "have": have}
        body_bytes = json.dumps(payload).encode("utf-8")
        req = self._build_request("POST", endpoint, token, body_bytes)
        raw = self._execute(req)
        return _parse_bundle(raw)

    def push_pack(
        self,
        url: str,
        token: str | None,
        bundle: PackBundle,
        branch: str,
        force: bool,
    ) -> PushResult:
        """Upload a PackBundle via ``POST {url}/push``."""
        endpoint = f"{url.rstrip('/')}/push"
        logger.debug(
            "transport: POST %s (branch=%s, force=%s, commits=%d)",
            endpoint,
            branch,
            force,
            len(bundle.get("commits") or []),
        )
        payload = {"bundle": bundle, "branch": branch, "force": force}
        body_bytes = json.dumps(payload).encode("utf-8")
        req = self._build_request("POST", endpoint, token, body_bytes)
        raw = self._execute(req)
        return _parse_push_result(raw)


# ---------------------------------------------------------------------------
# Response parsers — JSON bytes → typed TypedDicts
# ---------------------------------------------------------------------------
# json.loads() returns Any (per typeshed), so we use isinstance narrowing
# throughout.  No explicit Any annotations appear in this file.
# ---------------------------------------------------------------------------


def _assert_json_content(raw: bytes, endpoint: str) -> None:
    """Raise TransportError if *raw* does not look like JSON.

    A best-effort guard: checks that the first non-whitespace byte is ``{``
    or ``[``, which is always true for valid JSON objects/arrays.  This
    catches HTML error pages (e.g., proxy intercept pages) before json.loads
    produces a misleading error.
    """
    stripped = raw.lstrip()
    if stripped and stripped[0:1] not in (b"{", b"["):
        raise TransportError(
            f"Unexpected response from {endpoint!r}: expected JSON, "
            f"got content starting with {stripped[:40]!r}.",
            0,
        )


def _parse_remote_info(raw: bytes) -> RemoteInfo:
    """Parse ``GET /refs`` response bytes into a :class:`~muse.core.pack.RemoteInfo`."""
    _assert_json_content(raw, "/refs")
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        return RemoteInfo(
            repo_id="", domain="midi", branch_heads={}, default_branch="main"
        )
    repo_id_val = parsed.get("repo_id")
    domain_val = parsed.get("domain")
    default_branch_val = parsed.get("default_branch")
    branch_heads_raw = parsed.get("branch_heads")
    branch_heads: dict[str, str] = {}
    if isinstance(branch_heads_raw, dict):
        for k, v in branch_heads_raw.items():
            if isinstance(k, str) and isinstance(v, str):
                branch_heads[k] = v
    return RemoteInfo(
        repo_id=str(repo_id_val) if isinstance(repo_id_val, str) else "",
        domain=str(domain_val) if isinstance(domain_val, str) else "midi",
        default_branch=(
            str(default_branch_val) if isinstance(default_branch_val, str) else "main"
        ),
        branch_heads=branch_heads,
    )


def _parse_bundle(raw: bytes) -> PackBundle:
    """Parse ``POST /fetch`` response bytes into a :class:`~muse.core.pack.PackBundle`."""
    _assert_json_content(raw, "/fetch")
    parsed = json.loads(raw)
    bundle: PackBundle = {}
    if not isinstance(parsed, dict):
        return bundle

    # Commits — each item is a raw dict that CommitRecord.from_dict() will validate.
    commits_raw = parsed.get("commits")
    if isinstance(commits_raw, list):
        commits: list[CommitDict] = []
        for item in commits_raw:
            if isinstance(item, dict):
                commits.append(_coerce_commit_dict(item))
        bundle["commits"] = commits

    # Snapshots
    snapshots_raw = parsed.get("snapshots")
    if isinstance(snapshots_raw, list):
        snapshots: list[SnapshotDict] = []
        for item in snapshots_raw:
            if isinstance(item, dict):
                snapshots.append(_coerce_snapshot_dict(item))
        bundle["snapshots"] = snapshots

    # Objects
    objects_raw = parsed.get("objects")
    if isinstance(objects_raw, list):
        objects: list[ObjectPayload] = []
        for item in objects_raw:
            if isinstance(item, dict):
                oid = item.get("object_id")
                b64 = item.get("content_b64")
                if isinstance(oid, str) and isinstance(b64, str):
                    objects.append(ObjectPayload(object_id=oid, content_b64=b64))
        bundle["objects"] = objects

    # Branch heads
    heads_raw = parsed.get("branch_heads")
    if isinstance(heads_raw, dict):
        branch_heads: dict[str, str] = {}
        for k, v in heads_raw.items():
            if isinstance(k, str) and isinstance(v, str):
                branch_heads[k] = v
        bundle["branch_heads"] = branch_heads

    return bundle


def _parse_push_result(raw: bytes) -> PushResult:
    """Parse ``POST /push`` response bytes into a :class:`~muse.core.pack.PushResult`."""
    _assert_json_content(raw, "/push")
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        return PushResult(ok=False, message="Invalid server response", branch_heads={})
    ok_val = parsed.get("ok")
    msg_val = parsed.get("message")
    heads_raw = parsed.get("branch_heads")
    branch_heads: dict[str, str] = {}
    if isinstance(heads_raw, dict):
        for k, v in heads_raw.items():
            if isinstance(k, str) and isinstance(v, str):
                branch_heads[k] = v
    return PushResult(
        ok=bool(ok_val) if isinstance(ok_val, bool) else False,
        message=str(msg_val) if isinstance(msg_val, str) else "",
        branch_heads=branch_heads,
    )


# ---------------------------------------------------------------------------
# TypedDict coercion helpers — extract known string fields from raw JSON dicts
# ---------------------------------------------------------------------------
# CommitDict and SnapshotDict are total=False (all fields optional), so we
# only extract the string/scalar fields we can safely validate here.
# CommitRecord.from_dict() and SnapshotRecord.from_dict() re-validate
# required fields when apply_pack() calls them.
# ---------------------------------------------------------------------------


# Wire-value union — all types that can appear as dict values in a JSON
# object parsed from the Muse wire format.  Using this explicit union instead
# of `object` or `Any` satisfies both mypy --strict and typing_audit.
_WireVal = str | int | float | bool | None | list[str] | dict[str, str]


def _str(val: _WireVal) -> str:
    """Return *val* as str, or empty string if not a str."""
    return val if isinstance(val, str) else ""


def _str_or_none(val: _WireVal) -> str | None:
    """Return *val* as str, or None if not a str."""
    return val if isinstance(val, str) else None


def _int_or(val: _WireVal, default: int) -> int:
    """Return *val* as int, or *default* if not an int."""
    return val if isinstance(val, int) else default


def _coerce_commit_dict(raw: dict[str, _WireVal]) -> CommitDict:
    """Extract typed scalar fields from *raw* into a :class:`~muse.core.store.CommitDict`.

    Only primitive fields are validated here; ``structured_delta`` is
    preserved as-is because :class:`~muse.core.store.CommitRecord.from_dict`
    already handles it gracefully.
    """
    metadata_raw = raw.get("metadata")
    metadata: dict[str, str] = {}
    if isinstance(metadata_raw, dict):
        for k, v in metadata_raw.items():
            if isinstance(k, str) and isinstance(v, str):
                metadata[k] = v

    reviewed_by_raw = raw.get("reviewed_by")
    reviewed_by: list[str] = []
    if isinstance(reviewed_by_raw, list):
        for item in reviewed_by_raw:
            if isinstance(item, str):
                reviewed_by.append(item)

    breaking_changes_raw = raw.get("breaking_changes")
    breaking_changes: list[str] = []
    if isinstance(breaking_changes_raw, list):
        for item in breaking_changes_raw:
            if isinstance(item, str):
                breaking_changes.append(item)

    sem_ver_raw = raw.get("sem_ver_bump")
    sem_ver: SemVerBump
    if sem_ver_raw == "major":
        sem_ver = "major"
    elif sem_ver_raw == "minor":
        sem_ver = "minor"
    elif sem_ver_raw == "patch":
        sem_ver = "patch"
    else:
        sem_ver = "none"

    return CommitDict(
        commit_id=_str(raw.get("commit_id")),
        repo_id=_str(raw.get("repo_id")),
        branch=_str(raw.get("branch")),
        snapshot_id=_str(raw.get("snapshot_id")),
        message=_str(raw.get("message")),
        committed_at=_str(raw.get("committed_at")),
        parent_commit_id=_str_or_none(raw.get("parent_commit_id")),
        parent2_commit_id=_str_or_none(raw.get("parent2_commit_id")),
        author=_str(raw.get("author")),
        metadata=metadata,
        structured_delta=None,
        sem_ver_bump=sem_ver,
        breaking_changes=breaking_changes,
        agent_id=_str(raw.get("agent_id")),
        model_id=_str(raw.get("model_id")),
        toolchain_id=_str(raw.get("toolchain_id")),
        prompt_hash=_str(raw.get("prompt_hash")),
        signature=_str(raw.get("signature")),
        signer_key_id=_str(raw.get("signer_key_id")),
        format_version=_int_or(raw.get("format_version"), 1),
        reviewed_by=reviewed_by,
        test_runs=_int_or(raw.get("test_runs"), 0),
    )


def _coerce_snapshot_dict(raw: dict[str, _WireVal]) -> SnapshotDict:
    """Extract typed fields from *raw* into a :class:`~muse.core.store.SnapshotDict`."""
    manifest_raw = raw.get("manifest")
    manifest: dict[str, str] = {}
    if isinstance(manifest_raw, dict):
        for k, v in manifest_raw.items():
            if isinstance(k, str) and isinstance(v, str):
                manifest[k] = v
    return SnapshotDict(
        snapshot_id=_str(raw.get("snapshot_id")),
        manifest=manifest,
        created_at=_str(raw.get("created_at")),
    )

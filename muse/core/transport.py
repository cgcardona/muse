"""Muse transport layer — HTTP and local-filesystem remote communication.

The :class:`MuseTransport` Protocol defines the interface between the Muse CLI
and a remote host.  The CLI calls this Protocol; the implementation is chosen
at runtime by :func:`make_transport` based on the URL scheme.

Transport implementations
--------------------------

:class:`HttpTransport`
    Synchronous HTTPS transport using stdlib ``urllib.request``.  Used for
    ``https://`` remote URLs (MuseHub server required).  Encodes all request
    bodies as ``msgpack`` (``Content-Type: application/x-msgpack``), which
    eliminates the 33 % base64 inflation of the old JSON+base64 protocol.

:class:`LocalFileTransport`
    Zero-network transport for ``file://`` URLs.  Reads and writes directly
    from the remote's ``.muse/`` directory on the local filesystem (or a
    shared network mount).  No server is required — ideal for local testing,
    monorepo setups, and offline workflows.

Use :func:`make_transport` instead of constructing either class directly —
it inspects the URL scheme and returns the appropriate implementation.

MWP — Muse Wire Protocol
-------------------------------

All endpoints live under the remote repository URL.  MWP introduces five
new phases on top of the original refs/fetch/push trio:

+-----------------------------------+----------------------------------------+
| Endpoint                          | Purpose                                |
+===================================+========================================+
| GET  {url}/refs                   | Pre-flight: branch heads + metadata    |
+-----------------------------------+----------------------------------------+
| POST {url}/filter-objects         | Phase 1: dedup — missing object IDs   |
+-----------------------------------+----------------------------------------+
| POST {url}/presign                | Phase 3: presigned S3/R2 PUT/GET URLs |
+-----------------------------------+----------------------------------------+
| POST {url}/push/objects           | Pre-upload object chunks (small objs) |
+-----------------------------------+----------------------------------------+
| POST {url}/push                   | Phase 5: commit + snapshot push        |
+-----------------------------------+----------------------------------------+
| POST {url}/fetch                  | Download commit delta pack             |
+-----------------------------------+----------------------------------------+
| POST {url}/negotiate              | Phase 5: depth-limited have/ack loop  |
+-----------------------------------+----------------------------------------+

Wire encoding
~~~~~~~~~~~~~

Request bodies are ``msgpack``-encoded dicts (``Content-Type:
application/x-msgpack``).  The ``Accept`` header advertises both ``msgpack``
and ``application/json`` so older servers can respond in JSON.  Objects are
transmitted as raw ``bytes`` in :class:`~muse.core.pack.ObjectPayload`
(``content`` field) — no base64 encoding.

Authentication
--------------

All endpoints accept an ``Authorization: Bearer <token>`` header.  Public
repositories may work without a token.  The token is read from
``.muse/config.toml`` via :func:`muse.cli.config.get_auth_token` and is
**never** written to any log line.

:class:`LocalFileTransport` ignores the ``token`` argument — local repos
do not require authentication (access is controlled by filesystem permissions).

Error codes (HttpTransport)
----------------------------

    401  Unauthorized — invalid or missing token
    404  Not found — repo does not exist on the remote
    409  Conflict — push rejected (non-fast-forward without ``--force``)
    5xx  Server error

Security model
--------------

HttpTransport:
- Refuses all HTTP redirects (prevents credential leakage to other hosts).
- Rejects non-HTTPS URLs when a token is present (prevents cleartext exposure).
- Caps response bodies at ``MAX_RESPONSE_BYTES`` (64 MiB) to prevent OOM.

LocalFileTransport:
- Calls ``.resolve()`` on all filesystem paths (canonicalises symlinks and
  ``..`` components before any I/O).
- Validates branch names with ``validate_branch_name`` (rejects null bytes,
  backslashes, consecutive dots, and other path-traversal primitives).
- Guards ref-file writes with ``contain_path`` (defence-in-depth: asserts the
  computed path stays inside ``.muse/refs/heads/`` even after symlink resolution).
- Never follows redirects, makes no network calls, and ignores the token arg.
"""

from __future__ import annotations

import http.client
import json
import logging
import pathlib
import types
import urllib.error
import urllib.parse
import urllib.request
import urllib.response
from typing import IO, Protocol, TypedDict, runtime_checkable

import msgpack

from muse.core.pack import (
    FetchRequest,
    ObjectPayload,
    ObjectsChunkResponse,
    PackBundle,
    PushResult,
    RemoteInfo,
    WireTag,
)
from muse.core.store import ChangelogEntry, CommitDict, ReleaseDict, SemVerTag, SnapshotDict
from muse.core.validation import MAX_RESPONSE_BYTES, contain_path, validate_branch_name
from muse.domain import SemVerBump

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 60

# Recursive type alias for msgpack-serializable values.
# Covers every type that msgpack can encode/decode natively — no base64,
# no `object`, no `Any`.  Python 3.12+ `type` statement creates a proper
# TypeAlias that mypy treats as a first-class recursive type.
type _MsgVal = (
    str | int | float | bool | bytes | None
    | list[_MsgVal]
    | dict[str, _MsgVal]
)

# Maximum number of objects to include in a single POST /push/objects call.
# Must stay strictly below the server's MAX_OBJECTS_PER_PUSH limit (1 000)
# to leave head room for Pydantic validation overhead and future additions.
CHUNK_OBJECTS: int = 400

# Objects above this byte threshold are candidates for presigned-URL upload
# (MWP Phase 3) — they bypass the API server and go directly to S3/R2.
# Objects at or below this size are included inline in the pack body.
LARGE_OBJECT_THRESHOLD: int = 64 * 1024  # 64 KiB

# Depth of the have-list sent per round of commit negotiation (MWP Phase 5).
# Caps the negotiation payload at ≤ NEGOTIATE_DEPTH commit IDs per request,
# keeping negotiation O(depth) rather than O(history).
NEGOTIATE_DEPTH: int = 64


# ---------------------------------------------------------------------------
# MWP response TypedDicts
# ---------------------------------------------------------------------------


class FilterResponse(TypedDict):
    """Response from ``POST {url}/filter-objects`` — MWP Phase 1."""

    missing: list[str]


class PresignResponse(TypedDict):
    """Response from ``POST {url}/presign`` — MWP Phase 3."""

    presigned: dict[str, str]   # object_id → presigned URL
    inline: list[str]           # IDs whose backend does not support presigned URLs


class NegotiateResponse(TypedDict):
    """Response from ``POST {url}/negotiate`` — MWP Phase 5."""

    ack: list[str]
    common_base: str | None
    ready: bool


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

    def push_objects(
        self,
        url: str,
        token: str | None,
        objects: list[ObjectPayload],
    ) -> ObjectsChunkResponse:
        """Pre-upload a batch of content-addressed objects via ``POST {url}/push/objects``.

        This is Phase 1 of a chunked push.  The caller splits the full object
        list into batches of at most :data:`CHUNK_OBJECTS` and calls this once
        per batch.  After all batches succeed, the caller issues a single
        ``push_pack`` (Phase 2) with an empty ``objects`` list — the final push
        only carries commits and snapshots, which are small.

        Objects are idempotent: the server skips any it already holds and
        counts them in ``skipped``.  Uploading the same object twice is always
        safe.

        Args:
            url:     Remote repository URL.
            token:   Bearer token, or ``None``.
            objects: Batch of objects to upload (``len ≤ CHUNK_OBJECTS``).

        Returns:
            :class:`~muse.core.pack.ObjectsChunkResponse` with ``stored`` and
            ``skipped`` counts.

        Raises:
            :class:`TransportError` on HTTP 4xx/5xx or network failure.
        """
        ...

    def filter_objects(
        self,
        url: str,
        token: str | None,
        object_ids: list[str],
    ) -> list[str]:
        """Return the subset of *object_ids* the remote does NOT already hold.

        MWP Phase 1 — object-level deduplication negotiation.  Before
        uploading any objects the client calls this with the full candidate
        list; the server responds with only the IDs it is missing.  The
        client then calls :func:`~muse.core.pack.build_pack` with
        ``only_objects`` set to that subset so no redundant blobs are sent.

        Args:
            url:        Remote repository URL.
            token:      Bearer token, or ``None``.
            object_ids: All object IDs the client intends to upload.

        Returns:
            Subset of *object_ids* missing on the remote.

        Raises:
            :class:`TransportError` on HTTP 4xx/5xx or network failure.
        """
        ...

    def presign_objects(
        self,
        url: str,
        token: str | None,
        object_ids: list[str],
        direction: str,
    ) -> PresignResponse:
        """Return presigned S3/R2 URLs for large-object direct transfer.

        MWP Phase 3 — objects above :data:`LARGE_OBJECT_THRESHOLD` bypass
        the API server.  The client uploads PUT presigned URLs directly to
        object storage, dramatically reducing API server load.

        When the backend is ``local://`` all IDs are returned in ``inline``
        and the client falls back to the normal pack flow.

        Args:
            url:        Remote repository URL.
            token:      Bearer token, or ``None``.
            object_ids: IDs of large objects to presign.
            direction:  ``"put"`` for push, ``"get"`` for pull.

        Returns:
            :class:`PresignResponse` with ``presigned`` URL map and ``inline``
            fallback list.

        Raises:
            :class:`TransportError` on HTTP 4xx/5xx or network failure.
        """
        ...

    def negotiate(
        self,
        url: str,
        token: str | None,
        want: list[str],
        have: list[str],
    ) -> NegotiateResponse:
        """Depth-limited commit negotiation (MWP Phase 5).

        Replaces sending the full local commit list as ``have``.  Each round
        sends ≤ :data:`NEGOTIATE_DEPTH` recent ancestors.  The server responds
        with which it recognises (``ack``), the common base commit (if found),
        and whether ``ready`` — i.e. enough context to compute the delta.

        Args:
            url:   Remote repository URL.
            token: Bearer token, or ``None``.
            want:  Branch tips the client wants to receive.
            have:  Recent local commit IDs (≤ NEGOTIATE_DEPTH per round).

        Returns:
            :class:`NegotiateResponse` with ``ack``, ``common_base``, ``ready``.

        Raises:
            :class:`TransportError` on HTTP 4xx/5xx or network failure.
        """
        ...

    def push_tags(
        self,
        url: str,
        token: str | None,
        tags: list[WireTag],
    ) -> int:
        """Push local tags to the remote via ``POST {url}/tags``.

        Tags are immutable once created on the remote — the server skips any
        it already holds.  Returns the number of tags newly stored.

        Args:
            url:   Remote repository URL.
            token: Bearer token, or ``None``.
            tags:  Tags to push.

        Raises:
            :class:`TransportError` on HTTP 4xx/5xx or network failure.
        """
        ...

    def create_release(
        self,
        url: str,
        token: str | None,
        release: ReleaseDict,
    ) -> str:
        """Create a release on the remote via ``POST {url}/releases``.

        Returns the ``release_id`` assigned by the server.

        Args:
            url:     Remote repository URL.
            token:   Bearer token.
            release: Fully-populated :class:`~muse.core.store.ReleaseDict`.

        Raises:
            :class:`TransportError` on HTTP 4xx/5xx or network failure.
        """
        ...

    def list_releases_remote(
        self,
        url: str,
        token: str | None,
        channel: str | None = None,
        include_drafts: bool = False,
    ) -> list[ReleaseDict]:
        """Fetch releases from the remote via ``GET {url}/releases``.

        Args:
            url:           Remote repository URL.
            token:         Bearer token, or ``None``.
            channel:       Filter by release channel; ``None`` returns all.
            include_drafts: Include draft releases when ``True``.

        Raises:
            :class:`TransportError` on HTTP 4xx/5xx or network failure.
        """
        ...

    def delete_release_remote(
        self,
        url: str,
        token: str | None,
        tag: str,
    ) -> None:
        """Retract a release from the remote via ``DELETE {url}/releases/{tag}``.

        Removes only the named label from the remote registry.  The underlying
        commit and snapshot objects are **not** affected.

        Args:
            url:   Remote repository URL.
            token: Bearer token (owner credentials required).
            tag:   Semver tag of the release to retract (e.g. ``"v1.2.0"``).

        Raises:
            :class:`TransportError` on HTTP 4xx/5xx, network failure, or if
            the release does not exist on the remote.
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
        content_type: str = "application/x-msgpack",
    ) -> urllib.request.Request:
        # Never send a bearer token over cleartext HTTP — the token would be
        # visible to any network observer on the path.
        # Localhost (127.0.0.1, ::1, "localhost") is exempt because the traffic
        # never leaves the machine and TLS would require a self-signed cert.
        _parsed = urllib.parse.urlparse(url)
        _is_loopback = _parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        if token and _parsed.scheme != "https" and not _is_loopback:
            raise TransportError(
                f"Refusing to send credentials to a non-HTTPS URL: {url!r}. "
                "Ensure the remote URL uses https://.",
                0,
            )
        # Advertise msgpack support so the server can respond in binary.
        headers: dict[str, str] = {
            "Accept": "application/x-msgpack, application/json",
        }
        if body_bytes is not None:
            headers["Content-Type"] = content_type
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return urllib.request.Request(
            url=url,
            data=body_bytes,
            headers=headers,
            method=method,
        )

    @staticmethod
    def _decode(raw: bytes) -> dict[str, _MsgVal]:
        """Decode a msgpack server response into a plain dict.

        Raises :class:`TransportError` if the payload is not valid msgpack.
        """
        if not raw:
            return {}
        try:
            result: _MsgVal = msgpack.unpackb(raw, raw=False)
        except Exception as exc:
            raise TransportError(f"Server returned invalid msgpack: {exc}", 0) from exc
        if not isinstance(result, dict):
            return {}
        return result

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
        body_bytes: bytes = msgpack.packb(payload, use_bin_type=True)
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
            "transport: POST %s (branch=%s, force=%s, commits=%d, objects=%d)",
            endpoint,
            branch,
            force,
            len(bundle.get("commits") or []),
            len(bundle.get("objects") or []),
        )
        body_bytes: bytes = msgpack.packb(
            {"bundle": bundle, "branch": branch, "force": force}, use_bin_type=True
        )
        req = self._build_request("POST", endpoint, token, body_bytes)
        raw = self._execute(req)
        return _parse_push_result(raw)

    def push_objects(
        self,
        url: str,
        token: str | None,
        objects: list[ObjectPayload],
    ) -> ObjectsChunkResponse:
        """Pre-upload an object batch via ``POST {url}/push/objects``."""
        endpoint = f"{url.rstrip('/')}/push/objects"
        logger.debug("transport: POST %s (objects=%d)", endpoint, len(objects))
        body_bytes: bytes = msgpack.packb({"objects": objects}, use_bin_type=True)
        req = self._build_request("POST", endpoint, token, body_bytes)
        raw = self._execute(req)
        return _parse_objects_response(raw)

    def filter_objects(
        self,
        url: str,
        token: str | None,
        object_ids: list[str],
    ) -> list[str]:
        """Return missing object IDs via ``POST {url}/filter-objects`` (MWP Phase 1)."""
        endpoint = f"{url.rstrip('/')}/filter-objects"
        logger.debug("transport: POST %s (candidates=%d)", endpoint, len(object_ids))
        body_bytes: bytes = msgpack.packb({"object_ids": object_ids}, use_bin_type=True)
        req = self._build_request("POST", endpoint, token, body_bytes)
        raw = self._execute(req)
        parsed = self._decode(raw)
        missing_raw = parsed.get("missing")
        return [m for m in missing_raw if isinstance(m, str)] if isinstance(missing_raw, list) else []

    def presign_objects(
        self,
        url: str,
        token: str | None,
        object_ids: list[str],
        direction: str,
    ) -> PresignResponse:
        """Return presigned S3/R2 URLs via ``POST {url}/presign`` (MWP Phase 3)."""
        endpoint = f"{url.rstrip('/')}/presign"
        logger.debug(
            "transport: POST %s (objects=%d, direction=%s)", endpoint, len(object_ids), direction
        )
        body_bytes: bytes = msgpack.packb(
            {"object_ids": object_ids, "direction": direction}, use_bin_type=True
        )
        req = self._build_request("POST", endpoint, token, body_bytes)
        raw = self._execute(req)
        parsed = self._decode(raw)
        presigned_raw = parsed.get("presigned", {})
        inline_raw = parsed.get("inline", [])
        presigned: dict[str, str] = (
            {str(k): str(v) for k, v in presigned_raw.items()}
            if isinstance(presigned_raw, dict)
            else {}
        )
        inline: list[str] = (
            [str(x) for x in inline_raw] if isinstance(inline_raw, list) else []
        )
        return PresignResponse(presigned=presigned, inline=inline)

    def negotiate(
        self,
        url: str,
        token: str | None,
        want: list[str],
        have: list[str],
    ) -> NegotiateResponse:
        """Depth-limited commit negotiation via ``POST {url}/negotiate`` (MWP Phase 5)."""
        endpoint = f"{url.rstrip('/')}/negotiate"
        logger.debug(
            "transport: POST %s (want=%d, have=%d)", endpoint, len(want), len(have)
        )
        body_bytes: bytes = msgpack.packb({"want": want, "have": have}, use_bin_type=True)
        req = self._build_request("POST", endpoint, token, body_bytes)
        raw = self._execute(req)
        parsed = self._decode(raw)
        ack_raw = parsed.get("ack", [])
        ack = [str(x) for x in ack_raw] if isinstance(ack_raw, list) else []
        common_base_raw = parsed.get("common_base")
        common_base = str(common_base_raw) if isinstance(common_base_raw, str) else None
        ready_raw = parsed.get("ready", False)
        return NegotiateResponse(
            ack=ack,
            common_base=common_base,
            ready=bool(ready_raw),
        )

    def push_tags(
        self,
        url: str,
        token: str | None,
        tags: list[WireTag],
    ) -> int:
        """Push tags via ``POST {url}/tags``."""
        endpoint = f"{url.rstrip('/')}/tags"
        logger.debug("transport: POST %s (tags=%d)", endpoint, len(tags))
        body_bytes: bytes = msgpack.packb({"tags": list(tags)}, use_bin_type=True)
        req = self._build_request("POST", endpoint, token, body_bytes)
        raw = self._execute(req)
        parsed = self._decode(raw)
        stored_val = parsed.get("stored")
        return int(stored_val) if isinstance(stored_val, int) else 0

    def create_release(
        self,
        url: str,
        token: str | None,
        release: ReleaseDict,
    ) -> str:
        """Create a release via ``POST {url}/releases``."""
        endpoint = f"{url.rstrip('/')}/releases"
        logger.debug("transport: POST %s (tag=%s)", endpoint, release.get("tag", ""))
        # ReleaseDict contains no bytes fields so JSON encoding works directly.
        body_bytes: bytes = json.dumps(release).encode("utf-8")
        req = self._build_request("POST", endpoint, token, body_bytes, content_type="application/json")
        raw = self._execute(req)
        parsed = self._decode(raw)
        release_id_val = parsed.get("release_id")
        return str(release_id_val) if isinstance(release_id_val, str) else ""

    def list_releases_remote(
        self,
        url: str,
        token: str | None,
        channel: str | None = None,
        include_drafts: bool = False,
    ) -> list[ReleaseDict]:
        """List releases via ``GET {url}/releases``."""
        qs_parts: list[str] = []
        if channel:
            qs_parts.append(f"channel={urllib.parse.quote(channel)}")
        if include_drafts:
            qs_parts.append("include_drafts=1")
        endpoint = f"{url.rstrip('/')}/releases"
        if qs_parts:
            endpoint = f"{endpoint}?{'&'.join(qs_parts)}"
        logger.debug("transport: GET %s", endpoint)
        req = self._build_request("GET", endpoint, token)
        raw = self._execute(req)
        parsed = self._decode(raw)
        return _parse_releases_list(parsed)

    def delete_release_remote(
        self,
        url: str,
        token: str | None,
        tag: str,
    ) -> None:
        """Retract a release via ``DELETE {url}/releases/{tag}``."""
        endpoint = f"{url.rstrip('/')}/releases/{urllib.parse.quote(tag, safe='')}"
        logger.debug("transport: DELETE %s", endpoint)
        req = self._build_request("DELETE", endpoint, token)
        self._execute(req)


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
    parsed = HttpTransport._decode(raw)
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
    parsed = HttpTransport._decode(raw)
    bundle: PackBundle = {}

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

    # Objects — raw bytes in "content" (MWP msgpack wire format)
    objects_raw = parsed.get("objects")
    if isinstance(objects_raw, list):
        objects: list[ObjectPayload] = []
        for item in objects_raw:
            if not isinstance(item, dict):
                continue
            oid = item.get("object_id")
            if not isinstance(oid, str):
                continue
            content_raw = item.get("content")
            if isinstance(content_raw, (bytes, bytearray)):
                objects.append(ObjectPayload(object_id=oid, content=bytes(content_raw)))
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
    parsed = HttpTransport._decode(raw)
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


def _parse_objects_response(raw: bytes) -> ObjectsChunkResponse:
    """Parse ``POST /push/objects`` response into an :class:`~muse.core.pack.ObjectsChunkResponse`."""
    parsed = HttpTransport._decode(raw)
    stored_val = parsed.get("stored")
    skipped_val = parsed.get("skipped")
    return ObjectsChunkResponse(
        stored=int(stored_val) if isinstance(stored_val, int) else 0,
        skipped=int(skipped_val) if isinstance(skipped_val, int) else 0,
    )


def _coerce_sem_ver_bump(raw: _MsgVal) -> SemVerBump:
    """Safely coerce a raw value to a :class:`~muse.domain.SemVerBump` literal."""
    if raw == "major":
        return "major"
    if raw == "minor":
        return "minor"
    if raw == "patch":
        return "patch"
    return "none"


def _parse_releases_list(parsed: dict[str, _MsgVal]) -> list[ReleaseDict]:
    """Extract a list of :class:`~muse.core.store.ReleaseDict` from a parsed response."""
    releases_raw = parsed.get("releases")
    if not isinstance(releases_raw, list):
        return []
    results: list[ReleaseDict] = []
    for item in releases_raw:
        if not isinstance(item, dict):
            continue
        try:
            semver_raw = item.get("semver")
            if isinstance(semver_raw, dict):
                sv_major = semver_raw.get("major")
                sv_minor = semver_raw.get("minor")
                sv_patch = semver_raw.get("patch")
                sv_pre = semver_raw.get("pre")
                sv_build = semver_raw.get("build")
                semver: SemVerTag = SemVerTag(
                    major=int(sv_major) if isinstance(sv_major, int) else 0,
                    minor=int(sv_minor) if isinstance(sv_minor, int) else 0,
                    patch=int(sv_patch) if isinstance(sv_patch, int) else 0,
                    pre=sv_pre if isinstance(sv_pre, str) else "",
                    build=sv_build if isinstance(sv_build, str) else "",
                )
            else:
                semver = SemVerTag(major=0, minor=0, patch=0, pre="", build="")
            changelog_raw = item.get("changelog") or []
            changelog: list[ChangelogEntry] = []
            if isinstance(changelog_raw, list):
                for entry in changelog_raw:
                    if not isinstance(entry, dict):
                        continue
                    bc_raw = entry.get("breaking_changes")
                    bc_list: list[str] = [str(b) for b in bc_raw if isinstance(b, str)] if isinstance(bc_raw, list) else []
                    changelog.append(ChangelogEntry(
                        commit_id=str(entry.get("commit_id", "")),
                        message=str(entry.get("message", "")),
                        sem_ver_bump=_coerce_sem_ver_bump(entry.get("sem_ver_bump")),
                        breaking_changes=bc_list,
                        author=str(entry.get("author", "")),
                        committed_at=str(entry.get("committed_at", "")),
                        agent_id=str(entry.get("agent_id", "")),
                        model_id=str(entry.get("model_id", "")),
                    ))
            results.append(ReleaseDict(
                release_id=str(item.get("release_id", "")),
                repo_id=str(item.get("repo_id", "")),
                tag=str(item.get("tag", "")),
                semver=semver,
                channel=str(item.get("channel", "stable")),
                commit_id=str(item.get("commit_id", "")),
                snapshot_id=str(item.get("snapshot_id", "")),
                title=str(item.get("title", "")),
                body=str(item.get("body", "")),
                changelog=changelog,
                agent_id=str(item.get("agent_id", "")),
                model_id=str(item.get("model_id", "")),
                is_draft=bool(item.get("is_draft", False)),
                gpg_signature=str(item.get("gpg_signature", "")),
                created_at=str(item.get("created_at", "")),
            ))
        except (KeyError, TypeError, ValueError):
            continue
    return results


# ---------------------------------------------------------------------------
# TypedDict coercion helpers — extract known string fields from raw JSON dicts
# ---------------------------------------------------------------------------
# CommitDict and SnapshotDict are total=False (all fields optional), so we
# only extract the string/scalar fields we can safely validate here.
# CommitRecord.from_dict() and SnapshotRecord.from_dict() re-validate
# required fields when apply_pack() calls them.
# ---------------------------------------------------------------------------


# _WireVal is now an alias for _MsgVal — kept for readability at call sites.
_WireVal = _MsgVal


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


# ---------------------------------------------------------------------------
# LocalFileTransport helpers
# ---------------------------------------------------------------------------


def _is_ancestor(
    candidate: str,
    from_commit: str,
    bundle_by_id: dict[str, CommitDict],
    remote_root: pathlib.Path,
    max_depth: int = 100_000,
) -> bool:
    """Return True if *candidate* is an ancestor of (or equal to) *from_commit*.

    Walks the commit graph BFS-style starting from *from_commit*, consulting
    *bundle_by_id* first (commits included in the push bundle) and falling back
    to the existing commits on disk in *remote_root* (commits already present).

    This two-source walk is necessary because ``build_pack()`` excludes commits
    in the caller's ``have`` set from the bundle — those commits are already on
    disk at the remote and must be consulted directly.

    Args:
        candidate:    The commit ID to search for (typically the remote's current HEAD).
        from_commit:  Starting point of the BFS walk (typically the new tip being pushed).
        bundle_by_id: Commits included in the push bundle, keyed by commit_id.
        remote_root:  Root of the remote Muse repo (for reading pre-existing commits).
        max_depth:    BFS depth cap — prevents unbounded walks on corrupt graphs.

    Returns:
        ``True`` if *candidate* is reachable from *from_commit*, ``False`` otherwise.
    """
    from muse.core.store import read_commit as _rc

    seen: set[str] = set()
    queue: list[str] = [from_commit]
    depth = 0
    while queue and depth < max_depth:
        cid = queue.pop(0)
        if cid in seen:
            continue
        seen.add(cid)
        if cid == candidate:
            return True
        # Prefer bundle for unwritten commits; fall back to remote store.
        parent1: str | None
        parent2: str | None
        if cid in bundle_by_id:
            bc = bundle_by_id[cid]
            p1_raw = bc.get("parent_commit_id")
            p2_raw = bc.get("parent2_commit_id")
            parent1 = p1_raw if isinstance(p1_raw, str) else None
            parent2 = p2_raw if isinstance(p2_raw, str) else None
        else:
            rec = _rc(remote_root, cid)
            if rec is None:
                depth += 1
                continue
            parent1 = rec.parent_commit_id
            parent2 = rec.parent2_commit_id
        if parent1 and parent1 not in seen:
            queue.append(parent1)
        if parent2 and parent2 not in seen:
            queue.append(parent2)
        depth += 1
    return False


# ---------------------------------------------------------------------------
# LocalFileTransport — push/pull between two repos on the same filesystem
# ---------------------------------------------------------------------------


class LocalFileTransport:
    """Transport implementation for ``file://`` URLs.

    Allows ``muse push file:///path/to/repo`` and ``muse pull`` between two
    Muse repositories on the same filesystem (or a shared network mount)
    without requiring a MuseHub server.

    The remote path must be the root of an initialised Muse repository — it
    must contain a ``.muse/`` directory.  No separate "bare repo" format is
    required; Muse repositories are self-describing.

    This transport never makes network calls.  All operations are synchronous
    filesystem reads and writes, consistent with the rest of the Muse CLI.
    """

    @staticmethod
    def _repo_root(url: str) -> pathlib.Path:
        """Extract and validate the filesystem path from a ``file://`` URL.

        Security guarantees:
        - Rejects non-``file://`` schemes unconditionally.
        - Calls ``.resolve()`` to canonicalize the path, dereferencing all
          symlinks before any filesystem operations.  A symlink at the URL
          target that points to a directory without a ``.muse/`` subdirectory
          is rejected — the check is on the resolved, canonical path, not the
          symlink itself.
        - Verifies that ``.muse/`` exists at the resolved root, preventing
          accidental pushes to arbitrary directories.
        """
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "file":
            raise TransportError(
                f"LocalFileTransport requires a file:// URL, got: {url!r}", 0
            )
        # urllib.parse.urlparse on file:///abs/path gives netloc="" path="/abs/path".
        # On Windows file://C:/path gives netloc="" path="/C:/path" — strip leading
        # slash for Windows compatibility via pathlib.
        path_str = parsed.netloc + parsed.path
        # resolve() dereferences all symlinks and normalises ".." components.
        # This is the defence against symlink-based path escape attempts.
        root = pathlib.Path(path_str).resolve()
        if not (root / ".muse").is_dir():
            raise TransportError(
                f"Remote path {root!r} does not contain a .muse/ directory. "
                "Run 'muse init' in the target directory first.",
                404,
            )
        return root

    def fetch_remote_info(self, url: str, token: str | None) -> RemoteInfo:  # noqa: ARG002
        """Read branch heads directly from the remote's ref files."""
        from muse.core.store import get_all_branch_heads

        remote_root = self._repo_root(url)
        repo_json_path = remote_root / ".muse" / "repo.json"
        try:
            repo_data = json.loads(repo_json_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise TransportError(f"Cannot read remote repo.json: {exc}", 0) from exc

        repo_id = str(repo_data.get("repo_id", ""))
        domain = str(repo_data.get("domain", "midi"))
        default_branch = str(repo_data.get("default_branch", "main"))

        branch_heads = get_all_branch_heads(remote_root)

        return RemoteInfo(
            repo_id=repo_id,
            domain=domain,
            default_branch=default_branch,
            branch_heads=branch_heads,
        )

    def fetch_pack(
        self, url: str, token: str | None, want: list[str], have: list[str]  # noqa: ARG002
    ) -> PackBundle:
        """Build a PackBundle from the remote's local store."""
        from muse.core.pack import build_pack

        remote_root = self._repo_root(url)
        have_set = set(have)
        # Build a pack containing all wanted commits and their transitive deps,
        # excluding anything the caller already has.
        bundle = build_pack(remote_root, commit_ids=want, have=list(have_set))
        return bundle

    def push_pack(
        self,
        url: str,
        token: str | None,  # noqa: ARG002
        bundle: PackBundle,
        branch: str,
        force: bool,
    ) -> PushResult:
        """Write a PackBundle directly into the remote's local store.

        Security guarantees:
        - ``branch`` is validated with :func:`~muse.core.validation.validate_branch_name`
          before any I/O.  Names containing path traversal components (`..`),
          null bytes, backslashes, or other forbidden characters are rejected.
        - The ref file path is further hardened with
          :func:`~muse.core.validation.contain_path`, which resolves symlinks
          and asserts the result stays inside ``.muse/refs/heads/``.  A branch
          name that ``validate_branch_name`` would allow but that resolves
          outside the expected directory (e.g. via a pre-placed symlink) is
          rejected before any write occurs.
        - A fast-forward check prevents overwriting diverged remote history
          unless ``force=True`` is explicitly passed.
        """
        from muse.core.pack import apply_pack
        from muse.core.store import get_all_branch_heads, get_head_commit_id

        remote_root = self._repo_root(url)

        try:
            validate_branch_name(branch)
        except ValueError as exc:
            return PushResult(ok=False, message=str(exc), branch_heads={})

        # Determine the new tip for the branch.
        # Prefer an explicit branch_heads entry in the bundle (set by the push
        # command when it knows the local HEAD).  Fall back to computing the
        # leaf commit — the commit in the bundle that is not referenced as a
        # parent of any other commit in the bundle, filtered to the branch.
        # This handles bundles produced by build_pack(), which does not
        # populate branch_heads.
        bundle_heads = bundle.get("branch_heads") or {}
        new_tip: str | None = bundle_heads.get(branch)
        if new_tip is None:
            bundle_commits_list = bundle.get("commits") or []
            all_parent_ids: set[str] = set()
            for bc in bundle_commits_list:
                pid = bc.get("parent_commit_id")
                if isinstance(pid, str):
                    all_parent_ids.add(pid)
                pid2 = bc.get("parent2_commit_id")
                if isinstance(pid2, str):
                    all_parent_ids.add(pid2)
            # Leaf = commit whose ID is not a parent of any other bundle commit.
            # Prefer commits whose branch field matches; otherwise take any leaf.
            leaves_for_branch = [
                bc["commit_id"]
                for bc in bundle_commits_list
                if bc.get("commit_id") not in all_parent_ids
                and bc.get("branch") == branch
                and isinstance(bc.get("commit_id"), str)
            ]
            any_leaves = [
                bc["commit_id"]
                for bc in bundle_commits_list
                if bc.get("commit_id") not in all_parent_ids
                and isinstance(bc.get("commit_id"), str)
            ]
            fallback: list[str | None] = [None]
            new_tip = (leaves_for_branch or any_leaves or fallback)[0]

        # Fast-forward check: the remote's current HEAD for this branch must be
        # an ancestor of the tip commit in the bundle, unless --force is passed.
        if not force and new_tip:
            remote_tip = get_head_commit_id(remote_root, branch)
            if remote_tip and remote_tip != new_tip:
                # BFS from new_tip through bundle commits *and* existing remote
                # commits to find whether remote_tip is a reachable ancestor.
                # We cannot rely on bundle commits alone because build_pack()
                # excludes commits the receiver already has (the "have" set).
                bundle_by_id: dict[str, CommitDict] = {
                    c["commit_id"]: c
                    for c in (bundle.get("commits") or [])
                    if isinstance(c.get("commit_id"), str)
                }
                if not _is_ancestor(remote_tip, new_tip, bundle_by_id, remote_root):
                    return PushResult(
                        ok=False,
                        message=(
                            f"Push rejected: remote branch '{branch}' has diverged. "
                            "Pull and merge first, or use --force."
                        ),
                        branch_heads={},
                    )

        try:
            apply_pack(remote_root, bundle)
        except Exception as exc:  # noqa: BLE001
            return PushResult(ok=False, message=f"Failed to apply pack: {exc}", branch_heads={})

        # Update the remote branch ref to the new tip.
        # contain_path() resolves symlinks and asserts the result stays inside
        # .muse/refs/heads/ — defence-in-depth beyond validate_branch_name.
        if new_tip:
            heads_base = remote_root / ".muse" / "refs" / "heads"
            try:
                ref_path = contain_path(heads_base, branch)
            except ValueError as exc:
                return PushResult(
                    ok=False,
                    message=f"Rejected: branch ref path is unsafe — {exc}",
                    branch_heads={},
                )
            ref_path.parent.mkdir(parents=True, exist_ok=True)
            ref_path.write_text(new_tip, encoding="utf-8")
            logger.info("✅ local-transport: updated %s → %s", branch, new_tip[:8])

        return PushResult(
            ok=True,
            message=f"local push to {url!r} succeeded",
            branch_heads=get_all_branch_heads(remote_root),
        )

    def push_objects(
        self,
        url: str,
        token: str | None,  # noqa: ARG002
        objects: list[ObjectPayload],
    ) -> ObjectsChunkResponse:
        """Write objects directly into the remote's local object store.

        Mirrors the server-side ``POST /push/objects`` behaviour: objects are
        content-addressed and idempotent — already-present objects are skipped.
        No branch refs are touched; only blob bytes are written.
        """
        from muse.core.object_store import write_object

        remote_root = self._repo_root(url)
        stored = 0
        skipped = 0
        for obj in objects:
            oid = obj.get("object_id", "")
            raw = obj.get("content", b"")
            if not oid or not raw:
                continue
            if write_object(remote_root, oid, raw):
                stored += 1
            else:
                skipped += 1
        return ObjectsChunkResponse(stored=stored, skipped=skipped)

    def filter_objects(
        self,
        url: str,
        token: str | None,  # noqa: ARG002
        object_ids: list[str],
    ) -> list[str]:
        """Return object IDs missing from the remote's local store (MWP Phase 1)."""
        from muse.core.object_store import read_object

        remote_root = self._repo_root(url)
        missing: list[str] = []
        for oid in object_ids:
            if read_object(remote_root, oid) is None:
                missing.append(oid)
        return missing

    def presign_objects(
        self,
        url: str,
        token: str | None,  # noqa: ARG002
        object_ids: list[str],
        direction: str,  # noqa: ARG002
    ) -> PresignResponse:
        """Local transport has no object storage backend — return all as inline."""
        return PresignResponse(presigned={}, inline=list(object_ids))

    def negotiate(
        self,
        url: str,
        token: str | None,  # noqa: ARG002
        want: list[str],
        have: list[str],
    ) -> NegotiateResponse:
        """Commit negotiation against a local repo (MWP Phase 5)."""
        from muse.core.store import read_commit as _rc

        remote_root = self._repo_root(url)
        have_set = set(have)

        # Which of the client's have-IDs exist on the remote?
        ack = [cid for cid in have if _rc(remote_root, cid) is not None]
        ack_set = set(ack)

        common_base: str | None = None
        for cid in want:
            # Walk parents looking for an acked ancestor.
            commit = _rc(remote_root, cid)
            if commit is None:
                continue
            for pid in filter(None, [commit.parent_commit_id, commit.parent2_commit_id]):
                if pid in ack_set:
                    common_base = pid
                    break
            if common_base:
                break

        ready = common_base is not None or not have_set
        return NegotiateResponse(ack=ack, common_base=common_base, ready=ready)

    def push_tags(
        self,
        url: str,
        token: str | None,  # noqa: ARG002
        tags: list[WireTag],
    ) -> int:
        """Write tags directly into the remote's local tag store."""
        from muse.core.store import TagDict, TagRecord, write_tag

        remote_root = self._repo_root(url)
        stored = 0
        for wire_tag in tags:
            try:
                tag_record = TagRecord.from_dict(TagDict(
                    tag_id=wire_tag["tag_id"],
                    repo_id=wire_tag["repo_id"],
                    commit_id=wire_tag["commit_id"],
                    tag=wire_tag["tag"],
                    created_at=wire_tag["created_at"],
                ))
                write_tag(remote_root, tag_record)
                stored += 1
            except (KeyError, ValueError) as exc:
                logger.warning("⚠️ local-transport push_tags: bad tag — %s", exc)
        return stored

    def create_release(
        self,
        url: str,
        token: str | None,  # noqa: ARG002
        release: ReleaseDict,
    ) -> str:
        """Write a release directly into the remote's local release store."""
        from muse.core.store import ReleaseRecord, write_release

        remote_root = self._repo_root(url)
        try:
            release_record = ReleaseRecord.from_dict(release)
            write_release(remote_root, release_record)
            return release_record.release_id
        except (KeyError, ValueError) as exc:
            raise TransportError(f"create_release: invalid release data — {exc}", 0) from exc

    def list_releases_remote(
        self,
        url: str,
        token: str | None,  # noqa: ARG002
        channel: str | None = None,
        include_drafts: bool = False,
    ) -> list[ReleaseDict]:
        """Read releases from the remote's local release store."""
        from muse.core.store import ReleaseChannel, list_releases

        remote_root = self._repo_root(url)
        repo_json_path = remote_root / ".muse" / "repo.json"
        try:
            repo_data = json.loads(repo_json_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise TransportError(f"Cannot read remote repo.json: {exc}", 0) from exc
        repo_id = str(repo_data.get("repo_id", ""))
        _channel_map: dict[str, ReleaseChannel] = {
            "stable": "stable", "beta": "beta", "alpha": "alpha", "nightly": "nightly",
        }
        channel_arg: ReleaseChannel | None = _channel_map.get(channel, None) if channel else None
        records = list_releases(remote_root, repo_id, channel=channel_arg, include_drafts=include_drafts)
        return [r.to_dict() for r in records]

    def delete_release_remote(
        self,
        url: str,
        token: str | None,  # noqa: ARG002
        tag: str,
    ) -> None:
        """Delete a release record from the remote's local release store."""
        from muse.core.store import delete_release, get_release_for_tag

        remote_root = self._repo_root(url)
        repo_json_path = remote_root / ".muse" / "repo.json"
        try:
            repo_data = json.loads(repo_json_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise TransportError(f"Cannot read remote repo.json: {exc}", 0) from exc
        repo_id = str(repo_data.get("repo_id", ""))
        release = get_release_for_tag(remote_root, repo_id, tag)
        if release is None:
            raise TransportError(f"Release '{tag}' not found on remote.", 404)
        if not delete_release(remote_root, repo_id, release.release_id):
            raise TransportError(f"Failed to delete release '{tag}' on remote.", 0)


# ---------------------------------------------------------------------------
# Factory — select transport based on URL scheme
# ---------------------------------------------------------------------------


def make_transport(url: str) -> "HttpTransport | LocalFileTransport":
    """Return the appropriate transport for *url*.

    - ``file://`` URLs → :class:`LocalFileTransport` (no server required)
    - All other URLs  → :class:`HttpTransport` (requires MuseHub server)

    Args:
        url: Remote repository URL.

    Returns:
        A transport instance implementing :class:`MuseTransport`.
    """
    if urllib.parse.urlparse(url).scheme == "file":
        return LocalFileTransport()
    return HttpTransport()

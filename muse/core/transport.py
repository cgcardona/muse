"""Muse transport layer — HTTP and local-filesystem remote communication.

The :class:`MuseTransport` Protocol defines the interface between the Muse CLI
and a remote host.  The CLI calls this Protocol; the implementation is chosen
at runtime by :func:`make_transport` based on the URL scheme.

Transport implementations
--------------------------

:class:`HttpTransport`
    Synchronous HTTPS transport using stdlib ``urllib.request``.  Used for
    ``https://`` remote URLs (MuseHub server required).

:class:`LocalFileTransport`
    Zero-network transport for ``file://`` URLs.  Reads and writes directly
    from the remote's ``.muse/`` directory on the local filesystem (or a
    shared network mount).  No server is required — ideal for local testing,
    monorepo setups, and offline workflows.

Use :func:`make_transport` instead of constructing either class directly —
it inspects the URL scheme and returns the appropriate implementation.

MuseHub API contract (HttpTransport)
-------------------------------------

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
- Validates JSON content-type before parsing.

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
from typing import IO, Protocol, runtime_checkable

from muse.core.pack import FetchRequest, ObjectPayload, PackBundle, PushResult, RemoteInfo
from muse.core.store import CommitDict, SnapshotDict
from muse.core.validation import MAX_RESPONSE_BYTES, contain_path, validate_branch_name
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

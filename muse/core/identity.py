"""Global identity store — ``~/.muse/identity.toml``.

Credentials (bearer tokens) are kept here, separate from per-repository
configuration.  This means tokens are never accidentally committed to
version control, and a single identity can authenticate across all
repositories on the same hub.

Why global, not per-repo
-------------------------
Git hides tokens in ``~/.netrc`` or the credential helper chain — an
afterthought.  Muse makes identity a first-class, machine-scoped concept.
The repository knows *where* the hub is (``[hub] url`` in config.toml).
The machine knows *who you are* (this file).  The two concerns are
deliberately separated.

Identity types
--------------
``type = "human"``
    A person.  Authenticated via OAuth or a personal access token.  No
    explicit capability list — the hub governs what humans can do via roles.

``type = "agent"``
    An autonomous process.  Authenticated via a scoped capability token.
    The ``capabilities`` field in this file reflects what the token allows,
    enabling agents to self-inspect before attempting an operation.

File format
-----------
TOML with one section per hub hostname::

    ["musehub.ai"]
    type         = "human"
    name         = "Alice"
    id           = "usr_abc123"
    token        = "eyJ..."      # bearer token — NEVER logged

    ["staging.musehub.ai"]
    type         = "agent"
    name         = "composer-v2"
    id           = "agt_def456"
    token        = "eyJ..."
    capabilities = ["read:*", "write:midi", "commit"]

Security
--------
The file is created with mode 0o600 (user-readable only).  It is never
read or written as part of a repository snapshot.  Every log call that
references a token masks it as ``"Bearer ***"``.
"""

from __future__ import annotations

import logging
import os
import pathlib
import stat
import tomllib
from typing import TypedDict

logger = logging.getLogger(__name__)

_IDENTITY_DIR = pathlib.Path.home() / ".muse"
_IDENTITY_FILE = _IDENTITY_DIR / "identity.toml"


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


class IdentityEntry(TypedDict, total=False):
    """One authenticated identity, keyed by hub hostname in identity.toml."""

    type: str             # "human" | "agent"
    name: str             # display name
    id: str               # hub-assigned identity ID
    token: str            # bearer token — never logged
    capabilities: list[str]  # agent capability strings (empty for humans)


# ---------------------------------------------------------------------------
# Path helper
# ---------------------------------------------------------------------------


def get_identity_path() -> pathlib.Path:
    """Return the path to the global identity file (``~/.muse/identity.toml``)."""
    return _IDENTITY_FILE


# ---------------------------------------------------------------------------
# URL → hostname normalisation
# ---------------------------------------------------------------------------


def _hostname_from_url(url: str) -> str:
    """Extract the hostname from a URL or return the string as-is.

    Examples::

        "https://musehub.ai/repos/x" → "musehub.ai"
        "musehub.ai"                 → "musehub.ai"
        "https://musehub.ai"         → "musehub.ai"
    """
    stripped = url.strip().rstrip("/")
    if "://" in stripped:
        stripped = stripped.split("://", 1)[1]
    # Strip path component, keep only host[:port]
    return stripped.split("/")[0]


# ---------------------------------------------------------------------------
# TOML serialiser (write-side — stdlib tomllib is read-only)
# ---------------------------------------------------------------------------


def _dump_identity(identities: dict[str, IdentityEntry]) -> str:
    """Serialise a hostname → entry mapping to TOML text.

    All hostnames are quoted in the section header so that dotted names
    (e.g. ``musehub.ai``) are treated as literal keys, not nested tables.
    """
    lines: list[str] = []
    for hostname in sorted(identities):
        entry = identities[hostname]
        # Always quote the section key — dotted names are literal, not nested.
        escaped_host = hostname.replace("\\", "\\\\").replace('"', '\\"')
        lines.append(f'["{escaped_host}"]')
        t = entry.get("type", "")
        if t:
            lines.append(f'type = "{t}"')
        name = entry.get("name", "")
        if name:
            escaped_name = name.replace("\\", "\\\\").replace('"', '\\"')
            lines.append(f'name = "{escaped_name}"')
        identity_id = entry.get("id", "")
        if identity_id:
            lines.append(f'id = "{identity_id}"')
        token = entry.get("token", "")
        if token:
            escaped_tok = token.replace("\\", "\\\\").replace('"', '\\"')
            lines.append(f'token = "{escaped_tok}"')
        caps = entry.get("capabilities") or []
        if caps:
            caps_str = ", ".join(f'"{c}"' for c in caps)
            lines.append(f"capabilities = [{caps_str}]")
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Load / save
# ---------------------------------------------------------------------------


def _load_all(path: pathlib.Path) -> dict[str, IdentityEntry]:
    """Load all identity entries from *path*.  Returns empty dict if absent."""
    if not path.is_file():
        return {}
    try:
        with path.open("rb") as fh:
            raw = tomllib.load(fh)
    except Exception as exc:  # noqa: BLE001
        logger.warning("⚠️ Failed to parse identity file %s: %s", path, exc)
        return {}

    result: dict[str, IdentityEntry] = {}
    for hostname, raw_entry in raw.items():
        if not isinstance(raw_entry, dict):
            continue
        entry: IdentityEntry = {}
        t = raw_entry.get("type")
        if isinstance(t, str):
            entry["type"] = t
        n = raw_entry.get("name")
        if isinstance(n, str):
            entry["name"] = n
        i = raw_entry.get("id")
        if isinstance(i, str):
            entry["id"] = i
        tok = raw_entry.get("token")
        if isinstance(tok, str):
            entry["token"] = tok
        caps = raw_entry.get("capabilities")
        if isinstance(caps, list):
            entry["capabilities"] = [str(c) for c in caps if isinstance(c, str)]
        result[hostname] = entry

    return result


def _save_all(identities: dict[str, IdentityEntry], path: pathlib.Path) -> None:
    """Write *identities* to *path* with mode 0o600."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = _dump_identity(identities)
    path.write_text(text, encoding="utf-8")
    # Restrict to user-readable only — tokens must not be group/world readable.
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError as exc:
        logger.warning("⚠️ Could not restrict permissions on %s: %s", path, exc)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_identity(hub_url: str) -> IdentityEntry | None:
    """Return the stored identity for *hub_url*, or ``None`` if absent.

    The URL is normalised to a hostname before lookup, so
    ``https://musehub.ai/repos/x`` and ``musehub.ai`` resolve to the same
    entry.

    Args:
        hub_url: Hub URL or bare hostname.

    Returns:
        :class:`IdentityEntry` if an identity is stored, else ``None``.
    """
    hostname = _hostname_from_url(hub_url)
    return _load_all(_IDENTITY_FILE).get(hostname)


def save_identity(hub_url: str, entry: IdentityEntry) -> None:
    """Store *entry* as the identity for *hub_url*.

    Creates ``~/.muse/identity.toml`` with mode 0o600 if it does not exist.

    Args:
        hub_url: Hub URL or bare hostname.
        entry: Identity data to store.
    """
    hostname = _hostname_from_url(hub_url)
    identities = _load_all(_IDENTITY_FILE)
    identities[hostname] = entry
    _save_all(identities, _IDENTITY_FILE)
    logger.info("✅ Identity for %s saved (Bearer ***)", hostname)


def clear_identity(hub_url: str) -> bool:
    """Remove the stored identity for *hub_url*.

    Args:
        hub_url: Hub URL or bare hostname.

    Returns:
        ``True`` if an entry was removed, ``False`` if no entry existed.
    """
    hostname = _hostname_from_url(hub_url)
    identities = _load_all(_IDENTITY_FILE)
    if hostname not in identities:
        return False
    del identities[hostname]
    _save_all(identities, _IDENTITY_FILE)
    logger.info("✅ Identity for %s cleared", hostname)
    return True


def resolve_token(hub_url: str) -> str | None:
    """Return the bearer token for *hub_url*, or ``None``.

    The token is NEVER logged by this function.

    Args:
        hub_url: Hub URL or bare hostname.

    Returns:
        Token string if present and non-empty, else ``None``.
    """
    entry = load_identity(hub_url)
    if entry is None:
        return None
    tok = entry.get("token", "")
    return tok.strip() if tok.strip() else None


def list_all_identities() -> dict[str, IdentityEntry]:
    """Return all stored identities keyed by hub hostname.

    Returns an empty dict if the identity file does not exist.
    """
    return _load_all(_IDENTITY_FILE)

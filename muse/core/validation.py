"""Trust-boundary validation primitives for Muse.

Every function in this module operates on untrusted input and either returns a
safe value or raises ValueError / TypeError with a descriptive message.  No
other muse module is imported here — this module must stay at the bottom of
the dependency graph so it can be safely imported by every layer.
"""

from __future__ import annotations

import math
import pathlib
import re

# ---------------------------------------------------------------------------
# Size ceilings
# ---------------------------------------------------------------------------

MAX_FILE_BYTES: int = 256 * 1024 * 1024  # 256 MB — per-file read cap
MAX_RESPONSE_BYTES: int = 64 * 1024 * 1024  # 64 MB — HTTP response cap
MAX_SYSEX_BYTES: int = 65_536  # 64 KiB — MIDI sysex data truncation point

# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")

# Branch/ref names: follow Git conventions.
# Forbidden: backslash, null, CR, LF, leading/trailing dots, consecutive dots.
# Allowed: forward slash (enables feature/my-branch style namespacing).
# Max 255 chars.
_BRANCH_FORBIDDEN_RE = re.compile(
    r"[\\\x00\r\n\t]"   # backslash, null, CR, LF, tab (not forward slash)
    r"|^\."              # leading dot
    r"|\.$"             # trailing dot
    r"|\.{2,}"          # consecutive dots (..  ...)
    r"|//"              # consecutive slashes
    r"|^/"              # leading slash
    r"|/$"              # trailing slash
)

# Valid domain plugin name: lowercase letters, digits, hyphens, underscores;
# must start with a lowercase letter.
_DOMAIN_RE = re.compile(r"^[a-z][a-z0-9_-]{0,62}$")

# Control characters to strip from terminal output.
# Removes all C0 (0x00-0x1F) except \t (0x09) and \n (0x0A),
# plus DEL (0x7F), and C1 (0x80-0x9F).
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f\x80-\x9f]")

# Glob metacharacters that must not appear in prefix arguments.
_GLOB_META_RE = re.compile(r"[*?\[\]{}]")

# ---------------------------------------------------------------------------
# ID validation
# ---------------------------------------------------------------------------


def validate_object_id(s: str) -> str:
    """Return *s* unchanged if it is a valid SHA-256 hex string (64 lowercase hex chars).

    Raises ValueError for anything else, preventing path-traversal attacks
    built from crafted object IDs.
    """
    if not isinstance(s, str):
        raise TypeError(f"object_id must be str, got {type(s).__name__}")
    if not _HEX64_RE.match(s):
        raise ValueError(
            f"Invalid object ID {s!r}: expected exactly 64 lowercase hex characters."
        )
    return s


def validate_ref_id(s: str) -> str:
    """Return *s* unchanged if it is a valid commit/snapshot/tag ID.

    Uses the same 64-char hex rule as object IDs; the two functions exist as
    separate names so call-sites are self-documenting.
    """
    if not isinstance(s, str):
        raise TypeError(f"ref_id must be str, got {type(s).__name__}")
    if not _HEX64_RE.match(s):
        raise ValueError(
            f"Invalid ref ID {s!r}: expected exactly 64 lowercase hex characters."
        )
    return s


# ---------------------------------------------------------------------------
# Branch / repo-id validation
# ---------------------------------------------------------------------------


def validate_branch_name(name: str) -> str:
    """Return *name* unchanged if it is a safe branch name.

    Follows Git branch name conventions:
    - Forward slashes are allowed (enables ``feature/my-branch`` namespacing).
    - Backslashes, null bytes, CR, LF are rejected.
    - Leading or trailing dots are rejected.
    - Consecutive dots (..) are rejected (would create ``..`` traversal).
    - Leading, trailing, or consecutive slashes are rejected.
    - Empty string and names longer than 255 characters are rejected.
    """
    if not isinstance(name, str):
        raise TypeError(f"branch name must be str, got {type(name).__name__}")
    if not name:
        raise ValueError("Branch name must not be empty.")
    if len(name) > 255:
        raise ValueError(
            f"Branch name too long ({len(name)} chars); maximum is 255."
        )
    if _BRANCH_FORBIDDEN_RE.search(name):
        raise ValueError(
            f"Branch name {name!r} contains forbidden characters "
            "(path separators, null bytes, or consecutive dots)."
        )
    return name


def validate_repo_id(repo_id: str) -> str:
    """Return *repo_id* if it contains no path-traversal components.

    repo_id values are UUIDs in normal operation.  We enforce that they
    contain no path separators or dot-sequences rather than enforcing UUID
    format strictly, to allow future flexibility.
    """
    if not isinstance(repo_id, str):
        raise TypeError(f"repo_id must be str, got {type(repo_id).__name__}")
    if not repo_id:
        raise ValueError("repo_id must not be empty.")
    if len(repo_id) > 255:
        raise ValueError(f"repo_id too long ({len(repo_id)} chars).")
    if _BRANCH_FORBIDDEN_RE.search(repo_id):
        raise ValueError(
            f"repo_id {repo_id!r} contains forbidden characters."
        )
    return repo_id


def validate_domain_name(domain: str) -> str:
    """Return *domain* if it is a valid plugin domain name."""
    if not _DOMAIN_RE.match(domain):
        raise ValueError(
            f"Domain name {domain!r} is invalid. "
            "Must start with a lowercase letter and contain only "
            "lowercase letters, digits, hyphens, or underscores (max 63 chars)."
        )
    return domain


# ---------------------------------------------------------------------------
# Path containment
# ---------------------------------------------------------------------------


def contain_path(base: pathlib.Path, rel: str) -> pathlib.Path:
    """Join *base* / *rel*, resolve, and assert the result stays inside *base*.

    This is the central defence against zip-slip and path-traversal attacks
    in manifest keys, rel_path arguments, and any other user-controlled path
    component that is joined onto a trusted base directory.

    Raises ValueError if the resolved path escapes *base*.
    """
    if not isinstance(rel, str):
        raise TypeError(f"rel must be str, got {type(rel).__name__}")
    if not rel:
        raise ValueError("Relative path component must not be empty.")
    # Absolute paths on POSIX cause pathlib to discard the base entirely.
    joined = base / rel
    resolved = joined.resolve()
    base_resolved = base.resolve()
    if not resolved.is_relative_to(base_resolved):
        raise ValueError(
            f"Path traversal detected: {rel!r} escapes the base directory "
            f"{base_resolved}."
        )
    return resolved


# ---------------------------------------------------------------------------
# Glob safety
# ---------------------------------------------------------------------------


def sanitize_glob_prefix(prefix: str) -> str:
    """Return *prefix* with glob metacharacters removed.

    Used in _find_commit_by_prefix to prevent glob injection turning a
    targeted lookup into an arbitrary filesystem scan.
    """
    return _GLOB_META_RE.sub("", prefix)


# ---------------------------------------------------------------------------
# Display sanitization
# ---------------------------------------------------------------------------


def sanitize_display(s: str) -> str:
    """Strip terminal control characters from *s* before echoing to the user.

    Preserves newline (\\n) and tab (\\t) as these are legitimate in
    multi-line commit messages.  Removes all other C0/C1 control characters
    including ESC (0x1B), BEL (0x07), and CSI (0x9B) — the entry points for
    ANSI/OSC terminal escape injection.

    Storage is never mutated; sanitization happens only at display time.
    """
    return _CONTROL_CHARS_RE.sub("", s)


# ---------------------------------------------------------------------------
# Numeric guards
# ---------------------------------------------------------------------------


def clamp_int(value: int, lo: int, hi: int, name: str = "value") -> int:
    """Return *value* clamped to [lo, hi], raising ValueError if out of range."""
    if not lo <= value <= hi:
        raise ValueError(
            f"{name} must be between {lo} and {hi}, got {value}."
        )
    return value


def finite_float(value: float, fallback: float, name: str = "value") -> float:
    """Return *value* if finite, else *fallback* (and log nothing here — caller logs)."""
    if not math.isfinite(value):
        return fallback
    return value

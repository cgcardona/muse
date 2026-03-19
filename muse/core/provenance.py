"""Agent identity and commit signing for the Muse VCS.

Every commit in Muse can carry cryptographic provenance metadata that
identifies *who* or *what* produced it — a human author, an autonomous AI
agent, or a specific toolchain run.

Signing model
-------------
Signatures use HMAC-SHA256 with a per-agent shared key stored under
``.muse/keys/<agent_id>.key`` (32 raw bytes).  This is a symmetric scheme
requiring no external cryptography library — only Python stdlib ``hmac``,
``hashlib``, and ``secrets``.

For production public-key (Ed25519) signing that allows third-party
verification without sharing secrets, add the ``cryptography`` package and
implement an ``Ed25519Signer`` adapter following the same interface as
:func:`sign_commit_hmac` / :func:`verify_commit_hmac`.

Key management
--------------
Keys live in ``.muse/keys/`` which should be added to ``.gitignore`` /
``.museignore``.  Each agent or human has one key file.  Key ID is the
first 16 hex characters of the SHA-256 of the raw key bytes — short enough
to log, long enough to be unique.

Usage
-----
::

    from muse.core.provenance import (
        make_agent_identity, generate_agent_key, write_agent_key,
        sign_commit_hmac, verify_commit_hmac,
    )

    key = generate_agent_key()
    write_agent_key(repo_root, "my-agent", key)
    identity = make_agent_identity("my-agent", model_id="claude-opus-4")
    sig = sign_commit_hmac(commit_hash, key)
    assert verify_commit_hmac(commit_hash, sig, key)
"""

import hashlib
import hmac
import logging
import pathlib
import secrets
from typing import TypedDict

logger = logging.getLogger(__name__)

_KEYS_DIR = ".muse/keys"


# ---------------------------------------------------------------------------
# Agent identity
# ---------------------------------------------------------------------------


class AgentIdentity(TypedDict, total=False):
    """Structured identity record for a human or AI agent.

    All fields are optional so that partial provenance (e.g. only
    ``agent_id`` is known) can be expressed without filling dummy values.

    ``agent_id``
        Stable human-readable identifier chosen by the agent or its operator.
        Should be unique within a team (e.g. ``"counterpoint-bot-v1"``).
    ``model_id``
        Model identifier for AI agents (e.g. ``"claude-opus-4"``).
        Empty for human authors.
    ``toolchain_id``
        Build system or IDE that produced the commit
        (e.g. ``"cursor-agent-v2"``).
    ``prompt_hash``
        SHA-256 hex of the instruction/prompt that triggered this session.
        Privacy-preserving: the hash is logged without storing the content.
    ``execution_context_hash``
        SHA-256 hex of any additional execution context (system prompt,
        environment config, etc.).
    """

    agent_id: str
    model_id: str
    toolchain_id: str
    prompt_hash: str
    execution_context_hash: str


def make_agent_identity(
    agent_id: str,
    *,
    model_id: str = "",
    toolchain_id: str = "",
    prompt: str = "",
    execution_context: str = "",
) -> AgentIdentity:
    """Build an :class:`AgentIdentity` with optional hashed sensitive fields.

    ``prompt`` and ``execution_context`` are hashed before storage so that
    the raw instruction text never appears in the commit record.

    Args:
        agent_id:           Stable agent identifier string.
        model_id:           Model name/version (empty for humans).
        toolchain_id:       Toolchain producing the commit.
        prompt:             Raw instruction text to hash (not stored).
        execution_context:  Additional context to hash (not stored).

    Returns:
        An :class:`AgentIdentity` with only non-empty fields populated.
    """
    identity = AgentIdentity(agent_id=agent_id)
    if model_id:
        identity["model_id"] = model_id
    if toolchain_id:
        identity["toolchain_id"] = toolchain_id
    if prompt:
        identity["prompt_hash"] = hashlib.sha256(prompt.encode()).hexdigest()
    if execution_context:
        identity["execution_context_hash"] = hashlib.sha256(
            execution_context.encode()
        ).hexdigest()
    return identity


# ---------------------------------------------------------------------------
# Key generation and I/O
# ---------------------------------------------------------------------------


def generate_agent_key() -> bytes:
    """Generate a cryptographically random 32-byte HMAC key.

    Uses :func:`secrets.token_bytes` which is seeded from OS entropy.

    Returns:
        32 raw bytes suitable for use with :func:`sign_commit_hmac`.
    """
    return secrets.token_bytes(32)


def key_fingerprint(key: bytes) -> str:
    """Return the first 16 hex characters of the SHA-256 of *key*.

    Short enough to log, long enough for practical uniqueness.

    Args:
        key: Raw HMAC key bytes.

    Returns:
        16-character lowercase hex string.
    """
    return hashlib.sha256(key).hexdigest()[:16]


def _keys_dir(repo_root: pathlib.Path) -> pathlib.Path:
    return repo_root / _KEYS_DIR


def _key_path(repo_root: pathlib.Path, agent_id: str) -> pathlib.Path:
    safe_id = agent_id.replace("/", "_").replace("..", "_")
    return _keys_dir(repo_root) / f"{safe_id}.key"


def write_agent_key(
    repo_root: pathlib.Path,
    agent_id: str,
    key: bytes,
) -> pathlib.Path:
    """Persist *key* for *agent_id* under ``.muse/keys/<agent_id>.key``.

    Creates the keys directory if it does not exist.  Overwrites any
    existing key for this agent without warning.

    Args:
        repo_root: Repository root.
        agent_id:  Stable agent identifier.
        key:       32-byte HMAC key to store.

    Returns:
        Path to the written key file.
    """
    keys_dir = _keys_dir(repo_root)
    keys_dir.mkdir(parents=True, exist_ok=True)
    path = _key_path(repo_root, agent_id)
    path.write_bytes(key)
    logger.debug("✅ Wrote key for agent %r (%s)", agent_id, key_fingerprint(key))
    return path


def read_agent_key(repo_root: pathlib.Path, agent_id: str) -> bytes | None:
    """Load the HMAC key for *agent_id* from ``.muse/keys/<agent_id>.key``.

    Args:
        repo_root: Repository root.
        agent_id:  Stable agent identifier.

    Returns:
        Raw key bytes, or ``None`` when the key file does not exist.
    """
    path = _key_path(repo_root, agent_id)
    if not path.exists():
        logger.debug("⚠️ No key file for agent %r at %s", agent_id, path)
        return None
    return path.read_bytes()


# ---------------------------------------------------------------------------
# HMAC-SHA256 signing and verification
# ---------------------------------------------------------------------------


def sign_commit_hmac(commit_hash: str, key: bytes) -> str:
    """Produce an HMAC-SHA256 signature over *commit_hash* using *key*.

    The signature covers the full commit hash string (UTF-8 encoded), which
    in turn is a SHA-256 of the canonical commit JSON.  This gives transitive
    coverage of all commit fields.

    Args:
        commit_hash: Hex SHA-256 commit ID to sign.
        key:         32-byte HMAC key for this agent.

    Returns:
        64-character lowercase hex HMAC-SHA256 digest.
    """
    return hmac.new(key, commit_hash.encode(), "sha256").hexdigest()


def verify_commit_hmac(commit_hash: str, signature: str, key: bytes) -> bool:
    """Verify an HMAC-SHA256 *signature* over *commit_hash*.

    Uses :func:`hmac.compare_digest` for constant-time comparison to
    prevent timing attacks.

    Args:
        commit_hash: The commit ID that was signed.
        signature:   64-character hex digest to verify.
        key:         HMAC key to verify against.

    Returns:
        ``True`` when the signature is valid, ``False`` otherwise.
    """
    expected = sign_commit_hmac(commit_hash, key)
    return hmac.compare_digest(expected, signature)


# ---------------------------------------------------------------------------
# Convenience: sign a CommitRecord in-place
# ---------------------------------------------------------------------------


def sign_commit_record(
    commit_id: str,
    agent_id: str,
    repo_root: pathlib.Path,
) -> tuple[str, str] | None:
    """Sign *commit_id* with the stored key for *agent_id*.

    Looks up the key file, computes the HMAC, and returns
    ``(signature, signer_key_id)`` ready to be stored in the
    :class:`~muse.core.store.CommitRecord`.

    Args:
        commit_id:  SHA-256 hex commit ID to sign.
        agent_id:   Agent whose key should be used.
        repo_root:  Repository root for key lookup.

    Returns:
        ``(signature_hex, key_fingerprint)`` on success, ``None`` when no
        key file exists for this agent.
    """
    key = read_agent_key(repo_root, agent_id)
    if key is None:
        return None
    sig = sign_commit_hmac(commit_id, key)
    fprint = key_fingerprint(key)
    logger.debug("✅ Signed commit %s with key %s", commit_id[:8], fprint)
    return sig, fprint

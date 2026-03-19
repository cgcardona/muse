"""Tests for muse.core.provenance — AgentIdentity, HMAC signing, key I/O."""

import pathlib
import tempfile

import pytest

from muse.core.provenance import (
    AgentIdentity,
    generate_agent_key,
    key_fingerprint,
    make_agent_identity,
    read_agent_key,
    sign_commit_hmac,
    sign_commit_record,
    verify_commit_hmac,
    write_agent_key,
)
from muse.core.store import CommitRecord
import datetime


# ---------------------------------------------------------------------------
# AgentIdentity factory
# ---------------------------------------------------------------------------


class TestMakeAgentIdentity:
    def test_required_fields_present(self) -> None:
        identity = make_agent_identity(
            agent_id="test-agent",
            model_id="gpt-5",
            toolchain_id="muse-v2",
        )
        assert identity["agent_id"] == "test-agent"
        assert identity.get("model_id") == "gpt-5"
        assert identity.get("toolchain_id") == "muse-v2"

    def test_prompt_hash_is_hex(self) -> None:
        identity = make_agent_identity(
            agent_id="a",
            model_id="m",
            toolchain_id="t",
            prompt="system: you are a music agent",
        )
        prompt_hash = identity.get("prompt_hash", "")
        assert isinstance(prompt_hash, str)
        assert len(prompt_hash) == 64
        assert all(c in "0123456789abcdef" for c in prompt_hash)

    def test_no_prompt_gives_no_hash_key(self) -> None:
        identity = make_agent_identity(agent_id="a", model_id="m", toolchain_id="t")
        # When no prompt is provided, prompt_hash is absent (total=False TypedDict).
        assert identity.get("prompt_hash", "") == ""

    def test_execution_context_hash_populated(self) -> None:
        identity = make_agent_identity(
            agent_id="a",
            model_id="m",
            toolchain_id="t",
            execution_context='{"env": "ci", "version": "1.2.3"}',
        )
        ec_hash = identity.get("execution_context_hash", "")
        assert isinstance(ec_hash, str)
        assert len(ec_hash) == 64


# ---------------------------------------------------------------------------
# Key generation and fingerprinting
# ---------------------------------------------------------------------------


class TestKeyGeneration:
    def test_generate_key_is_32_bytes(self) -> None:
        key = generate_agent_key()
        assert isinstance(key, bytes)
        assert len(key) == 32

    def test_keys_are_unique(self) -> None:
        keys = {generate_agent_key() for _ in range(10)}
        assert len(keys) == 10

    def test_fingerprint_is_short_hex(self) -> None:
        key = generate_agent_key()
        fp = key_fingerprint(key)
        assert isinstance(fp, str)
        assert 8 <= len(fp) <= 16
        assert all(c in "0123456789abcdef" for c in fp)

    def test_fingerprint_is_deterministic(self) -> None:
        key = b"\x01" * 32
        assert key_fingerprint(key) == key_fingerprint(key)


# ---------------------------------------------------------------------------
# Key I/O
# ---------------------------------------------------------------------------


class TestKeyIO:
    def test_write_and_read_roundtrip(self, tmp_path: pathlib.Path) -> None:
        key = generate_agent_key()
        agent_id = "roundtrip-agent"
        write_agent_key(tmp_path, agent_id, key)
        recovered = read_agent_key(tmp_path, agent_id)
        assert recovered == key

    def test_read_missing_key_returns_none(self, tmp_path: pathlib.Path) -> None:
        result = read_agent_key(tmp_path, "nonexistent-agent")
        assert result is None

    def test_key_file_is_readable(self, tmp_path: pathlib.Path) -> None:
        key = b"\xde\xad\xbe\xef" + b"\x00" * 28
        write_agent_key(tmp_path, "hex-agent", key)
        # Find the key file and verify it roundtrips correctly.
        key_dir = tmp_path / ".muse" / "keys"
        files = list(key_dir.rglob("*.key"))
        assert files
        recovered = read_agent_key(tmp_path, "hex-agent")
        assert recovered == key


# ---------------------------------------------------------------------------
# HMAC signing / verification
# ---------------------------------------------------------------------------


class TestHMACSigning:
    def test_sign_and_verify_succeed(self) -> None:
        key = generate_agent_key()
        commit_hash = "abc123def456" * 4
        sig = sign_commit_hmac(commit_hash, key)
        assert verify_commit_hmac(commit_hash, sig, key)

    def test_wrong_key_fails(self) -> None:
        key1 = generate_agent_key()
        key2 = generate_agent_key()
        commit_hash = "abc123"
        sig = sign_commit_hmac(commit_hash, key1)
        assert not verify_commit_hmac(commit_hash, sig, key2)

    def test_wrong_commit_hash_fails(self) -> None:
        key = generate_agent_key()
        sig = sign_commit_hmac("commit-a", key)
        assert not verify_commit_hmac("commit-b", sig, key)

    def test_tampered_signature_fails(self) -> None:
        key = generate_agent_key()
        sig = sign_commit_hmac("abc", key)
        tampered = sig[:-4] + "0000"
        assert not verify_commit_hmac("abc", tampered, key)

    def test_signature_is_hex_string(self) -> None:
        key = generate_agent_key()
        sig = sign_commit_hmac("test-commit", key)
        assert isinstance(sig, str)
        assert all(c in "0123456789abcdef" for c in sig)

    def test_signature_length_is_64(self) -> None:
        key = generate_agent_key()
        sig = sign_commit_hmac("test-commit", key)
        assert len(sig) == 64  # HMAC-SHA256 produces 32 bytes = 64 hex chars


# ---------------------------------------------------------------------------
# sign_commit_record
# ---------------------------------------------------------------------------


class TestSignCommitRecord:
    def test_sign_commit_record_writes_signature(self, tmp_path: pathlib.Path) -> None:
        key = generate_agent_key()
        agent_id = "sign-test-agent"
        write_agent_key(tmp_path, agent_id, key)

        commit_id = "deadbeef" * 8
        result = sign_commit_record(commit_id, agent_id, tmp_path)
        assert result is not None
        sig, fprint = result
        assert sig != ""
        assert fprint == key_fingerprint(key)
        assert verify_commit_hmac(commit_id, sig, key)

    def test_sign_commit_record_no_key_returns_none(self, tmp_path: pathlib.Path) -> None:
        result = sign_commit_record("aabbccdd" * 8, "ghost-agent", tmp_path)
        assert result is None

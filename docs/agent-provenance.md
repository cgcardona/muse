# Agent Provenance in Muse

## Overview

As Muse hosts multiple autonomous agents editing the same repository
concurrently, attribution and audit trail become critical.  Agent provenance
answers: **who wrote this commit, with which model, using which toolchain,
and can that attribution be verified cryptographically?**

## Commit-level fields

`CommitRecord` in `muse/core/store.py` carries six new optional fields
(all optional — omit if not tracking agent identity):

| Field | Description |
|-------|-------------|
| `agent_id` | Stable identifier for the agent or human (`"counterpoint-bot"`, `"alice"`) |
| `model_id` | AI model version used (`"gpt-5-turbo"`, `"claude-4"`) |
| `toolchain_id` | Agent framework version (`"muse-agent-v2.1"`) |
| `prompt_hash` | SHA-256 of the system prompt (no raw text stored) |
| `signature` | HMAC-SHA256 hex digest of the commit ID under the agent's key |
| `signer_key_id` | Short fingerprint of the signing key (for key lookup) |

## Signing and verification

`muse/core/provenance.py` provides:

```python
# Generate a new 32-byte HMAC key.
key = generate_agent_key()

# Persist the key to .muse/keys/<agent_id>-<fingerprint>.key
write_agent_key(repo_root, agent_id, key)

# Sign a commit ID.
sig = sign_commit_hmac(commit_id, key)

# Verify.
ok = verify_commit_hmac(commit_id, sig, key)  # uses hmac.compare_digest

# Convenience: sign a full CommitRecord in one call.
signed_commit = sign_commit_record(repo_root, commit_record)
```

HMAC-SHA256 using Python's standard `hmac` module requires no new
dependencies.  If stronger non-repudiation is needed in the future,
the `sign_commit_hmac` function can be upgraded to Ed25519 using the
`cryptography` package without changing any callers.

## AgentIdentity

`make_agent_identity()` constructs an `AgentIdentity` TypedDict:

```python
identity = make_agent_identity(
    agent_id="counterpoint-bot",
    model_id="gpt-5",
    toolchain_id="muse-agent-v2",
    prompt="system: you are a music composition agent",
    execution_context={"env": "ci", "run_id": "1234"},
)
```

Sensitive fields (`prompt`, `execution_context`) are hashed before storage —
the raw strings never appear in the identity record.

## Key storage

Agent keys live at `.muse/keys/<agent_id>-<fingerprint>.key` as hex-encoded
files.  Multiple agents can coexist; each has its own key file keyed by
`agent_id`.  Key files should be added to `.museignore` and managed
separately from the repository content.

## Querying provenance

The music query DSL supports provenance fields directly:

```bash
muse midi-query "agent_id == 'counterpoint-bot' and note.pitch > 60"
muse midi-query "model_id == 'gpt-5'"
```

## Related files

| File | Role |
|------|------|
| `muse/core/provenance.py` | `AgentIdentity`, key I/O, HMAC signing |
| `muse/core/store.py` | `CommitRecord`, `CommitDict` — six new fields |
| `tests/test_provenance.py` | Unit tests |

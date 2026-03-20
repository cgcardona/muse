# `muse auth` — Identity Management Reference

Muse has two primary user types: **humans** and **agents**.  Both are
first-class identities, authenticated identically.  This command manages the
full identity lifecycle: login, introspection, and logout.

---

## Table of Contents

1. [Why not `muse config set auth.token`?](#why-not-muse-config-set-authtoken)
2. [Identity File — `~/.muse/identity.toml`](#identity-file)
3. [Commands](#commands)
   - [muse auth login](#muse-auth-login)
   - [muse auth whoami](#muse-auth-whoami)
   - [muse auth logout](#muse-auth-logout)
4. [Authentication Flows](#authentication-flows)
5. [Token Security Best Practices](#token-security-best-practices)
6. [Environment Variables](#environment-variables)

---

## Why not `muse config set auth.token`?

Credentials belong to the **machine**, not the repository.  Storing a token
inside `.muse/config.toml` has three problems:

1. **Accidental commit** — `.muse/config.toml` is in the repo directory and
   could be committed to version control, exposing the token to everyone with
   access.
2. **Scope creep** — one machine may work with many repositories; the token is
   a machine-scoped credential.
3. **Tight coupling** — tying credentials to a repo prevents sharing them
   across projects on the same machine.

Muse separates these concerns:

| File | Stores | Scope |
|---|---|---|
| `.muse/config.toml` | Hub URL (`[hub] url`) | Per repository |
| `~/.muse/identity.toml` | Bearer token | Per machine |

The CLI reads the hub URL from the repo, the token from the machine.  The two
are combined only at request time — the token is never written to
`.muse/config.toml`.

---

## Identity File

**Path:** `~/.muse/identity.toml`  
**Permissions:** `0o600` (read/write owner only)  
**Directory permissions:** `0o700` (owner only)

### File format

TOML with one section per hub hostname.  The section key is the bare hostname
(no scheme, no path), always lowercase:

```toml
["musehub.ai"]
type         = "human"
name         = "Alice"
id           = "usr_abc123"
token        = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."

["staging.musehub.ai"]
type         = "agent"
name         = "composer-v2"
id           = "agt_def456"
token        = "muse_tok_..."
capabilities = ["read:*", "write:midi", "commit"]
```

### `IdentityEntry` fields

| Field | Type | Required | Description |
|---|---|---|---|
| `type` | `"human"` \| `"agent"` | Yes | Identity type |
| `token` | `str` | Yes | Bearer token — never logged |
| `name` | `str` | No | Display name (human name or agent handle) |
| `id` | `str` | No | Hub-assigned identity ID |
| `capabilities` | `list[str]` | No | Agent capability strings (empty for humans) |

### URL normalisation

The file is keyed by bare hostname, not full URL.  The following all resolve to
the same entry:

```
https://musehub.ai
https://musehub.ai/repos/my-song
MUSEHUB.AI
musehub.ai
```

Userinfo embedded in the URL (`user:password@musehub.ai`) is stripped before
use as the key — credentials are never stored inside the hostname key.

### Security properties

- The file is written with `0o600` permissions **from byte zero** using
  `os.open()` + `os.fchmod()`, eliminating the TOCTOU window that
  `write_text()` + `chmod()` creates.
- Writes are atomic: data goes to a temp file, then `os.replace()` renames it
  over the target.  A kill signal during write leaves the old file intact.
- A symlink at the target path is refused — symlink-based credential-overwrite
  attacks are blocked.
- An exclusive advisory lock (`fcntl.flock`) prevents concurrent write races
  when parallel agents log in simultaneously.
- The file is never read or written as part of a repository snapshot.

---

## Commands

### muse auth login

Store a bearer token in `~/.muse/identity.toml`.

```
muse auth login [OPTIONS]
```

**Options:**

| Flag | Env var | Description |
|---|---|---|
| `--token TOKEN` | `MUSE_TOKEN` | Bearer token. Reads `MUSE_TOKEN` if not passed explicitly. |
| `--hub URL` | — | Hub URL. Falls back to `[hub] url` in `.muse/config.toml`. |
| `--name NAME` | — | Display name for this identity. |
| `--id ID` | — | Hub-assigned identity ID (stored for reference). |
| `--agent` | — | Mark this identity as an agent (default: human). |

**Resolution order for the token:**

1. `MUSE_TOKEN` environment variable (preferred — does not appear in shell history)
2. `--token` CLI flag (warns about shell history exposure)
3. Interactive prompt via `getpass.getpass` (human-only flow)

**Examples:**

```bash
# Human — interactive prompt
muse auth login --hub https://musehub.ai

# Agent — non-interactive, token from environment variable
MUSE_TOKEN=$MY_SECRET muse auth login \
  --hub https://musehub.ai \
  --agent \
  --name "composer-v2" \
  --id "agt_xyz"

# Human — hub URL from repo config (no --hub needed after muse hub connect)
muse auth login

# Override identity metadata after initial login
muse auth login --token $MUSE_TOKEN --name "Alice (updated)" --hub musehub.ai
```

**What login does:**

1. Resolves the hub URL from `--hub` or the repo's `[hub] url` config.
2. Resolves the token from the environment, the flag, or an interactive prompt.
3. Warns if the token was passed via the `--token` CLI flag (shell history risk).
4. Creates or updates the `[<hostname>]` section in `~/.muse/identity.toml`.
5. Sets directory and file permissions (`0o700` / `0o600`).

---

### muse auth whoami

Display the stored identity for a hub.

```
muse auth whoami [OPTIONS]
```

**Options:**

| Flag | Description |
|---|---|
| `--hub URL` | Hub URL to inspect. Defaults to the repo's configured hub. |
| `--all` | Show identities for all configured hubs. |
| `--json` | Emit JSON instead of human-readable output. |

The raw token is **never** shown.  The output indicates only whether a token is
set (`set (Bearer ***)` or `not set`).

**Examples:**

```bash
# Human-readable output for the current repo's hub
muse auth whoami

# JSON output — for agent scripts
muse auth whoami --json

# Inspect a specific hub
muse auth whoami --hub https://staging.musehub.ai

# Show all stored identities
muse auth whoami --all
```

**JSON output shape:**

```json
{
  "hub": "musehub.ai",
  "type": "human",
  "name": "Alice",
  "id": "usr_abc123",
  "token_set": "true",
  "capabilities": []
}
```

**Exit codes:**

- `0` — identity found and displayed.
- Non-zero — no identity stored for the specified hub.  Useful in agent scripts:

```bash
muse auth whoami --hub musehub.ai --json || muse auth login --agent --hub musehub.ai
```

---

### muse auth logout

Remove stored credentials for a hub.

```
muse auth logout [OPTIONS]
```

**Options:**

| Flag | Description |
|---|---|
| `--hub URL` | Hub URL to log out from. Defaults to the repo's configured hub. |
| `--all` | Remove credentials for ALL configured hubs. |

The bearer token is deleted from `~/.muse/identity.toml`.  The hub URL in
`.muse/config.toml` is preserved — use `muse hub disconnect` to remove the hub
association from the repository as well.

**Examples:**

```bash
# Log out from the current repo's hub
muse auth logout

# Log out from a specific hub
muse auth logout --hub https://staging.musehub.ai

# Remove all stored credentials
muse auth logout --all
```

---

## Authentication Flows

### Human flow (interactive)

```bash
# 1. Connect the repo to a hub
muse hub connect https://musehub.ai

# 2. Log in (prompts for token)
muse auth login

# 3. Push
muse push
```

### Agent flow (non-interactive)

```bash
# In a CI/CD pipeline or autonomous agent:
MUSE_TOKEN="$SECRET_FROM_VAULT" muse auth login \
  --hub https://musehub.ai \
  --agent \
  --name "pipeline-agent-$BUILD_ID"

# Now push without further prompts
muse push
```

### Checking authentication status in a script

```bash
if muse auth whoami --json > /dev/null 2>&1; then
  echo "Authenticated — proceeding with push"
  muse push
else
  echo "Not authenticated — logging in"
  MUSE_TOKEN="$SECRET" muse auth login --hub https://musehub.ai --agent
fi
```

---

## Token Security Best Practices

**Prefer `MUSE_TOKEN` over `--token`.**  
Tokens passed as `--token` appear in:
- Shell history (`~/.zsh_history`, `~/.bash_history`)
- Process listings (`ps aux` on Linux)
- `/proc/PID/cmdline` on Linux

`MUSE_TOKEN` does not appear in any of these.  Muse warns when a token is
passed via the CLI flag:

```
⚠️  Token passed via --token flag.
   It may appear in your shell history and process listings.
   For automation, prefer: MUSE_TOKEN=<token> muse auth login ...
```

**Scope tokens to the minimum required capabilities.**  
For read-only agents, request read-only tokens.  For write agents, request
only the specific namespaces they need (e.g. `write:midi`).

**Rotate tokens on schedule.**  
Re-run `muse auth login` with a new token to overwrite the stored entry.  The
old token is replaced atomically.

**Do not share tokens across machines.**  
Each machine should have its own token.  This allows revoking access to a
single machine without affecting others.

---

## Environment Variables

| Variable | Description |
|---|---|
| `MUSE_TOKEN` | Bearer token for `muse auth login`. Preferred over `--token`. |
| `MUSE_REPO_ROOT` | Override the repository root (used in tests and CI). |

---

*See also:*

- [`docs/reference/hub.md`](hub.md) — `muse hub connect/status/disconnect/ping`
- [`docs/reference/remotes.md`](remotes.md) — `muse push`, `muse fetch`, `muse clone`
- [`docs/reference/security.md`](security.md) — security architecture and identity store guarantees

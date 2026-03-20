# `muse hub` — MuseHub Fabric Connection Reference

The hub is not just a remote.  It is the **primary identity fabric** — the
shared coordination layer where authentication, plugin discovery, and
multi-agent state synchronisation come together.

---

## Table of Contents

1. [Hub vs. Remote](#hub-vs-remote)
2. [Commands](#commands)
   - [muse hub connect](#muse-hub-connect)
   - [muse hub status](#muse-hub-status)
   - [muse hub disconnect](#muse-hub-disconnect)
   - [muse hub ping](#muse-hub-ping)
3. [HTTPS Enforcement](#https-enforcement)
4. [Redirect Refusal](#redirect-refusal)
5. [Configuration](#configuration)
6. [Typical Setup Workflow](#typical-setup-workflow)

---

## Hub vs. Remote

Muse uses two distinct concepts for network connections:

| | `muse hub` | `muse remote` |
|---|---|---|
| **Purpose** | Primary identity fabric | Generic push/pull endpoint |
| **Cardinality** | At most one per repo | Many per repo |
| **Manages** | Authentication, discovery, coordination | Object transport |
| **Config location** | `[hub] url` in `.muse/config.toml` | `[remotes.<name>]` in `.muse/config.toml` |
| **Credentials** | Stored in `~/.muse/identity.toml` | Inherited from hub identity |
| **Commands** | `connect`, `status`, `disconnect`, `ping` | `add`, `remove`, `rename`, `list`, `get-url`, `set-url` |

A repository has **at most one hub**.  It may have many remotes.  When you
`muse push` to a remote that lives on the same MuseHub instance as the
configured hub, the hub's token is used automatically.

---

## Commands

### muse hub connect

Attach this repository to a MuseHub instance.

```
muse hub connect <URL>
```

**Arguments:**

| Argument | Description |
|---|---|
| `URL` | MuseHub URL (e.g. `https://musehub.ai` or just `musehub.ai`). |

**What connect does:**

1. Normalises the URL (see [HTTPS Enforcement](#https-enforcement)).
2. Warns if the repo is already connected to a different hub.
3. Writes `[hub] url = "<url>"` to `.muse/config.toml`.
4. If an identity already exists for this hub in `~/.muse/identity.toml`,
   shows the stored name and type.
5. If no identity exists, prompts to run `muse auth login`.

**Does not** modify `~/.muse/identity.toml` — use `muse auth login` to
authenticate after connecting.

**Examples:**

```bash
# With full HTTPS URL
muse hub connect https://musehub.ai

# Bare hostname — HTTPS added automatically
muse hub connect musehub.ai

# Switch hubs (warns before overwriting)
muse hub connect https://staging.musehub.ai
```

---

### muse hub status

Show the hub connection and identity for this repository.

```
muse hub status [--json]
```

**Options:**

| Flag | Description |
|---|---|
| `--json` | Emit JSON instead of human-readable output. |

**Human-readable output:**

```
  Hub
    URL:       https://musehub.ai
    Type:      human
    Name:      Alice
    ID:        usr_abc123
    Token:     set (Bearer ***)
    Caps:      read:* write:midi
```

**JSON output** (`--json`):

```json
{
  "hub_url": "https://musehub.ai",
  "hostname": "musehub.ai",
  "authenticated": true,
  "identity_type": "human",
  "identity_name": "Alice",
  "identity_id": "usr_abc123"
}
```

When no identity is stored, `authenticated` is `false` and no identity fields
appear.

**Agent pattern:**

```bash
muse hub status --json | jq .authenticated
```

---

### muse hub disconnect

Remove the hub association from this repository.

```
muse hub disconnect
```

Removes `[hub] url` from `.muse/config.toml`.  Credentials in
`~/.muse/identity.toml` are **preserved** — use `muse auth logout` to remove
them as well.

**Examples:**

```bash
# Remove hub association only
muse hub disconnect

# Remove hub association + credentials
muse hub disconnect
muse auth logout --hub https://musehub.ai
```

---

### muse hub ping

Test HTTP connectivity to the configured hub.

```
muse hub ping
```

Sends `GET <hub_url>/health` and reports the result.

**Exit codes:**

| Code | Meaning |
|---|---|
| `0` | Hub is reachable (2xx response) |
| Non-zero | Hub is unreachable or returned an error |

**Examples:**

```bash
muse hub ping
# Pinging musehub.ai… ✅ HTTP 200 OK

muse hub ping
# Pinging staging.musehub.ai… ❌ timed out

# Use in a healthcheck script
muse hub ping || notify-send "MuseHub unreachable"
```

---

## HTTPS Enforcement

All hub URLs are required to use HTTPS.  The `_normalise_url()` function:

- Adds `https://` when no scheme is present.
- Raises `ValueError` (shown as a user-facing error) when an explicit
  `http://` scheme is given.

```bash
# These are all equivalent
muse hub connect musehub.ai
muse hub connect https://musehub.ai
muse hub connect https://musehub.ai/

# This is rejected — bearer tokens must not travel over cleartext HTTP
muse hub connect http://musehub.ai
# ❌ Insecure URL rejected: 'http://musehub.ai'
#    MuseHub requires HTTPS. Did you mean: https://musehub.ai
```

The HTTPS enforcement is a hard requirement, not a preference.  Bearer tokens
are authentication credentials — sending them over cleartext HTTP exposes them
to any network observer between the agent and the hub.

---

## Redirect Refusal

The `ping` command uses a custom `_NoRedirectHandler` that refuses all HTTP
redirects.  When the hub returns a 3xx response:

```
❌ Redirect refused (301): hub redirected to 'https://other.host/'.
   Update the hub URL.
```

**Why this matters:**

A hub that silently redirects `GET /health` to a different host is misleading
about what was actually reached.  More importantly, allowing redirects on the
`push` and `fetch` paths would cause the `Authorization: Bearer <token>` header
to be forwarded to the redirect destination — potentially a different host
entirely.

If your MuseHub instance redirects (e.g. from `www.musehub.ai` to
`musehub.ai`), update the hub URL to the final destination:

```bash
muse hub connect musehub.ai  # not www.musehub.ai
```

---

## Configuration

Hub connection state is stored in `.muse/config.toml`:

```toml
[hub]
url = "https://musehub.ai"
```

This file is per-repository and may be committed to version control — it
contains only the hub URL, never any credentials.  Credentials live in
`~/.muse/identity.toml` (see [`auth.md`](auth.md)).

---

## Typical Setup Workflow

### New repository

```bash
# 1. Initialise
muse init --domain midi

# 2. Connect to hub
muse hub connect https://musehub.ai

# 3. Authenticate
muse auth login

# 4. Add the remote (MuseHub creates it on first push)
muse remote add origin https://musehub.ai/repos/my-song

# 5. First push
muse push origin -u
```

### Cloned repository

`muse clone` sets the `origin` remote automatically.  Connect the hub
separately if you want `muse hub status` to work:

```bash
muse clone https://musehub.ai/repos/my-song
cd my-song
muse hub connect https://musehub.ai
muse auth login  # if not already logged in on this machine
```

### CI/CD agent

```bash
# In the pipeline environment
muse hub connect https://musehub.ai
MUSE_TOKEN="$CI_MUSE_TOKEN" muse auth login \
  --agent \
  --name "ci-agent-${BUILD_NUMBER}"
muse push origin
```

---

*See also:*

- [`docs/reference/auth.md`](auth.md) — `muse auth login/whoami/logout`
- [`docs/reference/remotes.md`](remotes.md) — `muse push/fetch/clone`
- [`docs/reference/security.md`](security.md) — security architecture

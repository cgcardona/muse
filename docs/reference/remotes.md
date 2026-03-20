# Remotes — Muse Remote Sync Reference

Muse supports synchronizing repositories with a remote host (e.g. MuseHub)
using a small set of commands modelled on Git's porcelain/plumbing separation.
The CLI is a **pure client** — no server runs inside `muse`. The server lives on
MuseHub.

---

## Table of Contents

1. [Overview](#overview)
2. [Transport Architecture](#transport-architecture)
3. [MuseHub API Contract](#musehub-api-contract)
4. [PackBundle Wire Format](#packbundle-wire-format)
5. [Authentication](#authentication)
6. [Commands](#commands)
   - [muse remote](#muse-remote)
   - [muse clone](#muse-clone)
   - [muse fetch](#muse-fetch)
   - [muse pull](#muse-pull)
   - [muse push](#muse-push)
   - [muse plumbing ls-remote](#muse-plumbing-ls-remote)
7. [Tracking Branches](#tracking-branches)
8. [Token Lifecycle](#token-lifecycle)

---

## Overview

The remote sync workflow mirrors Git's model:

```
muse clone <url>          # one-time: download a full copy of a remote repo
muse fetch [remote]       # download new commits without merging
muse pull [remote]        # fetch + three-way merge into current branch
muse push [remote]        # upload local commits to the remote
muse remote add <n> <url> # manage named remotes
muse plumbing ls-remote [remote]   # list remote branches (Tier 1 plumbing)
```

Remotes are named connections to a MuseHub repository URL. The default remote
name is `origin` (set automatically by `muse clone`).

---

## Transport Architecture

```
Muse CLI process                         MuseHub server
─────────────────                        ──────────────
HttpTransport                            REST API
 └─ urllib.request (stdlib)  ────HTTPS──► GET  /refs
                                          POST /fetch
                                          POST /push
```

The `MuseTransport` Protocol in `muse/core/transport.py` is the seam between
CLI command code and the HTTP implementation. It is entirely synchronous — the
Muse CLI has no async code. The `HttpTransport` class uses Python's stdlib
`urllib.request` (HTTP/1.1 + TLS), requiring zero new dependencies.

**Why HTTP/1.1?**  
Each `muse` CLI invocation is a short-lived OS process making 2–3 requests.
HTTP/2 multiplexing benefits arise within a single long-lived connection.
Agent scale comes from MuseHub handling millions of concurrent HTTP/1.1
connections via its load balancer — not from any individual CLI process.
When MuseHub is ready to upgrade, the `MuseTransport` Protocol seam means
only `HttpTransport` changes; all CLI command code stays the same.

---

## MuseHub API Contract

MuseHub must implement these three endpoints under each repository URL:

### `GET {repo_url}/refs`

Returns the current state of the repository.

**Response (JSON):**

```json
{
  "repo_id": "<uuid>",
  "domain": "midi",
  "default_branch": "main",
  "branch_heads": {
    "main": "<commit_id>",
    "dev":  "<commit_id>"
  }
}
```

### `POST {repo_url}/fetch`

Downloads commits, snapshots, and objects the client does not have.

**Request body (JSON):**

```json
{
  "want": ["<commit_id>", ...],
  "have": ["<commit_id>", ...]
}
```

`want` is the list of remote commit IDs the client wants to receive.  
`have` is the list of commit IDs the client already has locally, allowing the
server to compute the minimal delta to send (analogous to Git's fetch
negotiation).

**Response:** JSON `PackBundle` (see below).

### `POST {repo_url}/push`

Receives commits, snapshots, and objects from the client.

**Request body (JSON):**

```json
{
  "bundle":  { ... PackBundle ... },
  "branch":  "main",
  "force":   false
}
```

When `force` is `false`, MuseHub must verify the push is a fast-forward (the
current remote HEAD is an ancestor of the pushed commit). Return HTTP 409 to
reject a non-fast-forward push.

**Response (JSON):**

```json
{
  "ok":           true,
  "message":      "ok",
  "branch_heads": { "main": "<new_commit_id>" }
}
```

**HTTP error codes:**

| Code | Meaning |
|------|---------|
| 401  | Invalid or missing bearer token |
| 404  | Repository does not exist |
| 409  | Push rejected — non-fast-forward without `force: true` |
| 5xx  | Server error |

---

## PackBundle Wire Format

A `PackBundle` is a self-contained JSON object carrying everything needed to
reconstruct a slice of commit history:

```json
{
  "commits": [
    {
      "commit_id":        "<sha256>",
      "repo_id":          "<uuid>",
      "branch":           "main",
      "snapshot_id":      "<sha256>",
      "message":          "Add verse",
      "committed_at":     "2026-03-18T12:00:00+00:00",
      "parent_commit_id": "<sha256> | null",
      "author":           "gabriel",
      ...
    }
  ],
  "snapshots": [
    {
      "snapshot_id": "<sha256>",
      "manifest":    { "tracks/drums.mid": "<sha256>", ... },
      "created_at":  "2026-03-18T12:00:00+00:00"
    }
  ],
  "objects": [
    {
      "object_id":   "<sha256>",
      "content_b64": "<base64-encoded raw bytes>"
    }
  ],
  "branch_heads": {
    "main": "<commit_id>"
  }
}
```

Objects are the raw blob bytes stored in `.muse/objects/`, base64-encoded for
JSON transport. `apply_pack()` in `muse/core/pack.py` writes objects, then
snapshots, then commits — in dependency order.

---

## Authentication

All MuseHub API calls include an `Authorization: Bearer <token>` header when a
token is configured.

Store your token in `.muse/config.toml`:

```toml
[auth]
token = "your-hub-token-here"
```

The token is read by `muse.cli.config.get_auth_token()` and is **never**
written to any log line. Add `.muse/config.toml` to `.gitignore` to prevent
accidental commit.

---

## Commands

### muse remote

Manage named remote connections. Remote state is stored entirely in
`.muse/config.toml` and `.muse/remotes/<name>/<branch>`. No network calls.

#### Subcommands

```
muse remote add <name> <url>
```
Register a new named remote.  
`<name>` — identifier (e.g. `origin`, `upstream`).  
`<url>` — full MuseHub repository URL.

```
muse remote remove <name>
```
Remove a remote and all its tracking refs under `.muse/remotes/<name>/`.

```
muse remote rename <old> <new>
```
Rename a remote in config and move its tracking refs directory.

```
muse remote list [-v]
```
List configured remotes. With `-v` / `--verbose`, shows URL and upstream
tracking branch for each remote.

```
muse remote get-url <name>
```
Print the URL of a named remote.

```
muse remote set-url <name> <url>
```
Update the URL of an existing remote.

---

### muse clone

Clone a remote Muse repository into a new local directory.

```
muse clone <url> [<directory>]
```

**Options:**

| Flag | Description |
|------|-------------|
| `--branch <b>` | Check out branch `<b>` instead of the remote default branch |

**What clone does:**

1. Calls `GET <url>/refs` to discover the remote's repo_id, domain, and branch heads.
2. Creates the target directory and initialises `.muse/` with the remote's `repo_id` and `domain`.
3. Calls `POST <url>/fetch` with `want=all, have=[]` to download the complete history.
4. Applies the `PackBundle` (objects → snapshots → commits).
5. Sets `origin` remote and upstream tracking.
6. Restores `muse-work/` from the default branch HEAD snapshot.

**Examples:**

```bash
muse clone https://hub.muse.io/repos/my-song
muse clone https://hub.muse.io/repos/my-song local-copy
muse clone https://hub.muse.io/repos/my-song --branch dev
```

---

### muse fetch

Download commits, snapshots, and objects from a remote without merging.

```
muse fetch [<remote>] [--branch <b>]
```

**Options:**

| Flag | Description |
|------|-------------|
| `<remote>` | Remote name (default: `origin`) |
| `--branch <b>` / `-b <b>` | Remote branch to fetch (default: tracked branch or current branch) |

After fetch, the remote tracking pointer `.muse/remotes/<remote>/<branch>` is
updated. The local branch HEAD is **not** changed. Use `muse merge` or
`muse pull` to integrate the fetched commits.

**Examples:**

```bash
muse fetch
muse fetch origin
muse fetch origin --branch dev
```

---

### muse pull

Fetch from a remote and merge into the current branch.

```
muse pull [<remote>] [options]
```

**Options:**

| Flag | Description |
|------|-------------|
| `<remote>` | Remote name (default: `origin`) |
| `--branch <b>` / `-b <b>` | Remote branch to pull (default: tracked branch or current branch) |
| `--no-merge` | Stop after fetch; do not merge |
| `-m <msg>` / `--message <msg>` | Override the merge commit message |

**Merge behaviour:**

- Fast-forward: if the remote HEAD is a direct descendant of local HEAD, the
  local branch ref and `muse-work/` are advanced without a merge commit.
- Three-way merge: delegates to the active domain plugin's `merge()` /
  `merge_ops()` — identical to `muse merge`.
- Conflicts: MERGE_STATE.json is written; the user fixes conflicts then runs
  `muse commit`.

**Examples:**

```bash
muse pull
muse pull origin --branch dev
muse pull origin --no-merge   # equivalent to muse fetch
```

---

### muse push

Upload local commits, snapshots, and objects to a remote.

```
muse push [<remote>] [options]
```

**Options:**

| Flag | Description |
|------|-------------|
| `<remote>` | Remote name (default: `origin`) |
| `--branch <b>` / `-b <b>` | Branch to push (default: tracked branch or current branch) |
| `-u` / `--set-upstream` | Record `<remote>/<branch>` as the upstream for this branch |
| `--force` | Force-push even if the remote has diverged |

**Fast-forward enforcement:** by default, MuseHub rejects a push if its current
HEAD is not an ancestor of the local HEAD (HTTP 409). Pass `--force` to
override. Use `muse pull` first to integrate remote changes before pushing.

**First push workflow:**

```bash
muse push origin -u     # push + record origin/main as upstream
```

Subsequent pushes on the same branch:

```bash
muse push               # infers remote and branch from upstream config
```

---

### muse plumbing ls-remote

List branch references on a remote repository. **Plumbing command** — no local
state is written.

```
muse plumbing ls-remote [<remote-or-url>] [--json]
```

**Options:**

| Flag | Description |
|------|-------------|
| `<remote-or-url>` | Named remote or a full HTTPS URL (default: `origin`) |
| `--json` | Emit structured JSON instead of tab-delimited text |

**Default output** (tab-delimited, `*` marks default branch):

```
abc123def456...   main *
789012abc345...   dev
```

**JSON output** (for agent consumption):

```json
{
  "repo_id": "<uuid>",
  "domain": "midi",
  "default_branch": "main",
  "branches": {
    "main": "<commit_id>",
    "dev":  "<commit_id>"
  }
}
```

**Examples:**

```bash
muse plumbing ls-remote
muse plumbing ls-remote upstream
muse plumbing ls-remote https://hub.muse.io/repos/r1
muse plumbing ls-remote --json origin
```

---

## Tracking Branches

Muse tracks the relationship between local branches and remote branches in two
places:

1. **Upstream config** — `.muse/config.toml`:
   ```toml
   [remotes.origin]
   url    = "https://hub.muse.io/repos/my-song"
   branch = "main"   # local branch "main" tracks origin/main
   ```
   Written by `muse push -u` or `muse clone`.

2. **Remote tracking heads** — `.muse/remotes/<name>/<branch>`:
   Plain-text files containing the last-known commit ID for each remote branch.
   Written by `muse fetch`, `muse pull`, and `muse push`.

When a tracking relationship is set, `muse push` and `muse pull` resolve the
remote and branch automatically without additional arguments.

---

## Authentication

Muse stores bearer tokens in `~/.muse/identity.toml` — a machine-scoped,
`0o600` credential file that is never part of any repository snapshot.
Credentials are **not** stored in `.muse/config.toml`.

See [`docs/reference/auth.md`](auth.md) for the complete reference:

- `muse auth login` — store a token
- `muse auth whoami` — inspect stored identity
- `muse auth logout` — remove a token
- File format, permissions model, and security properties

The bearer token is automatically picked up by `fetch`, `pull`, `push`, and
`ls-remote` via `muse.core.identity.resolve_token()`.  The token value is
**never** written to any log line — only `"Bearer ***"` appears in debug logs.

---

*See also:*  
- [`docs/reference/auth.md`](auth.md) — identity management (`muse auth`)
- [`docs/reference/hub.md`](hub.md) — hub fabric connection (`muse hub`)
- [`docs/reference/security.md`](security.md) — full security architecture
- [`docs/reference/museignore.md`](museignore.md) — domain-scoped ignore rules  
- [`docs/reference/muse-attributes.md`](muse-attributes.md) — merge strategy overrides  
- [`docs/architecture/muse-vcs.md`](../architecture/muse-vcs.md) — system architecture

# Security Architecture — Muse Trust Boundary Reference

Muse is designed to run at the scale of millions of agent calls per minute.
Every data path that crosses a trust boundary — user input, remote HTTP
responses, manifest keys from the object store, terminal output — is guarded
by an explicit validation primitive.  This document describes each guard,
where it applies, and the attack it prevents.

---

## Table of Contents

1. [Threat Model](#threat-model)
2. [Trust Boundary Design](#trust-boundary-design)
3. [Validation Module — `muse/core/validation.py`](#validation-module)
4. [Object ID & Ref ID Validation](#object-id--ref-id-validation)
5. [Branch Name & Repo ID Validation](#branch-name--repo-id-validation)
6. [Path Containment — Zip-Slip Defence](#path-containment--zip-slip-defence)
7. [Display Sanitization — ANSI Injection Defence](#display-sanitization--ansi-injection-defence)
8. [Glob Injection Prevention](#glob-injection-prevention)
9. [Numeric Guards](#numeric-guards)
10. [XML Safety — `muse/core/xml_safe.py`](#xml-safety)
11. [HTTP Transport Hardening](#http-transport-hardening)
12. [Snapshot Integrity](#snapshot-integrity)
13. [Identity Store Security](#identity-store-security)
14. [Size Caps](#size-caps)

---

## Threat Model

Muse's primary threat surface has four entry points:

| Entry point | Source of untrusted data |
|---|---|
| CLI arguments | User shell input, agent-generated commands |
| Environment variables | CI systems, compromised orchestrators |
| Remote HTTP responses | MuseHub server, MitM attacker |
| On-disk data | Tampered `.muse/` directory, crafted MIDI / MusicXML files |

At the scale of millions of agents per minute, even a low-probability
exploitation path becomes a near-certainty.  Every function that accepts
external data must validate it before use.

---

## Trust Boundary Design

Muse uses a layered trust model:

```
External world (untrusted)
        |
        | CLI args, env vars, HTTP responses, files
        v
CLI commands  ←──────────────── muse/cli/commands/
        |
        | validated, typed data only
        v
Core engine   ←──────────────── muse/core/
        |
        | content-addressed blobs
        v
Object store  ←──────────────── muse/core/object_store.py
```

**Rule:** data is validated at the point it crosses from the external world
into the CLI layer, or from the network into the core.  Internal functions
that call each other do not re-validate data they receive from trusted callers.

The validation module — `muse/core/validation.py` — sits at the absolute
bottom of the dependency graph.  It imports no other Muse module.  Every layer
may import it; it imports nothing above itself.

---

## Validation Module

**`muse/core/validation.py`** — the single source of all trust-boundary
primitives.

```
muse/core/validation.py
├── validate_object_id(s)       → str | raises ValueError
├── validate_ref_id(s)          → str | raises ValueError
├── validate_branch_name(name)  → str | raises ValueError
├── validate_repo_id(repo_id)   → str | raises ValueError
├── validate_domain_name(domain)→ str | raises ValueError
├── contain_path(base, rel)     → pathlib.Path | raises ValueError
├── sanitize_glob_prefix(prefix)→ str (never raises)
├── sanitize_display(s)         → str (never raises)
├── clamp_int(value, lo, hi)    → int | raises ValueError
└── finite_float(value, fallback)→ float (never raises)
```

The convention: functions named `validate_*` raise on bad input; functions
named `sanitize_*` strip bad bytes and always return a safe string.

---

## Object ID & Ref ID Validation

**Function:** `validate_object_id(s)` and `validate_ref_id(s)`  
**Guard:** enforces exactly 64 lowercase hexadecimal characters.  
**Attack prevented:** path traversal via crafted object or commit IDs.

### Why this matters

Object IDs are used to construct filesystem paths:

```
.muse/objects/<id[:2]>/<id[2:]>
.muse/commits/<commit_id>.json
```

A crafted ID such as `../../../etc/passwd` followed by padding would construct
a path outside `.muse/`.  Enforcing the 64-char hex format closes this class
of attack completely — no character in `[0-9a-f]{64}` can form a path
separator.

### Where applied

- `object_store.object_path()` — before constructing the shard path
- `object_store.restore_object()` — before reading a blob
- `object_store.write_object()` — verifies the provided ID is valid hex
  **and** checks that the written content hashes to the provided ID
  (content integrity, not just format integrity)
- `store.resolve_commit_ref()` — sanitizes user-supplied ref before prefix scan
- `store.store_pulled_commit()` — validates commit and snapshot IDs from remote
- `merge_engine.read_merge_state()` — validates IDs read from MERGE_STATE.json
- `merge_engine.apply_resolution()` — validates the resolution object ID

---

## Branch Name & Repo ID Validation

**Function:** `validate_branch_name(name)` and `validate_repo_id(repo_id)`  
**Guard:** rejects backslashes, null bytes, CR/LF, leading/trailing dots,
consecutive dots, consecutive slashes, leading/trailing slashes, and names
longer than 255 characters.  
**Attack prevented:** path traversal via branch names used in ref paths, null
byte injection, and log injection via CR/LF.

### Branch name rules

| Allowed | Rejected |
|---|---|
| `main`, `dev`, `feature/my-branch` | Backslash: `evil\branch` |
| Digits, hyphens, underscores | Null byte: `branch\x00name` |
| Forward slashes (namespacing) | CR or LF: `branch\rname` |
| Up to 255 characters | Leading dot: `.hidden` |
| | Trailing dot: `branch.` |
| | Consecutive dots: `branch..name` |
| | Consecutive slashes: `feat//branch` |
| | Leading or trailing slash |

### Where applied

- `cli/commands/init.py` — `--default-branch` and `--domain` arguments
- `cli/commands/commit.py` — HEAD branch detection (HEAD-poisoning guard)
- `cli/commands/branch.py` — creation and deletion targets
- `cli/commands/checkout.py` — new branch creation via `-b`
- `cli/commands/merge.py` — target branch name
- `cli/commands/reset.py` — branch before writing the ref file
- `store.get_head_commit_id()` — branch from the ref layer

---

## Path Containment — Zip-Slip Defence

**Function:** `contain_path(base: pathlib.Path, rel: str) -> pathlib.Path`  
**Guard:** joins `base / rel`, resolves symlinks, then asserts the result is
inside `base`.  
**Attack prevented:** zip-slip (path traversal via manifest keys or
user-supplied relative paths).

### The zip-slip attack

A malicious archive or snapshot manifest can contain a key like
`../../.ssh/authorized_keys`.  If the restore loop does:

```python
dest = workdir / manifest_key
dest.write_bytes(blob)
```

…then a crafted key writes outside the working directory.  `contain_path`
closes this by checking:

```python
resolved = (base / rel).resolve()
if not resolved.is_relative_to(base.resolve()):
    raise ValueError("Path traversal detected")
```

### Symlink escape

`contain_path` resolves symlinks before the containment check.  A symlink
inside `state/` that points to `/etc/passwd` would resolve to a path
outside `state/`, causing `contain_path` to raise before any data is
written.

### Where applied

- `cli/commands/checkout.py` — `_checkout_snapshot()` for every restored file
- `cli/commands/merge.py` — `_restore_from_manifest()` for every restored file
- `cli/commands/reset.py` — `--hard` reset restore loop
- `cli/commands/revert.py` — revert restore loop
- `cli/commands/cherry_pick.py` — cherry-pick restore loop
- `cli/commands/stash.py` — `stash pop` restore loop
- All 7 semantic write commands (arpeggiate, humanize, invert, quantize,
  retrograde, velocity_normalize, midi_shard) — output file paths
- `merge_engine.read_merge_state()` — conflict path list from MERGE_STATE.json
- `merge_engine.apply_resolution()` — resolution target file path

---

## Display Sanitization — ANSI Injection Defence

**Function:** `sanitize_display(s: str) -> str`  
**Guard:** strips all C0 control characters except `\t` and `\n`, plus DEL
(`\x7f`) and C1 control characters (`\x80–\x9f`).  
**Attack prevented:** ANSI/OSC terminal escape injection via commit messages,
branch names, author fields, and other user-controlled strings echoed to the
terminal.

### The attack

A commit message like:

```
Add feature\x1b]2;Hacked terminal title\x07 (harmless-looking)
```

…would, when echoed to a terminal, silently change the terminal's title bar or
execute other OSC/CSI sequences.  At millions of agent calls per minute, a
malicious agent could systematically inject escape sequences into commit
messages that other users' terminals execute.

### Characters stripped

| Code point | Name | Why stripped |
|---|---|---|
| `\x00–\x08` | C0 (NUL to BS) | Control bytes; no legitimate use in display |
| `\x0b–\x0c` | VT, FF | Not standard line breaks; terminal control |
| `\x0d` | CR | Cursor return — log injection |
| `\x0e–\x1a` | SO to SUB | Control shift codes |
| `\x1b` | ESC | ANSI escape sequence start |
| `\x1c–\x1f` | FS to US | Control separators |
| `\x7f` | DEL | Backspace-style control |
| `\x80–\x9f` | C1 | CSI (`\x9b`) and other C1 escape starters |

**Preserved:** `\t` (tab) and `\n` (newline) — legitimate in commit messages.

### Where applied

All `typer.echo()` paths that output user-controlled strings:
`log`, `tag`, `branch`, `checkout`, `merge`, `reset`, `revert`,
`cherry_pick`, `commit`, `find_phrase`, `agent_map`.

---

## Glob Injection Prevention

**Function:** `sanitize_glob_prefix(prefix: str) -> str`  
**Guard:** strips the glob metacharacters `*`, `?`, `[`, `]`, `{`, `}` from
a string before it is used in a `pathlib.Path.glob()` pattern.  
**Attack prevented:** glob injection turning a targeted prefix lookup into an
arbitrary filesystem scan.

The function `_find_commit_by_prefix()` in `store.py` constructs:

```python
list(commits_dir.glob(f"{sanitized}*.json"))
```

Without sanitization, a crafted prefix like `**/*` would enumerate the
entire directory tree rooted at `.muse/commits/`.

---

## Numeric Guards

**Function:** `clamp_int(value, lo, hi, name)` and `finite_float(value, fallback)`  
**Guard:** raises `ValueError` for out-of-range integers; returns `fallback`
for `Inf` / `-Inf` / `NaN` floats.  
**Attack prevented:** resource exhaustion via large numeric arguments; NaN
propagation causing silent computation corruption.

### Where applied

| Command | Flag | Bounds |
|---|---|---|
| `muse log` | `--max-count` | ≥ 1 |
| `muse find_phrase` | `--depth` | 1–10,000 |
| `muse agent_map` | `--depth` | 1–10,000 |
| `muse find_phrase` | `--min-score` | 0.0–1.0 |
| `muse humanize` | `--timing` | ≤ 1.0 beat |
| `muse humanize` | `--velocity` | ≤ 127 |
| `muse invert` | `--pivot` | 0–127 (MIDI note range) |
| MIDI parser | `tempo` | guard against `tempo=0` (division by zero) |
| MIDI parser | `divisions` | guard against negative or zero values |

---

## XML Safety

**Module:** `muse/core/xml_safe.py`  
**Guard:** wraps `defusedxml.ElementTree.parse()` behind a typed `SafeET`
class.  
**Attack prevented:** Billion Laughs (entity expansion DoS), XXE (external
entity credential theft), and SSRF via XML.

### The attacks

**Billion Laughs:**
A DTD-defined entity that expands to another entity, repeated exponentially.
Parsing a single small file consumes gigabytes of memory.

**XXE (XML External Entity):**
```xml
<!ENTITY xxe SYSTEM "file:///etc/passwd">
<root>&xxe;</root>
```
The parser fetches the file and embeds its contents in the parse tree.  With a
`SYSTEM "http://..."` URL, it becomes an SSRF vector.

### Why a typed wrapper

`defusedxml` does not ship type stubs.  Importing it directly requires a
`# type: ignore` comment, which the project's zero-ignore rule bans.
`xml_safe.py` contains the single justified crossing of the typed/untyped
boundary and re-exports all necessary stdlib `ElementTree` types with full
type information.

```python
# Instead of:
import xml.etree.ElementTree as ET  # unsafe — no XXE protection
ET.parse("score.xml")

# Use:
from muse.core.xml_safe import SafeET
SafeET.parse("score.xml")  # fully typed, XXE-safe
```

---

## HTTP Transport Hardening

**Module:** `muse/core/transport.py` — `HttpTransport`

### Redirect refusal

`_STRICT_OPENER` is a `urllib.request.OpenerDirector` built with a custom
`_NoRedirectHandler` that raises on any HTTP redirect.  This prevents:

- **Authorization header leakage** — a redirect to a different host would
  carry the `Authorization: Bearer <token>` header to the attacker's server.
- **Scheme downgrade** — a redirect from `https://` to `http://` would
  expose the bearer token over cleartext.

### HTTPS enforcement

`_build_request()` uses `urllib.parse.urlparse(url).scheme` to check for
HTTPS.  A URL that uses any other scheme raises before a connection is
attempted.

### Response size cap

`_execute()` reads at most `MAX_RESPONSE_BYTES` (64 MB) from any HTTP
response.  If a `Content-Length` header declares a larger body, the request is
rejected before reading begins.  This prevents OOM attacks via an unbounded
response body.

### Content-Type guard

`_assert_json_content(raw, endpoint)` checks that the first non-whitespace
byte of a response body is `{` or `[` before calling `json.loads()`.  This
catches HTML error pages (proxy intercept pages, Cloudflare challenges) that
would otherwise produce a misleading `JSONDecodeError`.

---

## Local File Transport Hardening

**Module:** `muse/core/transport.py` — `LocalFileTransport`

`LocalFileTransport` handles `file://` URLs — direct filesystem reads and
writes between two Muse repositories on the same host (or a shared network
mount).  Because all I/O is local, the threat surface shifts from network
attacks to filesystem attacks.

### Symlink canonicalisation

`_repo_root()` calls `pathlib.Path.resolve()` on the path extracted from the
URL before any filesystem operation.  `resolve()` dereferences all symlinks
and normalises `..` path components.

**Attack prevented:** a crafted `file://` URL or a pre-placed symlink at the
URL target that points to a sensitive directory (one without `.muse/`) is
rejected because the containment check is made on the *canonical* resolved
path, not the symlink itself.

### Branch name validation

`push_pack()` calls `validate_branch_name(branch)` before any I/O.  This
rejects:

| Input | Why rejected |
|---|---|
| `../evil` | Leading `..` traversal |
| `foo\x00bar` | Null byte injection |
| `branch\revil` | CR log injection |
| `main\\escape` | Backslash path separator |
| `foo..bar` | Consecutive dots |
| `""` (empty) | Cannot form a valid ref path |

### Ref path containment

Even after `validate_branch_name` passes, the branch name is joined onto the
`.muse/refs/heads/` base path and validated with `contain_path()`.

`contain_path()` resolves symlinks on the *result* path and asserts it is
relative to the base directory.  This provides defence-in-depth against:

- **Pre-placed symlinks** — an attacker who can write to `.muse/refs/heads/`
  before a push could place a symlink named after a legitimate branch that
  points outside the directory.  `contain_path()` resolves that symlink and
  rejects the write.
- **Future branch-name edge cases** — any branch name that somehow passes
  `validate_branch_name` but resolves outside `refs/heads/` is still caught.

### Where applied

| Guard | Function | Attack prevented |
|---|---|---|
| `resolve()` | `_repo_root()` | Symlink traversal on URL path |
| `validate_branch_name()` | `push_pack()` | Branch-as-path injection |
| `contain_path()` | `push_pack()` | Pre-placed symlink in refs/heads/ |

---

## Snapshot Integrity

**Module:** `muse/core/snapshot.py`

### Null-byte separators in hash computation

`compute_snapshot_id()` and `compute_commit_id()` hash a canonical
representation of the manifest.  The separator between key and value is the
null byte (`\x00`) rather than a printable character like `|` or `:`.

**Why this matters:** if the separator is `:`, then a file named `a:b` with
object ID `c` and a file named `a` with object ID `b:c` produce the same hash
input.  The null byte cannot appear in filenames on POSIX or Windows, making
collisions structurally impossible.

### Symlink and hidden-file exclusion

`walk_workdir()` skips:
- **Symlinks** — following symlinks during snapshot could include files
  outside the working directory, leaking content.
- **Hidden files and directories** (names starting with `.`) — `.muse/` must
  never be snapshotted; other dotfiles (`.env`, `.git`) are excluded to prevent
  accidental credential capture.

---

## Identity Store Security

**Module:** `muse/core/identity.py`

The identity store (`~/.muse/identity.toml`) holds bearer tokens.  Several
layered controls protect it:

| Control | Implementation | Threat prevented |
|---|---|---|
| **0o700 directory** | `os.chmod(~/.muse/, 0o700)` | Other local users cannot list or traverse the directory |
| **0o600 from byte zero** | `os.open()` + `os.fchmod()` before writing | Eliminates the TOCTOU window that `write_text()` + `chmod()` creates |
| **Atomic rename** | Temp file + `os.replace()` | A crash or kill signal during write leaves the old file intact — never a partial file |
| **Symlink guard** | Check `path.is_symlink()` before write | Blocks pre-placed symlink attacks targeting a different credential file |
| **Exclusive write lock** | `fcntl.flock(LOCK_EX)` on `.identity.lock` | Prevents race conditions when parallel agents write simultaneously |
| **Token masking** | All log calls use `"Bearer ***"` | Tokens never appear in log output |
| **URL normalisation** | `_hostname_from_url()` strips scheme, userinfo, path | `https://admin:secret@musehub.ai/repos/x` and `musehub.ai` resolve to the same key |

---

## Size Caps

| Constant | Value | Where enforced |
|---|---|---|
| `MAX_FILE_BYTES` | 256 MB | `object_store.read_object()` — cap per-blob reads |
| `MAX_RESPONSE_BYTES` | 64 MB | `transport._execute()` — cap HTTP response body |
| `MAX_SYSEX_BYTES` | 64 KiB | `midi_merge._msg_to_dict()` — cap SysEx data per message |
| MIDI file size | `MAX_FILE_BYTES` | `midi_parser.parse_file()` — cap file size before parse |

---

*See also:*

- [`docs/reference/auth.md`](auth.md) — identity lifecycle (`muse auth`)
- [`docs/reference/hub.md`](hub.md) — hub connection management (`muse hub`)
- [`docs/reference/remotes.md`](remotes.md) — push, fetch, clone transport
- [`muse/core/validation.py`](../../muse/core/validation.py) — implementation
- [`tests/test_core_validation.py`](../../tests/test_core_validation.py) — test suite

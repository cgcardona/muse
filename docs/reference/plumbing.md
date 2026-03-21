# Muse Plumbing Commands Reference

Plumbing commands are the low-level, machine-readable layer of the Muse CLI.
They output JSON, stream bytes without size limits, use predictable exit codes,
and compose cleanly in shell pipelines and agent scripts.

If you want to automate Muse — write a script, build an agent workflow, or
integrate Muse into another tool — plumbing commands are the right entry point.
The higher-level porcelain commands (`muse commit`, `muse merge`, etc.) call
these internally.

---

## The Plumbing Contract

Every plumbing command follows the same rules:

| Property | Guarantee |
|---|---|
| **Output** | JSON to `stdout`, errors to `stderr` |
| **Exit 0** | Success — output is valid and complete |
| **Exit 1** | User error — bad input, ref not found, invalid ID |
| **Exit 3** | Internal error — I/O failure, integrity check failed |
| **Idempotent reads** | Reading commands never modify state |
| **Idempotent writes** | Writing the same object twice is a no-op |
| **Encoding** | All text I/O is UTF-8 |
| **Object IDs** | Always 64 lowercase hex characters (SHA-256) |
| **Short flags** | Every flag has a `-x` short form |

JSON output is always printed to `stdout`.  When an error occurs, the message
goes to `stderr`; some commands also write a machine-readable `{"error": "..."}` 
object to `stdout` so scripts that parse `stdout` can detect the failure
without inspecting exit codes.

---

## Command Reference

### `hash-object` — compute a content ID

```
muse plumbing hash-object <file> [-w] [-f json|text]
```

Computes the SHA-256 content address of a file.  Identical bytes always produce
the same ID; this is how Muse deduplicates storage.  With `--write` (`-w`) the
object is also stored in `.muse/objects/` so it can be referenced by future
snapshots and commits.  The file is streamed at 64 KiB at a time — arbitrarily
large blobs never spike memory.

**Flags**

| Flag | Short | Default | Description |
|---|---|---|---|
| `--write` | `-w` | off | Store the object after hashing |
| `--format` | `-f` | `json` | Output format: `json` or `text` |

**Output — JSON (default)**

```json
{"object_id": "a3f2...c8d1", "stored": false}
```

`stored` is `true` only when `--write` is passed and the object was not already
in the store.

**Output — `--format text`**

```
a3f2...c8d1
```

**Exit codes:** 0 success · 1 path not found, is a directory, or bad `--format` · 3 I/O write error or integrity check failed

---

### `cat-object` — read a stored object

```
muse plumbing cat-object <object-id> [-f raw|info]
```

Reads a content-addressed object from `.muse/objects/`.  With `--format raw`
(the default) the raw bytes are streamed to `stdout` at 64 KiB at a time —
pipe to a file, another process, or a network socket without any size ceiling.
With `--format info` a JSON summary is printed instead of the content.

**Flags**

| Flag | Short | Default | Description |
|---|---|---|---|
| `--format` | `-f` | `raw` | `raw` (stream bytes) or `info` (JSON metadata) |

**Output — `--format info`**

```json
{"object_id": "a3f2...c8d1", "present": true, "size_bytes": 4096}
```

When the object is absent and `--format info` is used, `present` is `false`
and `size_bytes` is `0` (exit 1).  When `--format raw` is used and the object
is absent, the error goes to `stderr` (exit 1).

**Exit codes:** 0 found · 1 not found or invalid ID format · 3 I/O read error

---

### `rev-parse` — resolve a ref to a commit ID

```
muse plumbing rev-parse <ref> [-f json|text]
```

Resolves a branch name, `HEAD`, or an abbreviated SHA prefix to the full
64-character commit ID.  Use this to canonicalise any ref before passing it to
other commands.

**Arguments**

| Argument | Description |
|---|---|
| `<ref>` | Branch name, `HEAD`, full commit ID, or unique prefix |

**Flags**

| Flag | Short | Default | Description |
|---|---|---|---|
| `--format` | `-f` | `json` | `json` or `text` |

**Output — JSON**

```json
{"ref": "main", "commit_id": "a3f2...c8d1"}
```

Ambiguous prefixes return an error object with a `candidates` list (exit 1).

**Output — `--format text`**

```
a3f2...c8d1
```

**Exit codes:** 0 resolved · 1 not found, ambiguous, or bad `--format`

---

### `ls-files` — list files in a snapshot

```
muse plumbing ls-files [--commit <id>] [-f json|text]
```

Lists every file tracked in a commit's snapshot together with its content
object ID.  Defaults to the HEAD commit of the current branch.

**Flags**

| Flag | Short | Default | Description |
|---|---|---|---|
| `--commit` | `-c` | HEAD | Commit ID to inspect |
| `--format` | `-f` | `json` | `json` or `text` |

**Output — JSON**

```json
{
  "commit_id": "a3f2...c8d1",
  "snapshot_id": "b7e4...f912",
  "file_count": 3,
  "files": [
    {"path": "tracks/bass.mid",  "object_id": "c1d2...a3b4"},
    {"path": "tracks/drums.mid", "object_id": "e5f6...b7c8"},
    {"path": "tracks/piano.mid", "object_id": "09ab...cd10"}
  ]
}
```

Files are sorted by path.

**Output — `--format text`** (tab-separated, suitable for `awk` / `cut`)

```
c1d2...a3b4	tracks/bass.mid
e5f6...b7c8	tracks/drums.mid
09ab...cd10	tracks/piano.mid
```

**Exit codes:** 0 listed · 1 commit or snapshot not found, or bad `--format`

---

### `read-commit` — print full commit metadata

```
muse plumbing read-commit <commit-id>
```

Emits the complete JSON record for a commit.  Accepts a full 64-character ID
or a unique prefix.  The schema is stable across Muse versions; use
`format_version` to detect any future schema changes.

**Output**

```json
{
  "format_version": 5,
  "commit_id": "a3f2...c8d1",
  "repo_id": "550e8400-e29b-41d4-a716-446655440000",
  "branch": "main",
  "snapshot_id": "b7e4...f912",
  "message": "Add verse melody",
  "committed_at": "2026-03-18T12:00:00+00:00",
  "parent_commit_id": "ff01...23ab",
  "parent2_commit_id": null,
  "author": "gabriel",
  "agent_id": "",
  "model_id": "",
  "toolchain_id": "",
  "prompt_hash": "",
  "signature": "",
  "signer_key_id": "",
  "sem_ver_bump": "none",
  "breaking_changes": [],
  "reviewed_by": [],
  "test_runs": 0,
  "metadata": {}
}
```

Error conditions also produce JSON on `stdout` so scripts can parse them
without inspecting `stderr`.

**Exit codes:** 0 found · 1 not found, ambiguous prefix, or invalid ID format

---

### `read-snapshot` — print full snapshot metadata

```
muse plumbing read-snapshot <snapshot-id>
```

Emits the complete JSON record for a snapshot.  Every commit references exactly
one snapshot.  Use `ls-files --commit <id>` if you want to look up a snapshot
from a commit ID rather than the snapshot ID directly.

**Output**

```json
{
  "snapshot_id": "b7e4...f912",
  "created_at": "2026-03-18T12:00:00+00:00",
  "file_count": 3,
  "manifest": {
    "tracks/bass.mid":  "c1d2...a3b4",
    "tracks/drums.mid": "e5f6...b7c8",
    "tracks/piano.mid": "09ab...cd10"
  }
}
```

**Exit codes:** 0 found · 1 not found or invalid ID format

---

### `commit-tree` — create a commit from a snapshot ID

```
muse plumbing commit-tree -s <snapshot-id> [-p <parent-id>]... [-m <message>] [-a <author>] [-b <branch>]
```

Low-level commit creation.  The snapshot must already exist in the store.  Use
`--parent` / `-p` once for a linear commit and twice for a merge commit.  The
commit is written to `.muse/commits/` but **no branch ref is updated** — use
`update-ref` to advance a branch to the new commit.

**Flags**

| Flag | Short | Required | Description |
|---|---|---|---|
| `--snapshot` | `-s` | ✅ | SHA-256 snapshot ID |
| `--parent` | `-p` | — | Parent commit ID (repeat for merges) |
| `--message` | `-m` | — | Commit message |
| `--author` | `-a` | — | Author name |
| `--branch` | `-b` | — | Branch name (default: current branch) |

**Output**

```json
{"commit_id": "a3f2...c8d1"}
```

**Exit codes:** 0 commit written · 1 snapshot or parent not found, or `repo.json` unreadable · 3 write failure

---

### `update-ref` — move a branch to a commit

```
muse plumbing update-ref <branch> <commit-id> [--no-verify]
muse plumbing update-ref <branch> --delete
```

Directly writes (or deletes) a branch reference file under `.muse/refs/heads/`.
By default, the commit must already exist in the local store (`--verify` is
on); pass `--no-verify` to write the ref before the commit is stored — useful
after an `unpack-objects` pipeline where objects arrive in dependency order.

The commit ID format is always validated regardless of `--no-verify`, so a
malformed ID can never corrupt the ref file.

**Flags**

| Flag | Short | Default | Description |
|---|---|---|---|
| `--delete` | `-d` | off | Delete the branch ref instead of updating it |
| `--verify/--no-verify` | — | `--verify` | Require commit to exist in store |

**Output — update**

```json
{"branch": "main", "commit_id": "a3f2...c8d1", "previous": "ff01...23ab"}
```

`previous` is `null` when the branch had no prior commit.

**Output — delete**

```json
{"branch": "feat/x", "deleted": true}
```

**Exit codes:** 0 done · 1 commit not in store (with `--verify`), invalid ID, or `--delete` on non-existent ref · 3 file write failure

---

### `commit-graph` — emit the commit DAG

```
muse plumbing commit-graph [--tip <id>] [--stop-at <id>] [-n <max>] [-f json|text]
```

Performs a BFS walk from a tip commit (defaulting to HEAD), following both
`parent_commit_id` and `parent2_commit_id` pointers.  Returns every reachable
commit as a JSON array.  Useful for building visualisations, computing
reachability sets, or finding the commits on a branch since it diverged from
another.

**Flags**

| Flag | Short | Default | Description |
|---|---|---|---|
| `--tip` | — | HEAD | Commit to start from |
| `--stop-at` | — | — | Stop BFS at this commit (exclusive) |
| `--max` | `-n` | 10 000 | Maximum commits to traverse |
| `--format` | `-f` | `json` | `json` or `text` (one ID per line) |

**Output — JSON**

```json
{
  "tip": "a3f2...c8d1",
  "count": 42,
  "truncated": false,
  "commits": [
    {
      "commit_id":        "a3f2...c8d1",
      "parent_commit_id": "ff01...23ab",
      "parent2_commit_id": null,
      "message":          "Add verse melody",
      "branch":           "main",
      "committed_at":     "2026-03-18T12:00:00+00:00",
      "snapshot_id":      "b7e4...f912",
      "author":           "gabriel"
    }
  ]
}
```

`truncated` is `true` when the graph was cut off by `--max`.  Use `--stop-at`
to compute the commits on a feature branch since it diverged from `main`:

```sh
BASE=$(muse plumbing rev-parse main -f text)
muse plumbing commit-graph --stop-at "$BASE" -f text
```

**Exit codes:** 0 graph emitted · 1 tip commit not found or bad `--format`

---

### `pack-objects` — bundle commits for transport

```
muse plumbing pack-objects <want>... [--have <id>...]
```

Collects a set of commits — and all their referenced snapshots and objects —
into a single JSON `PackBundle` written to `stdout`.  Pass `--have` to tell
the packer which commits the receiver already has; objects reachable only from
`--have` ancestors are excluded, minimising transfer size.

`<want>` may be a full commit ID or `HEAD`.

**Flags**

| Flag | Short | Description |
|---|---|---|
| `--have` | — | Commits the receiver already has (repeat for multiple) |

**Output** — a JSON `PackBundle` object (pipe to a file or `unpack-objects`)

```json
{
  "commits":      [...],
  "snapshots":    [...],
  "objects":      [{"object_id": "...", "data_b64": "..."}],
  "branch_heads": {"main": "a3f2...c8d1"}
}
```

`objects` entries are base64-encoded so the bundle is safe for any JSON-capable
transport (HTTP body, agent message, file).

**Exit codes:** 0 pack written · 1 a wanted commit not found or HEAD has no commits · 3 I/O error reading from the local store

---

### `unpack-objects` — apply a bundle to the local store

```
cat pack.json | muse plumbing unpack-objects
muse plumbing pack-objects HEAD | muse plumbing unpack-objects
```

Reads a `PackBundle` JSON document from `stdin` and writes its commits,
snapshots, and objects into `.muse/`.  Idempotent: objects already present in
the store are silently skipped.  Partial packs from interrupted transfers are
safe to re-apply.

**Output**

```json
{
  "commits_written":   12,
  "snapshots_written": 12,
  "objects_written":   47,
  "objects_skipped":    3
}
```

**Exit codes:** 0 unpacked (all objects stored) · 1 invalid JSON from stdin · 3 write failure

---

### `ls-remote` — list refs on a remote

```
muse plumbing ls-remote [<remote-or-url>] [-j]
```

Contacts a remote and lists every branch HEAD without altering local state.
The `<remote-or-url>` argument is either a remote name configured with
`muse remote add` (defaults to `origin`) or a full `https://` URL.

**Flags**

| Flag | Short | Default | Description |
|---|---|---|---|
| `--json` | `-j` | off | Emit structured JSON instead of tab-separated text |

**Output — text (default)**

One line per branch, tab-separated.  The default branch is marked with ` *`.

```
a3f2...c8d1	main *
b7e4...f912	feat/experiment
```

**Output — `--json`**

```json
{
  "repo_id":        "550e8400-e29b-41d4-a716-446655440000",
  "domain":         "midi",
  "default_branch": "main",
  "branches": {
    "main":             "a3f2...c8d1",
    "feat/experiment":  "b7e4...f912"
  }
}
```

**Exit codes:** 0 remote contacted · 1 remote not configured or URL invalid · 3 transport error (network, HTTP error)

---

## Composability Patterns

### Export a history range

```sh
# All commits on feat/x that are not on main
BASE=$(muse plumbing rev-parse main -f text)
TIP=$(muse plumbing rev-parse feat/x -f text)
muse plumbing commit-graph --tip "$TIP" --stop-at "$BASE" -f text
```

### Ship commits between two machines

```sh
# On the sender — pack everything the receiver doesn't have
HAVE=$(muse plumbing ls-remote origin -f text | awk '{print "--have " $1}' | tr '\n' ' ')
muse plumbing pack-objects HEAD $HAVE > bundle.json

# On the receiver
cat bundle.json | muse plumbing unpack-objects
muse plumbing update-ref main <commit-id>
```

### Verify a stored object

```sh
ID=$(muse plumbing hash-object tracks/drums.mid -f text)
muse plumbing cat-object "$ID" -f info
```

### Inspect what changed in the last commit

```sh
muse plumbing read-commit $(muse plumbing rev-parse HEAD -f text) | \
  python3 -c "import sys, json; d=json.load(sys.stdin); print(d['message'])"
```

### Script a bare commit (advanced)

```sh
# 1. Hash and store the files
OID=$(muse plumbing hash-object -w tracks/drums.mid -f text)

# 2. Build a snapshot manifest and write it (via muse commit is easier,
#    but for full control use commit-tree after writing the snapshot)
SNAP=$(muse plumbing rev-parse HEAD -f text | \
  xargs -I{} muse plumbing read-commit {} | \
  python3 -c "import sys,json; print(json.load(sys.stdin)['snapshot_id'])")

# 3. Create a commit on top of HEAD
PARENT=$(muse plumbing rev-parse HEAD -f text)
NEW=$(muse plumbing commit-tree -s "$SNAP" -p "$PARENT" -m "scripted commit" | \
  python3 -c "import sys,json; print(json.load(sys.stdin)['commit_id'])")

# 4. Advance the branch
muse plumbing update-ref main "$NEW"
```

---

## Object ID Quick Reference

All IDs in Muse are 64-character lowercase hex SHA-256 digests.  There are
three kinds:

| Kind | Computed from | Used by |
|---|---|---|
| **Object ID** | File bytes | `hash-object`, `cat-object`, snapshot manifests |
| **Snapshot ID** | Sorted `path:object_id` pairs | `read-snapshot`, `commit-tree` |
| **Commit ID** | Parent IDs + snapshot ID + message + timestamp | `read-commit`, `rev-parse`, `update-ref` |

Every ID is deterministic and content-addressed.  The same input always
produces the same ID; two different inputs never produce the same ID in
practice.

---

## Exit Code Summary

| Code | Constant | Meaning |
|---|---|---|
| 0 | `SUCCESS` | Command completed successfully |
| 1 | `USER_ERROR` | Bad input, ref not found, invalid format |
| 3 | `INTERNAL_ERROR` | I/O failure, integrity check, transport error |

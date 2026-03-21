# Muse Plumbing Commands Reference

Plumbing commands are the low-level, machine-readable layer of the Muse CLI.
They output JSON, stream bytes without size limits, use predictable exit codes,
and compose cleanly in shell pipelines and agent scripts.

If you want to automate Muse — write a script, build an agent workflow, or
integrate Muse into another tool — plumbing commands are the right entry point.
The higher-level porcelain commands (`muse commit`, `muse merge`, etc.) call
these internally.

---

## Quick Index

| Command | Purpose |
|---|---|
| [`hash-object`](#hash-object) | Compute SHA-256 of a file; optionally store it |
| [`cat-object`](#cat-object) | Stream raw bytes or metadata for a stored object |
| [`verify-object`](#verify-object) | Re-hash stored objects to detect corruption |
| [`rev-parse`](#rev-parse) | Resolve branch name / HEAD / prefix → full commit ID |
| [`read-commit`](#read-commit) | Print full commit JSON record |
| [`read-snapshot`](#read-snapshot) | Print full snapshot JSON record |
| [`ls-files`](#ls-files) | List tracked files and their object IDs |
| [`commit-tree`](#commit-tree) | Create a commit from an existing snapshot |
| [`update-ref`](#update-ref) | Move or delete a branch ref |
| [`commit-graph`](#commit-graph) | BFS walk of the commit DAG |
| [`merge-base`](#merge-base) | Find the lowest common ancestor of two commits |
| [`snapshot-diff`](#snapshot-diff) | Diff two snapshots: added / modified / deleted |
| [`pack-objects`](#pack-objects) | Bundle commits, snapshots, and objects into a PackBundle |
| [`unpack-objects`](#unpack-objects) | Apply a PackBundle to the local store |
| [`verify-pack`](#verify-pack) | Three-tier integrity check for a PackBundle |
| [`show-ref`](#show-ref) | List all branch refs and their commit IDs |
| [`symbolic-ref`](#symbolic-ref) | Read or write the HEAD symbolic reference |
| [`for-each-ref`](#for-each-ref) | Iterate refs with full commit metadata; sort and filter |
| [`name-rev`](#name-rev) | Map commit IDs to `<branch>~N` names |
| [`check-ref-format`](#check-ref-format) | Validate branch/ref names against naming rules |
| [`check-ignore`](#check-ignore) | Test whether paths match `.museignore` rules |
| [`check-attr`](#check-attr) | Query `.museattributes` for merge strategies |
| [`domain-info`](#domain-info) | Inspect the active domain plugin and its schema |
| [`ls-remote`](#ls-remote) | List refs on a remote without changing local state |

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

<a id="hash-object"></a>
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

<a id="cat-object"></a>
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

<a id="rev-parse"></a>
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

<a id="ls-files"></a>
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

<a id="read-commit"></a>
### `read-commit` — print full commit metadata

```
muse plumbing read-commit <commit-id> [-f json|text]
```

Emits the complete JSON record for a commit.  Accepts a full 64-character ID
or a unique prefix.  The schema is stable across Muse versions; use
`format_version` to detect any future schema changes.

**Flags**

| Flag | Short | Default | Description |
|---|---|---|---|
| `--format` | `-f` | `json` | `json` (full record) or `text` (compact one-liner) |

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

**Output — `--format text`**

```
a3f2...c8d1  main  gabriel  2026-03-21T12:00:00+00:00  Add verse melody
```

Error conditions always produce JSON on `stdout` so scripts can parse them
without inspecting `stderr`.

**Exit codes:** 0 found · 1 not found, ambiguous prefix, invalid ID format, or bad `--format`

---

<a id="read-snapshot"></a>
### `read-snapshot` — print full snapshot metadata

```
muse plumbing read-snapshot <snapshot-id> [-f json|text]
```

Emits the complete JSON record for a snapshot.  Every commit references exactly
one snapshot.  Use `ls-files --commit <id>` if you want to look up a snapshot
from a commit ID rather than the snapshot ID directly.

**Flags**

| Flag | Short | Default | Description |
|---|---|---|---|
| `--format` | `-f` | `json` | `json` (full manifest) or `text` (compact one-liner) |

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

**Output — `--format text`**

```
b7e4...f912  3 files  2026-03-21T12:00:00+00:00
```

**Exit codes:** 0 found · 1 not found, invalid ID format, or bad `--format`

---

<a id="commit-tree"></a>
### `commit-tree` — create a commit from a snapshot ID

```
muse plumbing commit-tree -s <snapshot-id> [-p <parent-id>]... [-m <message>] [-a <author>] [-b <branch>] [-f json|text]
```

Low-level commit creation.  The snapshot must already exist in the store.  Both
the snapshot ID and any parent IDs are validated as proper 64-character SHA-256
hex strings before any I/O is attempted.  Use `--parent` / `-p` once for a
linear commit and twice for a merge commit.  The commit is written to
`.muse/commits/` but **no branch ref is updated** — use `update-ref` to advance
a branch to the new commit.

**Flags**

| Flag | Short | Required | Description |
|---|---|---|---|
| `--snapshot` | `-s` | ✅ | SHA-256 snapshot ID |
| `--parent` | `-p` | — | Parent commit ID (repeat for merges) |
| `--message` | `-m` | — | Commit message |
| `--author` | `-a` | — | Author name |
| `--branch` | `-b` | — | Branch name (default: current branch) |
| `--format` | `-f` | `json` | `json` or `text` (bare commit ID) |

**Output — JSON (default)**

```json
{"commit_id": "a3f2...c8d1"}
```

**Output — `--format text`**

```
a3f2...c8d1
```

The text form is ideal for shell pipelines where you want to capture the ID
directly without a `jq` call: `NEW=$(muse plumbing commit-tree -s "$SNAP" -f text)`

**Exit codes:** 0 commit written · 1 snapshot or parent not found, invalid ID format, or `repo.json` unreadable · 3 write failure

---

<a id="update-ref"></a>
### `update-ref` — move a branch to a commit

```
muse plumbing update-ref <branch> <commit-id> [--no-verify] [-f json|text]
muse plumbing update-ref <branch> --delete [-f json|text]
```

Directly writes (or deletes) a branch reference file under `.muse/refs/heads/`.
The branch name is validated with the same rules as `check-ref-format` before
any file is written — path-traversal via crafted branch names is not possible.
The commit ID format is always validated regardless of `--no-verify`, so a
malformed ID can never corrupt the ref file.

By default, the commit must already exist in the local store (`--verify` is on);
pass `--no-verify` to write the ref before the commit is stored — useful after
an `unpack-objects` pipeline where objects arrive in dependency order.

**Flags**

| Flag | Short | Default | Description |
|---|---|---|---|
| `--delete` | `-d` | off | Delete the branch ref instead of updating it |
| `--verify/--no-verify` | — | `--verify` | Require commit to exist in store |
| `--format` | `-f` | `json` | `json` or `text` (silent on success — exits 0) |

**Output — JSON (default), update**

```json
{"branch": "main", "commit_id": "a3f2...c8d1", "previous": "ff01...23ab"}
```

`previous` is `null` when the branch had no prior commit.

**Output — JSON, delete**

```json
{"branch": "feat/x", "deleted": true}
```

**Output — `--format text`**

Silent on success (exit 0).  Mirrors the behaviour of `git update-ref`, making
it drop-in compatible with shell scripts that use exit code only.

**Exit codes:** 0 done · 1 commit not in store (with `--verify`), invalid branch or commit ID, or `--delete` on non-existent ref · 3 file write failure

---

<a id="commit-graph"></a>
### `commit-graph` — emit the commit DAG

```
muse plumbing commit-graph [--tip <id>] [--stop-at <id>] [-n <max>] [-c] [-1] [-a] [-f json|text]
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
| `--count` | `-c` | off | Emit only the integer count, not the full node list |
| `--first-parent` | `-1` | off | Follow only first-parent links — linear history, no merge parents |
| `--ancestry-path` | `-a` | off | With `--stop-at`: restrict to commits on the direct path between tip and stop-at (capped at 100 000 visited commits to guard against unbounded BFS) |
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

`truncated` is `true` when the graph was cut off by `--max`.

**Output — `--count`**

```json
{"tip": "a3f2...c8d1", "count": 42}
```

`--count` suppresses the `commits` array entirely, making it suitable for fast
cardinality checks without loading commit metadata.

**Examples**

Commits on a feature branch since it diverged from `main`:

```sh
BASE=$(muse plumbing merge-base feat/x main -f text)
muse plumbing commit-graph --tip feat/x --stop-at "$BASE" -f text
```

Count commits in a feature branch:

```sh
muse plumbing commit-graph \
  --tip $(muse plumbing rev-parse feat/x -f text) \
  --stop-at $(muse plumbing merge-base feat/x dev -f text) \
  --count
```

Linear history only (skip merge parents):

```sh
muse plumbing commit-graph --first-parent -f text
```

**Exit codes:** 0 graph emitted · 1 tip commit not found, `--ancestry-path` without `--stop-at`, or bad `--format`

---

<a id="pack-objects"></a>
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
  "objects":      [{"object_id": "...", "content_b64": "..."}],
  "branch_heads": {"main": "a3f2...c8d1"}
}
```

`objects` entries are base64-encoded so the bundle is safe for any JSON-capable
transport (HTTP body, agent message, file).

**Exit codes:** 0 pack written · 1 a wanted commit not found or HEAD has no commits · 3 I/O error reading from the local store

---

<a id="unpack-objects"></a>
### `unpack-objects` — apply a bundle to the local store

```
cat pack.json | muse plumbing unpack-objects [-f json|text]
muse plumbing pack-objects HEAD | muse plumbing unpack-objects
```

Reads a `PackBundle` JSON document from `stdin` and writes its commits,
snapshots, and objects into `.muse/`.  Idempotent: objects already present in
the store are silently skipped.  Partial packs from interrupted transfers are
safe to re-apply.

**Flags**

| Flag | Short | Default | Description |
|---|---|---|---|
| `--format` | `-f` | `json` | `json` (machine-readable counts) or `text` (human summary) |

**Output — JSON (default)**

```json
{
  "commits_written":   12,
  "snapshots_written": 12,
  "objects_written":   47,
  "objects_skipped":    3
}
```

**Output — `--format text`**

```
Wrote 12 commits, 12 snapshots, 47 objects (3 skipped).
```

**Exit codes:** 0 unpacked (all objects stored) · 1 invalid JSON from stdin or bad `--format` · 3 write failure

---

<a id="ls-remote"></a>
### `ls-remote` — list refs on a remote

```
muse plumbing ls-remote [<remote-or-url>] [-f json|text]
```

Contacts a remote and lists every branch HEAD without altering local state.
The `<remote-or-url>` argument is either a remote name configured with
`muse remote add` (defaults to `origin`) or a full `https://` URL.

**Flags**

| Flag | Short | Default | Description |
|---|---|---|---|
| `--format` | `-f` | `text` | `text` (tab-separated) or `json` (structured) |

**Output — `--format text` (default)**

One line per branch, tab-separated.  The default branch is marked with ` *`.

```
a3f2...c8d1	main *
b7e4...f912	feat/experiment
```

**Output — `--format json`**

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

**Exit codes:** 0 remote contacted · 1 remote not configured, URL invalid, or bad `--format` · 3 transport error (network, HTTP error)

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
HAVE=$(muse plumbing ls-remote origin --format text | awk '{print "--have " $1}' | tr '\n' ' ')
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

<a id="merge-base"></a>
### `merge-base` — find the common ancestor of two commits

Find the lowest common ancestor of two commits — the point at which two
branches diverged.

```sh
muse plumbing merge-base <commit-a> <commit-b> [-f json|text]
```

| Flag | Short | Default | Description |
|---|---|---|---|
| `--format` | `-f` | `json` | Output format: `json` or `text` |

Arguments accept full SHA-256 commit IDs, branch names, or `HEAD`.

**JSON output:**

```json
{
  "commit_a":   "<sha256>",
  "commit_b":   "<sha256>",
  "merge_base": "<sha256>"
}
```

When no common ancestor exists, `merge_base` is `null` and `error` is set.

| Exit | Meaning |
|---|---|
| 0 | Result computed (check `merge_base` for null vs. found) |
| 1 | Ref cannot be resolved; bad `--format` |
| 3 | DAG walk failed |

---

<a id="snapshot-diff"></a>
### `snapshot-diff` — diff two snapshot manifests

Compare two snapshots and categorise every changed path as added, modified,
or deleted.

```sh
muse plumbing snapshot-diff <ref-a> <ref-b> [-f json|text] [-s]
```

| Flag | Short | Default | Description |
|---|---|---|---|
| `--format` | `-f` | `json` | Output format: `json` or `text` |
| `--stat` | `-s` | false | Append a summary line in text mode |

Arguments accept snapshot IDs, commit IDs, branch names, or `HEAD`.

**JSON output:**

```json
{
  "snapshot_a":    "<sha256>",
  "snapshot_b":    "<sha256>",
  "added":         [{"path": "new.mid",  "object_id": "<sha256>"}],
  "modified":      [{"path": "main.mid", "object_id_a": "<sha256>", "object_id_b": "<sha256>"}],
  "deleted":       [{"path": "old.mid",  "object_id": "<sha256>"}],
  "total_changes": 3
}
```

**Text output:**

```
A  new.mid
M  main.mid
D  old.mid
```

| Exit | Meaning |
|---|---|
| 0 | Diff computed (zero changes is a valid result) |
| 1 | Ref cannot be resolved; bad `--format` |
| 3 | I/O error reading snapshot records |

---

<a id="domain-info"></a>
### `domain-info` — inspect the active domain plugin

Inspect the domain plugin active for this repository — its name, class,
optional protocol capabilities, and full structural schema.

```sh
muse plumbing domain-info [-f json|text] [-a]
```

| Flag | Short | Default | Description |
|---|---|---|---|
| `--format` | `-f` | `json` | Output format: `json` or `text` |
| `--all-domains` | `-a` | false | List every registered domain; no repo required |

**JSON output:**

```json
{
  "domain":       "midi",
  "plugin_class": "MidiPlugin",
  "capabilities": {
    "structured_merge": true,
    "crdt":             false,
    "rerere":           false
  },
  "schema": {
    "domain": "midi", "merge_mode": "three_way",
    "dimensions": [...], "top_level": {...}
  },
  "registered_domains": ["bitcoin", "code", "midi", "scaffold"]
}
```

| Exit | Meaning |
|---|---|
| 0 | Domain resolved and schema emitted |
| 1 | Domain not registered; bad `--format` |
| 3 | Plugin raised an error computing its schema |

---

<a id="show-ref"></a>
### `show-ref` — list all branch refs

List all branch refs and the commit IDs they point to.

```sh
muse plumbing show-ref [-f json|text] [-p PATTERN] [-H] [-v REF]
```

| Flag | Short | Default | Description |
|---|---|---|---|
| `--format` | `-f` | `json` | Output format: `json` or `text` |
| `--pattern` | `-p` | `""` | fnmatch glob to filter ref names |
| `--head` | `-H` | false | Print only HEAD ref and commit ID |
| `--verify` | `-v` | `""` | Silent existence check — exit 0 if found, 1 if not |

**JSON output:**

```json
{
  "refs": [
    {"ref": "refs/heads/dev",  "commit_id": "<sha256>"},
    {"ref": "refs/heads/main", "commit_id": "<sha256>"}
  ],
  "head":  {"ref": "refs/heads/main", "branch": "main", "commit_id": "<sha256>"},
  "count": 2
}
```

Use `--verify` in shell conditionals:

```sh
muse plumbing show-ref --verify refs/heads/my-branch && echo "branch exists"
```

| Exit | Meaning |
|---|---|
| 0 | Refs enumerated (or `--verify` ref exists) |
| 1 | `--verify` ref absent; bad `--format` |
| 3 | I/O error reading refs directory |

---

<a id="check-ignore"></a>
### `check-ignore` — test whether paths are excluded by `.museignore`

Test whether workspace paths are excluded by `.museignore` rules.

```sh
muse plumbing check-ignore <path>... [-f json|text] [-q] [-V]
```

| Flag | Short | Default | Description |
|---|---|---|---|
| `--format` | `-f` | `json` | Output format: `json` or `text` |
| `--quiet` | `-q` | false | No output; exit 0 if all ignored, 1 otherwise |
| `--verbose` | `-V` | false | Include matching pattern in text output |

**JSON output:**

```json
{
  "domain":          "midi",
  "patterns_loaded": 4,
  "results": [
    {"path": "build/out.bin", "ignored": true,  "matching_pattern": "build/"},
    {"path": "tracks/dr.mid", "ignored": false, "matching_pattern": null}
  ]
}
```

Last-match-wins: a negation rule (`!important.mid`) can un-ignore a path
matched by an earlier rule.

| Exit | Meaning |
|---|---|
| 0 | Results emitted (or `--quiet` with all ignored) |
| 1 | `--quiet` with any non-ignored path; missing args |
| 3 | TOML parse error in `.museignore` |

---

<a id="check-attr"></a>
### `check-attr` — query merge-strategy attributes for paths

Query merge-strategy attributes for workspace paths from `.museattributes`.

```sh
muse plumbing check-attr <path>... [-f json|text] [-d DIMENSION] [-A]
```

| Flag | Short | Default | Description |
|---|---|---|---|
| `--format` | `-f` | `json` | Output format: `json` or `text` |
| `--dimension` | `-d` | `*` | Domain axis to query (e.g. `notes`, `tempo`) |
| `--all-rules` | `-A` | false | Return every matching rule, not just first-match |

**JSON output (default: first-match):**

```json
{
  "domain":       "midi",
  "rules_loaded": 3,
  "dimension":    "*",
  "results": [
    {
      "path":      "drums/kit.mid",
      "dimension": "*",
      "strategy":  "ours",
      "rule": {"path_pattern": "drums/*", "strategy": "ours", "priority": 10, ...}
    }
  ]
}
```

When no rule matches, `strategy` is `"auto"` and `rule` is `null`.

| Exit | Meaning |
|---|---|
| 0 | Attributes resolved and emitted |
| 1 | Missing args; bad `--format` |
| 3 | TOML parse error in `.museattributes` |

---

<a id="verify-object"></a>
### `verify-object` — re-hash stored objects to detect corruption

Re-hash stored objects to detect silent data corruption.

```sh
muse plumbing verify-object <object-id>... [-f json|text] [-q]
```

| Flag | Short | Default | Description |
|---|---|---|---|
| `--format` | `-f` | `json` | Output format: `json` or `text` |
| `--quiet` | `-q` | false | No output; exit 0 if all OK, 1 otherwise |

Objects are streamed in 64 KiB chunks — safe for very large blobs.

**JSON output:**

```json
{
  "results": [
    {"object_id": "<sha256>", "ok": true,  "size_bytes": 4096, "error": null},
    {"object_id": "<sha256>", "ok": false, "size_bytes": null,
     "error": "object not found in store"}
  ],
  "all_ok":  false,
  "checked": 2,
  "failed":  1
}
```

Compose with `show-ref` to verify every commit in a repo:

```sh
muse plumbing show-ref -f json \
  | jq -r '.refs[].commit_id' \
  | xargs muse plumbing verify-object
```

| Exit | Meaning |
|---|---|
| 0 | All objects verified successfully |
| 1 | One or more objects failed; object not found; bad args |
| 3 | Unexpected I/O error (disk read failure) |

---

<a id="symbolic-ref"></a>
### `symbolic-ref` — read or write HEAD's symbolic reference

In Muse, HEAD is always a symbolic reference — it always points to a branch,
never directly to a commit.  `symbolic-ref` reads which branch HEAD tracks or,
with `--set`, points HEAD at a different branch.

```sh
# Read mode
muse plumbing symbolic-ref HEAD [-f json|text] [--short]

# Write mode
muse plumbing symbolic-ref HEAD --set <branch> [-f json|text]
```

| Flag | Short | Default | Description |
|---|---|---|---|
| `--set` | `-s` | `""` | Branch name to point HEAD at |
| `--short` | `-S` | false | Emit branch name only (not the full `refs/heads/…` path) |
| `--format` | `-f` | `json` | Output format: `json` or `text` |

**JSON output (read mode):**

```json
{
  "ref":             "HEAD",
  "symbolic_target": "refs/heads/main",
  "branch":          "main",
  "commit_id":       "<sha256>"
}
```

`commit_id` is `null` when the branch has no commits yet.

**Text output:** `refs/heads/main` (or just `main` with `--short`)

| Exit | Meaning |
|---|---|
| 0 | Ref read or written |
| 1 | `--set` target branch does not exist; bad `--format` |
| 3 | I/O error reading or writing HEAD |

---

<a id="for-each-ref"></a>
### `for-each-ref` — iterate all refs with rich commit metadata

Enumerates every branch ref together with the full commit metadata it points to.
Supports sorting by any commit field and glob-pattern filtering, making it
ideal for agent pipelines that need to slice the ref list without post-processing.

```sh
muse plumbing for-each-ref [-p <pattern>] [-s <field>] [-d] [-n <count>] [-f json|text]
```

| Flag | Short | Default | Description |
|---|---|---|---|
| `--pattern` | `-p` | `""` | fnmatch glob on the full ref name, e.g. `refs/heads/feat/*` |
| `--sort` | `-s` | `ref` | Sort field: `ref`, `branch`, `commit_id`, `author`, `committed_at`, `message` |
| `--desc` | `-d` | false | Reverse sort order (descending) |
| `--count` | `-n` | `0` | Limit to first N refs after sorting (0 = unlimited) |
| `--format` | `-f` | `json` | Output format: `json` or `text` |

**JSON output:**

```json
{
  "refs": [
    {
      "ref":          "refs/heads/dev",
      "branch":       "dev",
      "commit_id":    "<sha256>",
      "author":       "gabriel",
      "message":      "Add verse melody",
      "committed_at": "2026-01-01T00:00:00+00:00",
      "snapshot_id":  "<sha256>"
    }
  ],
  "count": 1
}
```

**Text output:** `<commit_id>  <ref>  <committed_at>  <author>`

**Example — three most recently committed branches:**

```sh
muse plumbing for-each-ref --sort committed_at --desc --count 3
```

| Exit | Meaning |
|---|---|
| 0 | Refs emitted (list may be empty) |
| 1 | Bad `--sort` field; bad `--format` |
| 3 | I/O error reading refs or commit records |

---

<a id="name-rev"></a>
### `name-rev` — map commit IDs to branch-relative names

For each supplied commit ID, performs a single multi-source BFS from all branch
tips and reports the closest branch and hop distance.  Results are expressed as
`<branch>~N` — where N is the number of parent hops from the tip.  When N is 0
(the commit is the exact branch tip) the name is the bare branch name with no
`~0` suffix.

```sh
muse plumbing name-rev <commit-id>... [-n] [-u <string>] [-f json|text]
```

| Flag | Short | Default | Description |
|---|---|---|---|
| `--name-only` | `-n` | false | Emit only the name (or the undefined string), not the commit ID |
| `--undefined` | `-u` | `"undefined"` | String to emit for unreachable commits |
| `--format` | `-f` | `json` | Output format: `json` or `text` |

**JSON output:**

```json
{
  "results": [
    {
      "commit_id": "<sha256>",
      "name":      "main~3",
      "branch":    "main",
      "distance":  3,
      "undefined": false
    },
    {
      "commit_id": "<sha256>",
      "name":      null,
      "branch":    null,
      "distance":  null,
      "undefined": true
    }
  ]
}
```

**Text output:** `<sha256>  main~3` (or `main~3` with `--name-only`)

**Performance:** A single O(total-commits) BFS from all branch tips simultaneously.
Every commit is visited at most once regardless of how many input IDs are supplied.

| Exit | Meaning |
|---|---|
| 0 | All results computed (some may be `undefined`) |
| 1 | Bad `--format`; no commit IDs supplied |
| 3 | I/O error reading commit records |

---

<a id="check-ref-format"></a>
### `check-ref-format` — validate branch and ref names

Tests one or more names against Muse's branch-naming rules — the same
validation used by `muse branch` and `muse plumbing update-ref`.  Use in
scripts to pre-validate names before attempting to create a branch.

```sh
muse plumbing check-ref-format <name>... [-q] [-f json|text]
```

| Flag | Short | Default | Description |
|---|---|---|---|
| `--quiet` | `-q` | false | No output — exit 0 if all valid, exit 1 otherwise |
| `--format` | `-f` | `json` | Output format: `json` or `text` |

**Rules enforced:** 1–255 chars; no backslash, null bytes, CR, LF, or tab;
no leading/trailing dot; no consecutive dots (`..`); no leading/trailing or
consecutive slashes.

**JSON output:**

```json
{
  "results": [
    {"name": "feat/my-branch", "valid": true,  "error": null},
    {"name": "bad..name",      "valid": false, "error": "..."}
  ],
  "all_valid": false
}
```

**Text output:**
```
ok    feat/my-branch
FAIL  bad..name  →  Branch name 'bad..name' contains forbidden characters
```

**Shell conditional:**

```sh
muse plumbing check-ref-format -q "$BRANCH" && git checkout -b "$BRANCH"
```

| Exit | Meaning |
|---|---|
| 0 | All names are valid |
| 1 | One or more names are invalid; no names supplied |

---

<a id="verify-pack"></a>
### `verify-pack` — verify PackBundle integrity

Reads a PackBundle JSON from stdin or `--file` and performs three-tier integrity
checking:

1. **Object integrity** — every object payload is base64-decoded and its SHA-256
   is recomputed.  The digest must match the declared `object_id`.
2. **Snapshot consistency** — every snapshot's manifest entries reference objects
   present in the bundle or already in the local store.
3. **Commit consistency** — every commit's `snapshot_id` is present in the bundle
   or already in the local store.

```sh
muse plumbing pack-objects main | muse plumbing verify-pack
muse plumbing verify-pack --file bundle.json
```

| Flag | Short | Default | Description |
|---|---|---|---|
| `--file` | `-i` | `""` | Path to bundle file (reads stdin when omitted) |
| `--quiet` | `-q` | false | No output — exit 0 if clean, exit 1 on any failure |
| `--no-local` | `-L` | false | Skip local store checks (verify bundle in isolation) |
| `--format` | `-f` | `json` | Output format: `json` or `text` |

**JSON output:**

```json
{
  "objects_checked":   42,
  "snapshots_checked": 5,
  "commits_checked":   5,
  "all_ok":            true,
  "failures":          []
}
```

**With failures:**

```json
{
  "all_ok": false,
  "failures": [
    {"kind": "object",   "id": "<sha256>", "error": "hash mismatch"},
    {"kind": "snapshot", "id": "<sha256>", "error": "missing object: ..."}
  ]
}
```

**Validate before upload:**

```sh
muse plumbing pack-objects main | muse plumbing verify-pack -q \
  && echo "bundle is clean — safe to push"
```

| Exit | Meaning |
|---|---|
| 0 | Bundle is fully intact |
| 1 | One or more integrity failures; malformed JSON; bad args |
| 3 | I/O error reading stdin or the bundle file |

---

---

## Composability Patterns — Advanced

### Name every commit reachable from a branch

```sh
# Get all commit IDs on feat/x since it diverged from dev
BASE=$(muse plumbing merge-base feat/x dev -f text)
muse plumbing commit-graph --tip feat/x --stop-at "$BASE" -f text \
  | xargs muse plumbing name-rev --name-only
```

### Audit all refs with full metadata and filter by recency

```sh
# List all branches modified in 2026, sorted newest-first
muse plumbing for-each-ref --sort committed_at --desc \
  | jq '.refs[] | select(.committed_at | startswith("2026"))'
```

### Validate a branch name before creating it

```sh
BRANCH="feat/my-feature"
muse plumbing check-ref-format -q "$BRANCH" \
  && echo "Name is valid — safe to branch" \
  || echo "Invalid branch name"
```

### Verify a bundle before shipping

```sh
muse plumbing pack-objects main | tee bundle.json | muse plumbing verify-pack -q \
  && echo "bundle is clean — safe to push" \
  || echo "bundle has integrity failures — do not push"
```

### Switch active branch via plumbing

```sh
# Check where HEAD is now
muse plumbing symbolic-ref HEAD -f text        # → refs/heads/main
# Redirect HEAD to dev
muse plumbing symbolic-ref HEAD --set dev
muse plumbing rev-parse HEAD -f text           # → tip of dev
```

### Find stale branches (no commits in the last 30 days)

```sh
# Requires `date` and `jq`
CUTOFF=$(date -u -v-30d +%Y-%m-%dT%H:%M:%SZ 2>/dev/null \
         || date -u --date="30 days ago" +%Y-%m-%dT%H:%M:%SZ)
muse plumbing for-each-ref -f json \
  | jq --arg c "$CUTOFF" '.refs[] | select(.committed_at < $c) | .branch'
```

### Check which files changed between two branches

```sh
BASE=$(muse plumbing merge-base main feat/x -f text)
muse plumbing snapshot-diff "$BASE" feat/x --format text --stat
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

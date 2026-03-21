# Muse Porcelain Commands

> **Layer guide:** Muse commands are organised into three tiers.
> This document covers **Tier 2 — Core Porcelain**: the high-level,
> human-friendly commands that build on the Tier 1 plumbing layer.
> Tier 3 commands (MIDI, Bitcoin, Code) live in their own reference docs.

---

## Quick Index

| Command | Description |
|---------|-------------|
| [`init`](#init) | Initialise a new Muse repository |
| [`commit`](#commit) | Record the working tree as a new version |
| [`status`](#status) | Show working-tree drift against HEAD |
| [`log`](#log) | Display commit history |
| [`diff`](#diff) | Compare working tree or two commits |
| [`show`](#show) | Inspect a commit — metadata, diff, files |
| [`branch`](#branch) | List, create, or delete branches |
| [`checkout`](#checkout) | Switch branches or restore a snapshot |
| [`merge`](#merge) | Three-way merge a branch into the current branch |
| [`rebase`](#rebase) | Replay commits onto a new base |
| [`reset`](#reset) | Move HEAD to a prior commit |
| [`revert`](#revert) | Undo a commit by creating a new one |
| [`cherry-pick`](#cherry-pick) | Apply a single commit's changes |
| [`stash`](#stash) | Shelve and restore uncommitted changes |
| [`tag`](#tag) | Attach and query semantic tags on commits |
| [`blame`](#blame) | Line-level attribution for any text file |
| [`reflog`](#reflog) | History of HEAD and branch-ref movements |
| [`rerere`](#rerere) | Reuse recorded conflict resolutions |
| [`gc`](#gc) | Garbage-collect unreachable objects |
| [`archive`](#archive) | Export a snapshot as tar.gz or zip |
| [`bisect`](#bisect) | Binary-search through history for a regression |
| [`worktree`](#worktree) | Multiple simultaneous branch checkouts |
| [`clean`](#clean) | Remove untracked files from the working tree |
| [`describe`](#describe) | Label a commit by its nearest tag |
| [`shortlog`](#shortlog) | Commit summary grouped by author or agent |
| [`verify`](#verify) | Whole-repository integrity check |
| [`snapshot`](#snapshot) | Explicit snapshot management |
| [`bundle`](#bundle) | Pack and unpack commits for offline transfer |
| [`content-grep`](#content-grep) | Full-text search across tracked file content |
| [`whoami`](#whoami) | Show the current identity |

---

## Established Core Porcelain

### `init` — initialise a repository

Create a fresh `.muse/` directory in the current folder.

```
muse init                  # initialise in current dir
muse init --domain midi    # set the active domain
muse init -d code          # short flag
```

**Exit codes:** `0` success · `1` already initialised

---

### `commit` — record the working tree

Snapshot the working tree and write a commit pointing to it.

```
muse commit -m "verse melody"
muse commit --message "Add chorus"
muse commit --allow-empty
```

**Exit codes:** `0` committed · `1` nothing to commit

---

### `status` — show drift against HEAD

```
muse status
muse status --short
muse status --json        # machine-readable
muse status -s -j
```

**Output:**
- `modified`: files changed since HEAD
- `added`: new files not in HEAD
- `deleted`: files present in HEAD but not on disk
- `clean` when working tree matches HEAD

**Exit codes:** `0` always (non-zero drift is shown, not signalled)

---

### `log` — display commit history

```
muse log
muse log --limit 20
muse log --branch feat/audio
muse log --format json
```

---

### `diff` — compare working tree or two commits

```
muse diff                       # working tree vs HEAD
muse diff --from HEAD~3
muse diff --from v1.0 --to v2.0
muse diff --format json
```

---

### `show` — inspect a commit

```
muse show HEAD
muse show abc123
muse show --format json
```

---

### `branch` — list, create, or delete branches

```
muse branch                      # list all
muse branch feat/reverb          # create
muse branch --delete feat/reverb
muse branch -d feat/reverb       # short flag
```

---

### `checkout` — switch branches or restore snapshot

```
muse checkout main
muse checkout feat/guitar
muse checkout --create feat/new-idea    # create and switch
muse checkout -c feat/new-idea          # short flag
```

---

### `merge` — three-way merge

```
muse merge feat/audio
muse merge --message "Merge audio feature"
muse merge --abort
muse merge --continue
```

**Conflict flow:**
1. `muse merge <branch>` → conflict reported, writes `MERGE_STATE.json`
2. Resolve files manually
3. `muse merge --continue` → commit the merge
4. Or `muse merge --abort` → restore original HEAD

---

### `reset` — move HEAD to a prior commit

```
muse reset HEAD~1          # move back one commit
muse reset abc123          # move to specific commit
muse reset --hard          # also reset working tree
```

---

### `revert` — undo a commit by creating a new one

Non-destructive: the original commit remains in history.

```
muse revert HEAD
muse revert abc123
muse revert --message "Undo broken change"
```

---

### `cherry-pick` — apply a single commit's changes

```
muse cherry-pick abc123
muse cherry-pick abc123 --message "Cherry: verse fix"
```

---

### `stash` — shelve and restore changes

```
muse stash push -m "WIP: bridge section"
muse stash list
muse stash pop
muse stash drop 0
```

---

### `tag` — semantic tags on commits

```
muse tag v1.0.0
muse tag v1.0.0 --commit abc123
muse tag list
muse tag delete v0.9.0
```

---

### `blame` — line-level attribution

```
muse blame song.mid
muse blame --format json song.mid
```

---

### `reflog` — HEAD and branch movement history

```
muse reflog
muse reflog --branch feat/audio
muse reflog --limit 50
muse reflog --format json
```

The reflog is the "undo safety net" — every ref movement is recorded so
you can recover from accidental resets, force-pushes, or botched rebases.

---

### `rerere` — reuse recorded resolutions

```
muse rerere list             # show cached resolutions
muse rerere apply            # auto-apply cached fixes to current conflicts
muse rerere forget abc123    # remove a cached resolution
muse rerere status           # show which conflicts have cached resolutions
```

**How it works:** After a successful merge, Muse records the resolution in
`.muse/rerere/`. On future conflicts with the same "conflict fingerprint",
`rerere apply` replays the resolution automatically.

---

### `gc` — garbage collect

```
muse gc                    # remove unreachable objects
muse gc --dry-run          # preview what would be removed
muse gc -n                 # short flag for --dry-run
```

---

### `archive` — export a snapshot

```
muse archive HEAD
muse archive HEAD --format zip --output release.zip
muse archive v1.0.0 --prefix project/
```

**Formats:** `tar.gz` (default), `zip`

---

### `bisect` — binary-search for a regression

```
muse bisect start
muse bisect good v1.0.0
muse bisect bad HEAD
muse bisect run pytest tests/
muse bisect reset
```

Muse bisect works on any domain — not just code. Use it to find which
commit introduced a melody change, a tuning drift, or a data regression.

---

### `worktree` — multiple simultaneous checkouts

```
muse worktree add /path/to/dir feat/audio
muse worktree list
muse worktree remove feat/audio
muse worktree prune
```

---

## New Gap-Fill Porcelain (v2.x)

The following commands were added to close the gap between Muse's
feature set and Git's porcelain layer, plus Muse-specific additions
that exploit domain-agnostic version control.

---

### `rebase` — replay commits onto a new base

Muse rebase replays a sequence of commits onto a new base using the same
three-way merge engine as `muse merge`.  Because commits are content-addressed,
each replayed commit gets a **new ID** — the originals are untouched in the store.

```
muse rebase main                          # replay current branch onto main
muse rebase --onto newbase upstream       # replay onto a specific base
muse rebase --squash main                 # collapse all commits into one
muse rebase --squash -m "feat: all in"   # squash with custom message
muse rebase --abort                       # restore original HEAD
muse rebase --continue                    # resume after conflict resolution
```

**Flags:**

| Flag | Short | Description |
|------|-------|-------------|
| `--onto <ref>` | `-o` | New base commit |
| `--squash` | `-s` | Collapse all commits into one |
| `--message <msg>` | `-m` | Message for squash commit |
| `--abort` | `-a` | Abort and restore original HEAD |
| `--continue` | `-c` | Resume after resolving a conflict |

**Conflict flow:**
1. `muse rebase main` → conflict reported, writes `REBASE_STATE.json` and `MERGE_STATE.json`
2. Resolve files manually
3. `muse rebase --continue` → commit the resolved state and continue
4. Or `muse rebase --abort` → restore the original branch pointer

**State file:** `.muse/REBASE_STATE.json` — tracks remaining/completed commits
and the `onto` base. Cleared automatically on successful completion or `--abort`.

**Exit codes:** `0` clean · `1` conflict or bad arguments

---

### `clean` — remove untracked files

Scans the working tree against the HEAD snapshot and removes files not tracked
in any commit.  `--force` is required to actually delete files (safety guard).

```
muse clean -n               # dry-run: show what would be removed
muse clean -f               # delete untracked files
muse clean -f -d            # also delete empty directories
muse clean -f -x            # also delete .museignore-excluded files
muse clean -f -d -x         # everything untracked + ignored + empty dirs
```

**Flags:**

| Flag | Short | Description |
|------|-------|-------------|
| `--dry-run` | `-n` | Preview without deleting |
| `--force` | `-f` | Required to actually delete |
| `--directories` | `-d` | Remove empty untracked directories |
| `--include-ignored` | `-x` | Also remove .museignore-excluded files |

**Exit codes:** `0` clean or cleaned · `1` untracked exist but `--force` not given

---

### `describe` — label by nearest tag

Walks backward from a commit and finds the nearest tag.  Returns `<tag>~N`
where N is the hop count.  N=0 gives the bare tag name.

```
muse describe                       # → v1.0.0~3
muse describe --ref feat/audio      # describe the tip of a branch
muse describe --long                # → v1.0.0-3-gabc123456789
muse describe --require-tag         # exit 1 if no tags exist
muse describe --format json         # machine-readable
```

**Flags:**

| Flag | Short | Description |
|------|-------|-------------|
| `--ref <ref>` | `-r` | Branch or commit to describe |
| `--long` | `-l` | Always show `<tag>-<dist>-g<sha>` |
| `--require-tag` | `-t` | Fail if no tag found |
| `--format <fmt>` | `-f` | `text` or `json` |

**JSON output schema:**
```json
{
  "commit_id": "string (full SHA-256)",
  "tag": "string | null",
  "distance": 0,
  "short_sha": "string (12 chars)",
  "name": "string (e.g. v1.0.0~3)"
}
```

**Exit codes:** `0` description produced · `1` ref not found or `--require-tag` with no tags

---

### `shortlog` — commit summary by author or agent

Groups commits by `author` or `agent_id` and prints a count + message list.
Especially expressive in Muse because both human and agent contributions are
tracked with full metadata.

```
muse shortlog                      # current branch
muse shortlog --all                # all branches
muse shortlog --numbered           # sort by commit count (most active first)
muse shortlog --email              # include agent_id alongside author name
muse shortlog --limit 100          # cap commit walk at 100
muse shortlog --format json        # JSON for agent consumption
```

**Flags:**

| Flag | Short | Description |
|------|-------|-------------|
| `--branch <br>` | `-b` | Branch to summarise |
| `--all` | `-a` | Summarise all branches |
| `--numbered` | `-n` | Sort by commit count |
| `--email` | `-e` | Include agent_id |
| `--limit <N>` | `-l` | Max commits to walk |
| `--format <fmt>` | `-f` | `text` or `json` |

**JSON output schema:**
```json
[
  {
    "author": "string",
    "count": 12,
    "commits": [
      { "commit_id": "...", "message": "...", "committed_at": "..." }
    ]
  }
]
```

**Exit codes:** `0` always

---

### `verify` — whole-repository integrity check

Walks every reachable commit from every branch ref and performs a three-tier check:

1. Every branch ref points to an existing commit.
2. Every commit's snapshot exists.
3. Every object referenced by every snapshot exists, and (unless `--no-objects`)
   its SHA-256 is recomputed to detect silent data corruption.

This is Muse's equivalent of `git fsck`.

```
muse verify                   # full integrity check (re-hashes all objects)
muse verify --no-objects      # existence-only check (faster)
muse verify --quiet           # exit code only — no output
muse verify -q && echo "healthy"
muse verify --format json | jq '.failures'
```

**Flags:**

| Flag | Short | Description |
|------|-------|-------------|
| `--quiet` | `-q` | No output; exit 0 = clean, 1 = failure |
| `--no-objects` | `-O` | Skip SHA-256 re-hashing |
| `--format <fmt>` | `-f` | `text` or `json` |

**JSON output schema:**
```json
{
  "refs_checked": 3,
  "commits_checked": 42,
  "snapshots_checked": 42,
  "objects_checked": 210,
  "all_ok": true,
  "failures": [
    {
      "kind": "object",
      "id": "abc123...",
      "error": "hash mismatch — data corruption detected"
    }
  ]
}
```

**Failure kinds:** `ref` · `commit` · `snapshot` · `object`

**Exit codes:** `0` all checks passed · `1` one or more failures

---

### `snapshot` — explicit snapshot management

A snapshot is Muse's fundamental unit of state: an immutable, content-addressed
record mapping workspace paths to their SHA-256 object IDs.

`muse snapshot` exposes snapshots as a first-class operation — capture, list,
show, and export them independently of commits.  Useful for mid-work checkpoints
in agent pipelines.

#### `snapshot create`

```
muse snapshot create
muse snapshot create -m "WIP: before refactor"
muse snapshot create --format json    # prints snapshot_id
```

**JSON output:**
```json
{
  "snapshot_id": "string",
  "file_count": 42,
  "note": "string",
  "created_at": "ISO8601"
}
```

#### `snapshot list`

```
muse snapshot list
muse snapshot list --limit 5
muse snapshot list --format json
```

#### `snapshot show`

```
muse snapshot show <snapshot_id>
muse snapshot show abc123            # prefix lookup
muse snapshot show abc123 --format text
```

#### `snapshot export`

```
muse snapshot export <snapshot_id>
muse snapshot export abc123 --format zip --output release.zip
muse snapshot export abc123 --prefix project/
```

**Archive formats:** `tar.gz` (default) · `zip`

**Flags (export):**

| Flag | Short | Description |
|------|-------|-------------|
| `--format <fmt>` | `-f` | `tar.gz` or `zip` |
| `--output <path>` | `-o` | Output file path |
| `--prefix <str>` | | Directory prefix inside archive |

**Exit codes:** `0` success · `1` snapshot not found

---

### `bundle` — offline commit transfer

A bundle is a self-contained JSON file carrying commits, snapshots, and objects.
Copy it over SSH, USB, or email — no network connection required.

The bundle format is identical to the plumbing `PackBundle` JSON and is
human-inspectable.

#### `bundle create`

```
muse bundle create out.bundle              # bundle from HEAD
muse bundle create out.bundle feat/audio  # bundle a specific branch
muse bundle create out.bundle HEAD --have old-sha  # delta bundle
```

#### `bundle unbundle`

```
muse bundle unbundle repo.bundle            # apply and update branch refs
muse bundle unbundle repo.bundle --no-update-refs  # objects only
```

#### `bundle verify`

```
muse bundle verify repo.bundle
muse bundle verify repo.bundle --quiet
muse bundle verify repo.bundle --format json
```

#### `bundle list-heads`

```
muse bundle list-heads repo.bundle
muse bundle list-heads repo.bundle --format json
```

**Bundle value-add over plumbing:** `unbundle` updates local branch refs from
the bundle's `branch_heads` map, so the receiver's repo reflects the sender's
branch state automatically.

**Exit codes:** `0` success · `1` file not found, corrupt, or bad args

---

### `content-grep` — full-text search across tracked files

Searches every file in the HEAD snapshot for a pattern.  Files are read from
the content-addressed object store.  Binary files and non-UTF-8 files are
silently skipped.

Muse-specific: the search target is the **immutable object store** — you're
searching a specific point in history, not the working tree.

```
muse content-grep --pattern "Cm7"
muse content-grep --pattern "TODO|FIXME" --files-only
muse content-grep --pattern "verse" --ignore-case
muse content-grep --pattern "tempo" --format json
muse content-grep --pattern "chord" --ref feat/harmony
muse content-grep --pattern "hit" --count
```

**Flags:**

| Flag | Short | Description |
|------|-------|-------------|
| `--pattern <regex>` | `-p` | Python regex to search for |
| `--ref <ref>` | `-r` | Branch or commit to search |
| `--ignore-case` | `-i` | Case-insensitive matching |
| `--files-only` | `-l` | Print only matching file paths |
| `--count` | `-c` | Print match count per file |
| `--format <fmt>` | `-f` | `text` or `json` |

**JSON output schema:**
```json
[
  {
    "path": "song.txt",
    "object_id": "abc123...",
    "match_count": 3,
    "matches": [
      { "line_number": 4, "text": "chord: Cm7" }
    ]
  }
]
```

**Exit codes:** `0` at least one match · `1` no matches

---

### `whoami` — show the current identity

A shortcut for `muse auth whoami`.

```
muse whoami
muse whoami --json          # JSON output for agent consumers
muse whoami --all           # show identities for all configured hubs
```

**Flags:**

| Flag | Short | Description |
|------|-------|-------------|
| `--json` | `-j` | JSON output |
| `--all` | `-a` | Show all hub identities |

**Text output:**
```
  hub:    app.musehub.ai
  type:   agent
  name:   mozart-agent-v2
  id:     usr_abc123
  token:  set
```

**JSON output:**
```json
{
  "hub": "app.musehub.ai",
  "type": "agent",
  "name": "mozart-agent-v2",
  "id": "usr_abc123",
  "token_set": "true"
}
```

**Exit codes:** `0` identity found · `1` no identity stored (not authenticated)

---

## Domain Auth & Config

### `auth` — identity management

```
muse auth login --hub app.musehub.ai
muse auth login --agent --agent-id mozart-v2 --model gpt-4.5
muse auth whoami
muse auth logout
```

### `config` — repository configuration

```
muse config show
muse config get core.author
muse config set core.author "Gabriel"
```

### `hub` — MuseHub connection

```
muse hub connect https://app.musehub.ai
muse hub status
muse hub disconnect
```

---

## Composability Patterns

Muse porcelain commands are designed to compose cleanly in pipelines.

**Check integrity before every push:**
```bash
muse verify --quiet || { echo "repo corrupt!"; exit 1; }
muse push
```

**Offline collaboration via bundle:**
```bash
# Sender:
muse bundle create session.bundle
scp session.bundle colleague:/tmp/

# Receiver:
muse bundle verify /tmp/session.bundle --quiet
muse bundle unbundle /tmp/session.bundle
```

**Generate a release label in CI:**
```bash
VERSION=$(muse describe --format json | jq -r .name)
echo "Building $VERSION..."
muse snapshot export HEAD --output "${VERSION}.tar.gz"
```

**Find which commits touched a melody line:**
```bash
muse content-grep --pattern "tempo: 120" --format json | jq '.[].path'
```

**Agent activity summary:**
```bash
muse shortlog --all --numbered --email --format json \
  | jq '.[] | select(.author | test("agent")) | {agent: .author, count: .count}'
```

**Checkpoint before a risky refactor:**
```bash
SNAP=$(muse snapshot create -m "pre-refactor" --format json | jq -r .snapshot_id)
# ... do the work ...
muse snapshot show "$SNAP" --format json | jq '.manifest | keys'  # check what was there
```

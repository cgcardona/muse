# Muse CLI — Three-Tier Architecture Reference

Muse CLI is organized into three formally separated tiers.  Each tier has a
distinct contract, audience, and stability guarantee.

```
┌──────────────────────────────────────────────────────────────────┐
│  Tier 3 — Semantic Porcelain                                     │
│  muse midi …   muse code …   muse coord …                       │
│  Domain-specific multidimensional commands                       │
├──────────────────────────────────────────────────────────────────┤
│  Tier 2 — Core Porcelain                                         │
│  muse init / commit / status / log / diff / show …              │
│  Human and agent VCS operations (domain-agnostic)               │
├──────────────────────────────────────────────────────────────────┤
│  Tier 1 — Plumbing                                               │
│  muse plumbing hash-object / cat-object / rev-parse …           │
│  Machine-readable, JSON-outputting, pipeable primitives          │
└──────────────────────────────────────────────────────────────────┘
```

---

## Tier 1 — Plumbing

**Namespace:** `muse plumbing <command>`

### Contract

Every Tier 1 command:

- **Outputs JSON by default** — machine-stable schema, versioned, agent-parseable.
- **Accepts `--format text`** — human-readable fallback where meaningful.
- **Never prompts** — strictly non-interactive; safe for agent pipelines.
- **Exit codes** — `0` success, `1` user error (bad args, missing ref), `3` internal error.
- **Pipeable** — reads from stdin (`unpack-objects`) or writes to stdout (`pack-objects`, `cat-object`).
- **Stable API** — output schemas do not break across Muse versions.

Tier 1 commands are the atoms from which Tier 2 porcelain is composed.  They
expose the raw engine directly, enabling MuseHub, agent orchestrators, CI
pipelines, and shell scripts to interact with the store without going through
the higher-level VCS logic.

### Commands

| Command | Description |
|---------|-------------|
| `muse plumbing hash-object [--write] <file>` | SHA-256 a file; optionally store it in `.muse/objects/` |
| `muse plumbing cat-object [--format raw\|info] <object_id>` | Emit raw bytes of a stored blob to stdout |
| `muse plumbing rev-parse [--format json\|text] <ref>` | Resolve branch / HEAD / SHA prefix → full commit ID |
| `muse plumbing ls-files [--commit <id>] [--format json\|text]` | List all tracked files and their object IDs |
| `muse plumbing read-commit <id>` | Emit full commit metadata as JSON |
| `muse plumbing read-snapshot <id>` | Emit full snapshot manifest and metadata as JSON |
| `muse plumbing commit-tree --snapshot <id> [--parent <id>]… [--message <msg>]` | Create a commit from an explicit snapshot ID |
| `muse plumbing update-ref [--delete\|--no-verify] <branch> [<commit_id>]` | Move or delete a branch HEAD |
| `muse plumbing commit-graph [--tip <id>] [--stop-at <id>] [--max N]` | Emit the commit DAG as a JSON node list |
| `muse plumbing pack-objects [--have <id>]… <want_id>…` | Build a `PackBundle` JSON and write to stdout |
| `muse plumbing unpack-objects` | Read `PackBundle` JSON from stdin, write to local store |
| `muse plumbing ls-remote [--json] <remote-or-url>` | List remote branch heads without modifying local state |

### JSON Output Schemas

#### `hash-object`

```json
{
  "object_id": "<sha256-hex-64>",
  "stored": false
}
```

#### `cat-object --format info`

```json
{
  "object_id": "<sha256-hex-64>",
  "present": true,
  "size_bytes": 1234
}
```

With `--format raw` (default): raw bytes written to stdout.

#### `rev-parse`

```json
{
  "ref": "main",
  "commit_id": "<sha256-hex-64>"
}
```

Error (exit 1):
```json
{
  "ref": "nonexistent",
  "commit_id": null,
  "error": "not found"
}
```

#### `ls-files`

```json
{
  "commit_id": "<sha256>",
  "snapshot_id": "<sha256>",
  "file_count": 3,
  "files": [
    {"path": "tracks/drums.mid", "object_id": "<sha256>"},
    {"path": "tracks/bass.mid",  "object_id": "<sha256>"}
  ]
}
```

#### `read-commit`

Full `CommitRecord` JSON — see `store.py` for the complete schema.
Key fields:

```json
{
  "commit_id": "<sha256>",
  "repo_id": "<uuid>",
  "branch": "main",
  "snapshot_id": "<sha256>",
  "message": "Add verse melody",
  "committed_at": "2026-03-18T12:00:00+00:00",
  "parent_commit_id": "<sha256> | null",
  "parent2_commit_id": null,
  "author": "gabriel",
  "agent_id": "",
  "sem_ver_bump": "none"
}
```

#### `read-snapshot`

```json
{
  "snapshot_id": "<sha256>",
  "created_at": "2026-03-18T12:00:00+00:00",
  "file_count": 3,
  "manifest": {
    "tracks/drums.mid": "<sha256>",
    "tracks/bass.mid":  "<sha256>"
  }
}
```

#### `commit-tree`

```json
{"commit_id": "<sha256>"}
```

#### `update-ref`

```json
{
  "branch": "main",
  "commit_id": "<sha256>",
  "previous": "<sha256> | null"
}
```

Delete (`--delete`):
```json
{"branch": "todelete", "deleted": true}
```

#### `commit-graph`

```json
{
  "tip": "<sha256>",
  "count": 42,
  "truncated": false,
  "commits": [
    {
      "commit_id": "<sha256>",
      "parent_commit_id": "<sha256> | null",
      "parent2_commit_id": null,
      "message": "Add verse melody",
      "branch": "main",
      "committed_at": "2026-03-18T12:00:00+00:00",
      "snapshot_id": "<sha256>",
      "author": "gabriel"
    }
  ]
}
```

#### `pack-objects` / `unpack-objects`

`pack-objects` writes a `PackBundle` JSON to stdout:

```json
{
  "commits":   [{ ...CommitDict... }],
  "snapshots": [{ ...SnapshotDict... }],
  "objects":   [{"object_id": "<sha256>", "content_b64": "<base64>"}],
  "branch_heads": {"main": "<sha256>"}
}
```

`unpack-objects` reads a `PackBundle` from stdin and outputs:

```json
{
  "commits_written":   12,
  "snapshots_written": 12,
  "objects_written":   47,
  "objects_skipped":   3
}
```

#### `ls-remote --json`

```json
{
  "repo_id": "<uuid>",
  "domain": "midi",
  "default_branch": "main",
  "branches": {
    "main": "<sha256>",
    "dev":  "<sha256>"
  }
}
```

---

## Tier 2 — Core Porcelain

**Namespace:** top-level `muse <command>`

These are the human and agent VCS commands — the interface most users interact
with.  They compose Tier 1 plumbing primitives into user-friendly workflows.

| Command | Description |
|---------|-------------|
| `muse init` | Initialise a new Muse repository |
| `muse commit` | Record the current working tree as a new version |
| `muse status` | Show working-tree drift against HEAD |
| `muse log` | Display commit history |
| `muse diff` | Compare working tree against HEAD, or two commits |
| `muse show` | Inspect a commit: metadata, diff, files |
| `muse branch` | List, create, or delete branches |
| `muse checkout` | Switch branches or restore working tree |
| `muse merge` | Three-way merge a branch into the current branch |
| `muse reset` | Move HEAD to a prior commit |
| `muse revert` | Create a commit that undoes a prior commit |
| `muse cherry-pick` | Apply a specific commit's changes on top of HEAD |
| `muse stash` | Shelve and restore uncommitted changes |
| `muse tag` | Attach and query semantic tags on commits |
| `muse domains` | Domain plugin dashboard |
| `muse attributes` | Display `.museattributes` merge-strategy rules |
| `muse remote` | Manage remote connections (add/remove/list/set-url) |
| `muse clone` | Create a local copy of a remote Muse repository |
| `muse fetch` | Download commits/snapshots/objects from a remote |
| `muse pull` | Fetch from a remote and merge into current branch |
| `muse push` | Upload local commits/snapshots/objects to a remote |
| `muse check` | Domain-agnostic invariant check |
| `muse annotate` | CRDT-backed commit annotations |

---

## Tier 3 — Semantic Porcelain

**Namespaces:** `muse midi …`, `muse code …`, `muse coord …`

Domain-specific commands that interpret multidimensional state.  These are
impossible to implement in Git — they require awareness of the domain's
semantic model (note events, symbol graphs, agent coordination).

### `muse midi …` — MIDI Domain

Full reference: [MIDI Domain Reference](midi-domain.md)

**Notation & Visualization**

| Command | Description |
|---------|-------------|
| `muse midi notes` | List every note in a MIDI track as musical notation |
| `muse midi piano-roll` | ASCII piano roll visualization |
| `muse midi instrumentation` | Per-channel note range, register, and velocity map |

**Pitch, Harmony & Scale**

| Command | Description |
|---------|-------------|
| `muse midi harmony` | Bar-by-bar chord detection and key signature estimation |
| `muse midi scale` | Scale/mode detection: 15 types × 12 roots, ranked by confidence |
| `muse midi contour` | Melodic contour shape and interval sequence |
| `muse midi tension` | Harmonic tension curve: dissonance score per bar |
| `muse midi cadence` | Cadence detection: authentic, deceptive, half, plagal |

**Rhythm & Dynamics**

| Command | Description |
|---------|-------------|
| `muse midi rhythm` | Syncopation score, swing ratio, quantisation accuracy, subdivision |
| `muse midi tempo` | BPM estimation via IOI voting; confidence rated |
| `muse midi density` | Notes-per-beat per bar — textural arc of a composition |
| `muse midi velocity-profile` | Dynamic range, RMS velocity, and histogram (ppp–fff) |

**Structure & Voice Leading**

| Command | Description |
|---------|-------------|
| `muse midi motif` | Recurring interval-pattern detection, transposition-invariant |
| `muse midi voice-leading` | Parallel fifths/octaves + large leaps — counterpoint lint |
| `muse midi compare` | Semantic diff across key, rhythm, density, swing between two commits |

**History & Attribution**

| Command | Description |
|---------|-------------|
| `muse midi note-log` | Note-level commit history |
| `muse midi note-blame` | Per-bar attribution: which commit introduced each note |
| `muse midi hotspots` | Bar-level churn leaderboard |

**Multi-Agent Intelligence**

| Command | Description |
|---------|-------------|
| `muse midi agent-map` | Bar-level blame: which agent last edited each bar |
| `muse midi find-phrase` | Phrase similarity search across commit history |
| `muse midi shard` | Partition composition into N bar-range shards for parallel agents |
| `muse midi query` | MIDI DSL predicate query over note data and commit history |

**Transformation**

| Command | Description |
|---------|-------------|
| `muse midi transpose` | Shift all pitches by N semitones |
| `muse midi invert` | Melodic inversion around a pivot pitch |
| `muse midi retrograde` | Reverse pitch order (retrograde transformation) |
| `muse midi quantize` | Snap onsets to a rhythmic grid with adjustable strength |
| `muse midi humanize` | Add timing/velocity jitter for human feel |
| `muse midi arpeggiate` | Convert chord voicings to arpeggios |
| `muse midi normalize` | Rescale velocities to a target dynamic range |
| `muse midi mix` | Combine notes from two MIDI tracks into one output file |

**Invariants & Quality Gates**

| Command | Description |
|---------|-------------|
| `muse midi check` | Enforce MIDI invariant rules (CI gate) |

### `muse code …` — Code Domain

| Command | Description |
|---------|-------------|
| `muse code symbols` | List every semantic symbol in a snapshot |
| `muse code symbol-log` | Track a symbol through commit history |
| `muse code detect-refactor` | Detect renames, moves, extractions |
| `muse code grep` | Search the symbol graph by name/kind/language |
| `muse code blame` | Which commit last touched a specific symbol? |
| `muse code hotspots` | Symbol churn leaderboard |
| `muse code stable` | Symbol stability leaderboard |
| `muse code coupling` | File co-change analysis |
| `muse code compare` | Deep semantic comparison between snapshots |
| `muse code languages` | Language and symbol-type breakdown |
| `muse code patch` | Surgical semantic patch on a single symbol |
| `muse code query` | Symbol graph predicate DSL |
| `muse code query-history` | Temporal symbol search across a commit range |
| `muse code deps` | Import graph + call-graph |
| `muse code find-symbol` | Cross-commit, cross-branch symbol search |
| `muse code impact` | Transitive blast-radius |
| `muse code dead` | Dead code candidates |
| `muse code coverage` | Class interface call-coverage |
| `muse code lineage` | Full provenance chain of a symbol |
| `muse code api-surface` | Public API surface at a commit |
| `muse code codemap` | Semantic topology |
| `muse code clones` | Exact and near-duplicate symbols |
| `muse code checkout-symbol` | Restore a historical version of one symbol |
| `muse code semantic-cherry-pick` | Cherry-pick named symbols from a commit |
| `muse code index` | Manage local indexes |
| `muse code breakage` | Detect symbol-level structural breakage |
| `muse code invariants` | Enforce architectural rules |
| `muse code check` | Semantic invariant enforcement |
| `muse code code-query` | Predicate query over code commit history |

### `muse coord …` — Multi-Agent Coordination

| Command | Description |
|---------|-------------|
| `muse coord reserve` | Advisory symbol reservation |
| `muse coord intent` | Declare a specific operation before executing it |
| `muse coord forecast` | Predict merge conflicts |
| `muse coord plan-merge` | Dry-run semantic merge plan |
| `muse coord shard` | Partition the codebase into N work zones |
| `muse coord reconcile` | Recommend merge ordering and integration strategy |

---

## Extending with New Domains

To add a new domain (e.g. `muse genomics …`):

1. Create `muse/plugins/genomics/plugin.py` implementing `MuseDomainPlugin`.
2. Create `muse/cli/commands/genomics_*.py` command modules.
3. Add a `genomics_cli = typer.Typer(name="genomics", …)` in `muse/cli/app.py`.
4. Register commands under `genomics_cli` and add `cli.add_typer(genomics_cli, name="genomics")`.
5. Write tests under `tests/` and docs under `docs/reference/`.

The core engine (`muse/core/`) is **never modified** for a new domain.


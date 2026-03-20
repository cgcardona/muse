# Muse VCS — Architecture Reference

> **Version:** v0.1.2
> **See also:** [Plugin Authoring Guide](../guide/plugin-authoring-guide.md) · [CRDT Reference](../guide/crdt-reference.md) · [E2E Walkthrough](muse-e2e-demo.md) · [Plugin Protocol](../protocol/muse-protocol.md) · [Domain Concepts](../protocol/muse-domain-concepts.md) · [Type Contracts](../reference/type-contracts.md)

---

## What Muse Is

Muse is a **domain-agnostic version control system for multidimensional state**. It provides
a complete DAG engine — content-addressed objects, commits, branches, three-way merge, drift
detection, time-travel checkout, and a full log graph — with one deliberate gap: it does not
know what "state" is.

That gap is the plugin slot. A `MuseDomainPlugin` tells Muse how to interpret your domain's
data. Everything else — the DAG, object store, branching, lineage walking, log, merge state
machine — is provided by the core engine and shared across all domains.

Muse v1.0 adds **four layers of semantic richness** on top of that base, each implemented as
an optional protocol extension that plugins can adopt without breaking anything:

| Phase | Protocol | What you gain |
|-------|----------|---------------|
| 1 — Typed Delta Algebra | `MuseDomainPlugin` (required) | Rich, typed operation lists instead of opaque file diffs |
| 2 — Domain Schema | `MuseDomainPlugin.schema()` (required) | Algorithm selection driven by declared data structure |
| 3 — OT Merge Engine | `StructuredMergePlugin` (optional) | Sub-file auto-merge using Operational Transformation |
| 4 — CRDT Semantics | `CRDTPlugin` (optional) | Convergent join — no conflicts ever possible |

---

## The Seven Invariants

```
State      = a serializable, content-addressed snapshot of any multidimensional space
Commit     = a named delta from a parent state, recorded in a DAG
Branch     = a divergent line of intent forked from a shared ancestor
Merge      = three-way reconciliation of two divergent state lines against a common base
Drift      = the gap between committed state and live state
Checkout   = deterministic reconstruction of any historical state from the DAG
Lineage    = the causal chain from root to any commit
```

None of those definitions contain the word "music."

---

## Repository Structure on Disk

Every Muse repository is a `.muse/` directory:

```
.muse/
  repo.json            — repository ID, domain name, creation metadata
  HEAD                 — ref pointer, e.g. refs/heads/main
  config.toml          — optional local config (auth token, remotes)
  refs/
    heads/
      main             — SHA-256 commit ID of branch HEAD
      feature/…        — additional branch HEADs
  objects/
    <sha2>/            — shard directory (first 2 hex chars)
      <sha62>          — raw content-addressed blob
  commits/
    <commit_id>.json   — CommitRecord (includes structured_delta since Phase 1)
  snapshots/
    <snapshot_id>.json — SnapshotRecord (manifest: {path → object_id})
  tags/
    <tag_id>.json      — TagRecord
  MERGE_STATE.json     — present only during an active merge conflict
state/             — the working tree (domain files live here)
.museattributes        — optional: per-path merge strategy overrides (TOML)
.museignore            — optional: snapshot exclusion rules (TOML, domain-scoped)
```

The object store mirrors Git's loose-object layout: sharding by the first two hex characters
of each SHA-256 digest prevents filesystem degradation as the repository grows.

---

## Core Engine Modules

```
muse/
  domain.py                 — all protocol definitions and shared type aliases
  core/
    store.py                — file-based commit / snapshot / tag CRUD
    repo.py                 — repository detection (MUSE_REPO_ROOT or directory walk)
    snapshot.py             — content-addressed snapshot and commit ID derivation
    object_store.py         — SHA-256 blob storage under .muse/objects/
    merge_engine.py         — three-way merge + CRDT join entry points
    op_transform.py         — Operational Transformation (Phase 3)
    schema.py               — DomainSchema TypedDicts (Phase 2)
    diff_algorithms/        — LCS, tree-edit, numerical, set diff (Phase 2)
    crdts/                  — VectorClock, LWWRegister, ORSet, RGA, AWMap, GCounter (Phase 4)
    errors.py               — ExitCode enum
    attributes.py           — .museattributes loading and strategy resolution
  plugins/
    registry.py             — domain name → MuseDomainPlugin instance
    music/
      plugin.py             — MidiPlugin: reference implementation of all protocols
      midi_diff.py          — note-level MIDI diff and MIDI reconstruction
    scaffold/
      plugin.py             — copy-paste template for new domain plugins
  cli/
    app.py                  — Typer application root, command registration
    commands/               — one file per Tier 2 command; plumbing/ for Tier 1
```

---

## Deterministic ID Derivation

All IDs are SHA-256 digests — the DAG is fully content-addressed:

```
object_id   = sha256(raw_file_bytes)
snapshot_id = sha256(sorted("path:object_id\n" pairs))
commit_id   = sha256(sorted_parent_ids | snapshot_id | message | timestamp_iso)
```

The same snapshot always produces the same ID. Two commits that point to identical state share
a `snapshot_id`. Objects are never overwritten — write is always idempotent.

---

## Phase 1 — Typed Delta Algebra

Every commit now carries a `structured_delta: StructuredDelta` alongside the snapshot
manifest. A `StructuredDelta` is a list of typed `DomainOp` entries:

| Op type | Meaning |
|---------|---------|
| `InsertOp` | An element was added at a position |
| `DeleteOp` | An element was removed |
| `MoveOp` | An element was repositioned |
| `ReplaceOp` | An element's value changed (before/after content hashes) |
| `PatchOp` | A container was internally modified (carries child ops recursively) |

This replaces the old opaque `{added, removed, modified}` path lists entirely. Every operation
carries a `content_id` (SHA-256 hash of the element), an `address` (domain-specific location),
and a `content_summary` (human-readable description for `muse show`).

`muse show <commit>` and `muse diff` display note-level diffs for MIDI files — not just "file
changed" but "3 notes added at bar 4, 1 note removed from bar 7."

---

## Phase 2 — Domain Schema & Diff Algorithm Library

Plugins implement `schema() -> DomainSchema` to declare the structural shape of their data.
The schema drives algorithm selection in `diff_by_schema()`:

| Schema kind | Diff algorithm | Use when… |
|-------------|---------------|-----------|
| `"sequence"` | Myers LCS | Ordered lists (note events, DNA sequences) |
| `"tree"` | LCS-based tree edit | Hierarchical structures (scene graphs, XML) |
| `"tensor"` | Epsilon-tolerant numerical | N-dimensional arrays (simulation grids) |
| `"set"` | Hash-set algebra | Unordered collections (annotation sets) |
| `"map"` | Per-key comparison | Key-value maps (manifests, configs) |

`DomainSchema.merge_mode` controls which merge path the core engine takes:
- `"three_way"` — classic three-way merge (Phases 1–3)
- `"crdt"` — convergent CRDT join (Phase 4)

---

## Phase 3 — Operation-Level Merge Engine

Plugins that implement `StructuredMergePlugin` gain sub-file auto-merge:

```python
@runtime_checkable
class StructuredMergePlugin(MuseDomainPlugin, Protocol):
    def merge_ops(
        self,
        base: StateSnapshot,
        ours_snap: StateSnapshot,
        theirs_snap: StateSnapshot,
        ours_ops: list[DomainOp],
        theirs_ops: list[DomainOp],
        *,
        repo_root: pathlib.Path | None = None,
    ) -> MergeResult: ...
```

The core merge engine detects this with `isinstance(plugin, StructuredMergePlugin)` and calls
`merge_ops()` when both branches have `StructuredDelta`. Non-supporting plugins fall back to
file-level `merge()` automatically.

### Operational Transformation (`muse/core/op_transform.py`)

| Function | Purpose |
|----------|---------|
| `ops_commute(a, b)` | Returns `True` when two ops can be applied in either order |
| `transform(a, b)` | Adjusts positions so the diamond property holds |
| `merge_op_lists(base, ours, theirs)` | Three-way OT merge; returns `MergeOpsResult` |
| `merge_structured(base_delta, ours_delta, theirs_delta)` | Wrapper for `StructuredDelta` inputs |

**Commutativity rules (all 25 op-pair combinations covered):**
- Different addresses → always commute
- `InsertOp` + `InsertOp` at same position → conflict
- `DeleteOp` + `DeleteOp` same content_id → idempotent (not a conflict)
- `PatchOp` + `PatchOp` → recursive check on child ops
- Cross-type pairs → generally commute (structural independence)

---

## Phase 4 — CRDT Semantics

Plugins that implement `CRDTPlugin` replace three-way merge with a mathematical `join` on a
lattice. **`join` always succeeds — no conflict state ever exists.**

```python
@runtime_checkable
class CRDTPlugin(MuseDomainPlugin, Protocol):
    def crdt_schema(self) -> list[CRDTDimensionSpec]: ...
    def join(self, a: CRDTSnapshotManifest, b: CRDTSnapshotManifest) -> CRDTSnapshotManifest: ...
    def to_crdt_state(self, snapshot: StateSnapshot) -> CRDTSnapshotManifest: ...
    def from_crdt_state(self, crdt: CRDTSnapshotManifest) -> StateSnapshot: ...
```

Entry point: `crdt_join_snapshots()` in `merge_engine.py`.

### CRDT Primitive Library (`muse/core/crdts/`)

| Primitive | File | Best for |
|-----------|------|---------|
| `VectorClock` | `vclock.py` | Causal ordering between agents |
| `LWWRegister` | `lww_register.py` | Scalar values; last write wins |
| `ORSet` | `or_set.py` | Unordered sets; adds always win |
| `RGA` | `rga.py` | Ordered sequences (collaborative editing) |
| `AWMap` | `aw_map.py` | Key-value maps; adds win |
| `GCounter` | `g_counter.py` | Monotonically increasing counters |

All six satisfy: commutativity, associativity, idempotency — the three lattice laws that
guarantee convergence regardless of message delivery order.

### When to use CRDT mode

| Scenario | Recommendation |
|----------|----------------|
| Human-paced commits (once per hour/day) | Three-way merge (Phases 1–3) |
| Many agents writing concurrently (sub-second) | CRDT mode |
| Shared annotation sets (many simultaneous contributors) | CRDT `ORSet` |
| Collaborative score editing (DAW-style) | CRDT `RGA` |
| Per-dimension mix | Set `merge_mode="crdt"` per `CRDTDimensionSpec` |

---

## The Full Plugin Protocol Stack

```
MuseDomainPlugin          ← required by every domain plugin
  ├── schema()            ← Phase 2: declare data structure
  ├── snapshot()          ← capture current live state
  ├── diff()              ← compute typed StructuredDelta
  ├── drift()             ← detect uncommitted changes
  ├── apply()             ← apply delta to working tree
  └── merge()             ← three-way merge (fallback)

StructuredMergePlugin     ← optional Phase 3 extension
  └── merge_ops()         ← operation-level OT merge

CRDTPlugin                ← optional Phase 4 extension
  ├── crdt_schema()       ← declare per-dimension CRDT types
  ├── join()              ← convergent lattice join
  ├── to_crdt_state()     ← lift plain snapshot to CRDT state
  └── from_crdt_state()   ← materialise CRDT state back to snapshot
```

The core engine detects capabilities at runtime via `isinstance`:

```python
if isinstance(plugin, CRDTPlugin) and schema["merge_mode"] == "crdt":
    return crdt_join_snapshots(plugin, ...)
elif isinstance(plugin, StructuredMergePlugin):
    return plugin.merge_ops(base, ours_snap, theirs_snap, ours_ops, theirs_ops)
else:
    return plugin.merge(base, left, right)
```

---

## How CLI Commands Use the Plugin

| Command | Plugin method(s) called |
|---------|------------------------|
| `muse commit` | `snapshot()`, `diff()` (for structured_delta) |
| `muse status` | `drift()` |
| `muse diff` | `diff()` |
| `muse show` | reads stored `structured_delta` |
| `muse merge` | `merge_ops()` or `merge()` (capability detection) |
| `muse cherry-pick` | `merge()` |
| `muse stash` | `snapshot()` |
| `muse checkout` | `diff()` + `apply()` |
| `muse domains` | `schema()`, capability introspection |

---

## Adding a New Domain — Quick Reference

1. Copy `muse/plugins/scaffold/plugin.py` → `muse/plugins/<domain>/plugin.py`
2. Implement all methods (every `raise NotImplementedError` must be replaced)
3. Register in `muse/plugins/registry.py`
4. Run `muse init --domain <domain>` in any project directory
5. All existing CLI commands work immediately

See the full [Plugin Authoring Guide](../guide/plugin-authoring-guide.md) for a step-by-step
walkthrough covering Phases 1–4 with examples.

---

## CLI Command Reference

Muse uses a **three-tier command architecture**. See [`docs/reference/cli-tiers.md`](../reference/cli-tiers.md) for the full specification and JSON output schemas.

### Tier 1 — Plumbing (`muse plumbing …`)

Machine-readable, JSON-outputting, pipeable primitives. Designed for scripts, agents, and CI automation.

| Command | Description |
|---------|-------------|
| `muse plumbing hash-object <file>` | SHA-256 a file; optionally store it |
| `muse plumbing cat-object <id>` | Emit raw bytes of a stored object |
| `muse plumbing rev-parse <ref>` | Resolve branch/HEAD/prefix → commit_id |
| `muse plumbing ls-files [<ref>]` | List tracked files and object IDs |
| `muse plumbing read-commit <id>` | Emit full commit JSON |
| `muse plumbing read-snapshot <id>` | Emit full snapshot JSON |
| `muse plumbing commit-tree <snap_id>` | Create a commit from an explicit snapshot |
| `muse plumbing update-ref <branch> <id>` | Move a branch HEAD |
| `muse plumbing commit-graph` | Emit commit DAG as JSON |
| `muse plumbing pack-objects <ids…>` | Build a PackBundle JSON to stdout |
| `muse plumbing unpack-objects` | Read PackBundle JSON from stdin, write to store |
| `muse plumbing ls-remote [remote]` | List branch refs on a remote |

### Tier 2 — Core Porcelain (top-level `muse …`)

Human and agent VCS commands. Domain-agnostic; delegate to `muse.core.*`.

| Command | Description |
|---------|-------------|
| `muse init [--domain <name>]` | Initialize a repository |
| `muse commit -m <msg>` | Snapshot live state and record a commit |
| `muse status` | Show drift between HEAD and working tree |
| `muse diff [<base>] [<target>]` | Show delta between commits or vs. working tree |
| `muse log [--oneline] [--graph] [--stat]` | Display commit history |
| `muse show [<ref>] [--json] [--stat]` | Inspect a single commit with operation-level detail |
| `muse branch [<name>] [-d <name>]` | Create or delete branches |
| `muse checkout <branch\|commit> [-b]` | Switch branches or restore historical state |
| `muse merge <branch>` | Three-way merge (or CRDT join, capability-detected) |
| `muse cherry-pick <commit>` | Apply a specific commit's delta on top of HEAD |
| `muse revert <commit>` | Create a new commit undoing a prior commit |
| `muse reset <commit> [--hard]` | Move branch pointer |
| `muse stash` / `pop` / `list` / `drop` | Temporarily shelve uncommitted changes |
| `muse tag add <tag> [<ref>]` | Tag a commit |
| `muse tag list [<ref>]` | List tags |
| `muse domains` | Show domain dashboard — registered domains, capabilities, schema |
| `muse remote add <name> <url>` | Register a named remote |
| `muse clone <url> [dir]` | Clone a remote repository |
| `muse fetch [remote]` | Download commits/snapshots/objects |
| `muse pull [remote]` | Fetch + three-way merge |
| `muse push [remote]` | Upload local commits/snapshots/objects |
| `muse check` | Domain-agnostic invariant check |
| `muse annotate` | CRDT-backed commit annotations |

### Tier 3 — Semantic Porcelain

Domain-specific commands that interpret multidimensional state. Each sub-namespace is served by the corresponding plugin.

#### MIDI domain (`muse midi …`)

| Command | Description |
|---------|-------------|
| `muse midi notes` | Every note in a track as musical notation |
| `muse midi note-log` | Note-level commit history |
| `muse midi note-blame` | Per-bar attribution |
| `muse midi harmony` | Chord analysis and key detection |
| `muse midi piano-roll` | ASCII piano roll visualization |
| `muse midi hotspots` | Bar-level churn leaderboard |
| `muse midi velocity-profile` | Dynamic range and velocity histogram |
| `muse midi transpose` | Transpose all notes by N semitones |
| `muse midi mix` | Combine two MIDI tracks into one |
| `muse midi query` | MIDI DSL predicate query over history |
| `muse midi check` | Enforce MIDI invariant rules |

#### Code domain (`muse code …`)

| Command | Description |
|---------|-------------|
| `muse code symbols` | List every symbol in a snapshot |
| `muse code symbol-log` | Full history of one symbol |
| `muse code detect-refactor` | Detect semantic refactoring operations |
| `muse code grep` | Search the symbol graph |
| `muse code blame` | Which commit last touched a symbol |
| `muse code hotspots` | Symbol churn leaderboard |
| `muse code stable` | Symbol stability leaderboard |
| `muse code coupling` | File co-change analysis |
| `muse code compare` | Deep semantic comparison between snapshots |
| `muse code languages` | Language and symbol-type breakdown |
| `muse code patch` | Surgical per-symbol modification |
| `muse code query` | Symbol graph predicate DSL |
| `muse code query-history` | Temporal symbol search |
| `muse code deps` | Import graph and call graph |
| `muse code find-symbol` | Cross-commit symbol search |
| `muse code impact` | Transitive blast-radius for a symbol |
| `muse code dead` | Dead code candidates |
| `muse code coverage` | Interface call-coverage |
| `muse code lineage` | Full provenance chain of a symbol |
| `muse code api-surface` | Public API surface at a commit |
| `muse code codemap` | Semantic topology of the codebase |
| `muse code clones` | Find duplicate symbols |
| `muse code checkout-symbol` | Restore a historical symbol version |
| `muse code semantic-cherry-pick` | Cherry-pick named symbols from history |
| `muse code index` | Manage local symbol indexes |
| `muse code breakage` | Detect structural breakage vs HEAD |
| `muse code invariants` | Enforce architectural rules |
| `muse code check` | Semantic invariant enforcement |

#### Coordination domain (`muse coord …`)

| Command | Description |
|---------|-------------|
| `muse coord reserve` | Advisory symbol reservation |
| `muse coord intent` | Declare an operation before executing |
| `muse coord forecast` | Predict merge conflicts |
| `muse coord plan-merge` | Dry-run semantic merge plan |
| `muse coord shard` | Partition codebase into parallel work zones |
| `muse coord reconcile` | Recommend merge ordering strategy |

---

## Remote Sync

Muse supports synchronizing repositories with a remote host (e.g. MuseHub)
through six commands built on a typed, swappable transport layer.

### Commands

| Command | Description |
|---------|-------------|
| `muse remote add <name> <url>` | Register a named remote connection |
| `muse clone <url> [dir]` | Create a local copy of a remote repository |
| `muse fetch [remote]` | Download commits/snapshots/objects (no merge) |
| `muse pull [remote]` | Fetch + three-way merge into current branch |
| `muse push [remote]` | Upload local commits/snapshots/objects |
| `muse plumbing ls-remote [remote]` | List branch refs on a remote (Tier 1 plumbing) |

### Transport Architecture

```
Muse CLI (client)                    MuseHub (server)
─────────────────                    ────────────────
MuseTransport Protocol
 └─ HttpTransport (urllib, stdlib) ──HTTPS──► GET  {url}/refs
                                              POST {url}/fetch
                                              POST {url}/push
```

The `MuseTransport` Protocol in `muse/core/transport.py` is the seam between
CLI commands and the HTTP implementation. Every command delegates to this
Protocol, so MuseHub can upgrade to HTTP/2 or gRPC without touching command
code — only `HttpTransport` changes.

### PackBundle Wire Format

The unit of exchange is a `PackBundle` (defined in `muse/core/pack.py`): a
JSON object carrying commits, snapshots, and base64-encoded object blobs.
`build_pack()` assembles a bundle from a set of local commit IDs;
`apply_pack()` writes a received bundle into the local `.muse/` directory in
dependency order (objects → snapshots → commits).

```
muse/core/pack.py
 ├─ build_pack(root, commit_ids, *, have) → PackBundle
 └─ apply_pack(root, bundle) → ApplyResult  # commits/snapshots/objects written + skipped
```

### Local State for Remotes

```
.muse/
 config.toml                    [remotes.<name>] url + branch
 remotes/<name>/<branch>        last-known remote commit ID (tracking head)
```

See `docs/reference/remotes.md` for the full reference including the MuseHub
API contract, authentication, and tracking branch semantics.

---

## Testing & Verification

```bash
# Full test suite (1903 tests)
.venv/bin/pytest tests/ -v

# Type checking (zero errors required)
mypy muse/

# Typing audit (zero Any violations required)
python tools/typing_audit.py --dirs muse/ tests/ --max-any 0
```

CI runs all three gates on every PR to `dev` and on every `dev → main` merge.

---

## Key Design Decisions

**Why no `async`?** The CLI is synchronous by design. All algorithms are CPU-bound and
complete in bounded time. If a domain's data is too large to diff synchronously, the plugin
should chunk it — this is a domain concern, not a core concern.

**Why TypedDicts over Pydantic?** Zero external dependencies. All types are JSON-serialisable
by construction. `mypy --strict` verifies them without runtime overhead.

**Why content-addressed storage?** Objects are never overwritten. Checkout, revert, and
cherry-pick cost zero bytes when the target objects already exist. The object store scales to
millions of fine-grained sub-elements (individual notes, nucleotides, mesh vertices) without
format changes.

**Why four phases?** Each phase is independently useful. A plugin that only implements
Phase 1 gets rich operation-level `muse show` output. Phase 2 adds algorithm selection.
Phase 3 adds sub-file auto-merge. Phase 4 adds convergent multi-agent semantics. Adoption
is incremental and current.

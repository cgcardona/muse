# MuseDomainPlugin Protocol — Language-Agnostic Specification

> **Status:** Canonical · **Version:** v0.1.1
> **Audience:** Anyone implementing a Muse domain plugin in any language.

---

## 0. Purpose

This document specifies the six-method contract a domain plugin must satisfy to
integrate with the Muse VCS engine, plus the two optional protocol extensions for
richer merge semantics. It is intentionally language-agnostic.

Muse provides the DAG, object store, branching, lineage, merge state machine, log,
and CLI. A plugin provides domain knowledge. This document defines the boundary
between them.

---

## 1. Design Principles

1. **Plugins are pure transformations.** A plugin method takes state in, returns state
   out. Side effects (writing to disk, calling APIs) belong to the CLI layer, not
   the plugin.
2. **All state is JSON-serializable.** Snapshots must be serializable to a
   content-addressable string. No opaque blobs inside snapshot values.
3. **Content-addressed identity.** The same state must always produce the same
   snapshot. Snapshots are compared by their SHA-256 digest — not by object identity.
4. **Idempotent writes.** Writing an object or snapshot that already exists is a
   no-op. The store never overwrites existing content.
5. **Conflicts are data, not exceptions.** A conflicted merge returns a `MergeResult`
   with a non-empty `conflicts` list. It does not raise.
6. **Drift is always relative.** `drift()` compares committed state against live
   state. It never modifies either.

---

## 2. Type Definitions

All types use Python as the reference notation. Implementations in other languages
should map to equivalent constructs.

```python
# A workspace-relative path mapped to its SHA-256 content digest.
# Must contain "files": dict[str, str] and "domain": str.
StateSnapshot = TypedDict("StateSnapshot", files=dict[str, str], domain=str)

# The "live" input to snapshot() and drift().
# Either a filesystem path to the working directory,
# or an existing StateSnapshot (used for in-memory operations).
LiveState = Path | StateSnapshot

# Output of diff(): a typed list of domain operations.
# StructuredDelta carries insert / delete / move / replace / patch ops,
# each with a content-addressed before/after reference.
StateDelta = StructuredDelta  # see type-contracts.md for the full shape

# Output of merge(): the reconciled snapshot + conflict + strategy metadata.
MergeResult = dataclass(
    merged: StateSnapshot,
    conflicts: list[str],
    applied_strategies: dict[str, str],
    dimension_reports: dict[str, dict[str, str]],
)

# Output of drift(): summary of how live state diverges from committed state.
DriftReport = dataclass(has_drift: bool, summary: str, delta: StateDelta)

# Output of schema(): structural declaration of the domain's data shape.
DomainSchema = TypedDict with keys: domain, schema_version, description,
               merge_mode, elements, dimensions  # see type-contracts.md
```

---

## 3. The Six Required Methods

### 3.1 `snapshot(live_state: LiveState) → StateSnapshot`

Capture the current live state as a serializable, content-addressable snapshot.

**Contract:**
- The return value MUST be JSON-serializable.
- The return value MUST contain a `"files"` key mapping workspace-relative path
  strings to their SHA-256 hex digests.
- The return value MUST contain a `"domain"` key matching the plugin's domain name.
- Given identical input, the output MUST be identical (deterministic).
- If `live_state` is already a `StateSnapshot` dict, return it unchanged.

**Called by:** `muse commit`, `muse stash`

---

### 3.2 `diff(base: StateSnapshot, target: StateSnapshot, *, repo_root: Path | None = None) → StateDelta`

Compute the typed delta between two snapshots.

**Contract:**
- Return MUST be a `StructuredDelta` containing a typed `ops` list.
- Each operation MUST have an `op` kind: `"insert"`, `"delete"`, `"move"`,
  `"replace"`, or `"patch"`.
- Each operation MUST have an `"address"` field identifying the element within
  the domain's namespace.
- Return MUST contain `"domain"` matching the plugin's domain name.
- `diff(s, s)` MUST return an empty `ops` list for identical snapshots.

**Called by:** `muse diff`, `muse checkout`, `muse show`

---

### 3.3 `merge(base, left, right: StateSnapshot, *, repo_root: Path | None = None) → MergeResult`

Three-way merge two divergent state lines against a common ancestor.

**Contract:**
- `base` is the common ancestor (merge base).
- `left` is the current branch's snapshot (ours).
- `right` is the incoming branch's snapshot (theirs).
- `repo_root`, when provided, is the filesystem root of the repository.
  Implementations SHOULD use it to load `.museattributes` and apply
  file-level or dimension-level merge strategies before falling back to
  conflict reporting.
- `result.merged` MUST be a valid `StateSnapshot`.
- `result.conflicts` MUST be a list of workspace-relative path strings.
  - An empty list means the merge was clean.
  - Paths in `result.conflicts` MUST also appear in `result.merged` (placeholder state).
- `result.applied_strategies` maps paths where a `.museattributes` rule overrode
  the default conflict behaviour to the strategy string that was used.
  Plugins SHOULD populate this for observability; it MAY be empty.
- `result.dimension_reports` maps paths that received dimension-level merge to
  a `{dimension: winner}` dict for each resolved dimension.
  Plugins that do not support dimension merge MAY always return `{}`.
- **Consensus deletion** (both sides deleted the same path) is NOT a conflict.
- This method MUST NOT raise on conflict — it returns the conflict list instead.

**Called by:** `muse merge`, `muse cherry-pick`

---

### 3.4 `drift(committed: StateSnapshot, live: LiveState) → DriftReport`

Detect how far the live state has diverged from the last committed snapshot.

**Contract:**
- `result.has_drift` is `True` if and only if `delta` is non-empty.
- `result.summary` is a human-readable string (e.g. `"2 added, 1 modified"`
  or `"working tree clean"`).
- `result.delta` is a valid `StateDelta`.
- This method MUST NOT modify any state.

**Called by:** `muse status`

---

### 3.5 `apply(delta: StateDelta, live_state: LiveState) → LiveState`

Apply a delta to produce a new live state. Serves as the domain-level
post-checkout hook.

**Contract:**
- When `live_state` is a filesystem `Path`: the caller has already applied the
  delta physically (removed deleted files, restored added/modified from the object
  store). The plugin SHOULD rescan the directory and return the authoritative new
  state as a `StateSnapshot`.
- When `live_state` is a `StateSnapshot` dict: apply removals to the in-memory dict.
  Added/modified paths SHOULD be noted as limitations — the delta does not carry
  content hashes, so the caller must supply them through another path.
- The return value MUST be a valid `LiveState`.

**Called by:** `muse checkout`

---

### 3.6 `schema() → DomainSchema`

Declare the structural shape of the domain's data.

**Contract:**
- Return MUST be a `DomainSchema` TypedDict.
- `schema["domain"]` MUST match the plugin's domain name.
- `schema["merge_mode"]` MUST be one of `"three_way"` or `"crdt"`.
- `schema["elements"]` MUST be a non-empty list of `ElementSchema` entries,
  each with a `"name"` and `"kind"` field.
- `schema["dimensions"]` MUST be a list of `DimensionSpec` entries,
  each with `"name"`, `"description"`, and `"diff_algorithm"` fields.
- This method MUST be idempotent (calling it multiple times returns structurally
  identical values).

**Called by:** `muse domains`, diff algorithm selection, merge engine conflict reporting.

---

## 4. Optional Protocol Extensions

### 4.1 `StructuredMergePlugin` — Operational Transformation Merge

Plugins may optionally implement `StructuredMergePlugin` by adding a `merge_ops()` method.

```python
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

When both branches produce a `StructuredDelta` from `diff()`, the merge engine
detects `isinstance(plugin, StructuredMergePlugin)` and calls `merge_ops()` for
operation-level conflict detection. Non-commuting operations become the minimal,
real conflict set. Non-supporting plugins fall back to the file-level `merge()` path.

**Contract for `merge_ops()`:**
- `ours_ops` and `theirs_ops` are the typed operation lists from each branch's
  `StructuredDelta`.
- The engine applies OT commutativity rules to determine which ops are
  auto-mergeable.
- `result.conflicts` contains only addresses where the operations genuinely
  conflict (non-commuting writes to the same address).

---

### 4.2 `CRDTPlugin` — Convergent Multi-Agent Merge

Plugins may optionally implement `CRDTPlugin` by adding four methods.

```python
class CRDTPlugin(MuseDomainPlugin, Protocol):
    def join(
        self,
        a: CRDTSnapshotManifest,
        b: CRDTSnapshotManifest,
    ) -> CRDTSnapshotManifest: ...

    def crdt_schema(self) -> list[CRDTDimensionSpec]: ...

    def to_crdt_state(self, snapshot: StateSnapshot) -> CRDTSnapshotManifest: ...

    def from_crdt_state(self, crdt: CRDTSnapshotManifest) -> StateSnapshot: ...
```

`join` always succeeds — no conflict state ever exists. Given any two
`CRDTSnapshotManifest` values, `join` produces a deterministic merged result
regardless of message delivery order. The engine detects `CRDTPlugin` via
`isinstance` at merge time. `DomainSchema.merge_mode == "crdt"` signals that
the CRDT path should be taken.

**Lattice laws `join` must satisfy:**
- **Commutativity:** `join(a, b) == join(b, a)`
- **Associativity:** `join(join(a, b), c) == join(a, join(b, c))`
- **Idempotency:** `join(a, a) == a`

Violation of any lattice law breaks convergence.

---

## 5. Snapshot Format (Normative)

The minimum required shape for a `StateSnapshot`:

```json
{
  "files": {
    "path/to/file-a": "sha256-hex-64-chars",
    "path/to/file-b": "sha256-hex-64-chars"
  },
  "domain": "my_domain_name"
}
```

Plugins MAY add additional top-level keys for domain-specific metadata:

```json
{
  "files": { ... },
  "domain": "music",
  "tempo_bpm": 120,
  "key": "Am"
}
```

Additional keys MUST be JSON-serializable. The core engine ignores them; they
are available to domain-specific CLI commands via `plugin.snapshot()`.

---

## 6. Naming Conventions

| Scope | Convention |
|---|---|
| Wire format (JSON) | `camelCase` |
| Python internals | `snake_case` |
| Plugin domain name in `repo.json` | `snake_case` |
| Workspace-relative paths in snapshots | POSIX forward-slash separators |

---

## 7. Implementing a Plugin

Minimum viable implementation in Python (required methods only):

```python
import pathlib
from muse.domain import (
    DriftReport, LiveState, MergeResult,
    MuseDomainPlugin, SnapshotManifest, StructuredDelta, StateSnapshot,
)
from muse.core.schema import DomainSchema

class MyDomainPlugin:
    def snapshot(self, live_state: LiveState) -> StateSnapshot:
        if isinstance(live_state, pathlib.Path):
            files = {
                f.relative_to(live_state).as_posix(): _hash(f)
                for f in sorted(live_state.rglob("*"))
                if f.is_file()
            }
            return SnapshotManifest(files=files, domain="my_domain")
        return live_state  # already a snapshot dict

    def diff(
        self,
        base: StateSnapshot,
        target: StateSnapshot,
        *,
        repo_root: pathlib.Path | None = None,
    ) -> StructuredDelta:
        # Compute typed operations between base and target.
        # Return StructuredDelta(domain="my_domain", ops=[...], summary="...")
        ...

    def merge(
        self,
        base: StateSnapshot,
        left: StateSnapshot,
        right: StateSnapshot,
        *,
        repo_root: pathlib.Path | None = None,
    ) -> MergeResult:
        # Domain-specific reconciliation.
        # Load .museattributes if repo_root is provided and apply strategies.
        ...

    def drift(self, committed: StateSnapshot, live: LiveState) -> DriftReport:
        live_snap = self.snapshot(live)
        delta = self.diff(committed, live_snap)
        has_drift = bool(delta["ops"])
        return DriftReport(has_drift=has_drift, summary="...", delta=delta)

    def apply(self, delta: StructuredDelta, live_state: LiveState) -> LiveState:
        if isinstance(live_state, pathlib.Path):
            return self.snapshot(live_state)
        # Apply deletions to in-memory snapshot dict.
        ...

    def schema(self) -> DomainSchema:
        return DomainSchema(
            domain="my_domain",
            schema_version=1,
            description="...",
            merge_mode="three_way",
            elements=[...],
            dimensions=[...],
        )
```

See `muse/plugins/scaffold/plugin.py` for the copy-paste template implementing all
methods including the `StructuredMergePlugin` and `CRDTPlugin` extensions.

See `muse/plugins/music/plugin.py` for the complete reference implementation.

---

## 8. Invariants the Core Engine Relies On

The core engine assumes:

1. `snapshot(snapshot_dict)` returns the dict unchanged.
2. `diff(s, s)` returns an empty `ops` list for identical snapshots.
3. `merge(base, s, s)` returns `s` with an empty `conflicts` list.
4. `drift(s, path_to_workdir_matching_s)` returns `has_drift=False`.
5. Object IDs in `StateSnapshot["files"]` are valid SHA-256 hex strings (64 chars).
6. `schema()` always returns structurally identical values (idempotent).

Violating these invariants will cause incorrect behavior in `checkout`, `status`,
and merge state detection.

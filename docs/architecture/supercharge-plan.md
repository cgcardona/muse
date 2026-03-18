# Muse — Supercharge Plan: From File-Hash MVP to Universal Multidimensional VCS

> **Status:** Working document — pre-implementation spec.
> No backward compatibility constraints. We own every line.

---

## Table of Contents

1. [Honest Assessment of Current State](#1-honest-assessment-of-current-state)
2. [North Star: What We're Building Toward](#2-north-star-what-were-building-toward)
3. [Phase 1 — Typed Delta Algebra](#3-phase-1--typed-delta-algebra)
4. [Phase 2 — Domain Schema & Diff Algorithm Library](#4-phase-2--domain-schema--diff-algorithm-library)
5. [Phase 3 — Operation-Level Merge Engine](#5-phase-3--operation-level-merge-engine)
6. [Phase 4 — CRDT Semantics for Convergent Multi-Agent Writes](#6-phase-4--crdt-semantics-for-convergent-multi-agent-writes)
7. [Cross-Cutting Concerns](#7-cross-cutting-concerns)
8. [Test Strategy](#8-test-strategy)
9. [Implementation Order and Dependencies](#9-implementation-order-and-dependencies)

---

## 1. Honest Assessment of Current State

### What is good and must be preserved

- **Content-addressed object store** — SHA-256 blobs in `.muse/objects/`. This is correct at every scale. Git proved it. Keep it forever.
- **Plugin protocol boundary** — `MuseDomainPlugin` is the right abstraction. Core engine is domain-agnostic. This must remain true through every phase.
- **BFS LCA merge-base finder** — mathematically correct for DAG commit graphs.
- **File-level three-way merge** — correct for the current granularity.
- **`.museattributes` strategy system** — the right place for declarative per-path merge policy.

### What is genuinely limited

The entire system operates at a single fixed level of abstraction: **the file-path level**. The only thing the core engine ever asks about a domain is:

```
{added: [path, ...], removed: [path, ...], modified: [path, ...]}
```

`modified` is completely opaque. The engine knows *that* a file changed (SHA-256 differs), but not *what* changed inside it, *where*, *how*, or whether the change commutes with the other branch's changes.

**Consequences of this ceiling:**

| Scenario | Current behavior | Correct behavior |
|---|---|---|
| Insert note at bar 12 on branch A; insert note at bar 45 on branch B | File-level conflict | Auto-merge: non-overlapping positions |
| Insert nucleotide at position 1000 on branch A; insert at position 5000 on branch B | File-level conflict | Auto-merge |
| Two agents edit different nodes in the same scene graph | File-level conflict | Auto-merge |
| `muse show <commit>` | "tracks/drums.mid modified" | "bar 12: C4 quarter inserted; velocity 80→90" |
| Every new domain plugin | Must implement its own merge engine | Gets LCS/tree-edit/numerical for free from core |

The music plugin works around this by implementing MIDI dimension-merge inside the plugin. But that means every new domain has to re-invent sub-file merge from scratch, in isolation, with no shared vocabulary or algorithm library. At thousands of domains and millions of agents, that's impossible.

---

## 2. North Star: What We're Building Toward

A universal multidimensional VCS where:

1. **Any domain** can declare its data structure schema (sequence, tree, graph, tensor, map, or composites) and immediately get the right diff and merge algorithm for free — without implementing one.

2. **Diffs are meaningful**, not just path lists. `muse show` displays "note C4 inserted at beat 3.5" or "gene BRCA1 exon 7 deleted" or "mesh vertex 42 moved (3.2, 0.0, -1.1)".

3. **Conflicts are detected at operation granularity**, not file granularity. Two agents editing non-overlapping parts of the same sequence never conflict.

4. **Millions of agents can converge** without explicit conflict resolution, by opting into CRDT semantics where merge is a mathematical join on a lattice.

5. **The core engine never changes** when new domains are added. Every improvement to the diff algorithm library automatically benefits all existing plugins.

---

## 3. Phase 1 — Typed Delta Algebra

**Goal:** Replace the opaque `{added, removed, modified}` delta with a rich, composable operation vocabulary. Plugins return structured deltas. The core engine stores them and displays them. No core engine conflict logic changes yet — that comes in Phase 3.

**Estimated scope:** 2–3 weeks of implementation, 1 week of test writing.

### 3.1 Motivation

Today `DeltaManifest.modified` is a `list[str]` of paths. It tells you nothing about what happened inside those files. This makes `muse show`, `muse diff`, and any programmatic consumer completely blind to intra-file changes.

Phase 1 fixes this without touching the merge engine. The protocol gets richer return types; the core engine stores and forwards them opaquely; plugins can now express sub-file changes precisely.

### 3.2 New Type System in `muse/domain.py`

Replace `DeltaManifest` with a composable operation-tree type system:

```python
# ---------------------------------------------------------------------------
# Atomic position types
# ---------------------------------------------------------------------------

# A DomainAddress is a path within a domain's object graph.
# - For file-level ops: a POSIX workspace path ("tracks/drums.mid")
# - For sub-file ops:   a JSON-pointer fragment within that file ("/notes/42")
# - Plugins define what addresses mean in their domain.
DomainAddress = str

# ---------------------------------------------------------------------------
# Atomic operation types
# ---------------------------------------------------------------------------

class InsertOp(TypedDict):
    """An element was inserted into an ordered or unordered collection."""
    op: Literal["insert"]
    address: DomainAddress        # where in the structure
    position: int | None          # index for ordered sequences; None for sets
    content_id: str               # SHA-256 of inserted content (stored in object store)
    content_summary: str          # human-readable description for display

class DeleteOp(TypedDict):
    """An element was removed."""
    op: Literal["delete"]
    address: DomainAddress
    position: int | None
    content_id: str               # SHA-256 of removed content
    content_summary: str

class MoveOp(TypedDict):
    """An element was repositioned within an ordered sequence."""
    op: Literal["move"]
    address: DomainAddress
    from_position: int
    to_position: int
    content_id: str

class ReplaceOp(TypedDict):
    """An element's value changed (atomic, leaf-level)."""
    op: Literal["replace"]
    address: DomainAddress
    position: int | None
    old_content_id: str
    new_content_id: str
    old_summary: str
    new_summary: str

class PatchOp(TypedDict):
    """A nested structure was modified; carries a child StructuredDelta."""
    op: Literal["patch"]
    address: DomainAddress        # the container being patched
    child_delta: StructuredDelta  # recursive — describes what changed inside

# The union of all operation types
DomainOp = InsertOp | DeleteOp | MoveOp | ReplaceOp | PatchOp

# ---------------------------------------------------------------------------
# The new StateDelta
# ---------------------------------------------------------------------------

class StructuredDelta(TypedDict):
    """Rich, composable delta between two domain snapshots.

    ``ops`` is an ordered list of operations that transforms ``base`` into
    ``target`` when applied in sequence. The core engine treats this as an
    opaque blob for storage and display. The merge engine in Phase 3 will
    reason over it for commutativity.

    ``summary`` is a precomputed human-readable string for ``muse show``.
    Plugins compute this because only they understand their domain semantics.
    """
    domain: str
    ops: list[DomainOp]
    summary: str                  # "3 notes added, 1 bar restructured"

# StateDelta is now StructuredDelta. DeltaManifest is gone.
StateDelta = StructuredDelta
```

**Key design decisions:**

- `content_id` references the object store (`.muse/objects/`). This means inserted/deleted/replaced sub-elements are content-addressed and retrievable. No bloat in the delta itself.
- `content_summary` is plugin-computed human-readable text. The core engine uses it verbatim in `muse show`. Plugins are responsible for making it meaningful.
- `PatchOp` is recursive. A MIDI file modification is a `PatchOp` whose `child_delta` contains `InsertOp`/`DeleteOp`/`MoveOp` on individual notes. A genomics sequence modification is a `PatchOp` whose child delta contains nucleotide-level ops.
- `position: int | None` — `None` signals an unordered collection (set semantics). The merge engine in Phase 3 uses this to determine commutativity: two inserts into the same unordered collection always commute; two inserts at the same position in an ordered sequence may conflict.

### 3.3 Updated Plugin Protocol

The `diff` method signature stays the same (`base: StateSnapshot, target: StateSnapshot) -> StateDelta`), but `StateDelta` is now `StructuredDelta`. The protocol docstring must document the expectation:

```python
def diff(self, base: StateSnapshot, target: StateSnapshot) -> StateDelta:
    """Compute the structured delta between two snapshots.

    Returns a ``StructuredDelta`` where ``ops`` is a minimal list of
    operations that transforms ``base`` into ``target``. Plugins should:

    1. Compute ops at the finest granularity they can interpret.
    2. Assign meaningful ``content_summary`` strings to each op.
    3. Store any new sub-element content in the object store if ``repo_root``
       is available; otherwise use deterministic synthetic IDs.
    4. Compute a human-readable ``summary`` across all ops.

    The core engine stores this delta alongside the commit record so that
    ``muse show`` and ``muse diff`` can display it without reloading blobs.
    """
    ...
```

`apply` also changes — it now receives a `StructuredDelta` and must apply its `ops` list:

```python
def apply(self, delta: StateDelta, live_state: LiveState) -> LiveState:
    """Apply a structured delta to produce a new live state.

    Plugins must implement application of all four op types. For plugins
    where full in-memory application is impractical (e.g. large binary
    files), ``live_state`` should be a ``pathlib.Path`` and the plugin
    should apply ops to disk files directly.
    """
    ...
```

### 3.4 Updated `DriftReport`

`DriftReport.delta` is now a `StructuredDelta`. This means `muse status` can display the rich summary:

```python
@dataclass
class DriftReport:
    has_drift: bool
    summary: str = ""
    delta: StateDelta = field(default_factory=lambda: StructuredDelta(
        domain="", ops=[], summary="working tree clean",
    ))
```

### 3.5 Updated `MergeResult`

`MergeResult` adds an `op_log` — the ordered list of operations that produced the merged snapshot, useful for audit and replay:

```python
@dataclass
class MergeResult:
    merged: StateSnapshot
    conflicts: list[str] = field(default_factory=list)
    applied_strategies: dict[str, str] = field(default_factory=dict)
    dimension_reports: dict[str, dict[str, str]] = field(default_factory=dict)
    op_log: list[DomainOp] = field(default_factory=list)  # NEW

    @property
    def is_clean(self) -> bool:
        return len(self.conflicts) == 0
```

### 3.6 Updated Music Plugin

The music plugin's `diff` method must now return a `StructuredDelta`. File-level ops are the minimum bar. The MIDI dimension merge already has the machinery to go deeper — it should produce `PatchOp` entries for modified `.mid` files:

```python
def diff(self, base: StateSnapshot, target: StateSnapshot) -> StateDelta:
    base_files = base["files"]
    target_files = target["files"]
    base_paths = set(base_files)
    target_paths = set(target_files)

    ops: list[DomainOp] = []

    for path in sorted(target_paths - base_paths):
        ops.append(InsertOp(
            op="insert",
            address=path,
            position=None,           # file collection is unordered
            content_id=target_files[path],
            content_summary=f"new file: {path}",
        ))

    for path in sorted(base_paths - target_paths):
        ops.append(DeleteOp(
            op="delete",
            address=path,
            position=None,
            content_id=base_files[path],
            content_summary=f"deleted: {path}",
        ))

    for path in sorted(p for p in base_paths & target_paths
                       if base_files[p] != target_files[p]):
        if path.lower().endswith(".mid"):
            # Attempt deep MIDI diff → PatchOp with note-level child ops
            child_delta = _diff_midi_deep(base_files[path], target_files[path])
            if child_delta is not None:
                ops.append(PatchOp(
                    op="patch",
                    address=path,
                    child_delta=child_delta,
                ))
                continue
        # Fallback: atomic replace
        ops.append(ReplaceOp(
            op="replace",
            address=path,
            position=None,
            old_content_id=base_files[path],
            new_content_id=target_files[path],
            old_summary=f"{path} (prev)",
            new_summary=f"{path} (new)",
        ))

    summary = _summarise_ops(ops)
    return StructuredDelta(domain=_DOMAIN_TAG, ops=ops, summary=summary)
```

`_diff_midi_deep` calls into a new `midi_diff.py` module (sibling of `midi_merge.py`) that runs the Myers LCS algorithm on the MIDI note sequence and returns a `StructuredDelta` with note-level `InsertOp`/`DeleteOp`/`MoveOp` entries.

### 3.7 Serialisation Contract

`StructuredDelta` must remain JSON-serialisable (the core engine stores it in commit records). The recursive `PatchOp.child_delta` is also a `StructuredDelta`, so it serialises naturally. All `content_id` references are SHA-256 hex strings pointing to objects already in the store — no embedded binary.

The commit record format (`CommitRecord`) must add a `structured_delta` field alongside (or replacing) the existing delta storage. This is a commit format change — acceptable since we have no backwards compat requirement.

### 3.8 Phase 1 Test Cases

**New test file: `tests/test_structured_delta.py`**

```
test_insert_op_round_trips_json
test_delete_op_round_trips_json
test_move_op_round_trips_json
test_replace_op_round_trips_json
test_patch_op_with_child_delta_round_trips_json
test_structured_delta_satisfies_state_delta_type
test_music_plugin_diff_returns_structured_delta
test_music_plugin_diff_file_add_produces_insert_op
test_music_plugin_diff_file_remove_produces_delete_op
test_music_plugin_diff_file_modify_produces_replace_op
test_music_plugin_diff_midi_modify_produces_patch_op_with_child_ops
test_drift_report_delta_is_structured_delta
test_muse_show_displays_structured_summary
test_muse_diff_displays_per_op_lines
```

**New test file: `tests/test_midi_diff.py`**

```
test_midi_diff_empty_to_single_note_is_one_insert
test_midi_diff_single_note_to_empty_is_one_delete
test_midi_diff_note_velocity_change_is_replace
test_midi_diff_note_inserted_in_middle_identified_correctly
test_midi_diff_note_transposition_identified_as_replace
test_midi_diff_no_change_returns_empty_ops
test_midi_diff_summary_string_is_human_readable
```

### 3.9 Files Changed in Phase 1

| File | Change |
|---|---|
| `muse/domain.py` | Replace `DeltaManifest`/`StateDelta` with `DomainOp` union + `StructuredDelta`. Update `DriftReport`, `MergeResult`. |
| `muse/core/store.py` | `CommitRecord` gains `structured_delta: StructuredDelta \| None` field. |
| `muse/plugins/music/plugin.py` | `diff()` returns `StructuredDelta`. `apply()` handles `StructuredDelta`. |
| `muse/plugins/music/midi_diff.py` | **New.** Myers LCS on MIDI note sequences → `StructuredDelta`. |
| `muse/cli/commands/show.py` | Display `structured_delta.summary` and per-op lines. |
| `muse/cli/commands/diff.py` | Display structured diff output. |
| `tests/test_structured_delta.py` | **New.** All Phase 1 tests. |
| `tests/test_midi_diff.py` | **New.** MIDI diff algorithm tests. |
| `tests/test_music_plugin.py` | Update to assert `StructuredDelta` return type. |

---

## 4. Phase 2 — Domain Schema & Diff Algorithm Library

**Goal:** Plugins declare their data structure schema. The core engine dispatches to the right diff algorithm automatically. New plugin authors get LCS, tree-edit, and numerical diff for free — no algorithm implementation required.

**Estimated scope:** 3–4 weeks of implementation, 1 week of test writing.

### 4.1 Motivation

After Phase 1, every plugin must still implement its own diff algorithm. A genomics plugin author has to implement Myers LCS to get note-level (nucleotide-level) diffs. A 3D plugin author has to implement tree-edit distance. This is a PhD-level prerequisite for every new domain.

Phase 2 inverts this: the plugin *declares* its data structure, and the core engine drives the right algorithm. The genomics plugin says "my data is an ordered sequence of nucleotides, use LCS" and gets exactly that — for free.

### 4.2 Domain Schema Types

**New file: `muse/core/schema.py`**

```python
"""Domain schema declaration types.

A plugin implements ``schema()`` returning a ``DomainSchema`` to declare
the structural shape of its data. The core engine uses this declaration
to drive the correct diff algorithm, validate delta types, and offer
informed merge conflict messages.
"""
from __future__ import annotations
from typing import Literal, TypedDict


# ---------------------------------------------------------------------------
# Primitive element schemas
# ---------------------------------------------------------------------------

class SequenceSchema(TypedDict):
    """Ordered sequence of homogeneous elements (LCS-diffable)."""
    kind: Literal["sequence"]
    element_type: str              # e.g. "note", "nucleotide", "frame", "voxel"
    identity: Literal["by_id", "by_position", "by_content"]
    diff_algorithm: Literal["lcs", "myers", "patience"]
    # Optional: alphabet constraint for validation
    alphabet: list[str] | None

class TreeSchema(TypedDict):
    """Hierarchical tree structure (tree-edit-diffable)."""
    kind: Literal["tree"]
    node_type: str                 # e.g. "scene_node", "xml_element", "ast_node"
    diff_algorithm: Literal["zhang_shasha", "gumtree"]

class TensorSchema(TypedDict):
    """N-dimensional numerical array (sparse-numerical-diffable)."""
    kind: Literal["tensor"]
    dtype: Literal["float32", "float64", "int8", "int16", "int32", "int64"]
    rank: int                      # number of dimensions
    epsilon: float                 # tolerance: |a - b| < epsilon → "unchanged"
    diff_mode: Literal["sparse", "block", "full"]

class MapSchema(TypedDict):
    """Key-value map with known or dynamic keys."""
    kind: Literal["map"]
    key_type: str
    value_schema: ElementSchema    # recursive
    identity: Literal["by_key"]

class SetSchema(TypedDict):
    """Unordered collection of unique elements (current hash-set approach)."""
    kind: Literal["set"]
    element_type: str
    identity: Literal["by_content", "by_id"]

# The union of all element schema types
ElementSchema = SequenceSchema | TreeSchema | TensorSchema | MapSchema | SetSchema


# ---------------------------------------------------------------------------
# Dimension spec — a named structural sub-dimension
# ---------------------------------------------------------------------------

class DimensionSpec(TypedDict):
    """A named semantic sub-dimension of the domain's state.

    For music: "melodic", "harmonic", "dynamic", "structural".
    For genomics: "exons", "introns", "promoters", "metadata".
    For 3D spatial: "geometry", "materials", "lighting", "animation".

    Each dimension can use a different element schema and diff algorithm.
    The merge engine can merge dimensions independently.
    """
    name: str
    description: str
    schema: ElementSchema
    # Whether conflicts in this dimension block the whole file's merge
    # or are resolved independently.
    independent_merge: bool


# ---------------------------------------------------------------------------
# Top-level domain schema
# ---------------------------------------------------------------------------

class DomainSchema(TypedDict):
    """Complete structural declaration for a domain plugin.

    Returned by ``MuseDomainPlugin.schema()``. The core engine reads this
    once at plugin registration time and uses it to:

    1. Select the correct diff algorithm for each dimension.
    2. Generate typed delta validation.
    3. Provide informed conflict messages (citing dimension names).
    4. Route to CRDT merge if ``merge_mode`` is ``"crdt"`` (Phase 4).
    """
    domain: str
    description: str
    # Dimensions that make up this domain's state.
    # The core engine merges each independently when possible.
    dimensions: list[DimensionSpec]
    # The top-level collection of domain objects (e.g. files, sequences, nodes)
    top_level: ElementSchema
    # Which merge strategy to use at the top level
    merge_mode: Literal["three_way", "crdt"]   # "crdt" is Phase 4
    # Version of the schema format itself (for future migrations)
    schema_version: Literal[1]
```

### 4.3 Schema Method Added to Plugin Protocol

```python
def schema(self) -> DomainSchema:
    """Declare the structural schema of this domain's state.

    The core engine calls this once at startup. Plugins should return a
    stable, deterministic ``DomainSchema``. This declaration drives diff
    algorithm selection, delta validation, and conflict messaging.

    See ``muse.core.schema`` for all available element schema types.
    """
    ...
```

### 4.4 Example Schema Declarations

**Music plugin:**

```python
def schema(self) -> DomainSchema:
    return DomainSchema(
        domain="music",
        description="MIDI and audio file versioning with note-level diff",
        top_level=SetSchema(
            kind="set",
            element_type="audio_file",
            identity="by_content",
        ),
        dimensions=[
            DimensionSpec(
                name="melodic",
                description="Note pitches and durations over time",
                schema=SequenceSchema(
                    kind="sequence",
                    element_type="note_event",
                    identity="by_position",
                    diff_algorithm="lcs",
                    alphabet=None,
                ),
                independent_merge=True,
            ),
            DimensionSpec(
                name="harmonic",
                description="Chord progressions and key signatures",
                schema=SequenceSchema(
                    kind="sequence",
                    element_type="chord_event",
                    identity="by_position",
                    diff_algorithm="lcs",
                    alphabet=None,
                ),
                independent_merge=True,
            ),
            DimensionSpec(
                name="dynamic",
                description="Velocity and expression curves",
                schema=TensorSchema(
                    kind="tensor",
                    dtype="float32",
                    rank=1,
                    epsilon=1.0,     # velocities are integers 0–127; 1.0 tolerance
                    diff_mode="sparse",
                ),
                independent_merge=True,
            ),
            DimensionSpec(
                name="structural",
                description="Track layout, time signatures, tempo map",
                schema=TreeSchema(
                    kind="tree",
                    node_type="track_node",
                    diff_algorithm="zhang_shasha",
                ),
                independent_merge=False,   # structural changes block all dimensions
            ),
        ],
        merge_mode="three_way",
        schema_version=1,
    )
```

**Hypothetical genomics plugin:**

```python
def schema(self) -> DomainSchema:
    return DomainSchema(
        domain="genomics",
        description="DNA/RNA sequence versioning at nucleotide resolution",
        top_level=MapSchema(
            kind="map",
            key_type="sequence_id",   # e.g. chromosome name
            value_schema=SequenceSchema(
                kind="sequence",
                element_type="nucleotide",
                identity="by_position",
                diff_algorithm="myers",
                alphabet=["A", "T", "C", "G", "U", "N", "-"],
            ),
            identity="by_key",
        ),
        dimensions=[
            DimensionSpec(
                name="coding_regions",
                description="Exons and coding sequence",
                schema=SequenceSchema(
                    kind="sequence",
                    element_type="nucleotide",
                    identity="by_position",
                    diff_algorithm="myers",
                    alphabet=["A", "T", "C", "G", "N"],
                ),
                independent_merge=True,
            ),
            DimensionSpec(
                name="regulatory",
                description="Promoters, enhancers, splice sites",
                schema=SetSchema(
                    kind="set",
                    element_type="regulatory_element",
                    identity="by_id",
                ),
                independent_merge=True,
            ),
            DimensionSpec(
                name="metadata",
                description="Annotations, quality scores, provenance",
                schema=MapSchema(
                    kind="map",
                    key_type="annotation_key",
                    value_schema=TensorSchema(
                        kind="tensor",
                        dtype="float32",
                        rank=1,
                        epsilon=0.001,
                        diff_mode="sparse",
                    ),
                    identity="by_key",
                ),
                independent_merge=True,
            ),
        ],
        merge_mode="three_way",
        schema_version=1,
    )
```

These declarations are roughly 30 lines each and require zero algorithm knowledge from the plugin author. The core engine does the rest.

### 4.5 Diff Algorithm Library

**New directory: `muse/core/diff_algorithms/`**

```
muse/core/diff_algorithms/
    __init__.py        → dispatch function
    lcs.py             → Myers / patience diff for ordered sequences
    tree_edit.py       → Zhang-Shasha tree edit distance
    numerical.py       → sparse numerical diff for tensors
    set_ops.py         → hash-set algebra (current approach, extracted)
```

**`muse/core/diff_algorithms/__init__.py`** — schema-driven dispatch:

```python
def diff_by_schema(
    schema: ElementSchema,
    base: SequenceData | TreeData | TensorData | SetData | MapData,
    target: SequenceData | TreeData | TensorData | SetData | MapData,
    *,
    domain: str,
    address: str = "",
) -> StructuredDelta:
    """Dispatch to the correct diff algorithm based on ``schema.kind``."""
    match schema["kind"]:
        case "sequence":
            return lcs.diff(schema, base, target, domain=domain, address=address)
        case "tree":
            return tree_edit.diff(schema, base, target, domain=domain, address=address)
        case "tensor":
            return numerical.diff(schema, base, target, domain=domain, address=address)
        case "set":
            return set_ops.diff(schema, base, target, domain=domain, address=address)
        case "map":
            return _diff_map(schema, base, target, domain=domain, address=address)
```

**`muse/core/diff_algorithms/lcs.py`** — Myers diff algorithm:

The Myers diff algorithm (the same one Git uses) finds the shortest edit script between two sequences. For ordered sequences, it gives minimal inserts and deletes. For moves, a post-processing pass detects delete+insert pairs of the same element.

Key functions:
- `myers_ses(base: list[T], target: list[T]) -> list[EditOp]` — shortest edit script
- `detect_moves(inserts: list[InsertOp], deletes: list[DeleteOp]) -> list[MoveOp]` — post-process
- `diff(schema: SequenceSchema, base: list[T], target: list[T], ...) -> StructuredDelta`

The patience diff variant (used by some Git backends) gives better results when sequences have many repeated elements — expose it as an option.

**`muse/core/diff_algorithms/tree_edit.py`** — Zhang-Shasha:

Zhang-Shasha computes the minimum edit distance between two labeled ordered trees. Operations: relabel (→ ReplaceOp), insert, delete. The algorithm is O(n²m) where n, m are tree sizes — acceptable for domain objects up to ~10k nodes.

Key functions:
- `zhang_shasha(base: TreeNode, target: TreeNode) -> list[TreeEditOp]` — edit script
- `diff(schema: TreeSchema, base: TreeNode, target: TreeNode, ...) -> StructuredDelta`

**`muse/core/diff_algorithms/numerical.py`** — sparse tensor diff:

For numerical arrays (simulation state, velocity curves, weight matrices), exact byte comparison is wrong — floating-point drift doesn't constitute a meaningful change. The numerical diff:

1. Compares element-wise with `schema.epsilon` tolerance.
2. Returns `ReplaceOp` only for elements where `|base[i] - target[i]| >= epsilon`.
3. In `"sparse"` mode: emits one `ReplaceOp` per changed element (good for sparse changes).
4. In `"block"` mode: groups adjacent changes into contiguous range ops (good for dense changes).
5. In `"full"` mode: emits a single `ReplaceOp` for the entire array if anything changed (fallback for very large tensors where element-wise ops are too expensive).

**`muse/core/diff_algorithms/set_ops.py`** — extracted from current code:

The current hash-set algebra pulled into a pure function that returns a `StructuredDelta`. No algorithmic change — this is refactoring to put existing logic into the new common library.

### 4.6 Plugin Registry Gains Schema Lookup

**`muse/core/plugin_registry.py`** gains a `schema_for(domain: str) -> DomainSchema | None` function. This allows the CLI and merge engine to look up a domain's schema without having a plugin instance.

### 4.7 Phase 2 Test Cases

**New test file: `tests/test_diff_algorithms.py`**

```
# LCS / Myers
test_lcs_empty_to_sequence_is_all_inserts
test_lcs_sequence_to_empty_is_all_deletes
test_lcs_identical_sequences_returns_no_ops
test_lcs_single_insert_in_middle
test_lcs_single_delete_in_middle
test_lcs_move_detected_from_delete_plus_insert
test_lcs_transposition_of_two_elements
test_lcs_patience_mode_handles_repeated_elements
test_lcs_produces_valid_structured_delta

# Tree edit
test_tree_edit_leaf_relabel_is_replace
test_tree_edit_node_insert
test_tree_edit_node_delete
test_tree_edit_subtree_move
test_tree_edit_identical_trees_returns_no_ops
test_tree_edit_produces_valid_structured_delta

# Numerical
test_numerical_within_epsilon_returns_no_ops
test_numerical_outside_epsilon_returns_replace
test_numerical_sparse_mode_one_op_per_element
test_numerical_block_mode_groups_adjacent
test_numerical_full_mode_single_op
test_numerical_produces_valid_structured_delta

# Set ops
test_set_diff_add_returns_insert
test_set_diff_remove_returns_delete
test_set_diff_no_change_returns_empty
test_set_diff_produces_valid_structured_delta

# Schema dispatch
test_dispatch_sequence_schema_calls_lcs
test_dispatch_tree_schema_calls_zhang_shasha
test_dispatch_tensor_schema_calls_numerical
test_dispatch_set_schema_calls_set_ops
test_dispatch_map_schema_recurses
```

**New test file: `tests/test_domain_schema.py`**

```
test_music_plugin_schema_returns_domain_schema
test_music_plugin_schema_has_four_dimensions
test_music_plugin_schema_melodic_dimension_is_sequence
test_music_plugin_schema_structural_dimension_is_tree
test_music_plugin_schema_dynamic_dimension_is_tensor
test_schema_round_trips_json
test_schema_version_is_1
test_plugin_registry_schema_lookup
```

### 4.8 Files Changed in Phase 2

| File | Change |
|---|---|
| `muse/core/schema.py` | **New.** All schema `TypedDict` types. |
| `muse/core/diff_algorithms/__init__.py` | **New.** Schema-driven dispatch. |
| `muse/core/diff_algorithms/lcs.py` | **New.** Myers + patience diff. |
| `muse/core/diff_algorithms/tree_edit.py` | **New.** Zhang-Shasha implementation. |
| `muse/core/diff_algorithms/numerical.py` | **New.** Sparse/block/full tensor diff. |
| `muse/core/diff_algorithms/set_ops.py` | **New.** Extracted from `merge_engine.py`. |
| `muse/domain.py` | Add `schema()` to `MuseDomainPlugin` protocol. |
| `muse/core/plugin_registry.py` | Add `schema_for(domain) -> DomainSchema \| None`. |
| `muse/plugins/music/plugin.py` | Implement `schema()` returning full `DomainSchema`. `diff()` dispatches through `diff_by_schema`. |
| `tests/test_diff_algorithms.py` | **New.** |
| `tests/test_domain_schema.py` | **New.** |

---

## 5. Phase 3 — Operation-Level Merge Engine

**Goal:** The core merge engine reasons over `DomainOp` trees, not just path sets. Two operations that touch non-overlapping positions auto-merge without conflict. The commutativity rules are uniform across all domains.

**Estimated scope:** 4–6 weeks (this is the hardest phase).

### 5.1 Motivation

After Phase 2, we can produce rich structured deltas and display them beautifully. But the merge engine still detects conflicts at file-path granularity. Two agents inserting notes at bar 12 and bar 45 respectively still produce a "conflict" even though their changes commute perfectly.

Phase 3 fixes this by making the merge engine reason over operations directly. This is operational transformation (OT) — the theory behind Google Docs' real-time collaborative editing, applied to version-controlled multidimensional state.

### 5.2 Commutativity Rules

Two operations `A` and `B` commute (can be auto-merged) if and only if applying them in any order produces the same result. The rules are:

| Op A | Op B | Commute? | Condition |
|---|---|---|---|
| `InsertOp(pos=i)` | `InsertOp(pos=j)` | **Yes** | `i ≠ j` (different positions) |
| `InsertOp(pos=i)` | `InsertOp(pos=i)` | **No** | Same position — positional conflict |
| `InsertOp` | `DeleteOp` | **No** | Unless different subtrees |
| `DeleteOp(addr=A)` | `DeleteOp(addr=B)` | **Yes** | `A ≠ B` |
| `DeleteOp(addr=A)` | `DeleteOp(addr=A)` | **Yes** | Consensus delete — clean |
| `ReplaceOp(addr=A)` | `ReplaceOp(addr=B)` | **Yes** | `A ≠ B` |
| `ReplaceOp(addr=A)` | `ReplaceOp(addr=A)` | **No** | Same address — value conflict |
| `MoveOp(from=i)` | `MoveOp(from=j)` | **Yes** | `i ≠ j` |
| `MoveOp(from=i)` | `DeleteOp(pos=i)` | **No** | Move-delete conflict |
| `PatchOp(addr=A)` | `PatchOp(addr=B)` | **Yes** | `A ≠ B` — recurse on children |
| `PatchOp(addr=A)` | `PatchOp(addr=A)` | Recurse | Check child ops for conflicts |

For unordered collections (`position=None`), inserts always commute with other inserts. For ordered sequences, inserts at the same position do NOT commute — this is a genuine conflict that requires resolution.

### 5.3 Operation Transformer Functions

**New file: `muse/core/op_transform.py`**

```python
"""Operational transformation for Muse domain operations.

Implements the commutativity rules that let the merge engine determine
which operation pairs can be auto-merged and which are true conflicts.

The public API is:

- ``ops_commute(a, b)`` — True if ops A and B can be applied in any order.
- ``transform(a, b)`` — Return a', b' such that applying a then b' = applying b then a'.
- ``merge_op_lists(base_ops, ours_ops, theirs_ops)`` → MergeOpsResult
"""
from __future__ import annotations
from dataclasses import dataclass, field
from muse.domain import DomainOp, InsertOp, DeleteOp, MoveOp, ReplaceOp, PatchOp


@dataclass
class MergeOpsResult:
    """Result of merging two operation lists against a common base."""
    merged_ops: list[DomainOp] = field(default_factory=list)
    conflict_ops: list[tuple[DomainOp, DomainOp]] = field(default_factory=list)

    @property
    def is_clean(self) -> bool:
        return len(self.conflict_ops) == 0


def ops_commute(a: DomainOp, b: DomainOp) -> bool:
    """Return True if operations A and B commute (auto-mergeable)."""
    ...

def transform(a: DomainOp, b: DomainOp) -> tuple[DomainOp, DomainOp]:
    """Return (a', b') such that a ∘ b' = b ∘ a'.

    This is the core OT transform function. When two operations a and b
    are generated concurrently against the same base, transform returns
    adjusted versions that can be applied sequentially to achieve the same
    final state.
    """
    ...

def merge_op_lists(
    base_ops: list[DomainOp],
    ours_ops: list[DomainOp],
    theirs_ops: list[DomainOp],
) -> MergeOpsResult:
    """Three-way merge at operation granularity.

    Applies commutativity rules to detect which pairs of operations truly
    conflict. Non-conflicting pairs are auto-merged by applying OT transform.
    Conflicting pairs are collected in ``conflict_ops`` for plugin resolution.
    """
    ...
```

### 5.4 Updated Core Merge Engine

`muse/core/merge_engine.py` gains a new entry point:

```python
def merge_structured(
    base_delta: StructuredDelta,
    ours_delta: StructuredDelta,
    theirs_delta: StructuredDelta,
) -> MergeOpsResult:
    """Merge two structured deltas against a common base delta.

    Uses ``op_transform.merge_op_lists`` for operation-level conflict
    detection. Falls back to file-level path detection for ops that do
    not carry position information (e.g. SetSchema domains).
    """
    from muse.core.op_transform import merge_op_lists
    return merge_op_lists(base_delta["ops"], ours_delta["ops"], theirs_delta["ops"])
```

The existing `diff_snapshots` / `detect_conflicts` / `apply_merge` functions remain for plugins that have not yet produced `StructuredDelta` from `diff()` — they serve as the fallback.

### 5.5 Plugin Protocol Gains `merge_ops`

A new optional method on the protocol (not required — the core engine falls back to file-level merge if absent):

```python
def merge_ops(
    self,
    base: StateSnapshot,
    ours_ops: list[DomainOp],
    theirs_ops: list[DomainOp],
    *,
    repo_root: pathlib.Path | None = None,
) -> MergeResult:
    """Merge two op lists against base, using domain-specific conflict resolution.

    The core engine calls this when both branches have produced
    ``StructuredDelta`` from ``diff()``. The plugin may use
    ``muse.core.op_transform.merge_op_lists`` as the foundation and
    add domain-specific resolution on top (e.g. checking ``.museattributes``).

    If not implemented, the core engine falls back to the existing
    three-way file-level merge via ``merge()``.
    """
    ...
```

### 5.6 Position Adjustment After Transform

A critical detail: when two inserts commute because they're at different positions, the positions of later-applied operations must be adjusted. This is the "index shifting" problem in OT:

```
Base: [A, B, C]
Ours: insert X at position 1 → [A, X, B, C]
Theirs: insert Y at position 2 → [A, B, Y, C]

After transform:
  ours' (applied after theirs): insert X at position 1 → [A, X, B, Y, C]  ✓
  theirs' (applied after ours): insert Y at position 3 → [A, X, B, Y, C]  ✓
```

The transform function must adjust positions for all sequence operations. This is well-understood in OT literature but requires care in implementation, particularly for interleaved inserts and deletes.

### 5.7 Phase 3 Test Cases

**New test file: `tests/test_op_transform.py`**

```
# Commutativity oracle
test_commute_inserts_at_different_positions_is_true
test_commute_inserts_at_same_position_is_false
test_commute_deletes_at_different_addresses_is_true
test_commute_consensus_delete_is_true
test_commute_replaces_at_different_addresses_is_true
test_commute_replaces_at_same_address_is_false
test_commute_move_and_delete_same_position_is_false
test_commute_patch_at_different_addresses_is_true
test_commute_patch_at_same_address_recurses_children

# OT transform function
test_transform_two_inserts_adjusts_positions_correctly
test_transform_insert_and_delete_produces_adjusted_ops
test_transform_identity_when_ops_commute

# Three-way merge
test_merge_op_lists_clean_non_overlapping
test_merge_op_lists_same_op_both_sides_is_idempotent
test_merge_op_lists_conflict_same_position_insert
test_merge_op_lists_conflict_same_address_replace
test_merge_op_lists_consensus_delete_both_sides
test_merge_op_lists_nested_patch_recurses
test_merge_op_lists_position_adjustment_cascades
test_merge_op_lists_empty_one_side_applies_other
test_merge_op_lists_both_empty_returns_base

# Integration: merge engine uses op_transform when structured deltas available
test_merge_engine_uses_op_transform_for_structured_deltas
test_merge_engine_falls_back_to_file_level_without_structured_deltas
test_full_merge_non_overlapping_note_inserts_auto_merges
test_full_merge_same_note_insert_produces_conflict
```

### 5.8 Files Changed in Phase 3

| File | Change |
|---|---|
| `muse/core/op_transform.py` | **New.** `ops_commute`, `transform`, `merge_op_lists`. |
| `muse/core/merge_engine.py` | Add `merge_structured()`. Fallback logic preserved. |
| `muse/domain.py` | Add optional `merge_ops()` to `MuseDomainPlugin` protocol. |
| `muse/plugins/music/plugin.py` | Implement `merge_ops()` using `op_transform`. |
| `tests/test_op_transform.py` | **New.** |
| `tests/test_core_merge_engine.py` | Add structured-delta merge tests. |

---

## 6. Phase 4 — CRDT Semantics for Convergent Multi-Agent Writes

**Goal:** Plugin authors can opt into CRDT (Conflict-free Replicated Data Type) semantics. Merge becomes a mathematical `join` on a lattice. No conflict state ever exists. Millions of agents can write concurrently and always converge.

**Estimated scope:** 6–8 weeks (significant distributed systems work).

### 6.1 Motivation

Phases 1–3 give you an extremely powerful three-way merge system. But three-way merge has a fundamental limit: it requires a common ancestor (merge base). In a world of millions of concurrent agents writing to shared state across unreliable networks, finding and coordinating around a merge base is expensive and sometimes impossible.

CRDTs eliminate this: given any two replicas of a CRDT data structure, the `join` operation produces a deterministic merged state — no base required, no conflicts possible, no coordination needed. This is mathematically guaranteed by the lattice laws (commutativity, associativity, idempotency of join).

This is the endgame for the multi-agent scenario.

### 6.2 CRDT Primitive Library

**New directory: `muse/core/crdts/`**

```
muse/core/crdts/
    __init__.py
    lww_register.py    → Last-Write-Wins Register
    or_set.py          → Observed-Remove Set
    rga.py             → Replicated Growable Array (ordered sequences)
    aw_map.py          → Add-Wins Map
    g_counter.py       → Grow-only Counter
    vclock.py          → Vector Clock (causal ordering)
```

**`muse/core/crdts/lww_register.py`** — Last-Write-Wins Register:

Stores a single value with a timestamp. `join` takes the value with the higher timestamp. Appropriate for scalar config values, metadata, labels. Requires a reliable wall clock or logical clock for correct behavior.

```python
class LWWValue(TypedDict):
    value: str          # JSON-serialisable value
    timestamp: float    # Unix timestamp or logical clock
    author: str         # Agent ID for tiebreaking

class LWWRegister:
    """A register where the last write (by timestamp) wins on merge."""
    def read(self) -> str: ...
    def write(self, value: str, timestamp: float, author: str) -> None: ...
    def join(self, other: LWWRegister) -> LWWRegister: ...  # convergent merge
    def to_dict(self) -> LWWValue: ...
    @classmethod
    def from_dict(cls, data: LWWValue) -> LWWRegister: ...
```

**`muse/core/crdts/or_set.py`** — Observed-Remove Set:

An unordered set where adds always win over concurrent removes (the "add-wins" property). Each element carries a unique tag set; removing requires knowing the tags of the current observed value. Safe for sets of domain objects.

**`muse/core/crdts/rga.py`** — Replicated Growable Array:

The RGA (Replicated Growable Array) is a CRDT for ordered sequences — the mathematical foundation of collaborative text editing. Each element carries a unique identifier (timestamp + author). Concurrent inserts at the same position are resolved deterministically by author ID. This gives you Google Docs-style collaborative editing semantics for any ordered sequence domain.

```python
class RGAElement(TypedDict):
    id: str             # stable unique ID: f"{timestamp}@{author}"
    value: str          # content hash of element (references object store)
    deleted: bool       # tombstone — never actually removed, marked deleted

class RGA:
    """Replicated Growable Array — CRDT for ordered sequences."""
    def insert(self, after_id: str | None, element: RGAElement) -> None: ...
    def delete(self, element_id: str) -> None: ...
    def join(self, other: RGA) -> RGA: ...  # always succeeds, no conflicts
    def to_sequence(self) -> list[str]: ...  # materialise visible elements
    def to_dict(self) -> list[RGAElement]: ...
    @classmethod
    def from_dict(cls, data: list[RGAElement]) -> RGA: ...
```

**`muse/core/crdts/vclock.py`** — Vector Clock:

Required for causal ordering in distributed multi-agent scenarios. A vector clock tracks how many events each agent has seen, enabling detection of concurrent vs. causally-ordered writes. Necessary for correct LWW behavior and for RGA tie-breaking.

```python
class VectorClock:
    """Causal clock for distributed agent writes."""
    def increment(self, agent_id: str) -> None: ...
    def merge(self, other: VectorClock) -> VectorClock: ...
    def happens_before(self, other: VectorClock) -> bool: ...
    def concurrent_with(self, other: VectorClock) -> bool: ...
    def to_dict(self) -> dict[str, int]: ...
    @classmethod
    def from_dict(cls, data: dict[str, int]) -> VectorClock: ...
```

### 6.3 CRDT-Aware Snapshot Format

When a plugin uses CRDT semantics, the `SnapshotManifest` carries additional metadata:

```python
class CRDTSnapshotManifest(TypedDict):
    """Extended snapshot for CRDT-mode plugins."""
    files: dict[str, str]          # path → content hash (as before)
    domain: str
    vclock: dict[str, int]         # vector clock at snapshot time
    crdt_state: dict[str, str]     # path → CRDT state hash (separate from content)
    schema_version: Literal[1]
```

The `crdt_state` stores the CRDT metadata (tombstones, element IDs, timestamps) separately from the content hashes. This keeps the content-addressed object store valid while allowing CRDT state to accumulate.

### 6.4 CRDTPlugin Protocol Extension

```python
class CRDTPlugin(MuseDomainPlugin, Protocol):
    """Extension of MuseDomainPlugin for CRDT-mode domains.

    Plugins implementing this protocol get convergent merge semantics:
    ``merge()`` is replaced by ``join()``, which always succeeds.
    """

    def crdt_schema(self) -> CRDTSchema:
        """Declare the CRDT types used for each dimension."""
        ...

    def join(
        self,
        a: CRDTSnapshotManifest,
        b: CRDTSnapshotManifest,
    ) -> CRDTSnapshotManifest:
        """Merge two snapshots by computing their lattice join.

        This operation is:
        - Commutative: join(a, b) = join(b, a)
        - Associative: join(join(a, b), c) = join(a, join(b, c))
        - Idempotent: join(a, a) = a

        These three properties guarantee convergence regardless of message
        order or delivery count.
        """
        ...

    def to_crdt_state(self, snapshot: StateSnapshot) -> CRDTSnapshotManifest:
        """Lift a plain snapshot into CRDT state representation."""
        ...

    def from_crdt_state(self, crdt: CRDTSnapshotManifest) -> StateSnapshot:
        """Materialise a CRDT state back to a plain snapshot."""
        ...
```

### 6.5 When to Use CRDT Mode

| Scenario | Recommendation |
|---|---|
| Human-paced commits (once per hour/day) | Three-way merge (Phases 1–3) — richer conflict resolution |
| Many agents writing concurrently (once per second) | CRDT mode — no coordination needed |
| Mix (some slow human commits, some fast agent writes) | CRDT mode with LWW per-dimension |
| Simulation state frames (sequential, one writer) | Three-way merge |
| Shared genomics annotation (many simultaneous annotators) | CRDT OR-Set for annotation set |
| Collaborative score editing (DAW-style) | CRDT RGA for note sequences |

The `DomainSchema.merge_mode` field controls which path the core engine takes. A plugin can declare `merge_mode: "crdt"` for some dimensions and fall back to `"three_way"` for others.

### 6.6 Phase 4 Test Cases

**New test file: `tests/test_crdts.py`**

```
# LWWRegister
test_lww_later_timestamp_wins
test_lww_same_timestamp_author_tiebreak
test_lww_join_is_commutative
test_lww_join_is_associative
test_lww_join_is_idempotent

# ORSet
test_or_set_add_survives_concurrent_remove
test_or_set_remove_observed_element_works
test_or_set_join_is_commutative
test_or_set_join_is_associative
test_or_set_join_is_idempotent

# RGA
test_rga_insert_after_none_is_prepend
test_rga_insert_at_end
test_rga_delete_marks_tombstone
test_rga_concurrent_insert_same_position_deterministic
test_rga_join_is_commutative
test_rga_join_is_associative
test_rga_join_is_idempotent
test_rga_to_sequence_excludes_tombstones
test_rga_round_trip_to_from_dict

# VectorClock
test_vclock_increment_own_agent
test_vclock_merge_takes_max_per_agent
test_vclock_happens_before_simple
test_vclock_concurrent_with_neither_dominates
test_vclock_idempotent_merge

# CRDTPlugin integration
test_crdt_plugin_join_produces_crdt_snapshot
test_crdt_plugin_join_commutes
test_crdt_join_via_core_merge_engine_uses_crdt_path
test_crdt_merge_never_produces_conflicts
```

### 6.7 Files Changed in Phase 4

| File | Change |
|---|---|
| `muse/core/crdts/__init__.py` | **New.** |
| `muse/core/crdts/lww_register.py` | **New.** |
| `muse/core/crdts/or_set.py` | **New.** |
| `muse/core/crdts/rga.py` | **New.** |
| `muse/core/crdts/aw_map.py` | **New.** |
| `muse/core/crdts/g_counter.py` | **New.** |
| `muse/core/crdts/vclock.py` | **New.** |
| `muse/domain.py` | Add `CRDTPlugin` protocol, `CRDTSnapshotManifest`. |
| `muse/core/schema.py` | Add `CRDTSchema`. `DomainSchema.merge_mode` supports `"crdt"`. |
| `muse/core/merge_engine.py` | Route to CRDT `join` when `merge_mode == "crdt"`. |
| `tests/test_crdts.py` | **New.** |

---

## 7. Cross-Cutting Concerns

### 7.1 Content-Addressed Object Store Compatibility

The object store (`.muse/objects/SHA-256`) requires no changes through all four phases. Phase 1's sub-element `content_id` fields in operation types reference objects already in the store. This means:

- Any binary element (a note, a nucleotide block, a mesh vertex group) stored via `write_object(repo_root, sha256, bytes)` is automatically deduplicated.
- The store scales to millions of fine-grained sub-elements without format changes.
- Pack files (future work) can be added without changing the protocol.

### 7.2 Commit Record Format Evolution

Each phase adds fields to `CommitRecord`. The commit record must carry a `format_version` field so future readers can understand what they're looking at:

```python
class CommitRecord(TypedDict):
    commit_id: str
    snapshot_id: str
    parent_commit_id: str | None
    parent2_commit_id: str | None
    message: str
    author: str
    committed_at: str
    domain: str
    structured_delta: StructuredDelta | None    # Phase 1
    format_version: Literal[1, 2, 3, 4]        # tracks schema changes
```

Since we have no backwards compat requirement, `format_version` starts at `1` and each phase that changes the record bumps it. Old records without new fields are read with `None` defaults.

### 7.3 Wire Format for Agent-to-Agent Communication

Phase 4 introduces the scenario of multiple agents writing concurrently. This requires a wire format for exchanging operations and CRDT state. All types used (`StructuredDelta`, `CRDTSnapshotManifest`, `VectorClock`) are `TypedDict` and JSON-serialisable by design — this was deliberate. Any transport (HTTP, message queue, filesystem sync) can carry them without additional serialisation work.

### 7.4 Typing Constraints

All new types must satisfy the zero-`Any` constraint enforced by `tools/typing_audit.py`. Key design decisions that ensure this:

- `DomainOp = InsertOp | DeleteOp | MoveOp | ReplaceOp | PatchOp` — exhaustive union, no `Any`.
- `ElementSchema = SequenceSchema | TreeSchema | TensorSchema | MapSchema | SetSchema` — same.
- `PatchOp.child_delta: StructuredDelta` — the recursive field is typed, not `dict[str, Any]`.
- All CRDT types carry concrete generic parameters.

The `match` statement in `diff_by_schema` uses exhaustive `case` matching on `schema["kind"]` literals — mypy can verify exhaustiveness.

### 7.5 Synchronous I/O Constraint

All algorithm implementations must remain synchronous. The LCS, tree-edit, and CRDT algorithms are all CPU-bound and complete in bounded time for bounded input sizes. No `async`, no `await`, no `asyncio` anywhere. If a domain's data is too large to diff synchronously, the plugin should chunk it — this is a domain concern, not a core engine concern.

---

## 8. Test Strategy

### 8.1 Test Pyramid

```
                    ┌─────────┐
                    │  E2E CLI │  (slow, few — cover user-visible behaviors)
                  ┌─┴─────────┴─┐
                  │ Integration  │  (medium — real components wired together)
                ┌─┴─────────────┴─┐
                │      Unit        │  (fast, many — every function in isolation)
                └──────────────────┘
```

Every new function gets a unit test before implementation (TDD). Every new interaction between two modules gets an integration test. Every new CLI behavior gets an E2E test.

### 8.2 Property-Based Testing for Algorithms

Correctness of LCS, tree-edit, and CRDTs is best verified with property-based tests (using `hypothesis`). Key properties:

**LCS:**
- Round-trip: `apply(base, diff(base, target)) == target` for all inputs
- Minimality: `len(diff(a, b).ops) <= len(a) + len(b)` (LCS is minimal)
- Identity: `diff(a, a).ops == []`

**CRDT lattice laws (all must hold for all inputs):**
- Commutativity: `join(a, b) == join(b, a)`
- Associativity: `join(join(a, b), c) == join(a, join(b, c))`
- Idempotency: `join(a, a) == a`

**OT transform (Phase 3):**
- Diamond property: `apply(apply(base, a), transform(a, b)[1]) == apply(apply(base, b), transform(a, b)[0])`

### 8.3 Regression Test Naming Convention

Every bug fix gets a regression test named:
```
test_<what_broke>_<correct_behavior>
```

Example: `test_concurrent_note_insert_same_bar_does_not_lose_notes`

### 8.4 Test Isolation

All tests must be hermetic — no shared mutable state, no real filesystem without `tmp_path` fixture. Algorithm tests (LCS, tree-edit, CRDT) are purely in-memory and have no filesystem dependencies at all.

---

## 9. Implementation Order and Dependencies

```
Phase 1 ──────────────────────────────────────────────────────────► Phase 2
  │  Typed delta algebra                                               │
  │  StructuredDelta replaces DeltaManifest                           │
  │  Music plugin: file→InsertOp, MIDI→PatchOp                        │
  │  midi_diff.py (LCS on note sequences)                             │
  │                                                                    │
  │  DEPENDS ON: nothing (self-contained)                             │  Domain schema declaration
  │                                                                    │  diff_algorithms/ library
  ▼                                                                    │  schema() on protocol
Phase 3 ◄────────────────────────────────────────────────────────────┘
  │  Operation-level merge engine
  │  ops_commute(), transform(), merge_op_lists()
  │  Core engine routes to op_transform when StructuredDelta available
  │
  │  DEPENDS ON: Phase 1 (needs StructuredDelta) + Phase 2 (needs position metadata)
  │
  ▼
Phase 4
  CRDT primitive library (LWWRegister, ORSet, RGA, AWMap, VectorClock)
  CRDTPlugin protocol extension
  Core merge engine: merge_mode == "crdt" → join()

  DEPENDS ON: Phase 1–3 complete
```

**Critical path:** Phase 1 → Phase 2 → Phase 3 → Phase 4. Each phase requires the previous. Do not skip phases or reorder.

**Parallel work possible within phases:**
- Phase 1: `midi_diff.py` can be implemented in parallel with the type system changes.
- Phase 2: `lcs.py`, `tree_edit.py`, `numerical.py` can be implemented in parallel.
- Phase 4: All CRDT types (`rga.py`, `or_set.py`, etc.) are independent and can be built in parallel.

### 9.1 Rough Timeline

| Phase | Calendar estimate | Primary difficulty |
|---|---|---|
| Phase 1 | 2–3 weeks | Protocol change propagation; Myers LCS for MIDI |
| Phase 2 | 3–4 weeks | Zhang-Shasha implementation; schema dispatch typing |
| Phase 3 | 4–6 weeks | OT transform correctness; position adjustment cascades |
| Phase 4 | 6–8 weeks | CRDT correctness proofs; vector clock integration |
| **Total** | **15–21 weeks** | |

Phase 3 is the hardest. The OT transform correctness for all op-pair combinations is subtle and requires exhaustive property testing with `hypothesis`. Budget extra time there.

### 9.2 Definition of Done Per Phase

**Phase 1 done when:**
- [ ] `DeltaManifest` is gone; `StructuredDelta` is the only `StateDelta`
- [ ] Music plugin's `diff()` returns `StructuredDelta` with `PatchOp` for `.mid` files
- [ ] `muse show <commit>` displays note-level diff summary
- [ ] `mypy muse/` zero errors
- [ ] `python tools/typing_audit.py --dirs muse/ tests/ --max-any 0` zero violations
- [ ] All new test cases pass

**Phase 2 done when:**
- [ ] `schema()` is on the `MuseDomainPlugin` protocol
- [ ] Music plugin returns a complete `DomainSchema`
- [ ] `diff_algorithms/` contains LCS, tree-edit, numerical, set implementations
- [ ] All four algorithms have property-based tests passing
- [ ] `diff_by_schema` dispatch is exhaustively typed (no `Any`, mypy verified)

**Phase 3 done when:**
- [ ] `ops_commute()` correctly handles all 25 op-pair combinations
- [ ] `transform()` passes the diamond property for all commuting pairs
- [ ] Music plugin: inserting notes at non-overlapping bars never conflicts
- [ ] Core merge engine uses `merge_op_lists` when `StructuredDelta` is available
- [ ] Property tests verify OT correctness on randomly generated op sequences

**Phase 4 done when:**
- [ ] All five CRDT types pass the three lattice laws (property tests via `hypothesis`)
- [ ] `VectorClock` correctly identifies concurrent vs. causally-ordered events
- [ ] A music plugin in CRDT mode never produces a `MergeResult.conflicts` entry
- [ ] The core merge engine's CRDT path is exercised by integration tests
- [ ] `CRDTPlugin` protocol is verified by a `runtime_checkable` assertion

---

*End of plan. Implementation begins at Phase 1. Each phase produces a PR against `dev` with its own verification checklist completed.*

# Muse Plugin Authoring Guide

> A complete walkthrough for building a domain plugin for Muse v0.1.1. By the end
> you will have a fully typed, schema-aware, OT-capable, CRDT-ready plugin that
> works with every `muse` CLI command immediately — no core changes needed.
>
> **Difficulty progression:** Core Protocol (30 min) → Domain Schema (30 min) → OT Merge (1 hr) → CRDT Semantics (1 hr)

---

## Table of Contents

1. [What a Plugin Is](#what-a-plugin-is)
2. [Quick Start — Copy the Scaffold](#quick-start--copy-the-scaffold)
3. [Core Protocol (Required)](#phase-1--core-protocol-required)
4. [Domain Schema](#phase-2--domain-schema)
5. [Operation-Level Merge (OT)](#phase-3--operation-level-merge)
6. [CRDT Semantics](#phase-4--crdt-semantics)
7. [Registering Your Plugin](#registering-your-plugin)
8. [Testing Your Plugin](#testing-your-plugin)
9. [Checklist Before You Ship](#checklist-before-you-ship)

---

## What a Plugin Is

A Muse plugin is a Python class that implements one or more protocols defined in
`muse/domain.py`. The core engine treats every domain identically — it knows nothing
about your data. You teach it by implementing the protocol.

The protocol stack has four levels. You must implement the base level. The rest are
optional and add progressively richer capabilities:

```
Level 1: MuseDomainPlugin        ← required — basic VCS operations
Level 2: schema()                ← declares data structure, enables algorithm selection
Level 3: StructuredMergePlugin   ← enables sub-file OT merge
Level 4: CRDTPlugin              ← enables convergent multi-agent join
```

The reference implementation is `muse/plugins/music/plugin.py`. Read it alongside this
guide — it shows every method with real implementation and full docstrings.

---

## Quick Start — Copy the Scaffold

The fastest path to a working plugin:

```bash
cp -r muse/plugins/scaffold muse/plugins/<your_domain>
```

Then open `muse/plugins/<your_domain>/plugin.py` and replace every `raise NotImplementedError`
with real code. The scaffold includes:

- Full type annotations for all four protocol levels
- Docstrings explaining what each method must return
- Inline TODO comments marking exactly what to fill in
- Example implementations you can adapt

Register and test:

```bash
# Add to muse/plugins/registry.py (see Registering Your Plugin below)
muse init --domain <your_domain>
muse commit -m "initial state"
muse domains                  # inspect your plugin's capabilities
```

---

## Core Protocol (Required)

Every plugin must implement these five methods. All are synchronous. None may import from
`muse.core.*` — the core engine calls you, not the other way around.

### Types you work with

```python
LiveState    = pathlib.Path | dict[str, bytes]
StateSnapshot = dict[str, str]     # {path: object_id (sha256 hex)}
StateDelta   = StructuredDelta     # list of DomainOp entries
DriftReport  = dict[str, list[str]]  # {"added": [...], "removed": [...], "modified": [...]}
```

### `snapshot(live_state) -> StateSnapshot`

Capture the current state of the working tree. The engine calls this on every `muse commit`.

**Contract:**
- Must be deterministic — same input always produces the same manifest
- Must hash every element that can independently change
- Must return a `dict` whose values are SHA-256 hex digests (object IDs)

```python
def snapshot(self, live_state: LiveState) -> StateSnapshot:
    """Walk live_state and return {path: sha256_hex} for every versioned element."""
    if isinstance(live_state, pathlib.Path):
        manifest: dict[str, str] = {}
        for p in sorted(live_state.rglob("*.your_extension")):
            raw = p.read_bytes()
            sha = hashlib.sha256(raw).hexdigest()
            manifest[str(p.relative_to(live_state))] = sha
        return manifest
    # dict[str, bytes] path — used by internal tests
    return {
        k: hashlib.sha256(v).hexdigest()
        for k, v in live_state.items()
    }
```

### `diff(base, target) -> StateDelta`

Compute the minimal delta between two snapshots. The engine calls this for `muse diff`,
`muse show`, and as the first step of `muse commit` (to build `structured_delta`).

**Contract:**
- Must return a `StructuredDelta` — a `list[DomainOp]` of typed operations
- Should be as granular as makes sense for your domain
- For sequences, use `diff_by_schema()` from `muse.core.diff_algorithms`

```python
from muse.core.diff_algorithms import diff_by_schema
from muse.domain import StructuredDelta, InsertOp, DeleteOp, ReplaceOp

def diff(self, base: StateSnapshot, target: StateSnapshot) -> StateDelta:
    ops: list[DomainOp] = []
    base_paths = set(base)
    target_paths = set(target)

    for path in sorted(target_paths - base_paths):
        ops.append(InsertOp(
            op="insert",
            address=path,
            position=None,
            content_id=target[path],
            content_summary=f"added {path}",
        ))
    for path in sorted(base_paths - target_paths):
        ops.append(DeleteOp(
            op="delete",
            address=path,
            content_id=base[path],
            content_summary=f"removed {path}",
        ))
    for path in sorted(base_paths & target_paths):
        if base[path] != target[path]:
            ops.append(ReplaceOp(
                op="replace",
                address=path,
                before_content_id=base[path],
                after_content_id=target[path],
                content_summary=f"modified {path}",
            ))
    return StructuredDelta(ops=ops)
```

### `merge(base, left, right, *, repo_root) -> MergeResult`

Three-way merge. The engine calls this for `muse merge` when the plugin does not implement
`StructuredMergePlugin`. Implement this even if you plan to implement OT merge — it is the
fallback for `muse cherry-pick`.

**Contract:**
- `merged` — the snapshot that results from reconciling left and right
- `conflicts` — list of paths that could not be auto-resolved
- `applied_strategies` — optional metadata about what resolution was applied
- `dimension_reports` — optional per-dimension auto-merge notes

```python
from muse.domain import MergeResult

def merge(
    self,
    base: StateSnapshot,
    left: StateSnapshot,
    right: StateSnapshot,
    *,
    repo_root: pathlib.Path | None = None,
) -> MergeResult:
    merged: dict[str, str] = dict(base)
    conflicts: list[str] = []

    all_paths = set(base) | set(left) | set(right)
    for path in sorted(all_paths):
        b, l, r = base.get(path), left.get(path), right.get(path)

        if l == r:                  # both sides agree
            if l is None:
                merged.pop(path, None)
            else:
                merged[path] = l
        elif b == l and r is not None:  # only right changed
            merged[path] = r
        elif b == r and l is not None:  # only left changed
            merged[path] = l
        else:                           # both changed differently
            conflicts.append(path)
            merged[path] = l or r or b or ""

    return MergeResult(
        merged=merged,
        conflicts=conflicts,
        applied_strategies={},
        dimension_reports={},
    )
```

### `drift(committed, live) -> DriftReport`

Report how much the working tree has diverged from the last committed snapshot.
The engine calls this for `muse status`.

```python
def drift(self, committed: StateSnapshot, live: LiveState) -> DriftReport:
    current = self.snapshot(live)
    delta = self.diff(committed, current)
    added = [op["address"] for op in delta["ops"] if op["op"] == "insert"]
    removed = [op["address"] for op in delta["ops"] if op["op"] == "delete"]
    modified = [op["address"] for op in delta["ops"] if op["op"] in ("replace", "patch")]
    return {"added": added, "removed": removed, "modified": modified}
```

### `apply(delta, live_state) -> LiveState`

Apply a delta to the working tree. The engine calls this at the end of `muse checkout`
for any domain-level post-processing after the file-level restore has already happened.

```python
def apply(self, delta: StateDelta, live_state: LiveState) -> LiveState:
    # For most domains: files are already restored by the engine.
    # Return live_state unchanged unless you need post-processing.
    return live_state
```

---

## Domain Schema

Implement `schema() -> DomainSchema` to declare the structural shape of your data.
This enables `diff_by_schema()` to automatically select the best diff algorithm for
each dimension, and powers the `muse domains` dashboard.

### Schema TypedDicts

```python
# All defined in muse/core/schema.py

DomainSchema = TypedDict("DomainSchema", {
    "domain": str,
    "version": str,
    "merge_mode": Literal["three_way", "crdt"],
    "elements": list[ElementSchema],
    "dimensions": list[DimensionSpec],
})

ElementSchema = TypedDict("ElementSchema", {
    "name": str,
    "kind": Literal["sequence", "tree", "tensor", "set", "map"],
    "description": str,
})

DimensionSpec = TypedDict("DimensionSpec", {
    "name": str,
    "element": str,
    "description": str,
})
```

### Choosing `kind` for each element

| Your data | Use `kind` | Diff algorithm |
|-----------|-----------|----------------|
| Ordered list of events (rows, notes, steps) | `"sequence"` | Myers LCS — O(nd) |
| Hierarchical tree (DOM, JSON tree, scene graph) | `"tree"` | LCS-based tree edit |
| N-dimensional numeric array | `"tensor"` | Epsilon-tolerant numerical |
| Unordered collection (labels, tags, gene sets) | `"set"` | Set algebra |
| Key-value dict (parameters, config) | `"map"` | Per-key comparison |

### Example — a genomics plugin schema

```python
from muse.core.schema import DomainSchema, ElementSchema, DimensionSpec

def schema(self) -> DomainSchema:
    return DomainSchema(
        domain="genomics",
        version="1.0",
        merge_mode="three_way",
        elements=[
            ElementSchema(
                name="nucleotide_sequence",
                kind="sequence",
                description="Ordered nucleotide positions in a chromosome",
            ),
            ElementSchema(
                name="annotation_set",
                kind="set",
                description="Gene ontology annotations on a locus",
            ),
            ElementSchema(
                name="expression_tensor",
                kind="tensor",
                description="3D array: sample × gene × timepoint expression values",
            ),
        ],
        dimensions=[
            DimensionSpec(
                name="sequence",
                element="nucleotide_sequence",
                description="The primary sequence dimension",
            ),
            DimensionSpec(
                name="annotations",
                element="annotation_set",
                description="Functional annotations",
            ),
            DimensionSpec(
                name="expression",
                element="expression_tensor",
                description="Quantitative expression data",
            ),
        ],
    )
```

---

## Operation-Level Merge (OT)

Implement `StructuredMergePlugin` to enable sub-file auto-merge using Operational
Transformation. When both sides have a `structured_delta`, the engine calls `merge_ops()`
instead of `merge()`.

### What OT gives you

Without OT merge: two branches that both modified the same file conflict at file granularity —
you get one conflict entry even if their changes are on completely different notes / rows / elements.

With OT merge: the engine computes which operations commute (can apply in either order with
the same result) and which don't. Non-commuting ops become the real, minimal conflict set.

### Protocol

```python
from muse.domain import StructuredMergePlugin, MergeResult, DomainOp

class YourPlugin(StructuredMergePlugin):
    def merge_ops(
        self,
        base: StateSnapshot,
        ours_snap: StateSnapshot,
        theirs_snap: StateSnapshot,
        ours_ops: list[DomainOp],
        theirs_ops: list[DomainOp],
        *,
        repo_root: pathlib.Path | None = None,
    ) -> MergeResult:
        from muse.core.op_transform import merge_op_lists
        result = merge_op_lists(
            base_ops=[],
            ours_ops=ours_ops,
            theirs_ops=theirs_ops,
        )

        if result.conflict_ops:
            # Build conflict list from the conflicting op addresses
            conflicts = list({op["address"] for op in result.conflict_ops})
        else:
            conflicts = []

        # Build merged snapshot from merged ops + your base state
        merged = self._apply_ops(base, ours_snap, theirs_snap, result.merged_ops)
        return MergeResult(
            merged=merged,
            conflicts=conflicts,
            applied_strategies={},
            dimension_reports={},
        )
```

### Commutativity — what the engine checks

The function `ops_commute(a, b)` in `muse/core/op_transform.py` covers all 25 op-pair
combinations. Key rules:

| Op pair | Commute? | Reasoning |
|---------|----------|-----------|
| Any ops at different addresses | ✓ always | Orthogonal files/dimensions |
| `InsertOp` + `InsertOp` at same address, different positions | ✓ | Position-disjoint |
| `InsertOp` + `InsertOp` at same address, same position | ✗ conflict | Ordering ambiguity |
| `DeleteOp` + `DeleteOp` same `content_id` | ✓ idempotent | Both deleted same thing |
| `ReplaceOp` + `ReplaceOp` same address | ✗ conflict | Both updated same element |
| `PatchOp` + `PatchOp` same address | recursive check | Recurse into child ops |

---

## CRDT Semantics

Implement `CRDTPlugin` to replace three-way merge with a mathematical join.
CRDTs are ideal when many agents write concurrently and you want **zero conflicts by construction**.

### When to choose CRDT mode

| Scenario | Right choice |
|----------|-------------|
| Human-paced commits (DAW, editor) | OT merge |
| Many autonomous agents writing sub-second | CRDT join |
| Collaborative annotation (many simultaneous adds) | CRDT `ORSet` |
| Collaborative sequence editing (multi-cursor) | CRDT `RGA` |
| Distributed sensor writes (telemetry, IoT) | CRDT `GCounter` or `LWWRegister` |

### Choosing CRDT primitives

```python
from muse.core.crdts import VectorClock, LWWRegister, ORSet, RGA, AWMap, GCounter
```

| Primitive | Use for | Semantics |
|-----------|---------|-----------|
| `VectorClock` | Causal ordering across agents | Track which agent wrote what |
| `LWWRegister[T]` | A scalar that one agent owns | Timestamp wins |
| `ORSet[T]` | A set where concurrent adds win | "Observed-Remove" — adds always beat removes |
| `RGA[T]` | An ordered sequence (list) | Insertion is commutative via parent-ID tree |
| `AWMap[K, V]` | A key-value map | Adds win; keys are independently managed |
| `GCounter` | A counter that only grows | Perfect for event counts, message IDs |

### Protocol implementation sketch

```python
from muse.core.schema import DomainSchema, CRDTDimensionSpec
from muse.domain import CRDTPlugin, CRDTSnapshotManifest
from muse.core.crdts import ORSet, RGA, VectorClock

class YourCRDTPlugin(CRDTPlugin):
    def crdt_schema(self) -> list[CRDTDimensionSpec]:
        return [
            CRDTDimensionSpec(
                name="labels",
                crdt_type="or_set",
                description="Unordered annotation labels",
            ),
            CRDTDimensionSpec(
                name="sequence",
                crdt_type="rga",
                description="Ordered element sequence",
            ),
        ]

    def join(
        self,
        a: CRDTSnapshotManifest,
        b: CRDTSnapshotManifest,
    ) -> CRDTSnapshotManifest:
        # Merge vector clocks
        vc_a = VectorClock.from_dict(a["vclock"])
        vc_b = VectorClock.from_dict(b["vclock"])
        merged_vc = vc_a.merge(vc_b)

        # Join each CRDT dimension
        labels_a = ORSet[str].from_dict(a["crdt_state"]["labels"])
        labels_b = ORSet[str].from_dict(b["crdt_state"]["labels"])
        merged_labels = labels_a.join(labels_b)

        seq_a = RGA[str].from_dict(a["crdt_state"]["sequence"])
        seq_b = RGA[str].from_dict(b["crdt_state"]["sequence"])
        merged_seq = seq_a.join(seq_b)

        return CRDTSnapshotManifest(
            files=a["files"],   # file-level manifest (from latest write)
            vclock=merged_vc.to_dict(),
            crdt_state={
                "labels": merged_labels.to_dict(),
                "sequence": merged_seq.to_dict(),
            },
        )

    def to_crdt_state(self, snapshot: StateSnapshot) -> CRDTSnapshotManifest:
        # Lift a plain snapshot into CRDT state (first time, or after plain checkout)
        return CRDTSnapshotManifest(
            files=snapshot,
            vclock=VectorClock().to_dict(),
            crdt_state={
                "labels": ORSet[str]().to_dict(),
                "sequence": RGA[str]().to_dict(),
            },
        )

    def from_crdt_state(self, crdt: CRDTSnapshotManifest) -> StateSnapshot:
        return crdt["files"]
```

### The three lattice laws (why join always converges)

Every CRDT `join` satisfies:

1. **Commutativity:** `a.join(b) == b.join(a)` — order of arrival doesn't matter
2. **Associativity:** `a.join(b.join(c)) == (a.join(b)).join(c)` — batching is fine
3. **Idempotency:** `a.join(a) == a` — duplicates are harmless

These three laws guarantee that no matter how many agents write concurrently, no matter what
order messages arrive, the final state always converges to the same value.

---

## Registering Your Plugin

Add one line to `muse/plugins/registry.py`:

```python
from muse.plugins.my_domain.plugin import MyDomainPlugin

_REGISTRY: dict[str, MuseDomainPlugin] = {
    "music":     MusicPlugin(),
    "my_domain": MyDomainPlugin(),   # ← add this
}
```

Then initialize:

```bash
muse init --domain my_domain
muse domains          # should show your domain with its capabilities
```

---

## Testing Your Plugin

Every plugin must have tests covering:

### 1. Protocol conformance

```python
from muse.domain import MuseDomainPlugin
from muse.plugins.my_domain.plugin import MyDomainPlugin

def test_plugin_satisfies_protocol() -> None:
    plugin = MyDomainPlugin()
    assert isinstance(plugin, MuseDomainPlugin)
```

### 2. Snapshot round-trip

```python
def test_snapshot_deterministic(tmp_path: pathlib.Path) -> None:
    plugin = MyDomainPlugin()
    (tmp_path / "element.ext").write_bytes(b"data")
    s1 = plugin.snapshot(tmp_path)
    s2 = plugin.snapshot(tmp_path)
    assert s1 == s2
```

### 3. Diff / apply round-trip

```python
def test_diff_apply_roundtrip() -> None:
    plugin = MyDomainPlugin()
    base = {"a.ext": sha256(b"v1")}
    target = {"a.ext": sha256(b"v2"), "b.ext": sha256(b"new")}
    delta = plugin.diff(base, target)
    assert any(op["op"] == "replace" for op in delta["ops"])
    assert any(op["op"] == "insert" for op in delta["ops"])
```

### 4. Merge — clean case

```python
def test_merge_clean_different_paths() -> None:
    plugin = MyDomainPlugin()
    base = {"a.ext": sha256(b"v1")}
    left = {"a.ext": sha256(b"v1"), "b.ext": sha256(b"left")}
    right = {"a.ext": sha256(b"v1"), "c.ext": sha256(b"right")}
    result = plugin.merge(base, left, right)
    assert result["conflicts"] == []
    assert "b.ext" in result["merged"]
    assert "c.ext" in result["merged"]
```

### 5. Merge — conflict case

```python
def test_merge_conflict_same_path() -> None:
    plugin = MyDomainPlugin()
    base = {"a.ext": sha256(b"v1")}
    left = {"a.ext": sha256(b"left")}
    right = {"a.ext": sha256(b"right")}
    result = plugin.merge(base, left, right)
    assert "a.ext" in result["conflicts"]
```

### 6. Schema

```python
from muse.core.schema import DomainSchema

def test_schema_shape() -> None:
    plugin = MyDomainPlugin()
    s = plugin.schema()
    assert s["domain"] == "my_domain"
    assert len(s["elements"]) > 0
    assert len(s["dimensions"]) > 0
    assert s["merge_mode"] in ("three_way", "crdt")
```

### 7. CRDT lattice laws

```python
def test_join_commutative() -> None:
    plugin = MyCRDTPlugin()
    a = plugin.to_crdt_state({"x": sha256(b"a")})
    b = plugin.to_crdt_state({"y": sha256(b"b")})
    ab = plugin.join(a, b)
    ba = plugin.join(b, a)
    # compare the domain-meaningful fields, not object identity
    assert ab["crdt_state"] == ba["crdt_state"]

def test_join_idempotent() -> None:
    plugin = MyCRDTPlugin()
    a = plugin.to_crdt_state({"x": sha256(b"a")})
    aa = plugin.join(a, a)
    assert aa["crdt_state"] == a["crdt_state"]
```

---

## Checklist Before You Ship

```
□ MuseDomainPlugin protocol: snapshot, diff, merge, drift, apply all implemented
□ schema() returns a valid DomainSchema with merge_mode set
□ All type hints pass mypy --strict with zero errors
□ python tools/typing_audit.py --dirs muse/ tests/ --max-any 0 passes (zero violations)
□ pytest tests/test_<domain>_plugin.py -v — all green
□ Registered in muse/plugins/registry.py
□ muse init --domain <your_domain> works
□ muse domains lists your domain with correct capabilities
□ If OT merge: StructuredMergePlugin isinstance check passes
□ If CRDT: join satisfies commutativity, associativity, idempotency
□ No Any, no object, no cast(), no type: ignore, no Optional[X], no print()
□ Module docstring on plugin.py explains what the domain models
```

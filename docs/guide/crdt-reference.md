# Muse CRDT Reference

> **Audience:** Plugin authors adding CRDT Semantics to their domain plugin,
> or anyone curious about how Muse achieves conflict-free convergence.
>
> **Implementation:** `muse/core/crdts/` — six primitives, each with full type safety,
> `to_dict`/`from_dict` serialisation, and tested lattice-law compliance.

---

## Table of Contents

1. [Why CRDTs?](#why-crdts)
2. [The Three Lattice Laws](#the-three-lattice-laws)
3. [Primitive Reference](#primitive-reference)
   - [VectorClock](#vectorclock)
   - [LWWRegister](#lwwregister)
   - [ORSet](#orset)
   - [RGA](#rga)
   - [AWMap](#awmap)
   - [GCounter](#gcounter)
4. [Combining Primitives](#combining-primitives)
5. [CRDTPlugin Integration](#crdtplugin-integration)
6. [Performance Notes](#performance-notes)
7. [When Not to Use CRDTs](#when-not-to-use-crdts)

---

## Why CRDTs?

In classical three-way merge (Muse Phases 1–3), two branches that both edit the same element
produce a conflict that a human must resolve. This is correct and desirable for human-paced
collaborative editing — the human has an opinion and should make the final call.

But consider a different scenario: twenty automated agents simultaneously annotating a genome,
or a distributed sensor network writing telemetry, or a DAW plugin streaming real-time automation
changes from multiple collaborators. In these cases:

- Conflicts are too frequent to be individually resolvable
- No human is present to arbitrate
- The agents don't coordinate in real time
- Messages may be delayed, reordered, or duplicated

CRDTs (**Conflict-free Replicated Data Types**) solve this by changing the definition of a "write."
Instead of "replace the current value," each write is a **join on a partial order** — the state
space is a lattice, and the merge of any two states is always the least upper bound of both.

The result: **join always converges to the same final state, regardless of message order, delay,
or duplication.** No conflict state ever exists.

---

## The Three Lattice Laws

Every CRDT `join` operation must satisfy all three laws. Muse's test suite verifies them for
all six primitives. If you build a composite CRDT from these primitives, your `join` inherits
these properties automatically (lattice composition is closed under these laws).

### 1. Commutativity

```
a.join(b) ≡ b.join(a)
```

The order in which you receive updates doesn't matter. Agent A sending its state to B produces
the same result as B sending to A first.

### 2. Associativity

```
a.join(b.join(c)) ≡ (a.join(b)).join(c)
```

You can batch updates in any grouping. Receiving 10 updates one at a time is equivalent to
receiving them all batched, or in any intermediate grouping.

### 3. Idempotency

```
a.join(a) ≡ a
```

Receiving the same update twice is harmless. Deduplication is not required.

These three laws together mean: **no matter how your network behaves — delays, reorders,
duplicates — all replicas eventually reach the same state once they have seen all updates.**

---

## Primitive Reference

All primitives are in `muse/core/crdts/`. Import them from the package:

```python
from muse.core.crdts import VectorClock, LWWRegister, ORSet, RGA, AWMap, GCounter
```

All primitives are **immutable** — every mutating method returns a new instance. This
makes them safe to use as values in `TypedDict` fields and easy to test.

---

### VectorClock

**File:** `muse/core/crdts/vclock.py`

A vector clock assigns a logical timestamp to each agent. It answers "does event A causally
precede event B?" without requiring a synchronized wall clock.

**Use for:** tracking causal ordering between agents. Required by all other CRDTs implicitly
when you need to know which write was "more recent."

#### API

```python
vc = VectorClock()                    # empty — all agents at tick 0

# Increment agent's own clock before a write
vc2 = vc.increment("agent-1")        # {"agent-1": 1}

# Merge with a clock received from another agent
merged = vc.merge(other_vc)          # take max per agent

# Causal comparison
vc_a.happens_before(vc_b)            # True if vc_a ≤ vc_b (strictly before)
vc_a.concurrent_with(vc_b)           # True if neither precedes the other
vc_a.equivalent(vc_b)                # True if all per-agent ticks are equal

# Serialisation
d = vc.to_dict()                     # {"agent-1": 1, "agent-2": 3}
vc3 = VectorClock.from_dict(d)
```

#### When to use

Always embed a `VectorClock` in your `CRDTSnapshotManifest["vclock"]` field. It tracks
which writes from which agents have been seen, enabling you to detect concurrent writes
and apply correct merge semantics.

---

### LWWRegister

**File:** `muse/core/crdts/lww_register.py`

A register holding a single value. When two agents write concurrently, the one with the
higher timestamp wins ("Last Write Wins").

**Use for:** scalar values where recency is the right semantic — tempo, a mode enum,
a display name, a configuration flag. Not appropriate when concurrent writes represent
genuinely independent work that should both be preserved.

#### API

```python
reg: LWWRegister[float] = LWWRegister()

# Write a new value (timestamp should be monotonically increasing per agent)
reg2 = reg.write(120.0, timestamp=1700000000.0, author="agent-1")

# Read current value
val = reg2.read()              # 120.0

# Join two registers — higher timestamp wins
merged = reg2.join(other_reg)

# Serialisation
d = reg.to_dict()              # {"value": ..., "timestamp": ..., "author": ...}
reg3 = LWWRegister[float].from_dict(d)

reg2.equivalent(reg3)          # True if same value/timestamp/author
```

#### Warning on timestamps

LWW correctness depends on timestamps being reasonably monotone. In a distributed system
with clock skew, use logical timestamps (derived from a `VectorClock`) rather than wall time.

---

### ORSet

**File:** `muse/core/crdts/or_set.py`

An Observed-Remove Set. Elements can be added and removed, but **concurrent adds win over
concurrent removes**. This is the opposite of a naive set where removes win.

**Why adds-win?** In collaborative scenarios, a concurrent remove means "I didn't know you
were going to add that" — not "I decided to delete your add." Adds-win semantics prevent
silent data loss.

**Use for:** annotation sets, tag collections, member lists, gene ontology terms, feature
flags — any unordered collection where concurrent adds should be preserved.

#### API

```python
s: ORSet[str] = ORSet()

# Add with a unique token (UUID or agent+timestamp combination)
s2 = s.add("annotation-GO:0001234", token="agent1-tick42")

# Remove by value (removes all tokens for that element)
s3 = s2.remove("annotation-GO:0001234")

# Query
"annotation-GO:0001234" in s2        # True
s2.elements()                        # frozenset({"annotation-GO:0001234"})
s2.tokens_for("annotation-GO:0001234")  # frozenset({"agent1-tick42"})

# Join — union of all add-tokens, then subtract remove-tokens
merged = s2.join(other_set)

# Serialisation
d = s.to_dict()
s4 = ORSet[str].from_dict(d)

s2.equivalent(s3)               # True if same elements and tokens
```

#### Concurrent add + remove example

```
Agent A: s.add("X", token="a1")
Agent B: s.remove("X")        (before seeing A's add)

After join:
  A's add token "a1" is present
  B's remove only targets tokens B has seen — not "a1"
  Result: "X" is in the merged set ✓
```

---

### RGA

**File:** `muse/core/crdts/rga.py`

A Replicated Growable Array — a list where concurrent insertions are commutative.
Two agents can insert at the same logical position; the result is deterministic based
on `element_id` ordering (larger ID appears first).

**Use for:** collaborative text editing, ordered note sequences, ordered event streams,
any sequence where multiple agents might insert concurrently.

#### API

```python
rga: RGA[str] = RGA()

# Insert after the virtual root (parent_id=None means "at the beginning")
rga2 = rga.insert(value="C4", element_id="id-100", parent_id=None)
rga3 = rga2.insert(value="D4", element_id="id-200", parent_id="id-100")
rga4 = rga3.insert(value="E4", element_id="id-300", parent_id="id-200")

# Delete by element_id (tombstones the element, does not shift IDs)
rga5 = rga4.delete("id-200")

# Read current sequence (tombstoned elements excluded)
rga4.to_sequence()    # ["C4", "D4", "E4"]
rga5.to_sequence()    # ["C4", "E4"]

len(rga4)             # 3

# Join — builds parent-ID tree, traverses in canonical order
merged = rga4.join(other_rga)

# Serialisation
d = rga.to_dict()
rga6 = RGA[str].from_dict(d)

rga4.equivalent(rga6)    # True if same elements in same order
```

#### How concurrent insertions resolve

```
Initial: ["A", "C"]  (A at id-100, C at id-300)

Agent 1: inserts "B" at id-200, parent_id="id-100"
Agent 2: inserts "X" at id-250, parent_id="id-100"

After join (same parent "id-100", id-250 > id-200):
  Result: ["A", "X", "B", "C"]
  (larger element_id appears first among siblings)
```

To get a specific ordering, choose `element_id` values accordingly. For sequential
insertions from a single agent, monotonically increasing IDs produce the expected order.

---

### AWMap

**File:** `muse/core/crdts/aw_map.py`

An Add-Wins Map. A dictionary where concurrent adds win over concurrent removes, and each
key is managed independently (adding a key does not conflict with removing a different key).

**Use for:** parameter maps, configuration dicts, per-dimension metadata, named dimension
states, any key-value structure where concurrent writes to different keys should not conflict.

#### API

```python
m: AWMap[str, float] = AWMap()

# Set a key-value pair (uses an ORSet internally per key)
m2 = m.set("tempo", 120.0, token="agent1-t1")
m3 = m2.set("key_sig", 0.0, token="agent1-t2")

# Remove a key
m4 = m3.remove("key_sig")

# Query
m3.get("tempo")          # 120.0
m3.get("missing")        # None
"tempo" in m3            # True
m3.keys()                # frozenset({"tempo", "key_sig"})

# Flatten to plain dict
m3.to_plain_dict()       # {"tempo": 120.0, "key_sig": 0.0}

# Join — union of all add-sets per key, removes applied per key
merged = m3.join(other_map)

# Serialisation
d = m.to_dict()
m5 = AWMap[str, float].from_dict(d)

m3.equivalent(m4)        # True if same key-value pairs
```

---

### GCounter

**File:** `muse/core/crdts/g_counter.py`

A grow-only counter. Each agent increments its own shard; the global value is the sum.
Decrement is not possible — this is by design. Counters that can only grow are trivially
convergent.

**Use for:** event counts, version numbers, message sequence numbers, commit counts,
read counts — any monotonically increasing quantity.

#### API

```python
gc = GCounter()

# Increment this agent's shard
gc2 = gc.increment("agent-1")
gc3 = gc2.increment("agent-1")
gc4 = gc3.increment("agent-2")

gc4.value()                   # 3
gc4.value_for("agent-1")      # 2
gc4.value_for("agent-2")      # 1
gc4.value_for("agent-99")     # 0

# Join — take max per agent shard
merged = gc4.join(other_counter)

# Serialisation
d = gc.to_dict()              # {"agent-1": 2, "agent-2": 1}
gc5 = GCounter.from_dict(d)

gc4.equivalent(gc5)           # True if same per-agent values
```

---

## Combining Primitives

Complex CRDT state is built by composing primitives. The composition inherits the lattice
laws because each primitive satisfies them and because `join` is applied field-by-field.

### Example: a collaborative score header

```python
@dataclass
class ScoreHeaderCRDT:
    """Convergent score header: tempo register + time_sig register + author set."""

    tempo: LWWRegister[float]
    time_sig: LWWRegister[str]
    authors: ORSet[str]

    def join(self, other: ScoreHeaderCRDT) -> ScoreHeaderCRDT:
        return ScoreHeaderCRDT(
            tempo=self.tempo.join(other.tempo),
            time_sig=self.time_sig.join(other.time_sig),
            authors=self.authors.join(other.authors),
        )
```

Because `LWWRegister.join` and `ORSet.join` both satisfy the three laws, `ScoreHeaderCRDT.join`
does too — for free, by composition.

---

## CRDTPlugin Integration

The entry point in the core engine is `crdt_join_snapshots()` in `muse/core/merge_engine.py`.
The `muse merge` command calls it when `isinstance(plugin, CRDTPlugin)` is `True`:

```python
from muse.core.merge_engine import crdt_join_snapshots
from muse.domain import CRDTPlugin, CRDTSnapshotManifest

# In merge_engine.py — called by the merge command
def crdt_join_snapshots(
    plugin: CRDTPlugin,
    ours: StateSnapshot,
    theirs: StateSnapshot,
) -> MergeResult:
    crdt_a = plugin.to_crdt_state(ours)
    crdt_b = plugin.to_crdt_state(theirs)
    joined = plugin.join(crdt_a, crdt_b)
    merged_snapshot = plugin.from_crdt_state(joined)
    return MergeResult(
        merged=merged_snapshot,
        conflicts=[],          # always empty — CRDT join never conflicts
        applied_strategies={},
        dimension_reports={},
    )
```

Notice `conflicts=[]` is always empty. This is the CRDT guarantee: **no human intervention
is ever required.**

---

## Performance Notes

| Primitive | Join complexity | Storage |
|-----------|----------------|---------|
| `VectorClock` | O(agents) | One int per agent |
| `LWWRegister` | O(1) | One value + timestamp |
| `ORSet` | O(n + m) tokens | One UUID per add operation |
| `RGA` | O(n log n) | One node per insert (tombstones retained) |
| `AWMap` | O(keys × tokens) | Per-key ORSet overhead |
| `GCounter` | O(agents) | One int per agent |

**RGA memory warning:** `RGA` retains tombstoned elements forever (this is required for
commutativity). For domains with high churn (many inserts and deletes), implement periodic
garbage collection by taking a snapshot of the live sequence, creating a fresh `RGA`, and
re-inserting only the live elements. This is a safe operation because garbage collection
only affects elements both sides have observed as deleted — a coordination-free safe point.

---

## When Not to Use CRDTs

CRDTs are not always the right choice. Use three-way merge (Phases 1–3) when:

- **Humans are making creative decisions** — a DAW producer choosing a chord voicing should
  not have their choice silently overwritten by a LWW timestamp. Use OT merge with conflicts.

- **The domain has invariants that CRDTs cannot enforce** — CRDTs converge, but they can
  produce semantically invalid states. A MIDI file with notes outside the pitch range 0–127
  is technically convergent but musically invalid. Invariant enforcement requires coordination.

- **Conflict visibility is a feature** — in code review, you want conflicts to be visible
  to humans. "This merge is clean" is meaningful precisely because conflicts exist.

- **You have a clear authority model** — if one agent is the "source of truth," LWW with
  that agent always winning is fine. But that's a policy, not a CRDT.

Use CRDTs when all of the following are true:
1. Many agents write concurrently (more than humans can coordinate)
2. No single agent is the authority
3. All writes are semantically valid in isolation
4. Convergence is more important than precision

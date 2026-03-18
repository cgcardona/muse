# Voice-Aware Music RGA

## Status

**Experimental** — not wired into the production merge path.  This module
(`muse/plugins/music/_crdt_notes.py`) exists to:

1. Demonstrate commutative concurrent note editing.
2. Benchmark voice-aware RGA vs. LSEQ and standard three-way merge.
3. Serve as the implementation foundation for a future live collaboration layer.

## Why standard RGA is wrong for music

Standard RGA (Roh et al., 2011) orders concurrent insertions at the same
position lexicographically by op_id.  Two agents inserting a bass note and a
soprano note at the same beat would have their pitches interleaved
arbitrarily — soprano might appear before bass, producing voice crossings that
are musicologically nonsensical.

## Music-RGA position key

`NotePosition` is a `NamedTuple` with four fields that are compared in order:

```
NotePosition = (measure, beat_sub, voice_lane, op_id)
```

| Field | Purpose |
|-------|---------|
| `measure` | 1-indexed bar number |
| `beat_sub` | Tick offset within the bar |
| `voice_lane` | 0=bass, 1=tenor, 2=alto, 3=soprano — orders by register |
| `op_id` | UUID4 tie-break for concurrent edits in the same voice |

At the same `(measure, beat_sub)`, notes are ordered by voice lane — bass
before treble — preventing voice crossings regardless of insertion order.

## CRDT laws

The three lattice laws hold:

1. **Commutativity**: `merge(a, b).to_sequence() == merge(b, a).to_sequence()`
2. **Associativity**: `merge(merge(a, b), c) == merge(a, merge(b, c))`
3. **Idempotency**: `merge(a, a).to_sequence() == a.to_sequence()`

Verified by `tests/test_crdt.py`.

## Tombstone semantics

Deleted entries are tombstoned (marked `tombstone=True`) rather than removed.
This is standard RGA: the tombstone ensures that the deleted entry's position
remains stable for other replicas that may have concurrent insertions relative
to it.  In the join operation, **tombstone wins**: if either replica has
deleted an entry, the merged result considers it deleted.

## Voice lane assignment

Automatic voice lane assignment uses a coarse tessiture model:

| MIDI pitch range | Voice lane | Label |
|-----------------|-----------|-------|
| 0–47 | 0 | Bass |
| 48–59 | 1 | Tenor |
| 60–71 | 2 | Alto |
| 72–127 | 3 | Soprano |

Agents performing explicit voice separation can override `voice_lane` when
calling `MusicRGA.insert()`.

## Relationship to the commit DAG

At commit time, `MusicRGA.to_domain_ops(base_sequence)` translates the CRDT
state into canonical `InsertOp` / `DeleteOp` entries for storage in the commit
record.  The CRDT state itself is ephemeral — not stored in the object store.

## Related files

| File | Role |
|------|------|
| `muse/plugins/music/_crdt_notes.py` | `NotePosition`, `RGANoteEntry`, `MusicRGA` |
| `tests/test_crdt.py` | CRDT law verification + unit tests |
| `tools/benchmark.py` | RGA throughput benchmark |

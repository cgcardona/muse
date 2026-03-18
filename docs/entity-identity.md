# Entity Identity in Muse

## The problem with content hashes as identity

Muse uses SHA-256 content hashes to address every object in its store.  Two
blobs with identical bytes have the same hash — content equality.  This is
correct for immutable storage but wrong for *entity identity*.

When a musician changes a note's velocity from 80 to 100, the note has the
same identity from the musician's perspective.  But the content hash changes,
so the old diff model produces a `DeleteOp + InsertOp` pair — the note
appears to have been removed and a completely different note inserted.  All
lineage, provenance, and causal history is lost.

## The solution: stable entity IDs

A `NoteEntity` in `muse/plugins/midi/entity.py` extends the five `NoteKey`
fields with an optional `entity_id` — a UUID4 that is assigned at first
insertion and **never changes**, regardless of how the note's fields are
mutated later.

```
NoteKey:    (pitch, velocity, start_tick, duration_ticks, channel)
              ↑ content equality

NoteEntity: NoteKey + entity_id (UUID4)
                       ↑ stable identity across mutations
```

## Entity assignment heuristic

`assign_entity_ids()` maps a new note list onto entity IDs from the prior
commit using a three-tier matching strategy:

1. **Exact content match** — all five fields identical → same entity, no mutation.
2. **Fuzzy match** — same pitch + channel, `|Δtick| ≤ threshold` (default 10),
   and `|Δvelocity| ≤ threshold` (default 20) → same entity, emit `MutateOp`.
3. **No match** → new entity, fresh UUID4, emit `InsertOp`.

Notes in the prior index that matched nothing → emit `DeleteOp`.

## MutateOp vs. DeleteOp + InsertOp

The `MutateOp` in `muse/domain.py` carries:

| Field | Description |
|-------|-------------|
| `entity_id` | Stable entity ID |
| `old_content_id` | SHA-256 of the note before the mutation |
| `new_content_id` | SHA-256 of the note after the mutation |
| `fields` | `dict[field_name, FieldMutation(old, new)]` |
| `old_summary` / `new_summary` | Human-readable before/after strings |

This enables queries like "show me all velocity edits to the cello part" across
the full commit history.

## Entity index storage

Entity indexes live under `.muse/entity_index/` as derived artifacts:

```
.muse/entity_index/
    <commit_id[:16]>/
        <track_safe_name>_<hash[:8]>.json
```

They are fully rebuildable from commit history and should be added to
`.museignore` in CI to avoid accidental commits.

## Independence from core

Entity identity is purely a music-plugin concern.  The core engine
(`muse/core/`) never imports from `muse/plugins/`.  The `MutateOp` and
`FieldMutation` types in `muse/domain.py` are domain-agnostic — a genomics
plugin can use the same types to track mutations in a nucleotide sequence.

## Related files

| File | Role |
|------|------|
| `muse/domain.py` | `MutateOp`, `FieldMutation`, `EntityProvenance` |
| `muse/plugins/midi/entity.py` | `NoteEntity`, `EntityIndex`, `assign_entity_ids`, `diff_with_entity_ids` |
| `muse/plugins/midi/midi_diff.py` | `diff_midi_notes_with_entities()` |
| `tests/test_entity.py` | Unit tests |

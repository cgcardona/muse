# Muse Variation Spec — Music Domain Reference

> **Scope:** This spec describes the *Variation* UX pattern as implemented
> for the music domain. It is not part of the core Muse VCS engine.
>
> For the domain-agnostic VCS protocol, see [muse-protocol.md](muse-protocol.md).
> For a discussion of how "Variation" might generalize across domains, see
> [muse-domain-concepts.md](muse-domain-concepts.md).

---

## What a Variation Is

A Variation is a **proposed musical change set awaiting human review** before
being committed to the Muse DAG. It is the music plugin's implementation of the
propose → review → commit pattern.

```
VCS equivalent mapping:
  Variation  = A staged diff
  Phrase     = A hunk (contiguous group of changes within a region)
  Accept     = muse commit
  Discard    = Discard working-tree changes (no commit)
  Undo       = muse revert
```

The defining characteristic: a Variation is *heard before committed* — the
musician auditions the proposed change in the DAW's playback engine and then
decides whether to accept or discard. This is layered on top of the VCS DAG,
not part of it.

---

## Lifecycle

```
1. Variation Proposed   — AI or user creates a proposed change set
2. Stream               — phrases stream to the DAW in SSE events
3. Review Mode Active   — DAW shows proposed state alongside canonical state
4. Accept (or partial)  — accepted phrases are committed; discarded phrases are dropped
5. Canonical state updates — only after commit
```

Canonical state MUST NOT change during review. The proposed state is always
ephemeral.

---

## Terminology

| Term | Definition |
|---|---|
| **Variation** | A proposed set of musical changes, organized as phrases |
| **Phrase** | A bounded group of note/controller changes within one region |
| **NoteChange** | An atomic note delta: added, removed, or modified |
| **Canonical State** | The actual committed project state in the Muse DAG |
| **Proposed State** | The ephemeral preview state during variation review |
| **Accept** | Committing accepted phrases to the Muse DAG |
| **Discard** | Dropping the variation; canonical state unchanged |

---

## Execution Mode Policy

| User Intent | Mode | Result |
|---|---|---|
| Composing / generating | `variation` | Produces a Variation for human review |
| Editing (add track, set tempo) | `apply` | Applied immediately, no review |
| Reasoning / chat only | `reasoning` | No state mutation |

The backend enforces execution mode. Frontends MUST NOT override it.

---

## Data Shapes

### Variation (meta event)

```json
{
  "variationId": "...",
  "intent": "add countermelody to verse",
  "aiExplanation": "I added a countermelody in the upper register...",
  "noteCounts": { "added": 12, "removed": 0, "modified": 3 }
}
```

### Phrase

```json
{
  "phraseId": "...",
  "trackId": "...",
  "regionId": "...",
  "startBeat": 1.0,
  "endBeat": 5.0,
  "label": "Verse countermelody",
  "noteChanges": [...],
  "controllerChanges": [...]
}
```

### NoteChange

```json
{
  "noteId": "...",
  "changeType": "added",
  "before": null,
  "after": {
    "startBeat": 1.5,
    "durationBeats": 0.5,
    "pitch": 72,
    "velocity": 80
  }
}
```

Rules:
- `added` → `before` MUST be `null`
- `removed` → `after` MUST be `null`
- `modified` → both `before` and `after` MUST be present

---

## SSE Streaming Contract

Events stream in this order (strictly):

```
meta → phrase* → done
```

| Event type | When sent |
|---|---|
| `meta` | First event; carries variation summary |
| `phrase` | One per phrase; may be many |
| `done` | Last event; signals review mode is active |

Event envelope:
```json
{
  "type": "phrase",
  "sequence": 3,
  "variationId": "...",
  "projectId": "...",
  "baseStateId": "...",
  "timestampMs": 1234567890,
  "payload": { ...phrase... }
}
```

`sequence` is strictly increasing. `baseStateId` is the Muse snapshot ID the
variation was computed against.

---

## API Endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/variation/propose` | Propose a new variation |
| `GET` | `/variation/stream` | SSE stream of meta + phrase events |
| `POST` | `/variation/commit` | Accept (all or partial phrases) |
| `POST` | `/variation/discard` | Discard without committing |
| `GET` | `/variation/{id}` | Poll status / reconnect |

### Commit request

```json
{
  "variationId": "...",
  "acceptedPhraseIds": ["phrase-1", "phrase-3"]
}
```

Partial acceptance is supported: only listed phrases are committed.

---

## Audition Modes

| Mode | What plays |
|---|---|
| Original | Canonical state only |
| Variation | Proposed state (with changes applied) |
| Delta Solo | Only the added/modified notes |

---

## Safety Rules

1. Review mode is isolated — destructive edits are blocked during review.
2. Canonical state MUST NOT mutate during proposal.
3. Commit is a single undo boundary: `muse revert` can undo the entire commit.
4. If the stream fails mid-phrase, keep received phrases and allow the user to
   discard or commit what arrived.

---

## Relationship to Muse VCS

A committed Variation becomes a standard `muse commit` in the DAG:

```bash
muse log --oneline
```

```
a1b2c3d4 (HEAD -> main) Add countermelody to verse
```

From Muse's perspective, a committed Variation is indistinguishable from any
other commit. The Variation UX is a music-domain layer on top of the standard
VCS commit cycle.

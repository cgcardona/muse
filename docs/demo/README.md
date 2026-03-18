# Muse — Demo Hub

> Domain-agnostic version control for multidimensional state.
> Music is the first domain. Code is the second. Genomics, 3D design, and
> spacetime simulation are next.

Choose a domain to see Muse's full power:

---

## [Music Tour de Force →](tour-de-force-music.md)

**9 new commands — version control that understands music.**

Muse treats MIDI as a typed, content-addressed graph of note events.  Every
note has a stable content ID.  Every commit stores a note-level structured
delta.  Two composers can independently harmonize the same track and merge
at the note level — changes to non-overlapping notes never conflict.

| Command | One-line description |
|---------|---------------------|
| `muse notes` | Every note in a MIDI track as musical notation — pitch, beat, duration, velocity |
| `muse note-log` | Note-level commit history — which notes were added/removed in each commit |
| `muse note-blame` | Which commit introduced the notes in bar N? One answer, per bar. |
| `muse harmony` | Chord analysis and key detection from note content |
| `muse piano-roll` | ASCII piano roll visualization — pitch vs time, bar lines included |
| `muse note-hotspots` | Bar-level churn leaderboard — which bars change most across commits |
| `muse velocity-profile` | Dynamic range, RMS, and velocity histogram; per-bar mode |
| `muse transpose` | Transpose all notes in a track by N semitones (agent command) |
| `muse mix` | Combine notes from two tracks into one output track (agent command) |

Plus the core VCS operations with musical semantics:
`muse diff` shows "C4 added at beat 3.5" · `muse merge` resolves conflicts
per dimension (melodic / harmonic / dynamic / structural) · `muse show`
displays note-level changes in musical notation.

---

## [Code Tour de Force →](tour-de-force-code.md)

**12 commands that are strictly impossible in Git.**

Muse treats code as a typed, content-addressed graph of named symbols — not
a bag of text lines.  Every commit stores a symbol-level structured delta.
Every function has a stable identity hash that survives renames and moves.

| Command | One-line description |
|---------|---------------------|
| `muse symbols` | Every function, class, and method in the snapshot — extracted from real ASTs |
| `muse grep` | Search the symbol graph by name, kind, or language — no false positives |
| `muse query` | Predicate DSL: `kind=function language=Go name~=handle hash=a3f2c9` |
| `muse languages` | Language + symbol-type breakdown across the whole repo |
| `muse blame` | Which commit last touched this exact function? One answer. |
| `muse symbol-log` | Full history of one symbol — renames and moves included |
| `muse detect-refactor` | Classify semantic operations: rename / move / signature / impl |
| `muse hotspots` | Symbol churn leaderboard — which functions change most? |
| `muse stable` | Symbol stability leaderboard — your bedrock, safe to build on |
| `muse coupling` | File co-change analysis — semantic hidden dependencies |
| `muse compare` | Deep semantic diff between any two historical snapshots |
| `muse patch` | Surgical per-symbol modification — the agent interface |

**Supported languages:** Python, TypeScript, JavaScript, Go, Rust, Java, C, C++, C#, Ruby, Kotlin

---

## Shared Architecture

Both domains build on the same engine:

```
Content-addressed object store  ← immutable, SHA-256
Snapshot manifest               ← file path → object hash
Structured delta                ← typed DomainOp tree (insert / delete / replace / move / patch)
Commit graph                    ← parent chain with structured deltas on every node
```

The **music plugin** adds:

```
Note event model      ← NoteKey (pitch, velocity, start_tick, duration_ticks, channel)
Note-level diffs      ← PatchOp with child InsertOp/DeleteOp per note
Dimensional merge     ← melodic / rhythmic / harmonic / dynamic / structural
Content IDs per note  ← SHA-256 of the five NoteKey fields
```

The **code plugin** adds:

```
AST symbol trees      ← SymbolRecord (kind, name, body_hash, signature_id, content_id)
Symbol-level diffs    ← PatchOp with child InsertOp/DeleteOp/ReplaceOp per symbol
Rename detection      ← body_hash match across addresses
Move detection        ← content_id match across files
```

Every domain command is a consumer of what the plugin already produces.
No new storage format.  No new protocol.  Just queries over the structured
commit history.

---

## The Semantic Stack

| Layer | What it stores | Music commands | Code commands |
|-------|---------------|----------------|---------------|
| **Object store** | Raw bytes, SHA-256 | All | All |
| **Snapshot manifest** | `file_path → sha256` | `notes`, `harmony`, `piano-roll` | `symbols`, `languages`, `compare` |
| **Structured delta** | Typed op tree per commit | `note-log`, `note-blame`, `note-hotspots` | `blame`, `hotspots`, `stable`, `coupling` |
| **Domain graph** | Notes / AST symbols | `velocity-profile`, `harmony`, `query` | `grep`, `query`, `patch` |
| **Write layer** | Live file modification | `transpose`, `mix` | `patch` |

---

*Muse v2 · Python 3.11 · `tree-sitter` for code · `mido` for music*

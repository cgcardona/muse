# Muse Demo Hub

Two domains. One abstraction. Everything Git can't do.

Muse is a domain-agnostic version control system built on a six-method plugin
interface. The same DAG, the same object store, the same branch and merge
engine — different semantic understanding of your data depending on which plugin
you use.

The two demos below show the full depth of what that means.

---

## Choose Your Domain

### [Music Demo](tour-de-force-script.md) — MIDI files, note-level diffs

The original Muse demo. A MIDI repository with five orthogonal dimensions:
melodic, rhythmic, harmonic, dynamic, structural.

**What it demonstrates:**

- A drummer and a pianist editing the same MIDI file simultaneously — no
  conflict, because they touched different dimensions
- A single structural conflict resolved while four dimensions auto-merge
- Note-level diffs: not "file changed" but "insert note C4 at tick 480,
  velocity 80, duration 240"
- Cherry-pick, stash, revert — the full VCS surface area
- OT Merge: two note insertions at different tick positions commute → auto-merge
- CRDT Semantics: `join()` always succeeds, never conflicts

**The key insight:** Git can't diff a MIDI file at all. Muse understands every
note, every chord voicing, every tempo change — and uses that understanding to
resolve conflicts that Git would mark as binary file conflicts.

**Runtime:** ~150ms · 14 commits · 6 branches · 1 conflict resolved

---

### [Code Demo](tour-de-force-code.md) — Source code, symbol-level diffs

The code plugin demo. A software repository in Python, TypeScript, Go, and Rust
— eleven languages total.

**What it demonstrates:**

- `muse symbols` — list every function, class, and method in a snapshot
  (impossible in Git — Git doesn't know what a function is)
- Rename detection via `body_hash`: same implementation, new name → `ReplaceOp`
  annotated "renamed to X", not a delete + add
- Cross-file move detection via `content_id`: function moved to a new module →
  connection preserved in the DAG forever
- Two engineers modify the same file, different functions → **auto-merge**
  (commuting ops)
- `muse symbol-log` — track one function's complete history, through renames,
  across the full commit graph (impossible in Git)
- `muse detect-refactor` — machine-generated semantic refactoring report:
  renames, moves, signature changes, implementation changes (impossible in Git)

**The key insight:** Git treats code as text files and lines. Muse treats code
as a structured graph of named, typed symbols with content-addressed identities
that persist across renames, moves, and refactors. Two engineers touching the
same file but different functions never conflict.

**Languages:** Python · TypeScript · JavaScript · Go · Rust · Java · C · C++ · C# · Ruby · Kotlin

---

## The Shared Architecture

Both demos run on the same engine. The only difference is the plugin.

```
muse init --domain music    # → MusicPlugin
muse init --domain code     # → CodePlugin
```

The DAG, object store, branch model, and merge state machine are identical.
Each plugin implements six methods that tell the engine how to interpret its data:

| Method | What it does |
|--------|-------------|
| `snapshot()` | Walk the working tree; return a content-addressed manifest |
| `diff(old, new)` | Produce a `StructuredDelta` of typed ops (Insert/Delete/Replace/Move/Patch) |
| `drift(live, snapshot)` | Detect uncommitted working-tree changes |
| `apply(delta, state)` | Apply a delta to a state (used by checkout/revert/cherry-pick) |
| `merge(base, left, right)` | Three-way merge with domain-specific conflict detection |
| `schema()` | Declare the domain schema; engine auto-selects diff algorithms |

Adding a new domain: `muse domains --new <name>`. Thirty seconds to scaffold.

---

## The Four Semantic Layers

Both plugins implement all four capability levels:

| Phase | Capability | What you gain |
|-------|-----------|---------------|
| 1 | **Typed Delta Algebra** | Typed op lists instead of opaque diffs |
| 2 | **Domain Schema** | Engine auto-selects the right diff algorithm per dimension |
| 3 | **OT Merge Engine** | Sub-symbol auto-merge via Operational Transformation |
| 4 | **CRDT Semantics** | `join()` always converges — no conflict state ever possible |

---

## What's Next

- **Genomics plugin** — annotate genomes with CRDT `ORSet` semantics; concurrent
  researcher annotations never conflict
- **3D spatial design** — version CAD models at the geometry level, not as binary
  blobs
- **Scientific simulation** — checkpoint simulation state at the tensor level;
  diff parameter sweeps by dimension
- **Neural network checkpoints** — version model weights by layer, not by file

`muse domains --new <your_domain>`. The plugin interface handles the rest.

# `.museattributes` Reference

`.museattributes` is a per-repository configuration file that declares merge
strategies for specific paths and dimensions.  It lives in the repository root,
alongside `muse-work/`.

---

## Why Muse is different

Git treats every file as an opaque byte sequence.  If two branches both touch
the same file, that is a conflict — full stop.  Git cannot know that one
collaborator edited the drumbeat rhythm while another adjusted the key-change
harmonic, because it has no concept of *dimensions* within a file.

Muse does.  A MIDI file has five orthogonal axes of change:

| Dimension | What it covers |
|---|---|
| `melodic` | `note_on` / `note_off` events — the notes played |
| `rhythmic` | Same events as `melodic` — timing is inseparable from pitch in the MIDI model; provided as a distinct user-facing label |
| `harmonic` | `pitchwheel` events |
| `dynamic` | `control_change` events |
| `structural` | Tempo, time-signature, key-signature, program changes, markers |

When two branches both modify the same `.mid` file, Muse asks:
*did they change the same dimension?*  If not, the merge is clean — no human
intervention required.  `.museattributes` is where you encode domain knowledge
to guide this process.

---

## File location

```
my-project/
├── .muse/               ← VCS metadata
├── muse-work/           ← tracked workspace
├── .museignore          ← snapshot exclusion rules
└── .museattributes      ← merge strategies (this file)
```

---

## File format

```
<path-pattern>  <dimension>  <strategy>
```

- **path-pattern** — an `fnmatch` glob matched against workspace-relative POSIX
  paths (e.g. `drums/*`, `src/models/**`, `*`).
- **dimension** — a domain-defined dimension name or `*` to match all dimensions.
- **strategy** — `ours | theirs | union | auto | manual`

Lines beginning with `#` and blank lines are ignored.  **First matching rule
wins.**

---

## Strategies

| Strategy | Behaviour |
|---|---|
| `ours` | Take the current branch's version.  Skip conflict detection for this path/dimension. |
| `theirs` | Take the incoming branch's version.  Skip conflict detection for this path/dimension. |
| `union` | Include both sides' changes.  Falls through to auto-merge logic (equivalent to `auto` at file level; reserved for future sub-event union). |
| `auto` | Let the merge engine decide.  Default when no rule matches. |
| `manual` | Flag this path/dimension for mandatory human resolution, even if the engine would auto-resolve it. |

---

## Merge algorithm

`muse merge` applies `.museattributes` in three sequential passes:

### Pass 1 — File-level strategy

For each path that both branches changed, `resolve_strategy(rules, path, "*")`
is called.

- `ours` → take the left branch's version; path is removed from the conflict
  list.
- `theirs` → take the right branch's version; path is removed from the conflict
  list.
- `manual` → keep in conflict list even if the engine would auto-merge.
- `auto` / `union` → proceed to Pass 2.

### Pass 2 — Dimension-level merge (MIDI files)

For `.mid` files that survive Pass 1 (no file-level rule resolved them), Muse:

1. Reads the base, left, and right MIDI content from the object store.
2. Parses each file and buckets events into four internal dimensions:
   `notes`, `harmonic`, `dynamic`, `structural`.
3. For each dimension, determines which sides changed it:
   - **Unchanged** → keep base.
   - **One side only** → take that side automatically.
   - **Both sides** → call `resolve_strategy(rules, path, dim)` for each
     user-facing alias of that dimension.
     - `ours` or `theirs` → apply and continue.
     - Anything else → dimension conflict; fall back to Pass 3.
4. If all dimensions are resolved, reconstructs a merged MIDI file (type 0,
   preserving `ticks_per_beat` from the base) and stores it as a new object.

The merged file contains the winning dimension events interleaved by absolute
tick time.

### Pass 3 — True conflict

Paths and dimensions that no rule resolves are reported as conflicts.
`MERGE_STATE.json` is written and `muse merge` exits non-zero.

### Manual forcing

For paths that auto-merged cleanly on both sides, a `manual` rule in
`.museattributes` forces them into the conflict list anyway.  This is useful for
contractually sensitive files that always require human sign-off.

---

## Music domain examples

```
# Drums are always authoritative — take our version on every dimension:
drums/*     *          ours

# Accept a collaborator's harmonic changes on key instruments:
keys/*      harmonic   theirs
bass/*      harmonic   theirs

# Require manual review for all structural changes project-wide:
*           structural manual

# Default for everything else:
*           *          auto
```

### What this achieves

If both branches modify `keys/piano.mid`:

- The `harmonic` dimension → `theirs` (collaborator's pitch-bends win).
- The `notes` dimension → no matching rule → dimension-level auto-merge.
  - If only one side changed notes → clean.
  - If both sides changed notes → conflict (no rule resolved it).
- The `structural` dimension → `manual` → always flagged for review.

---

## Generic domain examples

The `.museattributes` format is not music-specific.  Domain plugins define their
own dimension names.  Path patterns and strategy syntax are identical.

### Genomics

```
# Reference sequence is always canonical:
reference/*   *           ours

# Accept collaborator's annotations:
annotations/* semantic     theirs

# All structural edits require manual review:
*             structural   manual

# Default:
*             *            auto
```

### Scientific simulation

```
# Boundary conditions are owned by the lead author:
boundary/*    *            ours

# Accept collaborator's solver parameters:
params/*      numeric      theirs

# Require sign-off on mesh topology changes:
mesh/*        topology     manual
```

---

## CLI

```bash
muse attributes            # tabular display of rules
muse attributes --json     # JSON array for scripting
```

Example output:

```
Path pattern  Dimension   Strategy
------------  ----------  --------
drums/*       *           ours
keys/*        harmonic    theirs
*             structural  manual
*             *           auto
```

---

## `muse merge` output with attributes

When `.museattributes` auto-resolves a conflict, `muse merge` reports it:

```
  ✔ [ours] drums/kick.mid
  ✔ dimension-merge: keys/piano.mid (harmonic=right, notes=left, dynamic=base, structural=base)
Merged 'feature/harmonics' into 'main' (a1b2c3d4)
```

---

## Notes

- `ours` and `theirs` are positional: `ours` = the branch merging INTO (current
  HEAD), `theirs` = the branch merging FROM (incoming).
- Path patterns follow POSIX conventions (forward slashes).
- The file is optional.  Its absence has no effect on merge correctness — all
  paths use `auto`.
- `union` at the file level is equivalent to `auto` in the current
  implementation.  True event-level union (include both sides' note events)
  is reserved for a future release.
- MIDI dimension merge reconstructs a type-0 (single-track) file.  The
  original multi-track structure is preserved when all events fit into one
  track; multi-track reconstruction is a planned enhancement.

---

## Resolution precedence

Rules are evaluated top-to-bottom.  The first rule where **both** `path-pattern`
and `dimension` match (via `fnmatch`) wins.

If no rule matches, `auto` is returned.

---

## Implementation

Parsing and strategy resolution live in `muse/core/attributes.py`:

```python
from muse.core.attributes import load_attributes, resolve_strategy

rules = load_attributes(repo_root)                    # reads .museattributes
strategy = resolve_strategy(rules, "keys/piano.mid", "harmonic")  # → "theirs"
```

MIDI dimension merge lives in `muse/plugins/music/midi_merge.py`:

```python
from muse.plugins.music.midi_merge import extract_dimensions, merge_midi_dimensions

dims = extract_dimensions(midi_bytes)           # → MidiDimensions
result = merge_midi_dimensions(               # → (merged_bytes, report) | None
    base_bytes, left_bytes, right_bytes, rules, "keys/piano.mid"
)
```

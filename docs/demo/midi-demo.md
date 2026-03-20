# Muse MIDI Plugin — Demo

> **Version control for music is not "track changes to audio files."**
> **It is: version control that understands music.**

This is the full walk-through of every music-domain capability in Muse.
Every command below reasons about MIDI at the level of **individual notes,
chords, bars, and dimensions** — things no VCS has ever modelled.

Git stores music as binary blobs.  Muse stores it as a **content-addressed
graph of note events**, each with a stable identity that survives transpositions,
rearrangements, and cross-track moves.

---

## Setup

```bash
muse init --domain music
# Add some MIDI files to state/
cp ~/compositions/melody.mid state/tracks/melody.mid
cp ~/compositions/bass.mid   state/tracks/bass.mid
muse commit -m "Initial composition"
```

---

## Act I — What's in the Track?

### `muse midi notes` — musical notation view

```
$ muse midi notes tracks/melody.mid

tracks/melody.mid — 23 notes — cb4afaed
Key signature (estimated): G major

  Bar  Beat  Pitch  Vel  Dur(beats)  Channel
  ──────────────────────────────────────────────────
    1   1.00  G4      80    1.00      ch 0
    1   2.00  B4      75    0.50      ch 0
    1   2.50  D5      72    0.50      ch 0
    1   3.00  G4      80    1.00      ch 0
    2   1.00  A4      78    1.00      ch 0
    2   2.00  C5      75    0.75      ch 0
    ...

23 note(s) across 8 bar(s)
```

**Why Git can't do this:** `git show HEAD:tracks/melody.mid` gives you a
binary blob.  `muse midi notes` gives you the *actual musical content* — pitch
names, beat positions, durations, velocities — readable as sheet music,
queryable by an agent, auditable in a code review.

Use `--commit` to see the notes at any historical point:

```bash
muse midi notes tracks/melody.mid --commit HEAD~10
muse midi notes tracks/melody.mid --bar 4    # just bar 4
muse midi notes tracks/melody.mid --json    # machine-readable
```

---

## Act II — See the Score

### `muse midi piano-roll` — ASCII piano roll

```
$ muse midi piano-roll tracks/melody.mid --bars 1-4

Piano roll: tracks/melody.mid — cb4afaed  (bars 1–4,  res=2 cells/beat)

  D5   │D5════════              │                        │
  C5   │                        │C5════            C5════│════
  B4   │      B4════            │                        │
  A4   │                        │A4════════              │
  G4   │G4════════  G4════════  │                        │
       └────────────────────────┴────────────────────────┘
         1       2       3         1       2       3
```

One glance tells you everything: which pitches appear, how long they sustain,
where the bar lines fall.  This is the visual interface to a content-addressed
note graph.  It works on any historical snapshot.

```bash
muse midi piano-roll tracks/melody.mid --bars 1-8
muse midi piano-roll tracks/melody.mid --commit HEAD~5 --resolution 4  # sixteenth-note grid
```

---

## Act III — The Harmonic Layer

### `muse midi harmony` — chord analysis and key detection

```
$ muse midi harmony tracks/melody.mid

Harmonic analysis: tracks/melody.mid — cb4afaed
Key signature (estimated): G major
Total notes: 23  ·  Bars: 8

  Bar   Chord      Notes    Pitch classes
  ────────────────────────────────────────────────────────
    1   Gmaj           4    G, B, D
    2   Amin           3    A, C, E
    3   Cmaj           4    C, E, G
    4   D7             5    D, F#, A, C
    5   Gmaj           4    G, B, D
    6   Emin           3    E, G, B
    7   Amin           3    A, C, E
    8   Dmaj           4    D, F#, A

Pitch class distribution:
  G    ████████████████████ 8  (34.8%)
  B    ████████       3  (13.0%)
  D    ██████████     4  (17.4%)
  A    ████████       3  (13.0%)
  C    ██████         2  ( 8.7%)
  E    ██             1  ( 4.3%)
  F#   ██             1  ( 4.3%)
```

**This is impossible in Git** because Git has no model of what the bytes in a
`.mid` file mean.  Muse stores every note as a typed semantic event with a
stable content ID.  `muse midi harmony` reads the note graph and applies music
theory to find the implied chords — at any commit, for any track.

For AI agents, `muse midi harmony` is gold: an agent composing in a key can verify
the harmonic content of its work before committing.

---

## Act IV — The Dynamic Layer

### `muse midi velocity-profile` — dynamic range analysis

```
$ muse midi velocity-profile tracks/melody.mid

Velocity profile: tracks/melody.mid — cb4afaed
Notes: 23  ·  Range: 48–96  ·  Mean: 78.3  ·  RMS: 79.1

  ppp (  1– 15)  │                                │    0
  pp  ( 16– 31)  │                                │    0
  p   ( 32– 47)  │                                │    0
  mp  ( 48– 63)  │████                            │    2  ( 8.7%)
  mf  ( 64– 79)  │████████████████████████        │   12  (52.2%)
  f   ( 80– 95)  │████████████                    │    8  (34.8%)
  ff  ( 96–111)  │██                              │    1  ( 4.3%)
  fff (112–127)  │                                │    0

Dynamic character: mf
```

```
$ muse midi velocity-profile tracks/melody.mid --by-bar

  bar    1  ████████████████████████████████   avg= 80.0  (4 notes)
  bar    2  ██████████████████████████         avg= 76.0  (3 notes)
  bar    3  ████████████████████████████       avg= 78.0  (4 notes)
  bar    4  ████████████████████████████████   avg= 80.5  (5 notes)
```

The per-bar view reveals the dynamic arc of the composition — a crescendo
building through bars 1–4, a release in bars 5–6.  Agents can use this to
verify that a composition has the intended emotional shape.

---

## Act V — Note-Level History

### `muse midi note-log` — what changed in each commit

```
$ muse midi note-log tracks/melody.mid

Note history: tracks/melody.mid
Commits analysed: 12

cb4afaed  2026-03-16  "Add bridge section"  (4 changes)
  +  A4 vel=78 @beat=9.00 dur=1.00 ch 0
  +  B4 vel=75 @beat=10.00 dur=0.75 ch 0
  +  G4 vel=80 @beat=11.00 dur=1.00 ch 0
  +  D5 vel=72 @beat=12.00 dur=0.50 ch 0

1d2e3faa  2026-03-15  "Revise verse harmony"  (2 changes)
  +  D4 vel=75 @beat=5.00 dur=1.00 ch 0
  -  C4 vel=72 @beat=5.00 dur=1.00 ch 0  (removed)

a3f2c9e1  2026-03-14  "Initial composition"  (14 changes)
  +  G4 vel=80 @beat=1.00 dur=1.00 ch 0
  +  B4 vel=75 @beat=2.00 dur=0.50 ch 0
  ...
```

**Every change expressed in musical language**, not binary diffs.

`muse midi note-log` is the musical equivalent of `git log -p` — but instead of
showing `+line` / `-line`, it shows `+note` / `-note` with pitch name, beat
position, velocity, and duration.  A composer reading this log understands
immediately what changed between commits.

---

## Act VI — Note Attribution

### `muse midi note-blame` — which commit wrote these notes?

```
$ muse midi note-blame tracks/melody.mid --bar 4

Note attribution: tracks/melody.mid  bar 4

  D5    vel=72  @beat=1.00  dur=0.50  ch 0
  F#5   vel=75  @beat=1.50  dur=0.50  ch 0
  A5    vel=78  @beat=2.00  dur=1.00  ch 0
  C6    vel=72  @beat=3.00  dur=0.50  ch 0
  A5    vel=75  @beat=3.50  dur=0.50  ch 0

  5 notes in bar 4 introduced by:
  cb4afaed  2026-03-16  alice  "Add D7 arpeggiation in bar 4"
```

**This is strictly impossible in Git.**

Git cannot tell you "these specific notes in bar 4 were added in commit X"
because Git has no model of notes or bars.  `muse midi note-blame` traces the
exact content IDs of each note in the bar through the commit history to find
the commit that first inserted them.

For AI agents working collaboratively: "which agent wrote this phrase?"
One command. One answer.

---

## Act VII — Where is the Compositional Instability?

### `muse midi hotspots` — bar-level churn

```
$ muse midi hotspots --top 10

Note churn — top 10 most-changed bars
Commits analysed: 47

  1   tracks/melody.mid    bar  8    12 changes
  2   tracks/melody.mid    bar  4     9 changes
  3   tracks/bass.mid      bar  8     7 changes
  4   tracks/piano.mid     bar 12     5 changes
  5   tracks/melody.mid    bar 16     4 changes

High churn = compositional instability. Consider locking this section.
```

Bar 8 is the trouble spot.  Twelve revisions.  An agent or composer working
on a large piece can use this to identify which sections are unresolved —
the musical equivalent of `muse code hotspots` for code.

```bash
muse midi hotspots --track tracks/melody.mid   # focus on one track
muse midi hotspots --from HEAD~20 --top 5      # last 20 commits
```

---

## Act VIII — Agent Command: Transpose

### `muse midi transpose` — surgical pitch transformation

```bash
# Preview
$ muse midi transpose tracks/melody.mid --semitones 7 --dry-run

[dry-run] Would transpose tracks/melody.mid  +7 semitones
  Notes:       23
  Shifts:      G4 → D5, B4 → F#5, D5 → A5, …
  Pitch range: D5–A6  (was G4–D6)
  No changes written (--dry-run).

# Apply
$ muse midi transpose tracks/melody.mid --semitones 7

✅ Transposed tracks/melody.mid  +7 semitones
   23 notes shifted  (G4 → D5, B4 → F#5, D5 → A5, …)
   Pitch range: D5–A6  (was G4–D6)
   Run `muse status` to review, then `muse commit`
```

```bash
muse midi transpose tracks/bass.mid --semitones -12   # down an octave
muse midi transpose tracks/melody.mid --semitones 5   # up a perfect fourth
muse midi transpose tracks/melody.mid --semitones 2 --clamp  # clamp to MIDI range
```

For AI agents, `muse midi transpose` is the music equivalent of `muse code patch`:
a single command that applies a well-defined musical transformation.  The
agent says "move this track up a fifth" — Muse applies it surgically and
records the note-level delta in the next commit.

After transposing:

```bash
muse status          # shows melody.mid as modified
muse midi harmony tracks/melody.mid   # verify the new key — still G major? No, now D major
muse commit -m "Transpose melody up a fifth for verse 2"
```

The commit's structured delta records every note that changed pitch —
a note-level diff of the entire transposition.

---

## Act IX — Agent Command: Mix

### `muse midi mix` — layer two tracks into one

```bash
$ muse midi mix tracks/melody.mid tracks/harmony.mid \
    --output tracks/full.mid \
    --channel-a 0 \
    --channel-b 1

✅ Mixed tracks/melody.mid + tracks/harmony.mid → tracks/full.mid
   melody.mid:   23 notes  (G4–D6)
   harmony.mid:  18 notes  (C3–B4)
   full.mid:     41 notes  (C3–D6)
   Run `muse status` to review, then `muse commit`
```

`muse midi mix` is the compositional assembly command for the AI age.  An agent
that has generated a melody and a harmony in separate tracks can combine them
into a single performance track without a merge conflict.

The `--channel-a` / `--channel-b` flags assign distinct MIDI channels to each
source so instruments can be differentiated in the mixed output.

Agent workflow for a full arrangement:

```bash
# Agent generates individual parts
muse midi transpose tracks/violin.mid --semitones 0  # keeps content hash consistent
muse midi mix tracks/violin.mid tracks/cello.mid --output tracks/strings.mid --channel-a 0 --channel-b 1
muse midi mix tracks/strings.mid tracks/piano.mid  --output tracks/ensemble.mid --channel-a 0 --channel-b 2
muse commit -m "Assemble full ensemble arrangement"

# Verify the harmonic content of the final mix
muse midi harmony tracks/ensemble.mid
muse midi velocity-profile tracks/ensemble.mid --by-bar
```

---

## Act X — Rhythmic Intelligence

### `muse midi rhythm` — syncopation, swing, quantisation

```
$ muse midi rhythm tracks/drums.mid

Rhythmic analysis: tracks/drums.mid — working tree
Notes: 64  ·  Bars: 8  ·  Notes/bar avg: 8.0
Dominant subdivision: sixteenth
Quantisation score:   0.942  (very tight)
Syncopation score:    0.382  (moderate)
Swing ratio:          1.003  (straight)
```

Every rhythmic dimension in one command — impossible in Git.

```bash
muse midi rhythm tracks/drums.mid --commit HEAD~3    # historical snapshot
muse midi rhythm tracks/bass.mid --json              # agent-readable
```

---

### `muse midi tempo` — BPM estimation

```
$ muse midi tempo tracks/drums.mid

Tempo analysis: tracks/drums.mid — working tree
Estimated BPM:    96.0
Ticks per beat:   480
Confidence:       high  (ioi_voting method)
```

Uses inter-onset interval voting to estimate the underlying beat.  Use `--json` to pipe into downstream agents that need to match tempo across branches.

---

### `muse midi density` — note density arc

```
$ muse midi density tracks/drums.mid

Note density: tracks/drums.mid — working tree
Bars: 8  ·  Peak: bar 5 (6.25 notes/beat)  ·  Avg: 5.1

bar   1  ████████████         4.00 notes/beat  (16 notes)
bar   2  ████████████████████ 5.25 notes/beat  (21 notes)
bar   3  █████████████        4.25 notes/beat  (17 notes)
bar   4  █████████████        4.00 notes/beat  (16 notes)
bar   5  ████████████████████ 6.25 notes/beat  (25 notes)  ← peak
```

Reveals textural arc: sparse verses, dense choruses, quiet codas.

---

## Act XI — Pitch & Harmony (Deep)

### `muse midi scale` — scale and mode detection

```
$ muse midi scale tracks/epiano.mid --top 3

Scale analysis: tracks/epiano.mid — working tree

  Rank  Root   Scale             Confidence  Out-of-scale
  ─────────────────────────────────────────────────────────
     1  E      natural minor          0.971             0
     2  E      dorian                 0.929             2
     3  A      major                  0.886             4
```

Goes beyond key: tests 15 scale types (major, minor, all seven modes, pentatonic, blues, whole-tone, diminished, chromatic) across all 12 roots.

```bash
muse midi scale tracks/lead.mid                     # top 3 matches
muse midi scale tracks/melody.mid --top 5 --json    # agent-readable
```

---

### `muse midi tension` — harmonic tension curve

```
$ muse midi tension tracks/epiano.mid

Harmonic tension: tracks/epiano.mid — working tree

bar  1  ▂▂▂▂▂▂▂▂           0.08  consonant
bar  2  ████████████████   0.43  mild
bar  3  ████████████████████  0.67  tense
bar  4  ████               0.12  consonant
```

Scores each bar's dissonance level from 0 (consonant) to 1 (maximally tense).  Agents can use this as a quality gate: tension should build toward climaxes and resolve at cadences.

---

### `muse midi cadence` — cadence detection

```
$ muse midi cadence tracks/epiano.mid

Cadence analysis: tracks/epiano.mid — working tree
Found 2 cadences

  Bar   Type         From       To
  ──────────────────────────────────────
    5   half         Em         Bdom7
    9   authentic    Bdom7      Em     ← resolution
```

Detects authentic, deceptive, half, and plagal cadences at phrase boundaries.  Use `--strict` to fail CI if a composition lacks proper phrase closure.

---

### `muse midi contour` — melodic contour

```
$ muse midi contour tracks/lead.mid

Melodic contour: tracks/lead.mid — working tree
Shape:             arch
Pitch range:       D3 – C6  (35 semitones)
Direction changes: 6
Avg interval size: 2.43 semitones

Interval sequence (semitones):
  +2 +3 +2 -1 +4 -3 +2 -2 -3 +1 -1 +2 …
```

Six shape types: ascending, descending, arch, valley, wave, flat.  A fast structural fingerprint: detect when an agent has accidentally flattened or inverted a melody.

---

## Act XII — Structure & Counterpoint

### `muse midi motif` — recurring pattern detection

```
$ muse midi motif tracks/lead.mid

Motif analysis: tracks/lead.mid — working tree
Found 2 motifs

Motif 0  [+2 +2 -3]   3×   first: E4   bars: 1, 5, 9
Motif 1  [-2 +4 -2]   2×   first: G4   bars: 3, 7
```

Scans the interval sequence between consecutive notes for repeated sub-sequences.  Identifies thematic material independent of key — the pattern `[+2 +2 -3]` is the same motif whether it starts on E4 or G3.

```bash
muse midi motif tracks/melody.mid --min-length 4 --min-occurrences 3
muse midi motif tracks/theme.mid --commit HEAD~5    # did the motif survive the merge?
```

---

### `muse midi voice-leading` — counterpoint lint

```
$ muse midi voice-leading tracks/strings.mid

Voice-leading check: tracks/strings.mid — working tree
⚠️  2 issues found

  Bar   Type               Description
  ──────────────────────────────────────────────────────
    6   parallel_fifths    voices 0–1: parallel perfect fifths
    9   large_leap         top voice: leap of 11 semitones
```

Detects parallel fifths, parallel octaves, and large leaps in the top voice.  Use `--strict` in CI pipelines to block agents from committing harmonically problematic voice-leading.

```bash
muse midi voice-leading tracks/choir.mid --strict    # CI gate
muse midi voice-leading tracks/strings.mid --json    # agent-readable
```

---

### `muse midi instrumentation` — channel & register map

```
$ muse midi instrumentation tracks/full_score.mid

Instrumentation map: tracks/full_score.mid — working tree
Channels: 3  ·  Total notes: 106

  Ch   Notes  Range        Register   Mean vel
  ───────────────────────────────────────────────
   0      34  C2–G2        bass         84.2
   1      40  E4–B5        treble       71.8
   2      32  C3–A4        mid          78.6
```

Shows which MIDI channels carry notes, the pitch range each channel spans, and the register.  Verify that the bass channel stays low and the melody occupies the right register.

---

## Act XIII — History Deep-Dive

### `muse midi compare` — semantic diff between commits

```
$ muse midi compare tracks/epiano.mid HEAD~2 HEAD

Semantic comparison: tracks/epiano.mid
A: HEAD~2 (1b3c8f02)   B: HEAD (3f0b5c8d)

  Dimension          A              B              Δ
  ──────────────────────────────────────────────────────────
  Notes              18             32             +14
  Bars                4              8             +4
  Key                E minor        E minor         =
  Density avg        4.5/beat       5.1/beat       +0.6
  Swing ratio        1.00           1.00            0.0
  Syncopation        0.11           0.38           +0.27 (more syncopated)
  Quantisation       0.97           0.94           -0.03
  Subdivision        quarter        sixteenth       changed
```

Musical meaning of a diff: not "binary changed" but "8 bars added, syncopation doubled, subdivision tightened to sixteenth notes."

---

## Act XIV — Multi-Agent Intelligence

### `muse midi agent-map` — bar-level blame

```
$ muse midi agent-map tracks/lead.mid

Agent map: tracks/lead.mid

  Bar   Last author              Commit    Message
  ──────────────────────────────────────────────────────────────
    1   agent-melody             3f0b5c8d  Groove: full kit + lead
    2   agent-melody             3f0b5c8d  Groove: full kit + lead
    3   agent-harmony            4e2c91aa  Harmony: modal interchange
    4   agent-harmony            4e2c91aa  Harmony: modal interchange
    5   agent-arranger           1b2c3d4e  Structure: add bridge
```

The musical equivalent of `git blame` at the bar level.  "Which agent owns bars 3–4?"  One command.

```bash
muse midi agent-map tracks/lead.mid --depth 100    # walk deeper history
muse midi agent-map tracks/bass.mid --json         # pipe to dashboard
```

---

### `muse midi find-phrase` — phrase similarity search

```
$ muse midi find-phrase tracks/lead.mid --query query/motif.mid --depth 20

Phrase search: tracks/lead.mid  (query: query/motif.mid)
Scanning 20 commits…

  Score   Commit    Author              Message
  ──────────────────────────────────────────────────────────────────
  0.934   3f0b5c8d  agent-melody        Groove: full arrangement
  0.812   4e2c91aa  agent-harmony       Harmony: modal interchange
  0.643   2d9e1a47  agent-melody        Groove: syncopated kick
```

Answer the question: "At which commit did this theme first appear, and on which branches does it still live?"  Uses pitch-class histogram and interval fingerprint similarity — finds the motif regardless of transposition.

---

### `muse midi shard` — partition for parallel agents

```
$ muse midi shard tracks/full.mid --shards 4

Shard plan: tracks/full.mid  →  4 shards
Total bars: 16  ·  ~4 bars per shard

Shard 0  bars  1– 4  →  shards/full_shard_0.mid  (48 notes)
Shard 1  bars  5– 8  →  shards/full_shard_1.mid  (52 notes)
Shard 2  bars  9–12  →  shards/full_shard_2.mid  (41 notes)
Shard 3  bars 13–16  →  shards/full_shard_3.mid  (38 notes)

✅ 4 shards written to shards/
```

The musical equivalent of `muse coord shard` for code: partition a composition into non-overlapping bar ranges so an agent swarm can work in parallel with zero note-level conflicts.  Merge the shards back with `muse midi mix`.

```bash
muse midi shard tracks/symphony.mid --bars-per-shard 32 --output-dir agents/
muse midi shard tracks/full.mid --shards 8 --dry-run    # preview plan
```

---

## Act XV — Transformation Commands

### `muse midi quantize` — snap to rhythmic grid

```bash
# Preview
$ muse midi quantize tracks/piano.mid --grid 16th --strength 0.8 --dry-run

[dry-run] Would quantise tracks/piano.mid  →  16th-note grid  (strength=0.80)
  Notes adjusted:  28 / 32
  Avg tick shift:  18.4  ·  Max: 57
  No changes written (--dry-run).

# Apply
$ muse midi quantize tracks/piano.mid --grid 16th
```

Grid values: `whole`, `half`, `quarter`, `8th`, `16th`, `32nd`, `triplet-8th`, `triplet-16th`.
Use `--strength` < 1.0 for partial quantisation that preserves human feel.

---

### `muse midi humanize` — add human feel

```bash
$ muse midi humanize tracks/piano.mid --timing 0.015 --velocity 10 --seed 42

✅ Humanised tracks/piano.mid
   32 notes adjusted
   Timing jitter: ±0.015 beats  ·  Velocity jitter: ±10
   Run `muse status` to review, then `muse commit`
```

Applies controlled randomness to onset times and velocities.  Use `--seed` for reproducible results in deterministic agent pipelines.

---

### `muse midi invert` — melodic inversion

```bash
$ muse midi invert tracks/melody.mid --pivot E4 --dry-run

[dry-run] Would invert tracks/melody.mid  (pivot: E4 / MIDI 64)
  Notes:      23
  Transforms: G4 → C4, B4 → A3, D5 → F3, …
  New range:  B1–E4  (was E4–G6)
  No changes written (--dry-run).
```

Every upward interval becomes downward and vice versa, reflected around the pivot.  Classic fugal transformation — combinable with the original for invertible counterpoint.

---

### `muse midi retrograde` — play it backward

```bash
$ muse midi retrograde tracks/melody.mid

✅ Retrograded tracks/melody.mid
   23 notes reversed  (G4 → was last, now first)
   Duration preserved  ·  original span: 8.00 beats
   Run `muse status` to review, then `muse commit`
```

Reverses pitch order while preserving timing, velocity, and duration.  Fundamental twelve-tone operation; impossible to describe in Git's binary model.

---

### `muse midi arpeggiate` — chords → arpeggios

```bash
$ muse midi arpeggiate tracks/epiano.mid --rate 8th --order up-down

✅ Arpeggiated tracks/epiano.mid  (8th-note rate, up-down order)
   8 chord clusters → 40 arpeggio notes
   Run `muse status` to review, then `muse commit`
```

Orders: `up`, `down`, `up-down` (ping-pong), `random` (with `--seed` for reproducibility).

---

### `muse midi normalize` — rescale velocities

```bash
$ muse midi normalize tracks/lead.mid --min 50 --max 100

✅ Normalised tracks/lead.mid
   32 notes rescaled  ·  range: 62–104 → 50–100
   Mean velocity: 83.0 → 75.2
   Run `muse status` to review, then `muse commit`
```

Linearly maps the existing velocity range to [--min, --max], preserving relative dynamics.  Essential first step when integrating tracks from multiple agents recorded at different volumes.

---

## The Full Collaborative Music Workflow

Here's what a multi-agent music session looks like with Muse:

### Session Setup

```bash
muse init --domain music
# Agent A starts the melody
echo "..." | muse-generate --type melody > state/tracks/melody.mid
muse commit -m "Agent A: initial melody sketch"
```

### Agent B Adds Harmony

```bash
# Agent B branches
git checkout -b feat/harmony  # Muse branching

# Analyse what Agent A wrote
muse midi notes tracks/melody.mid
muse midi harmony tracks/melody.mid        # Key: G major
muse midi velocity-profile tracks/melody.mid  # Dynamic: mf

# Generate a compatible harmony
echo "..." | muse-generate --type harmony --key "G major" > state/tracks/harmony.mid
muse commit -m "Agent B: add harmony in G major"
```

### Merge

```bash
# Three-way merge at the note level
muse merge feat/harmony

# If both agents touched the same MIDI file:
#   Muse splits into melodic / rhythmic / harmonic / dynamic / structural dimensions
#   Each dimension merges independently
#   Only true note-level conflicts surface as merge conflicts
```

### Quality Check

```bash
# After merge, verify the full picture
muse midi harmony tracks/melody.mid    # still G major?
muse midi hotspots --top 5        # which bars got the most revisions?
muse midi velocity-profile tracks/melody.mid  # did the dynamics survive the merge?
muse midi piano-roll tracks/melody.mid --bars 1-8  # visual sanity check
```

---

## The Full Command Matrix — 31 Semantic Porcelain Commands

### Notation & Visualization

| Command | What it does |
|---------|-------------|
| `muse midi notes` | Every note as musical notation: pitch name, beat, velocity, duration |
| `muse midi piano-roll` | ASCII piano roll — pitches on Y-axis, time on X-axis |
| `muse midi instrumentation` | Per-channel note range, register (bass/mid/treble), velocity map |

### Pitch, Harmony & Scale

| Command | What it does |
|---------|-------------|
| `muse midi harmony` | Bar-by-bar chord detection + Krumhansl-Schmuckler key signature |
| `muse midi scale` | Scale/mode detection: 15 types × 12 roots, ranked by confidence |
| `muse midi contour` | Melodic contour shape (arch, ascending, valley, wave…) + interval sequence |
| `muse midi tension` | Harmonic tension curve: dissonance score per bar from interval weights |
| `muse midi cadence` | Cadence detection: authentic, deceptive, half, plagal at phrase boundaries |

### Rhythm & Dynamics

| Command | What it does |
|---------|-------------|
| `muse midi rhythm` | Syncopation score, swing ratio, quantisation accuracy, dominant subdivision |
| `muse midi tempo` | BPM estimation via IOI voting; confidence rated high/medium/low |
| `muse midi density` | Notes-per-beat per bar — textural arc of a composition |
| `muse midi velocity-profile` | Dynamic range, RMS velocity, and histogram (ppp–fff) |

### Structure & Voice Leading

| Command | What it does |
|---------|-------------|
| `muse midi motif` | Recurring interval-pattern (motif) detection, transposition-invariant |
| `muse midi voice-leading` | Parallel fifths/octaves + large leaps — classical counterpoint lint |
| `muse midi compare` | Semantic diff across key, rhythm, density, swing between two commits |

### History & Attribution

| Command | What it does |
|---------|-------------|
| `muse midi note-log` | Note-level commit history: pitches added/removed per commit |
| `muse midi note-blame` | Per-bar attribution: which commit introduced each note |
| `muse midi hotspots` | Bar-level churn leaderboard: which bars change most across commits |

### Multi-Agent Intelligence

| Command | What it does |
|---------|-------------|
| `muse midi agent-map` | Bar-level blame: which agent last edited each bar |
| `muse midi find-phrase` | Similarity search for a melodic phrase across all commit history |
| `muse midi shard` | Partition composition into N bar-range shards for parallel agent work |
| `muse midi query` | MIDI DSL predicate query: bar, pitch, velocity, agent, chord |

### Transformation

| Command | What it does |
|---------|-------------|
| `muse midi transpose` | Shift all pitches by N semitones; dry-run + clamp support |
| `muse midi invert` | Melodic inversion around a pivot pitch |
| `muse midi retrograde` | Reverse pitch order (retrograde transformation) |
| `muse midi quantize` | Snap onsets to a rhythmic grid with adjustable strength |
| `muse midi humanize` | Add timing/velocity jitter for human feel; seed for determinism |
| `muse midi arpeggiate` | Convert chord voicings to arpeggios (up/down/up-down/random) |
| `muse midi normalize` | Rescale velocities to a target dynamic range |
| `muse midi mix` | Combine notes from two MIDI tracks into one output file |

### Invariants & Quality Gates

| Command | What it does |
|---------|-------------|
| `muse midi check` | Enforce MIDI invariant rules: polyphony, range, key, parallel fifths |

---

Every command above operates on structured note data and works at any historical commit.
Every one is impossible in Git, which stores MIDI as an opaque binary blob.

---

## For AI Agents Creating Music

When millions of agents are composing music in real-time, you need:

1. **Musical reads** — `notes`, `harmony`, `scale`, `contour`, `rhythm`, `tension`, `density`
   return structured data agents can reason about, not binary blobs.

2. **Musical writes** — `transpose`, `invert`, `retrograde`, `quantize`, `humanize`,
   `arpeggiate`, `normalize`, `mix` apply well-defined transformations with full note-level
   attribution in the next commit.

3. **Swarm coordination** — `shard` partitions the composition for parallel agents;
   `agent-map` shows who owns which bars; `find-phrase` locates thematic material across
   branches; `query` answers arbitrary musical questions across all history.

4. **Quality gates** — `check` enforces MIDI invariants; `voice-leading --strict` blocks
   parallel fifths; `cadence` verifies phrase closure; `tension` ensures the emotional arc.

5. **Semantic merges** — two agents independently harmonizing the same melody
   can merge at the note level — changes to non-overlapping notes never conflict.

6. **Structured history** — every commit records a note-level structured delta;
   every note has a content ID; `note-blame` attributes any bar to any agent;
   `compare` shows the musical meaning of any diff.

Muse doesn't just store your music.  It understands it.

---

*Next: [Code Demo →](demo-code.md)*

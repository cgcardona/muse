# Muse Music Plugin — Tour de Force

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
# Add some MIDI files to muse-work/
cp ~/compositions/melody.mid muse-work/tracks/melody.mid
cp ~/compositions/bass.mid   muse-work/tracks/bass.mid
muse commit -m "Initial composition"
```

---

## Act I — What's in the Track?

### `muse notes` — musical notation view

```
$ muse notes tracks/melody.mid

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
binary blob.  `muse notes` gives you the *actual musical content* — pitch
names, beat positions, durations, velocities — readable as sheet music,
queryable by an agent, auditable in a code review.

Use `--commit` to see the notes at any historical point:

```bash
muse notes tracks/melody.mid --commit HEAD~10
muse notes tracks/melody.mid --bar 4    # just bar 4
muse notes tracks/melody.mid --json    # machine-readable
```

---

## Act II — See the Score

### `muse piano-roll` — ASCII piano roll

```
$ muse piano-roll tracks/melody.mid --bars 1-4

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
muse piano-roll tracks/melody.mid --bars 1-8
muse piano-roll tracks/melody.mid --commit HEAD~5 --resolution 4  # sixteenth-note grid
```

---

## Act III — The Harmonic Layer

### `muse harmony` — chord analysis and key detection

```
$ muse harmony tracks/melody.mid

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
stable content ID.  `muse harmony` reads the note graph and applies music
theory to find the implied chords — at any commit, for any track.

For AI agents, `muse harmony` is gold: an agent composing in a key can verify
the harmonic content of its work before committing.

---

## Act IV — The Dynamic Layer

### `muse velocity-profile` — dynamic range analysis

```
$ muse velocity-profile tracks/melody.mid

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
$ muse velocity-profile tracks/melody.mid --by-bar

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

### `muse note-log` — what changed in each commit

```
$ muse note-log tracks/melody.mid

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

`muse note-log` is the musical equivalent of `git log -p` — but instead of
showing `+line` / `-line`, it shows `+note` / `-note` with pitch name, beat
position, velocity, and duration.  A composer reading this log understands
immediately what changed between commits.

---

## Act VI — Note Attribution

### `muse note-blame` — which commit wrote these notes?

```
$ muse note-blame tracks/melody.mid --bar 4

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
because Git has no model of notes or bars.  `muse note-blame` traces the
exact content IDs of each note in the bar through the commit history to find
the commit that first inserted them.

For AI agents working collaboratively: "which agent wrote this phrase?"
One command. One answer.

---

## Act VII — Where is the Compositional Instability?

### `muse note-hotspots` — bar-level churn

```
$ muse note-hotspots --top 10

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
the musical equivalent of `muse hotspots` for code.

```bash
muse note-hotspots --track tracks/melody.mid   # focus on one track
muse note-hotspots --from HEAD~20 --top 5      # last 20 commits
```

---

## Act VIII — Agent Command: Transpose

### `muse transpose` — surgical pitch transformation

```bash
# Preview
$ muse transpose tracks/melody.mid --semitones 7 --dry-run

[dry-run] Would transpose tracks/melody.mid  +7 semitones
  Notes:       23
  Shifts:      G4 → D5, B4 → F#5, D5 → A5, …
  Pitch range: D5–A6  (was G4–D6)
  No changes written (--dry-run).

# Apply
$ muse transpose tracks/melody.mid --semitones 7

✅ Transposed tracks/melody.mid  +7 semitones
   23 notes shifted  (G4 → D5, B4 → F#5, D5 → A5, …)
   Pitch range: D5–A6  (was G4–D6)
   Run `muse status` to review, then `muse commit`
```

```bash
muse transpose tracks/bass.mid --semitones -12   # down an octave
muse transpose tracks/melody.mid --semitones 5   # up a perfect fourth
muse transpose tracks/melody.mid --semitones 2 --clamp  # clamp to MIDI range
```

For AI agents, `muse transpose` is the music equivalent of `muse patch`:
a single command that applies a well-defined musical transformation.  The
agent says "move this track up a fifth" — Muse applies it surgically and
records the note-level delta in the next commit.

After transposing:

```bash
muse status          # shows melody.mid as modified
muse harmony tracks/melody.mid   # verify the new key — still G major? No, now D major
muse commit -m "Transpose melody up a fifth for verse 2"
```

The commit's structured delta records every note that changed pitch —
a note-level diff of the entire transposition.

---

## Act IX — Agent Command: Mix

### `muse mix` — layer two tracks into one

```bash
$ muse mix tracks/melody.mid tracks/harmony.mid \
    --output tracks/full.mid \
    --channel-a 0 \
    --channel-b 1

✅ Mixed tracks/melody.mid + tracks/harmony.mid → tracks/full.mid
   melody.mid:   23 notes  (G4–D6)
   harmony.mid:  18 notes  (C3–B4)
   full.mid:     41 notes  (C3–D6)
   Run `muse status` to review, then `muse commit`
```

`muse mix` is the compositional assembly command for the AI age.  An agent
that has generated a melody and a harmony in separate tracks can combine them
into a single performance track without a merge conflict.

The `--channel-a` / `--channel-b` flags assign distinct MIDI channels to each
source so instruments can be differentiated in the mixed output.

Agent workflow for a full arrangement:

```bash
# Agent generates individual parts
muse transpose tracks/violin.mid --semitones 0  # keeps content hash consistent
muse mix tracks/violin.mid tracks/cello.mid --output tracks/strings.mid --channel-a 0 --channel-b 1
muse mix tracks/strings.mid tracks/piano.mid  --output tracks/ensemble.mid --channel-a 0 --channel-b 2
muse commit -m "Assemble full ensemble arrangement"

# Verify the harmonic content of the final mix
muse harmony tracks/ensemble.mid
muse velocity-profile tracks/ensemble.mid --by-bar
```

---

## The Full Collaborative Music Workflow

Here's what a multi-agent music session looks like with Muse:

### Session Setup

```bash
muse init --domain music
# Agent A starts the melody
echo "..." | muse-generate --type melody > muse-work/tracks/melody.mid
muse commit -m "Agent A: initial melody sketch"
```

### Agent B Adds Harmony

```bash
# Agent B branches
git checkout -b feat/harmony  # Muse branching

# Analyse what Agent A wrote
muse notes tracks/melody.mid
muse harmony tracks/melody.mid        # Key: G major
muse velocity-profile tracks/melody.mid  # Dynamic: mf

# Generate a compatible harmony
echo "..." | muse-generate --type harmony --key "G major" > muse-work/tracks/harmony.mid
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
muse harmony tracks/melody.mid    # still G major?
muse note-hotspots --top 5        # which bars got the most revisions?
muse velocity-profile tracks/melody.mid  # did the dynamics survive the merge?
muse piano-roll tracks/melody.mid --bars 1-8  # visual sanity check
```

---

## The Full Command Matrix

| Command | What it does | Impossible in Git because… |
|---------|-------------|---------------------------|
| `muse notes` | Every note as musical notation | Git stores .mid as binary |
| `muse note-log` | Note-level change history | Git log shows binary diffs |
| `muse note-blame` | Per-bar attribution | Git blame is per line |
| `muse harmony` | Chord analysis + key detection | Git has no MIDI model |
| `muse piano-roll` | ASCII piano roll visualization | Git has no MIDI model |
| `muse note-hotspots` | Bar-level churn leaderboard | Git churn is file/line-level |
| `muse velocity-profile` | Dynamic range + histogram | Git has no MIDI model |
| `muse transpose` | Surgical pitch transformation | Git has no musical operations |
| `muse mix` | Combine two tracks into one | Git has no MIDI assembly |

Plus the core VCS operations, all working at note level:

| Command | What's different in Muse |
|---------|-------------------------|
| `muse commit` | Structured delta records note-level inserts/deletes |
| `muse diff` | Shows "C4 added at beat 3.5", not "binary changed" |
| `muse merge` | Three-way merge per dimension (melodic/harmonic/dynamic/structural) |
| `muse show` | Displays note-level changes in musical notation |

---

## For AI Agents Creating Music

When millions of agents are composing music in real-time, you need:

1. **Musical reads** — `muse notes`, `muse harmony`, `muse piano-roll` return
   structured note data that agents can reason about, not binary blobs

2. **Musical writes** — `muse transpose`, `muse mix` apply well-defined
   transformations that produce valid MIDI, with full note-level attribution

3. **Creative intelligence** — `muse harmony` gives agents harmonic awareness;
   `muse velocity-profile` gives dynamic awareness; `muse note-hotspots` reveals
   which sections are in flux

4. **Semantic merges** — two agents independently harmonizing the same melody
   can merge at the note level — changes to non-overlapping notes never conflict

5. **Structured history** — every commit records a note-level structured delta;
   every note has a content ID; `muse note-blame` attributes any bar to any agent

Muse doesn't just store your music.  It understands it.

---

*Next: [Code Tour de Force →](tour-de-force-code.md)*

# MIDI Domain — Complete Reference

> **Engine:** `muse/plugins/music/` · **Dependency:** `mido` for MIDI I/O
> **Scope:** Every command, module, type, and protocol in the MIDI domain plugin

---

## Overview

The MIDI domain plugin treats a composition as a **content-addressed graph of note events** — not as a binary blob.  Every note becomes a `NoteInfo` record with a stable SHA-256 content ID derived from its five fields.  This unlocks operations that are structurally impossible in Git:

- Read a track as musical notation, not binary bytes.
- Attribute any bar to the exact commit (and agent) that wrote it.
- Detect recurring melodic motifs independent of transposition.
- Partition a composition for parallel agent work with zero note-level conflicts.
- Enforce harmonic quality gates (no parallel fifths, proper cadences) in CI.
- Cherry-pick a harmonic transformation from one branch to another.

---

## Contents

1. [Note Identity Model](#1-note-identity-model)
2. [Notation & Visualization Commands](#2-notation--visualization-commands)
3. [Pitch, Harmony & Scale Commands](#3-pitch-harmony--scale-commands)
4. [Rhythm & Dynamics Commands](#4-rhythm--dynamics-commands)
5. [Structure & Voice-Leading Commands](#5-structure--voice-leading-commands)
6. [History & Attribution Commands](#6-history--attribution-commands)
7. [Multi-Agent Intelligence Commands](#7-multi-agent-intelligence-commands)
8. [Transformation Commands](#8-transformation-commands)
9. [Invariants & Quality Gate Commands](#9-invariants--quality-gate-commands)
10. [Type Reference](#10-type-reference)

---

## 1. Note Identity Model

Every note is a `NoteInfo` NamedTuple carrying five fields:

| Field | Type | Description |
|---|---|---|
| `pitch` | `int` | MIDI note number (0–127; middle C = 60) |
| `velocity` | `int` | Note-on velocity (0–127) |
| `start_tick` | `int` | Onset in MIDI ticks from the beginning of the file |
| `duration_ticks` | `int` | Duration in MIDI ticks |
| `channel` | `int` | MIDI channel (0–15) |

The content ID of a note is `SHA-256(pitch | velocity | start_tick | duration_ticks | channel)`.
Two notes with identical content IDs are the same note regardless of which file or commit they appear in.

### Bar Conversion

MIDI ticks are converted to bar/beat positions using the file header's `ticks_per_beat` and an assumed 4/4 time signature.

```
bar   = (start_tick // ticks_per_beat // 4) + 1
beat  = ((start_tick // ticks_per_beat) % 4) + 1.0 + fraction_within_beat
```

All commands accept `--bars N` or `--bar N` to filter output to a specific bar or range.

---

## 2. Notation & Visualization Commands

### `muse midi notes TRACK`

List every note in a MIDI track as human-readable musical notation.

```
muse midi notes tracks/melody.mid
muse midi notes tracks/melody.mid --commit HEAD~10
muse midi notes tracks/melody.mid --bar 4
muse midi notes tracks/melody.mid --json
```

**Flags**

| Flag | Default | Description |
|---|---|---|
| `--commit TEXT` | working tree | Resolve the track from a historical commit |
| `--bar INTEGER` | all | Filter to a single bar |
| `--json` | false | Emit JSON instead of text |

**Text output columns:** `Bar`, `Beat`, `Pitch`, `Vel`, `Dur(beats)`, `Channel`

**JSON output**

```json
{
  "track": "tracks/melody.mid",
  "commit": "cb4afaed",
  "note_count": 23,
  "bar_count": 8,
  "key_estimate": "G major",
  "notes": [
    {
      "bar": 1, "beat": 1.0, "pitch_name": "G4", "pitch": 67,
      "velocity": 80, "duration_beats": 1.0, "channel": 0
    }
  ]
}
```

---

### `muse midi piano-roll TRACK`

ASCII piano roll visualization — pitch on the Y-axis, time on the X-axis, bar lines included.

```
muse midi piano-roll tracks/melody.mid
muse midi piano-roll tracks/melody.mid --bars 1-4
muse midi piano-roll tracks/melody.mid --commit HEAD~5 --resolution 4
```

**Flags**

| Flag | Default | Description |
|---|---|---|
| `--commit TEXT` | working tree | Historical snapshot |
| `--bars TEXT` | all | Bar range (`1-8`, `3-6`) |
| `--resolution INTEGER` | 2 | Cells per beat (2 = eighth-note, 4 = sixteenth-note) |

Each cell represents one time slice.  `════` indicates a sustained note; the pitch label appears at the onset.

---

### `muse midi instrumentation TRACK`

Per-channel note count, pitch range, register classification, and mean velocity.

```
muse midi instrumentation tracks/full_score.mid
muse midi instrumentation tracks/full_score.mid --commit HEAD --json
```

**Flags**

| Flag | Default | Description |
|---|---|---|
| `--commit TEXT` | working tree | Historical snapshot |
| `--json` | false | Machine-readable output |

**Register classification:** `bass` (MIDI < 48), `mid` (48–71), `treble` (≥ 72).

**JSON output**

```json
{
  "track": "tracks/full_score.mid",
  "channel_count": 3,
  "total_notes": 106,
  "channels": [
    {
      "channel": 0, "note_count": 34,
      "pitch_min": 36, "pitch_max": 43,
      "pitch_min_name": "C2", "pitch_max_name": "G2",
      "register": "bass", "mean_velocity": 84.2
    }
  ]
}
```

---

## 3. Pitch, Harmony & Scale Commands

### `muse midi harmony TRACK`

Bar-by-bar chord detection and key signature estimation using the Krumhansl-Schmuckler key-finding algorithm.

```
muse midi harmony tracks/melody.mid
muse midi harmony tracks/melody.mid --commit HEAD~3
muse midi harmony tracks/melody.mid --json
```

**Flags**

| Flag | Default | Description |
|---|---|---|
| `--commit TEXT` | working tree | Historical snapshot |
| `--json` | false | Machine-readable output |

**Text output:** key estimate, table of `Bar → Chord → Notes → Pitch classes`, pitch class distribution histogram.

**JSON output**

```json
{
  "track": "tracks/melody.mid",
  "key_estimate": "G major",
  "total_notes": 23,
  "bar_count": 8,
  "bars": [
    {"bar": 1, "chord": "Gmaj", "pitch_classes": ["G", "B", "D"], "note_count": 4}
  ],
  "pitch_class_distribution": {"G": 8, "B": 3, "D": 4}
}
```

---

### `muse midi scale TRACK`

Scale and mode detection across 15 scale types and all 12 chromatic roots, ranked by confidence.

**Supported scales:** major, natural minor, harmonic minor, melodic minor, dorian, phrygian, lydian, mixolydian, locrian, major pentatonic, minor pentatonic, blues, whole-tone, diminished, chromatic.

```
muse midi scale tracks/lead.mid
muse midi scale tracks/lead.mid --top 5
muse midi scale tracks/melody.mid --json
```

**Flags**

| Flag | Default | Description |
|---|---|---|
| `--commit TEXT` | working tree | Historical snapshot |
| `--top INTEGER` | 3 | Number of best-match results to display |
| `--json` | false | Machine-readable output |

**JSON output**

```json
{
  "track": "tracks/lead.mid",
  "matches": [
    {
      "rank": 1, "root": "E", "scale": "natural minor",
      "confidence": 0.971, "out_of_scale_count": 0
    }
  ]
}
```

---

### `muse midi contour TRACK`

Melodic contour analysis: shape classification, pitch range, direction-change count, and interval sequence.

**Shape types:** `ascending`, `descending`, `arch`, `valley`, `wave`, `flat`.

```
muse midi contour tracks/lead.mid
muse midi contour tracks/lead.mid --commit HEAD~5 --json
```

**Flags**

| Flag | Default | Description |
|---|---|---|
| `--commit TEXT` | working tree | Historical snapshot |
| `--json` | false | Machine-readable output |

**JSON output**

```json
{
  "track": "tracks/lead.mid",
  "shape": "arch",
  "pitch_range_semitones": 35,
  "pitch_min_name": "D3",
  "pitch_max_name": "C6",
  "direction_changes": 6,
  "avg_interval_size": 2.43,
  "intervals": [2, 3, 2, -1, 4, -3, 2]
}
```

---

### `muse midi tension TRACK`

Harmonic tension curve: dissonance score per bar on a 0 (consonant) → 1 (maximally tense) scale.

Tension is computed by summing the interval-dissonance weights for every simultaneous note pair in a bar.  The weight table treats unisons and octaves as 0.0, perfect fifths as 0.1, major thirds as 0.2, and minor seconds as 1.0.

```
muse midi tension tracks/epiano.mid
muse midi tension tracks/epiano.mid --commit HEAD~2 --json
```

**Flags**

| Flag | Default | Description |
|---|---|---|
| `--commit TEXT` | working tree | Historical snapshot |
| `--json` | false | Machine-readable output |

**Tension labels:** `consonant` (< 0.15), `mild` (0.15–0.40), `tense` (0.40–0.65), `very tense` (≥ 0.65).

**JSON output**

```json
{
  "track": "tracks/epiano.mid",
  "bars": [
    {"bar": 1, "tension": 0.08, "label": "consonant"},
    {"bar": 3, "tension": 0.67, "label": "tense"}
  ]
}
```

---

### `muse midi cadence TRACK`

Cadence detection at phrase boundaries: authentic, deceptive, half, and plagal.

A cadence is detected when the last chord of a phrase (determined by note density minima) moves to another chord matching one of the four cadence patterns.

```
muse midi cadence tracks/epiano.mid
muse midi cadence tracks/epiano.mid --strict
muse midi cadence tracks/epiano.mid --json
```

**Flags**

| Flag | Default | Description |
|---|---|---|
| `--commit TEXT` | working tree | Historical snapshot |
| `--strict` | false | Exit 1 if no cadences are found (CI gate) |
| `--json` | false | Machine-readable output |

**JSON output**

```json
{
  "track": "tracks/epiano.mid",
  "cadence_count": 2,
  "cadences": [
    {"bar": 5, "cadence_type": "half", "from_chord": "Em", "to_chord": "Bdom7"},
    {"bar": 9, "cadence_type": "authentic", "from_chord": "Bdom7", "to_chord": "Em"}
  ]
}
```

---

## 4. Rhythm & Dynamics Commands

### `muse midi rhythm TRACK`

Rhythmic analysis: syncopation score, swing ratio, quantisation accuracy, and dominant subdivision.

```
muse midi rhythm tracks/drums.mid
muse midi rhythm tracks/drums.mid --commit HEAD~3
muse midi rhythm tracks/bass.mid --json
```

**Flags**

| Flag | Default | Description |
|---|---|---|
| `--commit TEXT` | working tree | Historical snapshot |
| `--json` | false | Machine-readable output |

**Computed fields:**

| Field | Range | Meaning |
|---|---|---|
| `quantization_score` | 0–1 | 1.0 = every note perfectly on the grid |
| `syncopation_score` | 0–1 | 0 = no off-beat notes; 1 = fully syncopated |
| `swing_ratio` | ≥ 1.0 | 1.0 = straight; > 1.3 = noticeable swing |
| `dominant_subdivision` | string | The most common rhythmic grid in the track |

**JSON output**

```json
{
  "track": "tracks/drums.mid",
  "note_count": 64,
  "bar_count": 8,
  "notes_per_bar_avg": 8.0,
  "dominant_subdivision": "sixteenth",
  "quantization_score": 0.942,
  "syncopation_score": 0.382,
  "swing_ratio": 1.003
}
```

---

### `muse midi tempo TRACK`

BPM estimation via inter-onset interval (IOI) voting.

Onset times are converted to seconds using `ticks_per_beat`.  The most common IOI is detected and inverted to beats-per-minute.  Confidence is rated based on how many onsets agree with the estimate.

```
muse midi tempo tracks/drums.mid
muse midi tempo tracks/drums.mid --json
```

**Flags**

| Flag | Default | Description |
|---|---|---|
| `--commit TEXT` | working tree | Historical snapshot |
| `--json` | false | Machine-readable output |

**JSON output**

```json
{
  "track": "tracks/drums.mid",
  "bpm": 96.0,
  "ticks_per_beat": 480,
  "confidence": "high",
  "method": "ioi_voting"
}
```

---

### `muse midi density TRACK`

Notes-per-beat per bar — the textural arc of a composition.

```
muse midi density tracks/drums.mid
muse midi density tracks/full.mid --commit HEAD~5 --json
```

**Flags**

| Flag | Default | Description |
|---|---|---|
| `--commit TEXT` | working tree | Historical snapshot |
| `--json` | false | Machine-readable output |

**JSON output**

```json
{
  "track": "tracks/drums.mid",
  "bar_count": 8,
  "peak_bar": 5,
  "peak_density": 6.25,
  "avg_density": 5.1,
  "bars": [
    {"bar": 1, "notes": 16, "density": 4.0},
    {"bar": 5, "notes": 25, "density": 6.25}
  ]
}
```

---

### `muse midi velocity-profile TRACK`

Dynamic range, RMS velocity, and histogram across the standard dynamic markings (ppp–fff).

```
muse midi velocity-profile tracks/melody.mid
muse midi velocity-profile tracks/melody.mid --by-bar
muse midi velocity-profile tracks/melody.mid --commit HEAD~2 --json
```

**Flags**

| Flag | Default | Description |
|---|---|---|
| `--commit TEXT` | working tree | Historical snapshot |
| `--by-bar` | false | Show per-bar average velocity as a histogram |
| `--json` | false | Machine-readable output |

**JSON output**

```json
{
  "track": "tracks/melody.mid",
  "note_count": 23,
  "velocity_min": 48,
  "velocity_max": 96,
  "velocity_mean": 78.3,
  "velocity_rms": 79.1,
  "dynamic_character": "mf",
  "histogram": {
    "ppp": 0, "pp": 0, "p": 0, "mp": 2,
    "mf": 12, "f": 8, "ff": 1, "fff": 0
  }
}
```

---

## 5. Structure & Voice-Leading Commands

### `muse midi motif TRACK`

Recurring interval-pattern detection.  Scans the interval sequence between consecutive notes for repeated sub-sequences of length ≥ `--min-length`.  Motifs are identified by their interval vector, making detection transposition-invariant.

```
muse midi motif tracks/lead.mid
muse midi motif tracks/melody.mid --min-length 4 --min-occurrences 3
muse midi motif tracks/theme.mid --commit HEAD~5 --json
```

**Flags**

| Flag | Default | Description |
|---|---|---|
| `--commit TEXT` | working tree | Historical snapshot |
| `--min-length INTEGER` | 3 | Minimum number of intervals (notes − 1) in a motif |
| `--min-occurrences INTEGER` | 2 | Minimum number of times a pattern must appear |
| `--json` | false | Machine-readable output |

**JSON output**

```json
{
  "track": "tracks/lead.mid",
  "motif_count": 2,
  "motifs": [
    {
      "intervals": [2, 2, -3],
      "occurrences": 3,
      "first_pitch_name": "E4",
      "bars": [1, 5, 9]
    }
  ]
}
```

---

### `muse midi voice-leading TRACK`

Classical counterpoint lint: parallel fifths, parallel octaves, and large leaps in the top voice.

Voice-leading issues are detected by comparing successive simultaneous note pairs across MIDI channels.  "Parallel" motion means two voices move in the same direction by the same interval class.

```
muse midi voice-leading tracks/strings.mid
muse midi voice-leading tracks/choir.mid --strict
muse midi voice-leading tracks/strings.mid --json
```

**Flags**

| Flag | Default | Description |
|---|---|---|
| `--commit TEXT` | working tree | Historical snapshot |
| `--strict` | false | Exit 1 if any issues are found (CI gate) |
| `--json` | false | Machine-readable output |

**Issue types:** `parallel_fifths`, `parallel_octaves`, `large_leap` (> 7 semitones in the top voice).

**JSON output**

```json
{
  "track": "tracks/strings.mid",
  "issue_count": 2,
  "issues": [
    {"bar": 6, "issue_type": "parallel_fifths", "description": "voices 0–1: parallel perfect fifths"},
    {"bar": 9, "issue_type": "large_leap", "description": "top voice: leap of 11 semitones"}
  ]
}
```

---

### `muse midi compare TRACK COMMIT_A COMMIT_B`

Semantic diff between two commits: side-by-side comparison of key, density, rhythm, and structure dimensions.

```
muse midi compare tracks/epiano.mid HEAD~2 HEAD
muse midi compare tracks/melody.mid main feat/variation --json
```

**Flags**

| Flag | Default | Description |
|---|---|---|
| `--json` | false | Machine-readable output |

**Compared dimensions:** note count, bar count, key estimate, density (avg notes/beat), swing ratio, syncopation score, quantisation score, dominant subdivision.

**JSON output**

```json
{
  "track": "tracks/epiano.mid",
  "commit_a": "1b3c8f02",
  "commit_b": "3f0b5c8d",
  "dimensions": {
    "note_count":          {"a": 18, "b": 32, "delta": 14},
    "bar_count":           {"a": 4,  "b": 8,  "delta": 4},
    "key_estimate":        {"a": "E minor", "b": "E minor", "delta": "="},
    "density_avg":         {"a": 4.5, "b": 5.1, "delta": 0.6},
    "swing_ratio":         {"a": 1.0, "b": 1.0, "delta": 0.0},
    "syncopation_score":   {"a": 0.11, "b": 0.38, "delta": 0.27},
    "quantization_score":  {"a": 0.97, "b": 0.94, "delta": -0.03},
    "dominant_subdivision":{"a": "quarter", "b": "sixteenth", "delta": "changed"}
  }
}
```

---

## 6. History & Attribution Commands

### `muse midi note-log TRACK`

Note-level commit history: every commit that touched this track, with its note insertions and deletions expressed as musical notation.

```
muse midi note-log tracks/melody.mid
muse midi note-log tracks/melody.mid --limit 10
muse midi note-log tracks/melody.mid --json
```

**Flags**

| Flag | Default | Description |
|---|---|---|
| `--limit INTEGER` | all | Maximum number of commits to display |
| `--json` | false | Machine-readable output |

**Text output format:** `+ pitch vel @beat dur ch` for insertions, `- pitch vel @beat dur ch (removed)` for deletions.

---

### `muse midi note-blame TRACK`

Per-bar attribution: which commit (and author) introduced the notes in a given bar.

```
muse midi note-blame tracks/melody.mid --bar 4
muse midi note-blame tracks/melody.mid --json
```

**Flags**

| Flag | Default | Description |
|---|---|---|
| `--bar INTEGER` | 1 | Bar number to inspect |
| `--json` | false | Machine-readable output |

**JSON output**

```json
{
  "track": "tracks/melody.mid",
  "bar": 4,
  "note_count": 5,
  "commit_id": "cb4afaed",
  "committed_at": "2026-03-16T10:00:00+00:00",
  "author": "alice",
  "message": "Add D7 arpeggiation in bar 4"
}
```

---

### `muse midi hotspots`

Bar-level churn leaderboard: which bars have accumulated the most note insertions and deletions across all commits.

```
muse midi hotspots
muse midi hotspots --top 10
muse midi hotspots --track tracks/melody.mid
muse midi hotspots --from HEAD~20 --top 5
muse midi hotspots --json
```

**Flags**

| Flag | Default | Description |
|---|---|---|
| `--top INTEGER` | 10 | Number of bars to display |
| `--track TEXT` | all | Restrict to a single MIDI file |
| `--from TEXT` | initial commit | Start of the commit range |
| `--json` | false | Machine-readable output |

**JSON output**

```json
{
  "commit_count": 47,
  "hotspots": [
    {"rank": 1, "track": "tracks/melody.mid", "bar": 8, "changes": 12},
    {"rank": 2, "track": "tracks/melody.mid", "bar": 4, "changes": 9}
  ]
}
```

---

## 7. Multi-Agent Intelligence Commands

### `muse midi agent-map TRACK`

Bar-level blame showing which author and commit last edited each bar.  The musical equivalent of `git blame` at bar granularity.

```
muse midi agent-map tracks/lead.mid
muse midi agent-map tracks/lead.mid --depth 100
muse midi agent-map tracks/bass.mid --json
```

**Flags**

| Flag | Default | Description |
|---|---|---|
| `--depth INTEGER` | 50 | Maximum number of commits to walk |
| `--json` | false | Machine-readable output |

**JSON output**

```json
{
  "track": "tracks/lead.mid",
  "bars": [
    {"bar": 1, "author": "agent-melody", "commit_id": "3f0b5c8d", "message": "Groove: full arrangement"},
    {"bar": 3, "author": "agent-harmony", "commit_id": "4e2c91aa", "message": "Harmony: modal interchange"}
  ]
}
```

---

### `muse midi find-phrase TRACK --query QUERY_FILE`

Phrase similarity search: find commits where the content of `TRACK` most closely resembles `QUERY_FILE`, using pitch-class histogram and interval fingerprint similarity.  Detection is transposition-invariant — the same motif in a different key is still found.

```
muse midi find-phrase tracks/lead.mid --query query/motif.mid
muse midi find-phrase tracks/lead.mid --query query/motif.mid --depth 20
muse midi find-phrase tracks/lead.mid --query query/motif.mid --json
```

**Flags**

| Flag | Default | Description |
|---|---|---|
| `--query TEXT` | required | Path to the query MIDI file |
| `--depth INTEGER` | 20 | Maximum number of commits to scan |
| `--json` | false | Machine-readable output |

**Similarity score:** `0.0` = no resemblance; `1.0` = identical.  Composed from 60% interval fingerprint + 40% pitch-class histogram.

**JSON output**

```json
{
  "track": "tracks/lead.mid",
  "query": "query/motif.mid",
  "results": [
    {"score": 0.934, "commit_id": "3f0b5c8d", "author": "agent-melody", "message": "Groove: full arrangement"},
    {"score": 0.812, "commit_id": "4e2c91aa", "author": "agent-harmony", "message": "Harmony: modal interchange"}
  ]
}
```

---

### `muse midi shard TRACK --shards N`

Partition a MIDI track into N non-overlapping bar-range shards for parallel agent work.  Agents working on different shards can commit independently and merge with zero note-level conflicts — identical to `muse coord shard` for code.

```
muse midi shard tracks/full.mid --shards 4
muse midi shard tracks/symphony.mid --bars-per-shard 32 --output-dir agents/
muse midi shard tracks/full.mid --shards 8 --dry-run
```

**Flags**

| Flag | Default | Description |
|---|---|---|
| `--shards INTEGER` | required | Number of shards to produce |
| `--bars-per-shard INTEGER` | auto | Fixed bar count per shard (overrides `--shards`) |
| `--output-dir TEXT` | `shards/` | Directory for output shard files |
| `--dry-run` | false | Print shard plan without writing files |

**Shard file naming:** `{stem}_shard_{n}.mid` in `--output-dir`.

**Workflow:** shard → assign shards to agents → agents commit → `muse midi mix` to recombine.

---

### `muse midi query TRACK`

MIDI DSL predicate query over note data and commit history.

```
muse midi query tracks/melody.mid "bar=4"
muse midi query tracks/melody.mid "pitch=G4 velocity>70"
muse midi query tracks/melody.mid "bar>=2 bar<=5 channel=0"
muse midi query tracks/melody.mid "agent=agent-melody" --commit HEAD~10
```

**DSL predicates**

| Predicate | Example | Description |
|---|---|---|
| `bar=N` | `bar=4` | Match notes in exactly bar N |
| `bar>=N`, `bar<=N` | `bar>=2 bar<=5` | Bar range |
| `pitch=X` | `pitch=G4` | Match by pitch name or MIDI number |
| `velocity>N` | `velocity>70` | Velocity threshold |
| `channel=N` | `channel=0` | MIDI channel filter |
| `agent=X` | `agent=alice` | Last-author filter (walks blame history) |

**Flags**

| Flag | Default | Description |
|---|---|---|
| `--commit TEXT` | working tree | Historical snapshot |
| `--json` | false | Machine-readable note list |

---

## 8. Transformation Commands

All transformation commands modify the working tree.  Run `muse status` after, then `muse commit` to record the note-level delta.

### `muse midi transpose TRACK --semitones N`

Shift all notes by N semitones (positive = up, negative = down).

```
muse midi transpose tracks/melody.mid --semitones 7
muse midi transpose tracks/bass.mid --semitones -12
muse midi transpose tracks/melody.mid --semitones 5 --clamp
muse midi transpose tracks/melody.mid --semitones 7 --dry-run
```

**Flags**

| Flag | Default | Description |
|---|---|---|
| `--semitones INTEGER` | required | Shift amount; negative = down |
| `--clamp` | false | Clamp out-of-range pitches to 0–127 instead of skipping |
| `--dry-run` | false | Preview without writing |

---

### `muse midi quantize TRACK --grid GRID`

Snap note onset times to a rhythmic grid with adjustable strength.

```
muse midi quantize tracks/piano.mid --grid 16th
muse midi quantize tracks/piano.mid --grid triplet-8th --strength 0.5 --dry-run
```

**Flags**

| Flag | Default | Description |
|---|---|---|
| `--grid TEXT` | required | Grid value: `whole`, `half`, `quarter`, `8th`, `16th`, `32nd`, `triplet-8th`, `triplet-16th` |
| `--strength FLOAT` | 1.0 | Quantisation strength 0.0–1.0; < 1.0 preserves human feel |
| `--dry-run` | false | Preview without writing |

---

### `muse midi humanize TRACK`

Add controlled randomness to onset times and velocities to simulate human performance.

```
muse midi humanize tracks/piano.mid
muse midi humanize tracks/piano.mid --timing 0.015 --velocity 10 --seed 42
```

**Flags**

| Flag | Default | Description |
|---|---|---|
| `--timing FLOAT` | 0.01 | Max timing jitter in beats |
| `--velocity INTEGER` | 8 | Max velocity jitter (±) |
| `--seed INTEGER` | none | RNG seed for reproducible agent pipelines |

---

### `muse midi invert TRACK --pivot PITCH`

Melodic inversion: every upward interval becomes downward and vice versa, reflected around the pivot pitch.

```
muse midi invert tracks/melody.mid --pivot E4
muse midi invert tracks/melody.mid --pivot E4 --dry-run
```

**Flags**

| Flag | Default | Description |
|---|---|---|
| `--pivot TEXT` | required | Pivot pitch name (`E4`) or MIDI number (`64`) |
| `--dry-run` | false | Preview without writing |

The pivot pitch maps to itself; all other pitches are reflected symmetrically.

---

### `muse midi retrograde TRACK`

Reverse the pitch order of all notes while preserving their timing, velocities, and durations.

```
muse midi retrograde tracks/melody.mid
muse midi retrograde tracks/theme.mid --dry-run
```

**Flags**

| Flag | Default | Description |
|---|---|---|
| `--dry-run` | false | Preview without writing |

Classic twelve-tone row operation.  Combine with `invert` to produce the retrograde-inversion.

---

### `muse midi arpeggiate TRACK --rate RATE`

Convert simultaneous chord voicings to sequential arpeggio notes.

```
muse midi arpeggiate tracks/epiano.mid --rate 8th --order up-down
muse midi arpeggiate tracks/piano.mid --rate 16th --order random --seed 7
```

**Flags**

| Flag | Default | Description |
|---|---|---|
| `--rate TEXT` | `8th` | Rhythmic rate: `quarter`, `8th`, `16th`, `32nd` |
| `--order TEXT` | `up` | Arpeggio direction: `up`, `down`, `up-down`, `random` |
| `--seed INTEGER` | none | RNG seed (used with `--order random`) |

Simultaneous notes (onset within 10 ticks of each other) are grouped into chord clusters and distributed sequentially at the chosen rate.

---

### `muse midi normalize TRACK`

Linearly rescale all note velocities to a target dynamic range while preserving relative dynamics.

```
muse midi normalize tracks/lead.mid --min 50 --max 100
muse midi normalize tracks/drums.mid --min 40 --max 120
```

**Flags**

| Flag | Default | Description |
|---|---|---|
| `--min INTEGER` | 40 | Target minimum velocity |
| `--max INTEGER` | 110 | Target maximum velocity |

Essential when integrating tracks from multiple agents recorded at different volume levels.

---

### `muse midi mix TRACK_A TRACK_B --output OUTPUT`

Combine notes from two MIDI tracks into a single output file.

```
muse midi mix tracks/melody.mid tracks/harmony.mid \
    --output tracks/full.mid \
    --channel-a 0 \
    --channel-b 1
```

**Flags**

| Flag | Default | Description |
|---|---|---|
| `--output TEXT` | required | Output file path |
| `--channel-a INTEGER` | 0 | MIDI channel to assign to TRACK_A notes in output |
| `--channel-b INTEGER` | 1 | MIDI channel to assign to TRACK_B notes in output |

The `--channel-a` / `--channel-b` flags ensure instruments can be differentiated in the mixed output and individual parts can be extracted later.

---

## 9. Invariants & Quality Gate Commands

### `muse midi check`

Enforce MIDI invariant rules defined in `.museattributes` or inline flags.

```
muse midi check
muse midi check --track tracks/melody.mid
muse midi check --key "G major" --max-polyphony 4 --strict
```

**Flags**

| Flag | Default | Description |
|---|---|---|
| `--track TEXT` | all | Restrict check to a single MIDI file |
| `--key TEXT` | none | Assert that the detected key matches this value |
| `--max-polyphony INTEGER` | none | Assert that no bar exceeds N simultaneous notes |
| `--strict` | false | Exit 1 on any violation (CI gate) |
| `--json` | false | Machine-readable results |

**Use in CI:** add `muse midi check --strict` as a pre-commit hook or CI step to prevent agents from committing tracks that violate compositional rules.

---

## 10. Type Reference

### `NoteInfo`

```python
class NoteInfo(NamedTuple):
    pitch: int           # MIDI note number 0–127
    velocity: int        # note-on velocity 0–127
    start_tick: int      # onset in MIDI ticks
    duration_ticks: int  # duration in MIDI ticks
    channel: int         # MIDI channel 0–15
```

### Analysis TypedDicts (from `muse/plugins/midi/_analysis.py`)

```python
class ScaleMatch(TypedDict):
    root: str
    scale: str
    confidence: float
    out_of_scale_count: int

class RhythmAnalysis(TypedDict):
    quantization_score: float
    syncopation_score: float
    swing_ratio: float
    dominant_subdivision: str

class ContourAnalysis(TypedDict):
    shape: str                  # ascending | descending | arch | valley | wave | flat
    pitch_range_semitones: int
    direction_changes: int
    avg_interval_size: float
    intervals: list[int]

class BarDensity(TypedDict):
    bar: int
    notes: int
    density: float

class BarTension(TypedDict):
    bar: int
    tension: float
    label: str

class Cadence(TypedDict):
    bar: int
    cadence_type: str           # authentic | deceptive | half | plagal
    from_chord: str
    to_chord: str

class Motif(TypedDict):
    intervals: list[int]
    occurrences: int
    first_pitch_name: str
    bars: list[int]

class VoiceLeadingIssue(TypedDict):
    bar: int
    issue_type: str             # parallel_fifths | parallel_octaves | large_leap
    description: str

class TempoEstimate(TypedDict):
    bpm: float
    ticks_per_beat: int
    confidence: str             # high | medium | low
    method: str

class ChannelInfo(TypedDict):
    channel: int
    note_count: int
    pitch_min: int
    pitch_max: int
    pitch_min_name: str
    pitch_max_name: str
    register: str               # bass | mid | treble
    mean_velocity: float

class BarAttribution(TypedDict):
    bar: int
    author: str
    commit_id: str
    message: str
```

### `PhraseMatch`

```python
class PhraseMatch(TypedDict):
    score: float                # 0.0–1.0 similarity
    commit_id: str
    author: str
    message: str
```

---

## Command Quick-Reference

| Group | Command | One-line description |
|---|---|---|
| **Notation** | `muse midi notes` | Every note as musical notation |
| **Notation** | `muse midi piano-roll` | ASCII piano roll |
| **Notation** | `muse midi instrumentation` | Per-channel range, register, velocity |
| **Harmony** | `muse midi harmony` | Bar-by-bar chord detection + key |
| **Harmony** | `muse midi scale` | Scale/mode detection (15 types × 12 roots) |
| **Harmony** | `muse midi contour` | Melodic contour shape + interval sequence |
| **Harmony** | `muse midi tension` | Dissonance score per bar (0–1) |
| **Harmony** | `muse midi cadence` | Cadence detection at phrase boundaries |
| **Rhythm** | `muse midi rhythm` | Syncopation, swing, quantisation, subdivision |
| **Rhythm** | `muse midi tempo` | BPM estimation via IOI voting |
| **Rhythm** | `muse midi density` | Notes-per-beat per bar — textural arc |
| **Rhythm** | `muse midi velocity-profile` | Dynamic range and histogram (ppp–fff) |
| **Structure** | `muse midi motif` | Transposition-invariant motif detection |
| **Structure** | `muse midi voice-leading` | Parallel fifths/octaves + large leaps lint |
| **Structure** | `muse midi compare` | Semantic diff across musical dimensions |
| **History** | `muse midi note-log` | Note-level commit history |
| **History** | `muse midi note-blame` | Per-bar: which commit wrote these notes |
| **History** | `muse midi hotspots` | Bar-level churn leaderboard |
| **Multi-agent** | `muse midi agent-map` | Bar-level blame: which agent last edited |
| **Multi-agent** | `muse midi find-phrase` | Phrase similarity search across history |
| **Multi-agent** | `muse midi shard` | Partition into N bar-range shards |
| **Multi-agent** | `muse midi query` | MIDI DSL predicate query |
| **Transform** | `muse midi transpose` | Shift all pitches by N semitones |
| **Transform** | `muse midi invert` | Melodic inversion around a pivot |
| **Transform** | `muse midi retrograde` | Reverse pitch order |
| **Transform** | `muse midi quantize` | Snap onsets to a rhythmic grid |
| **Transform** | `muse midi humanize` | Add timing/velocity jitter |
| **Transform** | `muse midi arpeggiate` | Chords → arpeggios |
| **Transform** | `muse midi normalize` | Rescale velocities to target range |
| **Transform** | `muse midi mix` | Combine two MIDI tracks into one |
| **Invariants** | `muse midi check` | Enforce MIDI invariant rules |

---

*See also: [Demo walkthrough](../demo/midi-demo.md) · [CLI Tiers Reference](cli-tiers.md) · [Plugin Authoring Guide](../guide/plugin-authoring-guide.md)*

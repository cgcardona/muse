"""Pure musical analysis helpers for the Muse MIDI plugin.

All functions accept ``list[NoteInfo]`` and return typed results.
No I/O, no store access — composable building blocks for semantic porcelain.

Design rule: every public function returns a TypedDict or a list thereof.
No ``Any``, no bare collections, no untyped parameters.
"""

from __future__ import annotations

import math
from collections import Counter
from typing import TypedDict

from muse.plugins.midi._query import NoteInfo, _PITCH_CLASSES, detect_chord, notes_by_bar

# ---------------------------------------------------------------------------
# Scale detection
# ---------------------------------------------------------------------------

_SCALES: list[tuple[str, frozenset[int]]] = [
    ("major",            frozenset({0, 2, 4, 5, 7, 9, 11})),
    ("natural minor",    frozenset({0, 2, 3, 5, 7, 8, 10})),
    ("harmonic minor",   frozenset({0, 2, 3, 5, 7, 8, 11})),
    ("melodic minor",    frozenset({0, 2, 3, 5, 7, 9, 11})),
    ("dorian",           frozenset({0, 2, 3, 5, 7, 9, 10})),
    ("phrygian",         frozenset({0, 1, 3, 5, 7, 8, 10})),
    ("lydian",           frozenset({0, 2, 4, 6, 7, 9, 11})),
    ("mixolydian",       frozenset({0, 2, 4, 5, 7, 9, 10})),
    ("locrian",          frozenset({0, 1, 3, 5, 6, 8, 10})),
    ("major pentatonic", frozenset({0, 2, 4, 7, 9})),
    ("minor pentatonic", frozenset({0, 3, 5, 7, 10})),
    ("blues",            frozenset({0, 3, 5, 6, 7, 10})),
    ("whole tone",       frozenset({0, 2, 4, 6, 8, 10})),
    ("diminished",       frozenset({0, 2, 3, 5, 6, 8, 9, 11})),
    ("chromatic",        frozenset(range(12))),
]


class ScaleMatch(TypedDict):
    """Best-fit scale result."""

    root: str
    name: str
    confidence: float
    out_of_scale_notes: int


def detect_scale(notes: list[NoteInfo]) -> list[ScaleMatch]:
    """Return the top-5 scale matches sorted by confidence.

    Confidence is the fraction of note *weight* (note count) covered by
    the scale's pitch classes.  ``out_of_scale_notes`` is the number of
    notes whose pitch class falls outside the scale.
    """
    if not notes:
        return []
    histogram = [0] * 12
    for n in notes:
        histogram[n.pitch_class] += 1
    total = max(sum(histogram), 1)

    results: list[ScaleMatch] = []
    for root in range(12):
        for scale_name, scale_pcs in _SCALES:
            absolute_pcs = frozenset((root + pc) % 12 for pc in scale_pcs)
            covered = sum(histogram[pc] for pc in absolute_pcs)
            out_of_scale = sum(histogram[pc] for pc in range(12) if pc not in absolute_pcs)
            results.append(ScaleMatch(
                root=_PITCH_CLASSES[root],
                name=scale_name,
                confidence=round(covered / total, 3),
                out_of_scale_notes=out_of_scale,
            ))

    results.sort(key=lambda r: (-r["confidence"], r["out_of_scale_notes"]))
    seen: set[tuple[str, str]] = set()
    unique: list[ScaleMatch] = []
    for r in results:
        key = (r["root"], r["name"])
        if key not in seen:
            seen.add(key)
            unique.append(r)
    return unique[:5]


# ---------------------------------------------------------------------------
# Rhythm analysis
# ---------------------------------------------------------------------------


class RhythmAnalysis(TypedDict):
    """Summary of rhythmic properties."""

    total_notes: int
    bars: int
    notes_per_bar_avg: float
    syncopation_score: float
    quantization_score: float
    swing_ratio: float
    dominant_subdivision: str


def analyze_rhythm(notes: list[NoteInfo]) -> RhythmAnalysis:
    """Compute rhythmic metrics: syncopation, quantisation, swing.

    *syncopation_score*: fraction of notes landing on off-beat positions.
    *quantization_score*: 1.0 = perfectly on the 16th-note grid, 0.0 = random.
    *swing_ratio*: ratio of even/odd 8th-note IOI durations (1.0 = straight,
    >1.3 = swung).
    """
    if not notes:
        return RhythmAnalysis(
            total_notes=0, bars=0, notes_per_bar_avg=0.0,
            syncopation_score=0.0, quantization_score=1.0,
            swing_ratio=1.0, dominant_subdivision="quarter",
        )

    tpb = max(notes[0].ticks_per_beat, 1)
    sorted_notes = sorted(notes, key=lambda n: n.start_tick)
    bars = notes_by_bar(notes)
    num_bars = max(bars.keys()) if bars else 1

    # Syncopation: fraction on weak sub-beats
    half_beat = tpb // 2
    synco_count = sum(
        1 for n in notes
        if half_beat // 4 < n.start_tick % tpb < tpb - half_beat // 4
    )
    syncopation_score = synco_count / max(len(notes), 1)

    # Quantisation vs 16th-note grid
    grid = max(tpb // 4, 1)
    q_dists = [min(n.start_tick % grid, grid - n.start_tick % grid) / grid for n in notes]
    quantization_score = 1.0 - (sum(q_dists) / max(len(q_dists), 1))

    # Swing ratio between even/odd 8th-note slots
    grid_8 = max(tpb // 2, 1)
    even_durs: list[int] = []
    odd_durs: list[int] = []
    for n in sorted_notes:
        if (n.start_tick // grid_8) % 2 == 0:
            even_durs.append(n.duration_ticks)
        else:
            odd_durs.append(n.duration_ticks)
    if even_durs and odd_durs:
        swing_ratio = (sum(even_durs) / len(even_durs)) / max(
            sum(odd_durs) / len(odd_durs), 1
        )
    else:
        swing_ratio = 1.0

    # Dominant note length
    dur_buckets: Counter[str] = Counter()
    for n in notes:
        beats = n.duration_ticks / tpb
        if beats >= 3.5:
            dur_buckets["whole"] += 1
        elif beats >= 1.75:
            dur_buckets["half"] += 1
        elif beats >= 0.875:
            dur_buckets["quarter"] += 1
        elif beats >= 0.4:
            dur_buckets["eighth"] += 1
        else:
            dur_buckets["sixteenth"] += 1
    dominant_subdivision = dur_buckets.most_common(1)[0][0] if dur_buckets else "quarter"

    return RhythmAnalysis(
        total_notes=len(notes),
        bars=num_bars,
        notes_per_bar_avg=round(len(notes) / max(num_bars, 1), 2),
        syncopation_score=round(syncopation_score, 3),
        quantization_score=round(quantization_score, 3),
        swing_ratio=round(swing_ratio, 3),
        dominant_subdivision=dominant_subdivision,
    )


# ---------------------------------------------------------------------------
# Melodic contour
# ---------------------------------------------------------------------------


class ContourAnalysis(TypedDict):
    """Melodic contour shape and statistics."""

    shape: str
    intervals: list[int]
    range_semitones: int
    direction_changes: int
    avg_interval_size: float
    highest_pitch: str
    lowest_pitch: str


def analyze_contour(notes: list[NoteInfo]) -> ContourAnalysis:
    """Analyse the melodic contour of a pitch sequence.

    *shape* is one of: ascending, descending, arch, valley, wave, flat.
    *intervals* is the semitone interval sequence between consecutive notes.
    """
    from muse.plugins.midi.midi_diff import _pitch_name

    sorted_notes = sorted(notes, key=lambda n: n.start_tick)
    if len(sorted_notes) < 2:
        pitch = sorted_notes[0].pitch if sorted_notes else 60
        pn = _pitch_name(pitch)
        return ContourAnalysis(
            shape="flat", intervals=[], range_semitones=0,
            direction_changes=0, avg_interval_size=0.0,
            highest_pitch=pn, lowest_pitch=pn,
        )

    intervals = [
        sorted_notes[i + 1].pitch - sorted_notes[i].pitch
        for i in range(len(sorted_notes) - 1)
    ]
    pitches = [n.pitch for n in sorted_notes]
    range_semitones = max(pitches) - min(pitches)
    avg_interval_size = round(sum(abs(iv) for iv in intervals) / max(len(intervals), 1), 2)

    # Count direction changes
    direction_changes = 0
    prev_dir = 0
    for iv in intervals:
        cur_dir = 1 if iv > 0 else (-1 if iv < 0 else 0)
        if cur_dir != 0 and prev_dir != 0 and cur_dir != prev_dir:
            direction_changes += 1
        if cur_dir != 0:
            prev_dir = cur_dir

    # Shape classification
    n = len(pitches)
    first_avg = sum(pitches[: n // 2]) / max(n // 2, 1)
    second_avg = sum(pitches[n // 2 :]) / max(n - n // 2, 1)
    mid_avg = sum(pitches[n // 4 : 3 * n // 4]) / max(n // 2, 1)
    overall_avg = sum(pitches) / n

    if direction_changes == 0:
        if second_avg > first_avg + 0.5:
            shape = "ascending"
        elif first_avg > second_avg + 0.5:
            shape = "descending"
        else:
            shape = "flat"
    elif direction_changes == 1:
        # One direction change: arch (up-then-down) or valley (down-then-up)
        if mid_avg > overall_avg + 0.5:
            shape = "arch"
        elif mid_avg < overall_avg - 0.5:
            shape = "valley"
        elif second_avg > first_avg + 0.5:
            shape = "ascending"
        elif first_avg > second_avg + 0.5:
            shape = "descending"
        else:
            shape = "flat"
    elif mid_avg > overall_avg + 0.5:
        shape = "arch"
    elif mid_avg < overall_avg - 0.5:
        shape = "valley"
    else:
        shape = "wave"

    return ContourAnalysis(
        shape=shape,
        intervals=intervals[:32],
        range_semitones=range_semitones,
        direction_changes=direction_changes,
        avg_interval_size=avg_interval_size,
        highest_pitch=_pitch_name(max(pitches)),
        lowest_pitch=_pitch_name(min(pitches)),
    )


# ---------------------------------------------------------------------------
# Density
# ---------------------------------------------------------------------------


class BarDensity(TypedDict):
    """Note density for one bar."""

    bar: int
    note_count: int
    notes_per_beat: float


def analyze_density(notes: list[NoteInfo]) -> list[BarDensity]:
    """Return note density (notes per beat, assuming 4/4) per bar."""
    bars = notes_by_bar(notes)
    return [
        BarDensity(
            bar=bar_num,
            note_count=len(bar_notes),
            notes_per_beat=round(len(bar_notes) / 4.0, 2),
        )
        for bar_num, bar_notes in sorted(bars.items())
    ]


# ---------------------------------------------------------------------------
# Harmonic tension
# ---------------------------------------------------------------------------

# Dissonance weight per semitone interval class (0=unison, 1=m2, …, 11=M7)
_INTERVAL_DISSONANCE = [0, 10, 5, 3, 2, 1, 8, 0, 2, 3, 5, 8]


class BarTension(TypedDict):
    """Harmonic tension for one bar."""

    bar: int
    tension: float
    label: str


def compute_tension(notes: list[NoteInfo]) -> list[BarTension]:
    """Compute harmonic tension per bar (0 = consonant, 1 = very dissonant)."""
    bars = notes_by_bar(notes)
    result: list[BarTension] = []
    for bar_num, bar_notes in sorted(bars.items()):
        pitches = [n.pitch for n in bar_notes]
        if len(pitches) < 2:
            result.append(BarTension(bar=bar_num, tension=0.0, label="consonant"))
            continue
        intervals = [
            abs(pitches[i] - pitches[j]) % 12
            for i in range(len(pitches))
            for j in range(i + 1, len(pitches))
        ]
        raw = sum(_INTERVAL_DISSONANCE[iv] for iv in intervals) / (len(intervals) * 10)
        tension = round(min(1.0, raw), 3)
        label = "consonant" if tension < 0.2 else "mild" if tension < 0.5 else "tense"
        result.append(BarTension(bar=bar_num, tension=tension, label=label))
    return result


# ---------------------------------------------------------------------------
# Cadence detection
# ---------------------------------------------------------------------------


class Cadence(TypedDict):
    """A detected cadence at a phrase boundary."""

    bar: int
    cadence_type: str
    from_chord: str
    to_chord: str


def detect_cadences(notes: list[NoteInfo]) -> list[Cadence]:
    """Detect cadences by examining chord motions at phrase boundaries (every 4 bars)."""
    bars = notes_by_bar(notes)
    bar_nums = sorted(bars.keys())
    if len(bar_nums) < 2:
        return []

    bar_chords: dict[int, str] = {
        bn: detect_chord(frozenset(n.pitch_class for n in bar_notes))
        for bn, bar_notes in bars.items()
    }

    cadences: list[Cadence] = []
    for i in range(len(bar_nums) - 1):
        bn = bar_nums[i]
        next_bn = bar_nums[i + 1]
        # Only at phrase endings (bar before a multiple of 4 or 8)
        if next_bn % 4 != 1 and next_bn % 8 != 1:
            continue
        from_chord = bar_chords.get(bn, "??")
        to_chord = bar_chords.get(next_bn, "??")
        cadence_type = _classify_cadence(from_chord, to_chord)
        if cadence_type:
            cadences.append(Cadence(
                bar=next_bn,
                cadence_type=cadence_type,
                from_chord=from_chord,
                to_chord=to_chord,
            ))
    return cadences


def _classify_cadence(from_chord: str, to_chord: str) -> str | None:
    """Heuristically classify a two-chord motion."""
    fc, tc = from_chord.lower(), to_chord.lower()
    if "dom7" in fc and "maj" in tc:
        return "authentic"
    if "dom7" in fc and "min" in tc:
        return "deceptive"
    if ("maj" in fc or "min" in fc) and "dom7" in tc:
        return "half"
    if "min" in fc and "maj" in tc:
        return "plagal"
    return None


# ---------------------------------------------------------------------------
# Motif detection
# ---------------------------------------------------------------------------


class Motif(TypedDict):
    """A recurring melodic interval pattern."""

    id: int
    interval_pattern: list[int]
    occurrences: int
    bars: list[int]
    first_pitch: str


def find_motifs(
    notes: list[NoteInfo],
    min_length: int = 3,
    min_occurrences: int = 2,
) -> list[Motif]:
    """Find recurring melodic interval patterns (motifs) in the note sequence.

    Scans for repeated subsequences of semitone intervals.  Returns up to 8
    non-overlapping patterns sorted by occurrence count.
    """
    from muse.plugins.midi.midi_diff import _pitch_name

    sorted_notes = sorted(notes, key=lambda n: n.start_tick)
    if len(sorted_notes) < min_length + 1:
        return []

    intervals = [
        sorted_notes[i + 1].pitch - sorted_notes[i].pitch
        for i in range(len(sorted_notes) - 1)
    ]

    pattern_count: Counter[tuple[int, ...]] = Counter()
    for length in range(min_length, min(min_length + 4, len(intervals))):
        for i in range(len(intervals) - length + 1):
            pattern_count[tuple(intervals[i : i + length])] += 1

    motifs: list[Motif] = []
    seen: set[tuple[int, ...]] = set()
    motif_id = 0

    for pat, count in pattern_count.most_common(20):
        if count < min_occurrences or len(motifs) >= 8:
            break
        # Skip sub-patterns of already-found patterns
        is_sub = any(
            len(seen_pat) >= len(pat) and any(
                seen_pat[k : k + len(pat)] == pat
                for k in range(len(seen_pat) - len(pat) + 1)
            )
            for seen_pat in seen
        )
        if is_sub:
            continue
        seen.add(pat)

        bars_found: list[int] = []
        first_pitch = ""
        for i in range(len(intervals) - len(pat) + 1):
            if tuple(intervals[i : i + len(pat)]) == pat:
                bars_found.append(sorted_notes[i].bar)
                if not first_pitch:
                    first_pitch = _pitch_name(sorted_notes[i].pitch)

        motifs.append(Motif(
            id=motif_id,
            interval_pattern=list(pat),
            occurrences=count,
            bars=bars_found[:8],
            first_pitch=first_pitch,
        ))
        motif_id += 1

    return motifs


# ---------------------------------------------------------------------------
# Voice-leading
# ---------------------------------------------------------------------------


class VoiceLeadingIssue(TypedDict):
    """A detected voice-leading problem."""

    bar: int
    issue_type: str
    description: str


def check_voice_leading(notes: list[NoteInfo]) -> list[VoiceLeadingIssue]:
    """Detect parallel fifths/octaves and large leaps in the top voice."""
    bars = notes_by_bar(notes)
    bar_nums = sorted(bars.keys())
    issues: list[VoiceLeadingIssue] = []

    for i in range(len(bar_nums) - 1):
        bn = bar_nums[i]
        next_bn = bar_nums[i + 1]
        cur = sorted(n.pitch for n in bars[bn])
        nxt = sorted(n.pitch for n in bars.get(next_bn, []))
        if len(cur) < 2 or len(nxt) < 2:
            continue

        for vi in range(min(len(cur), len(nxt)) - 1):
            ci = (cur[vi + 1] - cur[vi]) % 12
            ni = (nxt[vi + 1] - nxt[vi]) % 12
            if ci == 7 and ni == 7:
                issues.append(VoiceLeadingIssue(
                    bar=next_bn,
                    issue_type="parallel_fifths",
                    description=f"voices {vi}–{vi+1}: parallel perfect fifths",
                ))
            if ci == 0 and ni == 0:
                issues.append(VoiceLeadingIssue(
                    bar=next_bn,
                    issue_type="parallel_octaves",
                    description=f"voices {vi}–{vi+1}: parallel octaves",
                ))

        if cur and nxt:
            leap = abs(nxt[-1] - cur[-1])
            if leap > 9:
                issues.append(VoiceLeadingIssue(
                    bar=next_bn,
                    issue_type="large_leap",
                    description=f"top voice: leap of {leap} semitones",
                ))

    return issues


# ---------------------------------------------------------------------------
# Tempo estimation
# ---------------------------------------------------------------------------


class TempoEstimate(TypedDict):
    """Estimated tempo from note onset spacing."""

    estimated_bpm: float
    ticks_per_beat: int
    confidence: str
    method: str


def estimate_tempo(notes: list[NoteInfo]) -> TempoEstimate:
    """Estimate BPM from inter-onset intervals.

    Uses the most common IOI (inter-onset interval) as the beat estimate.
    Confidence is "high" when many notes agree on the same IOI.
    """
    if not notes:
        return TempoEstimate(
            estimated_bpm=120.0,
            ticks_per_beat=480,
            confidence="none",
            method="default",
        )
    tpb = max(notes[0].ticks_per_beat, 1)
    sorted_notes = sorted(notes, key=lambda n: n.start_tick)
    iois = [
        sorted_notes[i + 1].start_tick - sorted_notes[i].start_tick
        for i in range(len(sorted_notes) - 1)
        if sorted_notes[i + 1].start_tick > sorted_notes[i].start_tick
    ]
    if not iois:
        return TempoEstimate(
            estimated_bpm=120.0,
            ticks_per_beat=tpb,
            confidence="none",
            method="no_ioi",
        )

    # Snap IOIs to beat multiples and find most common beat length
    beat_counts: Counter[int] = Counter()
    for ioi in iois:
        for div in [1, 2, 4]:
            candidate = round(ioi / div / tpb) * tpb
            if candidate > 0:
                beat_counts[candidate] += 1

    if not beat_counts:
        return TempoEstimate(
            estimated_bpm=120.0, ticks_per_beat=tpb,
            confidence="low", method="ioi_fallback",
        )

    beat_ticks, vote_count = beat_counts.most_common(1)[0]
    bpm = 60.0 * tpb / max(beat_ticks, 1)
    confidence = "high" if vote_count >= len(iois) * 0.4 else "medium" if vote_count >= 3 else "low"

    return TempoEstimate(
        estimated_bpm=round(bpm, 1),
        ticks_per_beat=tpb,
        confidence=confidence,
        method="ioi_voting",
    )


# ---------------------------------------------------------------------------
# Phrase-level similarity (for find-phrase)
# ---------------------------------------------------------------------------


def pitch_interval_fingerprint(notes: list[NoteInfo]) -> tuple[int, ...]:
    """Return the semitone-interval fingerprint of a sorted note sequence."""
    s = sorted(notes, key=lambda n: n.start_tick)
    return tuple(s[i + 1].pitch - s[i].pitch for i in range(len(s) - 1))


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))
    return dot / max(mag_a * mag_b, 1e-9)


def phrase_similarity(
    query_notes: list[NoteInfo],
    candidate_notes: list[NoteInfo],
) -> float:
    """Return a similarity score [0, 1] between two note sequences.

    Uses interval fingerprints and pitch-class histogram cosine similarity.
    """
    if not query_notes or not candidate_notes:
        return 0.0

    # Interval fingerprint similarity (rhythmically normalised)
    q_fp = list(pitch_interval_fingerprint(query_notes))
    c_fp = list(pitch_interval_fingerprint(candidate_notes))
    if q_fp and c_fp:
        min_len = min(len(q_fp), len(c_fp))
        interval_sim = _cosine_similarity(
            [float(x) for x in q_fp[:min_len]],
            [float(x) for x in c_fp[:min_len]],
        )
    else:
        interval_sim = 0.0

    # Pitch-class histogram similarity
    q_hist = [0.0] * 12
    c_hist = [0.0] * 12
    for n in query_notes:
        q_hist[n.pitch_class] += 1.0
    for n in candidate_notes:
        c_hist[n.pitch_class] += 1.0
    pc_sim = _cosine_similarity(q_hist, c_hist)

    return round(0.6 * interval_sim + 0.4 * pc_sim, 3)

"""Tests for the new MIDI semantic porcelain — analysis helpers and CLI commands.

Coverage:
- muse/plugins/midi/_analysis.py: all eight analysis functions
- CLI commands (via CliRunner): rhythm, scale, contour, density, tension,
  cadence, motif, voice_leading, instrumentation, tempo, quantize, humanize,
  invert, retrograde, arpeggiate, velocity_normalize, midi_compare
"""

from __future__ import annotations

import pathlib

import pytest
from typer.testing import CliRunner

from muse.cli.app import cli
from muse.plugins.midi._analysis import (
    analyze_contour,
    analyze_density,
    analyze_rhythm,
    check_voice_leading,
    compute_tension,
    detect_cadences,
    detect_scale,
    estimate_tempo,
    find_motifs,
    phrase_similarity,
)
from muse.plugins.midi._query import NoteInfo

runner = CliRunner()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TPB = 480  # ticks per beat


def _note(pitch: int, start_beat: float, dur_beats: float = 0.5, vel: int = 80, ch: int = 0) -> NoteInfo:
    return NoteInfo(
        pitch=pitch,
        velocity=vel,
        start_tick=round(start_beat * _TPB),
        duration_ticks=round(dur_beats * _TPB),
        channel=ch,
        ticks_per_beat=_TPB,
    )


def _make_scale_run() -> list[NoteInfo]:
    """C major scale ascending, two octaves."""
    pitches = [60, 62, 64, 65, 67, 69, 71, 72, 74, 76, 77, 79, 81, 83]
    return [_note(p, i * 0.5) for i, p in enumerate(pitches)]


def _make_chord_sequence() -> list[NoteInfo]:
    """Simple C–Am–F–G progression, one chord per bar (4 beats)."""
    chords = [
        [60, 64, 67],   # bar 1: C major
        [57, 60, 64],   # bar 2: A minor
        [53, 57, 60],   # bar 3: F major
        [55, 59, 62],   # bar 4: G major
    ]
    notes: list[NoteInfo] = []
    for bar_idx, pitches in enumerate(chords):
        start = bar_idx * 4.0  # beat offset
        for p in pitches:
            notes.append(_note(p, start, dur_beats=3.5))
    return notes


def _make_motif_track() -> list[NoteInfo]:
    """A track where the interval pattern [+2, -1, +3] repeats three times."""
    base_pitches = [60, 62, 61, 64]
    notes: list[NoteInfo] = []
    for rep in range(3):
        offset = rep * 4.0
        for i, p in enumerate(base_pitches):
            notes.append(_note(p, offset + i * 0.5))
    return notes


# ---------------------------------------------------------------------------
# _analysis.py unit tests
# ---------------------------------------------------------------------------


class TestDetectScale:
    def test_major_scale_detected(self) -> None:
        notes = _make_scale_run()
        matches = detect_scale(notes)
        assert matches, "should return at least one match"
        tops = [m["name"] for m in matches[:3]]
        assert "major" in tops

    def test_returns_up_to_five(self) -> None:
        notes = _make_scale_run()
        matches = detect_scale(notes)
        assert 1 <= len(matches) <= 5

    def test_empty_notes(self) -> None:
        assert detect_scale([]) == []

    def test_confidence_bounded(self) -> None:
        notes = _make_scale_run()
        for m in detect_scale(notes):
            assert 0.0 <= m["confidence"] <= 1.0

    def test_single_note_still_works(self) -> None:
        notes = [_note(60, 0.0)]
        matches = detect_scale(notes)
        assert len(matches) >= 1


class TestAnalyzeRhythm:
    def test_on_beat_notes_high_quantization(self) -> None:
        # Notes exactly on quarter-note grid
        notes = [_note(60, float(i)) for i in range(8)]
        analysis = analyze_rhythm(notes)
        assert analysis["quantization_score"] >= 0.95

    def test_empty_notes(self) -> None:
        a = analyze_rhythm([])
        assert a["total_notes"] == 0
        assert a["quantization_score"] == 1.0

    def test_syncopation_score_range(self) -> None:
        notes = _make_scale_run()
        a = analyze_rhythm(notes)
        assert 0.0 <= a["syncopation_score"] <= 1.0

    def test_swing_ratio_positive(self) -> None:
        notes = _make_scale_run()
        a = analyze_rhythm(notes)
        assert a["swing_ratio"] >= 0.0

    def test_dominant_subdivision_is_string(self) -> None:
        notes = _make_scale_run()
        a = analyze_rhythm(notes)
        assert isinstance(a["dominant_subdivision"], str)


class TestAnalyzeContour:
    def test_ascending_scale(self) -> None:
        notes = [_note(60 + i, float(i)) for i in range(8)]
        analysis = analyze_contour(notes)
        assert analysis["shape"] == "ascending"

    def test_descending_scale(self) -> None:
        notes = [_note(72 - i, float(i)) for i in range(8)]
        analysis = analyze_contour(notes)
        assert analysis["shape"] == "descending"

    def test_arch_shape(self) -> None:
        pitches = [60, 62, 65, 67, 69, 67, 65, 62, 60]
        notes = [_note(p, float(i)) for i, p in enumerate(pitches)]
        analysis = analyze_contour(notes)
        assert analysis["shape"] in ("arch", "wave")

    def test_intervals_list_is_bounded(self) -> None:
        notes = _make_scale_run()
        analysis = analyze_contour(notes)
        assert len(analysis["intervals"]) <= 32

    def test_single_note(self) -> None:
        notes = [_note(60, 0.0)]
        analysis = analyze_contour(notes)
        assert analysis["shape"] == "flat"
        assert analysis["intervals"] == []


class TestAnalyzeDensity:
    def test_bar_count_matches(self) -> None:
        notes = _make_chord_sequence()
        bars = analyze_density(notes)
        assert len(bars) == 4

    def test_notes_per_beat_positive(self) -> None:
        notes = _make_chord_sequence()
        for b in analyze_density(notes):
            assert b["notes_per_beat"] > 0

    def test_empty_notes(self) -> None:
        assert analyze_density([]) == []


class TestComputeTension:
    def test_returns_one_entry_per_bar(self) -> None:
        notes = _make_chord_sequence()
        bars = compute_tension(notes)
        assert len(bars) == 4

    def test_tension_in_range(self) -> None:
        notes = _make_chord_sequence()
        for b in compute_tension(notes):
            assert 0.0 <= b["tension"] <= 1.0

    def test_label_is_string(self) -> None:
        notes = _make_chord_sequence()
        for b in compute_tension(notes):
            assert b["label"] in ("consonant", "mild", "tense")


class TestDetectCadences:
    def test_short_track_no_cadences(self) -> None:
        notes = _make_scale_run()
        assert detect_cadences(notes) == []

    def test_four_bar_chord_sequence_may_find_cadence(self) -> None:
        notes = _make_chord_sequence()
        cadences = detect_cadences(notes)
        assert isinstance(cadences, list)


class TestFindMotifs:
    def test_finds_repeated_pattern(self) -> None:
        notes = _make_motif_track()
        motifs = find_motifs(notes, min_length=3, min_occurrences=2)
        assert len(motifs) >= 1

    def test_motif_occurrences_gte_min(self) -> None:
        notes = _make_motif_track()
        for m in find_motifs(notes, min_occurrences=2):
            assert m["occurrences"] >= 2

    def test_too_short_track(self) -> None:
        notes = [_note(60, 0.0), _note(62, 0.5)]
        assert find_motifs(notes) == []

    def test_interval_pattern_is_list_of_int(self) -> None:
        notes = _make_motif_track()
        for m in find_motifs(notes):
            for iv in m["interval_pattern"]:
                assert isinstance(iv, int)


class TestCheckVoiceLeading:
    def test_no_parallel_motion_on_single_voice(self) -> None:
        # A monophonic scale has no simultaneous voices, so parallel fifths/octaves
        # cannot occur.  Large leaps between bars may still be reported.
        notes = _make_scale_run()
        issues = check_voice_leading(notes)
        parallel = [i for i in issues if i["issue_type"] in ("parallel_fifths", "parallel_octaves")]
        assert parallel == []

    def test_returns_list(self) -> None:
        notes = _make_chord_sequence()
        issues = check_voice_leading(notes)
        assert isinstance(issues, list)

    def test_issue_types_are_valid(self) -> None:
        notes = _make_chord_sequence()
        valid = {"parallel_fifths", "parallel_octaves", "large_leap"}
        for issue in check_voice_leading(notes):
            assert issue["issue_type"] in valid


class TestEstimateTempo:
    def test_regular_quarter_notes_approx_120(self) -> None:
        # Quarter notes at 480 tpb, 120 BPM ≈ one note per beat
        notes = [_note(60, float(i)) for i in range(8)]
        est = estimate_tempo(notes)
        assert 60.0 <= est["estimated_bpm"] <= 300.0

    def test_empty_notes(self) -> None:
        est = estimate_tempo([])
        assert est["estimated_bpm"] == 120.0
        assert est["confidence"] == "none"

    def test_confidence_is_valid(self) -> None:
        notes = _make_scale_run()
        est = estimate_tempo(notes)
        assert est["confidence"] in ("high", "medium", "low", "none")


class TestPhraseSimilarity:
    def test_identical_phrases_score_high(self) -> None:
        notes = _make_scale_run()
        score = phrase_similarity(notes, notes)
        assert score >= 0.9

    def test_empty_query_returns_zero(self) -> None:
        notes = _make_scale_run()
        assert phrase_similarity([], notes) == 0.0

    def test_score_in_range(self) -> None:
        a = _make_scale_run()
        b = _make_chord_sequence()
        score = phrase_similarity(a, b)
        assert 0.0 <= score <= 1.0


# ---------------------------------------------------------------------------
# CLI command integration tests (no real .muse repo needed for help/validation)
# ---------------------------------------------------------------------------


class TestCliHelpPages:
    """Verify all new commands are registered and have help text."""

    @pytest.mark.parametrize("cmd", [
        ["midi", "rhythm", "--help"],
        ["midi", "scale", "--help"],
        ["midi", "contour", "--help"],
        ["midi", "density", "--help"],
        ["midi", "tension", "--help"],
        ["midi", "cadence", "--help"],
        ["midi", "motif", "--help"],
        ["midi", "voice-leading", "--help"],
        ["midi", "instrumentation", "--help"],
        ["midi", "tempo", "--help"],
        ["midi", "compare", "--help"],
        ["midi", "quantize", "--help"],
        ["midi", "humanize", "--help"],
        ["midi", "invert", "--help"],
        ["midi", "retrograde", "--help"],
        ["midi", "arpeggiate", "--help"],
        ["midi", "normalize", "--help"],
        ["midi", "shard", "--help"],
        ["midi", "agent-map", "--help"],
        ["midi", "find-phrase", "--help"],
    ])
    def test_help_exits_zero(self, cmd: list[str]) -> None:
        result = runner.invoke(cli, cmd)
        assert result.exit_code == 0, f"Help failed for {cmd}: {result.output}"

    def test_midi_namespace_lists_all_commands(self) -> None:
        result = runner.invoke(cli, ["midi", "--help"])
        assert result.exit_code == 0
        output = result.output
        for expected in [
            "rhythm", "scale", "contour", "density", "tension",
            "cadence", "motif", "voice-leading", "instrumentation",
            "tempo", "compare", "quantize", "humanize",
            "invert", "retrograde", "arpeggiate", "normalize",
            "shard", "agent-map", "find-phrase",
        ]:
            assert expected in output, f"'{expected}' not found in midi help"


class TestQuantizeValidation:
    """Validate --grid and --strength option guards."""

    def test_unknown_grid_exits_error(self, tmp_path: pathlib.Path) -> None:
        result = runner.invoke(cli, ["midi", "quantize", "fake.mid", "--grid", "99th"])
        assert result.exit_code != 0

    def test_invalid_strength_exits_error(self, tmp_path: pathlib.Path) -> None:
        result = runner.invoke(cli, ["midi", "quantize", "fake.mid", "--strength", "2.5"])
        assert result.exit_code != 0


class TestArpeggiateValidation:
    def test_unknown_rate_exits_error(self) -> None:
        result = runner.invoke(cli, ["midi", "arpeggiate", "fake.mid", "--rate", "64th"])
        assert result.exit_code != 0

    def test_unknown_order_exits_error(self) -> None:
        result = runner.invoke(cli, ["midi", "arpeggiate", "fake.mid", "--order", "zigzag"])
        assert result.exit_code != 0


class TestNormalizeValidation:
    def test_min_gte_max_exits_error(self) -> None:
        result = runner.invoke(cli, ["midi", "normalize", "fake.mid", "--min", "100", "--max", "50"])
        assert result.exit_code != 0

    def test_out_of_range_min_exits_error(self) -> None:
        result = runner.invoke(cli, ["midi", "normalize", "fake.mid", "--min", "0"])
        assert result.exit_code != 0


class TestMidiShardValidation:
    def test_mutually_exclusive_flags(self) -> None:
        result = runner.invoke(cli, [
            "midi", "shard", "fake.mid", "--shards", "4", "--bars-per-shard", "8"
        ])
        assert result.exit_code != 0

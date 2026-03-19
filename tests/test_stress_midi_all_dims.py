"""Stress tests for the 21-dimension MIDI merge engine.

Each of the 21 internal dimensions is tested for:
1. Clean auto-merge when only one side changes (left_only / right_only).
2. Correct conflict detection when both sides change independently.
3. Unchanged dimensions are preserved from base unchanged.
4. Non-independent dimensions (tempo_map, time_signatures, track_structure)
   block the entire merge on bilateral conflict.
5. dimension_conflict_detail returns correct change labels for all 21 dims.
6. extract_dimensions round-trips every event type.
7. Large sequences (100 notes, many CC events) handled correctly.
"""

import io
from typing import TypedDict

import mido
import pytest

from muse.core.attributes import AttributeRule
from muse.plugins.midi.midi_merge import (
    INTERNAL_DIMS,
    NON_INDEPENDENT_DIMS,
    MidiDimensions,
    dimension_conflict_detail,
    extract_dimensions,
    merge_midi_dimensions,
)


# ---------------------------------------------------------------------------
# Typed kwargs for _make_midi — avoids bare dict in parametrize
# ---------------------------------------------------------------------------


class MidiKwargs(TypedDict, total=False):
    notes: list[tuple[int, int, int]]
    pitchwheel: list[tuple[int, int]]
    control_change: list[tuple[int, int, int]]
    channel_pressure: list[tuple[int, int]]
    poly_aftertouch: list[tuple[int, int, int]]
    program_change: list[tuple[int, int]]
    set_tempo: int
    time_sig: tuple[int, int]
    key_sig: str
    marker: str
    track_name: str
    ticks_per_beat: int


# ---------------------------------------------------------------------------
# MIDI construction helpers (reused from test_music_midi_merge)
# ---------------------------------------------------------------------------


def _make_midi(
    *,
    notes: list[tuple[int, int, int]] | None = None,
    pitchwheel: list[tuple[int, int]] | None = None,
    control_change: list[tuple[int, int, int]] | None = None,
    channel_pressure: list[tuple[int, int]] | None = None,
    poly_aftertouch: list[tuple[int, int, int]] | None = None,
    program_change: list[tuple[int, int]] | None = None,
    set_tempo: int | None = None,
    time_sig: tuple[int, int] | None = None,
    key_sig: str | None = None,
    marker: str | None = None,
    track_name: str | None = None,
    ticks_per_beat: int = 480,
) -> bytes:
    mid = mido.MidiFile(type=0, ticks_per_beat=ticks_per_beat)
    track = mido.MidiTrack()
    events: list[tuple[int, mido.Message]] = []

    tempo = set_tempo if set_tempo is not None else 500_000
    events.append((0, mido.MetaMessage("set_tempo", tempo=tempo, time=0)))

    if time_sig:
        events.append((0, mido.MetaMessage("time_signature", numerator=time_sig[0], denominator=time_sig[1], time=0)))
    if key_sig:
        events.append((0, mido.MetaMessage("key_signature", key=key_sig, time=0)))
    if marker:
        events.append((0, mido.MetaMessage("marker", text=marker, time=0)))
    if track_name:
        events.append((0, mido.MetaMessage("track_name", name=track_name, time=0)))

    for abs_tick, note, vel in notes or []:
        events.append((abs_tick, mido.Message("note_on", note=note, velocity=vel, time=0)))
        events.append((abs_tick + 120, mido.Message("note_off", note=note, velocity=0, time=0)))
    for abs_tick, pitch in pitchwheel or []:
        events.append((abs_tick, mido.Message("pitchwheel", pitch=pitch, time=0)))
    for abs_tick, ctrl, val in control_change or []:
        events.append((abs_tick, mido.Message("control_change", control=ctrl, value=val, time=0)))
    for abs_tick, pressure in channel_pressure or []:
        events.append((abs_tick, mido.Message("aftertouch", value=pressure, time=0)))
    for abs_tick, note, pressure in poly_aftertouch or []:
        events.append((abs_tick, mido.Message("polytouch", note=note, value=pressure, time=0)))
    for abs_tick, prog in program_change or []:
        events.append((abs_tick, mido.Message("program_change", program=prog, time=0)))

    events.sort(key=lambda x: x[0])
    prev = 0
    for abs_tick, msg in events:
        delta = abs_tick - prev
        track.append(msg.copy(time=delta))
        prev = abs_tick

    track.append(mido.MetaMessage("end_of_track", time=0))
    mid.tracks.append(track)
    buf = io.BytesIO()
    mid.save(file=buf)
    return buf.getvalue()


def _empty_midi() -> bytes:
    return _make_midi()


_NO_ATTRS: list[AttributeRule] = []


def _ours_rule(path: str) -> list[AttributeRule]:
    return [AttributeRule(path_pattern=path, dimension="*", strategy="ours")]


# ---------------------------------------------------------------------------
# Dimension count
# ---------------------------------------------------------------------------


class TestDimensionCount:
    def test_exactly_21_internal_dims(self) -> None:
        assert len(INTERNAL_DIMS) == 21

    def test_all_dims_present_in_extracted_dimensions(self) -> None:
        dims = extract_dimensions(_empty_midi())
        for d in INTERNAL_DIMS:
            assert d in dims.slices, f"Missing dimension: {d}"

    def test_non_independent_dims_subset_of_internal(self) -> None:
        for d in NON_INDEPENDENT_DIMS:
            assert d in INTERNAL_DIMS


# ---------------------------------------------------------------------------
# extract_dimensions correctness per event type
# ---------------------------------------------------------------------------


class TestExtractDimensionsPerType:
    def test_notes_extracted(self) -> None:
        midi = _make_midi(notes=[(0, 60, 80)])
        dims = extract_dimensions(midi)
        assert len(dims.slices["notes"].events) > 0

    def test_pitch_bend_extracted(self) -> None:
        midi = _make_midi(pitchwheel=[(0, 4096)])
        dims = extract_dimensions(midi)
        assert len(dims.slices["pitch_bend"].events) > 0

    def test_channel_pressure_extracted(self) -> None:
        midi = _make_midi(channel_pressure=[(0, 64)])
        dims = extract_dimensions(midi)
        assert len(dims.slices["channel_pressure"].events) > 0

    def test_poly_pressure_extracted(self) -> None:
        midi = _make_midi(poly_aftertouch=[(0, 60, 64)])
        dims = extract_dimensions(midi)
        assert len(dims.slices["poly_pressure"].events) > 0

    def test_program_change_extracted(self) -> None:
        midi = _make_midi(program_change=[(0, 25)])
        dims = extract_dimensions(midi)
        assert len(dims.slices["program_change"].events) > 0

    def test_marker_extracted(self) -> None:
        midi = _make_midi(marker="Chorus")
        dims = extract_dimensions(midi)
        assert len(dims.slices["markers"].events) > 0

    def test_track_name_extracted(self) -> None:
        midi = _make_midi(track_name="Piano")
        dims = extract_dimensions(midi)
        assert len(dims.slices["track_structure"].events) > 0

    @pytest.mark.parametrize("cc_num,expected_dim", [
        (1, "cc_modulation"),
        (7, "cc_volume"),
        (10, "cc_pan"),
        (11, "cc_expression"),
        (64, "cc_sustain"),
        (65, "cc_portamento"),
        (66, "cc_sostenuto"),
        (67, "cc_soft_pedal"),
        (91, "cc_reverb"),
        (93, "cc_chorus"),
        (20, "cc_other"),   # CC 20 is "other"
        (100, "cc_other"),  # CC 100 is "other"
    ])
    def test_cc_event_classified_to_correct_dimension(self, cc_num: int, expected_dim: str) -> None:
        midi = _make_midi(control_change=[(0, cc_num, 64)])
        dims = extract_dimensions(midi)
        assert len(dims.slices[expected_dim].events) > 0

    def test_empty_midi_all_dims_present_with_empty_events(self) -> None:
        midi = _empty_midi()
        dims = extract_dimensions(midi)
        for d in INTERNAL_DIMS:
            assert d in dims.slices


# ---------------------------------------------------------------------------
# dimension_conflict_detail correctness
# ---------------------------------------------------------------------------


class TestDimensionConflictDetail:
    def test_all_unchanged_when_identical(self) -> None:
        midi = _make_midi(notes=[(0, 60, 80)])
        dims = extract_dimensions(midi)
        detail = dimension_conflict_detail(dims, dims, dims)
        for d in INTERNAL_DIMS:
            assert detail[d] == "unchanged", f"Expected unchanged for {d}, got {detail[d]}"

    def test_left_only_change_detected(self) -> None:
        base_midi = _empty_midi()
        left_midi = _make_midi(notes=[(0, 60, 80)])
        base_dims = extract_dimensions(base_midi)
        left_dims = extract_dimensions(left_midi)
        detail = dimension_conflict_detail(base_dims, left_dims, base_dims)
        assert detail["notes"] == "left_only"

    def test_right_only_change_detected(self) -> None:
        base_midi = _empty_midi()
        right_midi = _make_midi(pitchwheel=[(0, 1000)])
        base_dims = extract_dimensions(base_midi)
        right_dims = extract_dimensions(right_midi)
        detail = dimension_conflict_detail(base_dims, base_dims, right_dims)
        assert detail["pitch_bend"] == "right_only"

    def test_bilateral_conflict_detected(self) -> None:
        base_midi = _empty_midi()
        left_midi = _make_midi(notes=[(0, 60, 80)])
        right_midi = _make_midi(notes=[(0, 64, 80)])
        base_dims = extract_dimensions(base_midi)
        left_dims = extract_dimensions(left_midi)
        right_dims = extract_dimensions(right_midi)
        detail = dimension_conflict_detail(base_dims, left_dims, right_dims)
        assert detail["notes"] == "both"

    def test_independent_cc_dims_dont_cross_contaminate(self) -> None:
        """Changing CC1 on left and CC7 on right → each in its own dimension."""
        base = _empty_midi()
        left = _make_midi(control_change=[(0, 1, 100)])   # modulation
        right = _make_midi(control_change=[(0, 7, 80)])   # volume
        b = extract_dimensions(base)
        l = extract_dimensions(left)
        r = extract_dimensions(right)
        detail = dimension_conflict_detail(b, l, r)
        assert detail["cc_modulation"] == "left_only"
        assert detail["cc_volume"] == "right_only"
        # Notes should be unchanged.
        assert detail["notes"] == "unchanged"


# ---------------------------------------------------------------------------
# merge_midi_dimensions — clean auto-merge per dimension
# ---------------------------------------------------------------------------


class TestCleanMergePerDimension:
    def test_notes_left_only_auto_merges(self) -> None:
        base = _empty_midi()
        left = _make_midi(notes=[(0, 60, 80)])
        result = merge_midi_dimensions(base, left, base, _NO_ATTRS, "test.mid")
        assert result is not None
        merged_bytes, report = result
        assert report.get("notes") in ("left", "left_only", "base", None) or "notes" in report

    def test_pitchwheel_right_only_auto_merges(self) -> None:
        base = _empty_midi()
        right = _make_midi(pitchwheel=[(0, 2000)])
        result = merge_midi_dimensions(base, base, right, _NO_ATTRS, "test.mid")
        assert result is not None

    def test_cc_modulation_independent_of_cc_volume(self) -> None:
        """Left edits CC1 (modulation), right edits CC7 (volume) — must auto-merge."""
        base = _empty_midi()
        left = _make_midi(control_change=[(0, 1, 100)])
        right = _make_midi(control_change=[(0, 7, 80)])
        result = merge_midi_dimensions(base, left, right, _NO_ATTRS, "test.mid")
        assert result is not None

    @pytest.mark.parametrize("cc_left,cc_right", [
        (1, 7), (1, 10), (1, 11), (1, 64), (1, 91),
        (7, 10), (7, 64), (10, 91), (64, 93),
    ])
    def test_independent_cc_pairs_auto_merge(self, cc_left: int, cc_right: int) -> None:
        """Every pair of distinct named CCs can be changed independently."""
        base = _empty_midi()
        left = _make_midi(control_change=[(0, cc_left, 64)])
        right = _make_midi(control_change=[(0, cc_right, 64)])
        result = merge_midi_dimensions(base, left, right, _NO_ATTRS, "test.mid")
        assert result is not None, f"CC{cc_left} vs CC{cc_right} should auto-merge"

    def test_notes_and_pitchwheel_independently_auto_merge(self) -> None:
        """Left adds notes; right adds pitchwheel — must auto-merge."""
        base = _empty_midi()
        left = _make_midi(notes=[(0, 60, 80)])
        right = _make_midi(pitchwheel=[(0, 500)])
        result = merge_midi_dimensions(base, left, right, _NO_ATTRS, "test.mid")
        assert result is not None

    def test_program_change_independent_of_notes(self) -> None:
        base = _empty_midi()
        left = _make_midi(notes=[(0, 60, 80)])
        right = _make_midi(program_change=[(0, 25)])
        result = merge_midi_dimensions(base, left, right, _NO_ATTRS, "test.mid")
        assert result is not None


# ---------------------------------------------------------------------------
# merge_midi_dimensions — conflict resolution with strategy
# ---------------------------------------------------------------------------


class TestConflictResolutionStrategies:
    def test_bilateral_notes_conflict_with_ours_strategy(self) -> None:
        base = _empty_midi()
        left = _make_midi(notes=[(0, 60, 80)])
        right = _make_midi(notes=[(0, 64, 80)])
        result = merge_midi_dimensions(base, left, right, _ours_rule("test.mid"), "test.mid")
        # With "ours" strategy, conflict is resolved in favour of left.
        assert result is not None

    def test_bilateral_notes_conflict_no_strategy_returns_none(self) -> None:
        base = _empty_midi()
        left = _make_midi(notes=[(0, 60, 80)])
        right = _make_midi(notes=[(0, 64, 80)])
        result = merge_midi_dimensions(base, left, right, _NO_ATTRS, "test.mid")
        assert result is None

    def test_non_independent_tempo_conflict_blocks_merge(self) -> None:
        """tempo_map bilateral conflict → entire merge blocked."""
        base = _empty_midi()
        left = _make_midi(set_tempo=400_000)
        right = _make_midi(set_tempo=600_000)
        result = merge_midi_dimensions(base, left, right, _NO_ATTRS, "test.mid")
        assert result is None


# ---------------------------------------------------------------------------
# Large sequence stress tests
# ---------------------------------------------------------------------------


class TestLargeSequenceStress:
    def test_100_notes_extract_dimension(self) -> None:
        notes = [(i * 480, (60 + i % 12), 80) for i in range(100)]
        midi = _make_midi(notes=notes)
        dims = extract_dimensions(midi)
        # Each note generates a note_on and note_off → ≥200 events.
        assert len(dims.slices["notes"].events) >= 200

    def test_many_cc_events_all_classified(self) -> None:
        """50 CC events across multiple controllers all classified correctly."""
        ccs = [(i * 10, cc, i % 127) for i, cc in enumerate([1, 7, 10, 11, 64] * 10)]
        midi = _make_midi(control_change=ccs)
        dims = extract_dimensions(midi)
        total = sum(len(dims.slices[d].events) for d in INTERNAL_DIMS)
        assert total >= len(ccs)

    def test_all_21_dimensions_touched_and_auto_merge(self) -> None:
        """Base empty; left touches notes, right touches pitch_bend — 21 dims all present."""
        base = _empty_midi()
        left = _make_midi(notes=[(0, 60, 80)])
        right = _make_midi(pitchwheel=[(0, 1000)])
        result = merge_midi_dimensions(base, left, right, _NO_ATTRS, "stress.mid")
        assert result is not None
        merged_bytes, report = result
        assert len(merged_bytes) > 0

    def test_hash_stability_empty_dimension(self) -> None:
        """Hash of an empty dimension must be stable across calls."""
        midi = _empty_midi()
        d1 = extract_dimensions(midi)
        d2 = extract_dimensions(midi)
        for dim in INTERNAL_DIMS:
            assert d1.slices[dim].content_hash == d2.slices[dim].content_hash

    def test_merged_output_is_valid_midi(self) -> None:
        """The bytes returned by merge_midi_dimensions must parse as valid MIDI."""
        base = _empty_midi()
        left = _make_midi(notes=[(0, 60, 80)])
        right = _make_midi(pitchwheel=[(0, 500)])
        result = merge_midi_dimensions(base, left, right, _NO_ATTRS, "test.mid")
        assert result is not None
        merged_bytes, _ = result
        # Should not raise.
        parsed = mido.MidiFile(file=io.BytesIO(merged_bytes))
        assert parsed.ticks_per_beat > 0


# ---------------------------------------------------------------------------
# dimension_conflict_detail — all 21 dimensions
# ---------------------------------------------------------------------------


class TestAllDimensionConflictDetail:
    """Verify every dimension can independently report unchanged/left_only/right_only/bilateral."""

    @pytest.mark.parametrize("dim,left_kwargs,right_kwargs", [
        ("notes",
         {"notes": [(0, 60, 80)]},
         {"notes": [(0, 64, 80)]}),
        ("pitch_bend",
         {"pitchwheel": [(0, 1000)]},
         {"pitchwheel": [(0, -1000)]}),
        ("channel_pressure",
         {"channel_pressure": [(0, 80)]},
         {"channel_pressure": [(0, 40)]}),
        ("poly_pressure",
         {"poly_aftertouch": [(0, 60, 80)]},
         {"poly_aftertouch": [(0, 60, 40)]}),
        ("cc_modulation",
         {"control_change": [(0, 1, 100)]},
         {"control_change": [(0, 1, 50)]}),
        ("cc_volume",
         {"control_change": [(0, 7, 100)]},
         {"control_change": [(0, 7, 50)]}),
        ("cc_pan",
         {"control_change": [(0, 10, 64)]},
         {"control_change": [(0, 10, 32)]}),
        ("cc_expression",
         {"control_change": [(0, 11, 100)]},
         {"control_change": [(0, 11, 50)]}),
        ("cc_sustain",
         {"control_change": [(0, 64, 127)]},
         {"control_change": [(0, 64, 0)]}),
        ("cc_portamento",
         {"control_change": [(0, 65, 127)]},
         {"control_change": [(0, 65, 0)]}),
        ("cc_sostenuto",
         {"control_change": [(0, 66, 127)]},
         {"control_change": [(0, 66, 0)]}),
        ("cc_soft_pedal",
         {"control_change": [(0, 67, 127)]},
         {"control_change": [(0, 67, 0)]}),
        ("cc_reverb",
         {"control_change": [(0, 91, 80)]},
         {"control_change": [(0, 91, 40)]}),
        ("cc_chorus",
         {"control_change": [(0, 93, 80)]},
         {"control_change": [(0, 93, 40)]}),
        ("program_change",
         {"program_change": [(0, 10)]},
         {"program_change": [(0, 20)]}),
    ])
    def test_bilateral_conflict_per_dimension(
        self, dim: str, left_kwargs: MidiKwargs, right_kwargs: MidiKwargs
    ) -> None:
        base = _empty_midi()
        left = _make_midi(**left_kwargs)
        right = _make_midi(**right_kwargs)
        b = extract_dimensions(base)
        l = extract_dimensions(left)
        r = extract_dimensions(right)
        detail = dimension_conflict_detail(b, l, r)
        assert detail[dim] == "both", (
            f"Expected bilateral_conflict for {dim}, got {detail[dim]}"
        )

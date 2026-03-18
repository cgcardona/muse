"""Tests for muse/plugins/midi/midi_merge.py — 21-dimension MIDI merge."""
from __future__ import annotations

import io

import mido
import pytest

from muse.core.attributes import AttributeRule
from muse.plugins.midi.midi_merge import (
    INTERNAL_DIMS,
    DIM_ALIAS,
    DimensionSlice,
    MidiDimensions,
    NON_INDEPENDENT_DIMS,
    _classify_event,
    _hash_events,
    dimension_conflict_detail,
    extract_dimensions,
    merge_midi_dimensions,
)


# ---------------------------------------------------------------------------
# MIDI builder helpers
# ---------------------------------------------------------------------------


def _make_midi(
    *,
    notes: list[tuple[int, int, int]] | None = None,
    pitchwheel: list[tuple[int, int]] | None = None,
    control_change: list[tuple[int, int, int]] | None = None,
    channel_pressure: list[tuple[int, int]] | None = None,
    poly_aftertouch: list[tuple[int, int, int]] | None = None,
    program_change: list[tuple[int, int]] | None = None,
    tempo: int = 500_000,
    ticks_per_beat: int = 480,
) -> bytes:
    """Build a minimal type-0 MIDI file in memory.

    Args:
        notes:            List of (abs_tick, note, velocity) note-on events.
        pitchwheel:       List of (abs_tick, pitch) pitchwheel events.
        control_change:   List of (abs_tick, control, value) CC events.
        channel_pressure: List of (abs_tick, pressure) channel pressure events.
        poly_aftertouch:  List of (abs_tick, note, pressure) poly-pressure events.
        program_change:   List of (abs_tick, program) program-change events.
        tempo:            Microseconds per beat (default 120 BPM).
        ticks_per_beat:   MIDI resolution.
    """
    mid = mido.MidiFile(type=0, ticks_per_beat=ticks_per_beat)
    track = mido.MidiTrack()

    events: list[tuple[int, mido.Message]] = []
    events.append((0, mido.MetaMessage("set_tempo", tempo=tempo, time=0)))

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

    for abs_tick, program in program_change or []:
        events.append((abs_tick, mido.Message("program_change", program=program, time=0)))

    events.sort(key=lambda x: (x[0], x[1].type))
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


def _midi_bytes_to_notes(midi_bytes: bytes) -> set[int]:
    mid = mido.MidiFile(file=io.BytesIO(midi_bytes))
    notes: set[int] = set()
    for track in mid.tracks:
        for msg in track:
            if msg.type == "note_on" and msg.velocity > 0:
                notes.add(msg.note)
    return notes


def _midi_bytes_to_pitchwheels(midi_bytes: bytes) -> list[int]:
    mid = mido.MidiFile(file=io.BytesIO(midi_bytes))
    values: list[int] = []
    for track in mid.tracks:
        for msg in track:
            if msg.type == "pitchwheel":
                values.append(msg.pitch)
    return values


def _midi_bytes_to_ccs(midi_bytes: bytes) -> list[tuple[int, int]]:
    mid = mido.MidiFile(file=io.BytesIO(midi_bytes))
    ccs: list[tuple[int, int]] = []
    for track in mid.tracks:
        for msg in track:
            if msg.type == "control_change":
                ccs.append((msg.control, msg.value))
    return ccs


# ---------------------------------------------------------------------------
# INTERNAL_DIMS — verify all 21 dimensions declared
# ---------------------------------------------------------------------------


class TestInternalDims:
    _EXPECTED_21 = [
        "notes", "pitch_bend", "channel_pressure", "poly_pressure",
        "cc_modulation", "cc_volume", "cc_pan", "cc_expression",
        "cc_sustain", "cc_portamento", "cc_sostenuto", "cc_soft_pedal",
        "cc_reverb", "cc_chorus", "cc_other",
        "program_change", "tempo_map", "time_signatures",
        "key_signatures", "markers", "track_structure",
    ]

    def test_exactly_21_dims(self) -> None:
        assert len(INTERNAL_DIMS) == 21

    def test_all_expected_names_present(self) -> None:
        assert set(INTERNAL_DIMS) == set(self._EXPECTED_21)

    def test_non_independent_dims(self) -> None:
        assert NON_INDEPENDENT_DIMS == frozenset({"tempo_map", "time_signatures", "track_structure"})

    def test_no_old_coarse_names_in_dims(self) -> None:
        """Old coarse names (melodic, rhythmic, harmonic, dynamic, structural) must be gone."""
        old_names = {"melodic", "rhythmic", "harmonic", "dynamic", "structural"}
        assert old_names.isdisjoint(set(INTERNAL_DIMS))

    def test_no_old_coarse_aliases_in_dim_alias(self) -> None:
        """Old aliases removed from DIM_ALIAS — no backward-compat shims."""
        old_aliases = {"melodic", "rhythmic", "harmonic", "dynamic", "structural"}
        assert old_aliases.isdisjoint(set(DIM_ALIAS))


# ---------------------------------------------------------------------------
# _classify_event — fine-grained 21-dimension routing
# ---------------------------------------------------------------------------


class TestClassifyEvent:
    # Note events → notes
    def test_note_on(self) -> None:
        assert _classify_event(mido.Message("note_on", note=60)) == "notes"

    def test_note_off(self) -> None:
        assert _classify_event(mido.Message("note_off", note=60)) == "notes"

    # Pitch bend → pitch_bend
    def test_pitchwheel(self) -> None:
        assert _classify_event(mido.Message("pitchwheel", pitch=100)) == "pitch_bend"

    # Channel pressure → channel_pressure
    def test_channel_aftertouch(self) -> None:
        assert _classify_event(mido.Message("aftertouch", value=64)) == "channel_pressure"

    # Polyphonic aftertouch → poly_pressure
    def test_poly_aftertouch(self) -> None:
        assert _classify_event(mido.Message("polytouch", note=60, value=64)) == "poly_pressure"

    # Named CC controllers
    def test_cc_1_modulation(self) -> None:
        assert _classify_event(mido.Message("control_change", control=1, value=64)) == "cc_modulation"

    def test_cc_7_volume(self) -> None:
        assert _classify_event(mido.Message("control_change", control=7, value=100)) == "cc_volume"

    def test_cc_10_pan(self) -> None:
        assert _classify_event(mido.Message("control_change", control=10, value=64)) == "cc_pan"

    def test_cc_11_expression(self) -> None:
        assert _classify_event(mido.Message("control_change", control=11, value=100)) == "cc_expression"

    def test_cc_64_sustain(self) -> None:
        assert _classify_event(mido.Message("control_change", control=64, value=127)) == "cc_sustain"

    def test_cc_65_portamento(self) -> None:
        assert _classify_event(mido.Message("control_change", control=65, value=0)) == "cc_portamento"

    def test_cc_66_sostenuto(self) -> None:
        assert _classify_event(mido.Message("control_change", control=66, value=127)) == "cc_sostenuto"

    def test_cc_67_soft_pedal(self) -> None:
        assert _classify_event(mido.Message("control_change", control=67, value=64)) == "cc_soft_pedal"

    def test_cc_91_reverb(self) -> None:
        assert _classify_event(mido.Message("control_change", control=91, value=40)) == "cc_reverb"

    def test_cc_93_chorus(self) -> None:
        assert _classify_event(mido.Message("control_change", control=93, value=20)) == "cc_chorus"

    def test_cc_other_unlisted(self) -> None:
        # CC 2 is not individually named → cc_other
        assert _classify_event(mido.Message("control_change", control=2, value=50)) == "cc_other"

    def test_cc_3_other(self) -> None:
        assert _classify_event(mido.Message("control_change", control=3, value=50)) == "cc_other"

    # Program change
    def test_program_change(self) -> None:
        assert _classify_event(mido.Message("program_change", program=40)) == "program_change"

    # Tempo / time-sig → non-independent
    def test_set_tempo(self) -> None:
        assert _classify_event(mido.MetaMessage("set_tempo", tempo=500_000)) == "tempo_map"

    def test_time_signature(self) -> None:
        msg = mido.MetaMessage(
            "time_signature", numerator=4, denominator=4,
            clocks_per_click=24, notated_32nd_notes_per_beat=8,
        )
        assert _classify_event(msg) == "time_signatures"

    # Key signature
    def test_key_signature(self) -> None:
        assert _classify_event(mido.MetaMessage("key_signature", key="C")) == "key_signatures"

    # Markers
    def test_marker(self) -> None:
        assert _classify_event(mido.MetaMessage("marker", text="verse")) == "markers"

    def test_text(self) -> None:
        assert _classify_event(mido.MetaMessage("text", text="hello")) == "markers"

    # Track structure
    def test_track_name(self) -> None:
        assert _classify_event(mido.MetaMessage("track_name", name="Piano")) == "track_structure"

    def test_end_of_track_returns_none(self) -> None:
        # end_of_track is reconstructed during MIDI assembly, not stored in any dim
        assert _classify_event(mido.MetaMessage("end_of_track")) is None


# ---------------------------------------------------------------------------
# extract_dimensions
# ---------------------------------------------------------------------------


class TestExtractDimensions:
    def test_empty_midi_has_all_21_dims(self) -> None:
        midi = _make_midi()
        dims = extract_dimensions(midi)
        assert set(dims.slices.keys()) == set(INTERNAL_DIMS)

    def test_notes_in_notes_bucket(self) -> None:
        midi = _make_midi(notes=[(0, 60, 80), (480, 64, 80)])
        dims = extract_dimensions(midi)
        note_on = [msg for _, msg in dims.slices["notes"].events if msg.type == "note_on"]
        assert len(note_on) == 2

    def test_pitchwheel_in_pitch_bend(self) -> None:
        midi = _make_midi(pitchwheel=[(100, 500), (200, -500)])
        dims = extract_dimensions(midi)
        assert len(dims.slices["pitch_bend"].events) == 2

    def test_cc_volume_bucket(self) -> None:
        midi = _make_midi(control_change=[(0, 7, 100)])
        dims = extract_dimensions(midi)
        assert len(dims.slices["cc_volume"].events) == 1

    def test_cc_sustain_bucket(self) -> None:
        midi = _make_midi(control_change=[(0, 64, 127)])
        dims = extract_dimensions(midi)
        assert len(dims.slices["cc_sustain"].events) == 1

    def test_cc_modulation_bucket(self) -> None:
        midi = _make_midi(control_change=[(0, 1, 90)])
        dims = extract_dimensions(midi)
        assert len(dims.slices["cc_modulation"].events) == 1

    def test_cc_other_bucket(self) -> None:
        midi = _make_midi(control_change=[(0, 2, 50)])
        dims = extract_dimensions(midi)
        assert len(dims.slices["cc_other"].events) == 1

    def test_tempo_in_tempo_map(self) -> None:
        midi = _make_midi(tempo=600_000)
        dims = extract_dimensions(midi)
        types = {msg.type for _, msg in dims.slices["tempo_map"].events}
        assert "set_tempo" in types

    def test_content_hash_is_deterministic(self) -> None:
        midi = _make_midi(notes=[(0, 60, 80)])
        d1 = extract_dimensions(midi)
        d2 = extract_dimensions(midi)
        assert d1.slices["notes"].content_hash == d2.slices["notes"].content_hash

    def test_different_notes_give_different_hash(self) -> None:
        da = extract_dimensions(_make_midi(notes=[(0, 60, 80)]))
        db = extract_dimensions(_make_midi(notes=[(0, 62, 80)]))
        assert da.slices["notes"].content_hash != db.slices["notes"].content_hash

    def test_different_dimensions_independent_hashes(self) -> None:
        """Changing notes must not affect pitch_bend hash."""
        base = _make_midi(pitchwheel=[(0, 200)])
        with_notes = _make_midi(notes=[(0, 60, 80)], pitchwheel=[(0, 200)])
        da = extract_dimensions(base)
        db = extract_dimensions(with_notes)
        assert da.slices["pitch_bend"].content_hash == db.slices["pitch_bend"].content_hash
        assert da.slices["notes"].content_hash != db.slices["notes"].content_hash

    def test_ticks_per_beat_preserved(self) -> None:
        midi = _make_midi(ticks_per_beat=960)
        assert extract_dimensions(midi).ticks_per_beat == 960

    def test_invalid_bytes_raises(self) -> None:
        with pytest.raises(ValueError, match="Failed to parse"):
            extract_dimensions(b"not a midi file")

    def test_get_by_fine_alias(self) -> None:
        midi = _make_midi(pitchwheel=[(0, 100)])
        dims = extract_dimensions(midi)
        assert dims.get("pitch_bend").name == "pitch_bend"
        assert dims.get("sustain").name == "cc_sustain"
        assert dims.get("volume").name == "cc_volume"

    def test_get_unknown_alias_raises(self) -> None:
        midi = _make_midi()
        dims = extract_dimensions(midi)
        with pytest.raises(KeyError):
            dims.get("melodic")   # old alias — removed


# ---------------------------------------------------------------------------
# dimension_conflict_detail
# ---------------------------------------------------------------------------


class TestDimensionConflictDetail:
    def _dims_from(
        self,
        notes: list[tuple[int, int, int]] | None = None,
        pitchwheel: list[tuple[int, int]] | None = None,
        control_change: list[tuple[int, int, int]] | None = None,
        tempo: int = 500_000,
    ) -> MidiDimensions:
        return extract_dimensions(_make_midi(
            notes=notes, pitchwheel=pitchwheel,
            control_change=control_change, tempo=tempo,
        ))

    def test_unchanged_when_all_same(self) -> None:
        base = self._dims_from(notes=[(0, 60, 80)])
        detail = dimension_conflict_detail(base, base, base)
        assert all(v == "unchanged" for v in detail.values())

    def test_notes_left_only(self) -> None:
        base = self._dims_from()
        left = self._dims_from(notes=[(0, 60, 80)])
        detail = dimension_conflict_detail(base, left, base)
        assert detail["notes"] == "left_only"
        assert detail["pitch_bend"] == "unchanged"

    def test_pitch_bend_right_only(self) -> None:
        base = self._dims_from()
        right = self._dims_from(pitchwheel=[(0, 100)])
        detail = dimension_conflict_detail(base, base, right)
        assert detail["pitch_bend"] == "right_only"

    def test_both_sides_change_notes(self) -> None:
        base = self._dims_from()
        left = self._dims_from(notes=[(0, 60, 80)])
        right = self._dims_from(notes=[(0, 64, 80)])
        detail = dimension_conflict_detail(base, left, right)
        assert detail["notes"] == "both"

    def test_independent_changes_in_separate_dims(self) -> None:
        base = self._dims_from()
        left = self._dims_from(notes=[(0, 60, 80)])
        right = self._dims_from(pitchwheel=[(0, 200)])
        detail = dimension_conflict_detail(base, left, right)
        assert detail["notes"] == "left_only"
        assert detail["pitch_bend"] == "right_only"
        assert detail["cc_volume"] == "unchanged"

    def test_cc_volume_vs_cc_sustain_independent(self) -> None:
        """Two different CC dims changed independently."""
        base = self._dims_from()
        left = self._dims_from(control_change=[(0, 7, 100)])   # cc_volume
        right = self._dims_from(control_change=[(0, 64, 127)])  # cc_sustain
        detail = dimension_conflict_detail(base, left, right)
        assert detail["cc_volume"] == "left_only"
        assert detail["cc_sustain"] == "right_only"


# ---------------------------------------------------------------------------
# merge_midi_dimensions
# ---------------------------------------------------------------------------


class TestMergeMidiDimensions:
    def _midi(
        self,
        notes: list[tuple[int, int, int]] | None = None,
        pitchwheel: list[tuple[int, int]] | None = None,
        control_change: list[tuple[int, int, int]] | None = None,
        tempo: int = 500_000,
        ticks_per_beat: int = 480,
    ) -> bytes:
        return _make_midi(
            notes=notes, pitchwheel=pitchwheel, control_change=control_change,
            tempo=tempo, ticks_per_beat=ticks_per_beat,
        )

    def _rules(self, *rules: tuple[str, str, str]) -> list[AttributeRule]:
        return [AttributeRule(p, d, s, i + 1) for i, (p, d, s) in enumerate(rules)]

    # --- Clean auto-merge: independent dimensions ---------------------------

    def test_independent_notes_and_pitch_bend(self) -> None:
        """Left changed notes, right changed pitch_bend → clean auto-merge."""
        base = self._midi()
        left = self._midi(notes=[(0, 60, 80)])
        right = self._midi(pitchwheel=[(0, 500)])
        result = merge_midi_dimensions(base, left, right, [], "song.mid")
        assert result is not None
        merged, _ = result
        assert _midi_bytes_to_notes(merged) == {60}
        assert _midi_bytes_to_pitchwheels(merged) == [500]

    def test_independent_two_cc_dims(self) -> None:
        """Left changed cc_volume, right changed cc_sustain → clean auto-merge."""
        base = self._midi()
        left = self._midi(control_change=[(0, 7, 100)])    # cc_volume
        right = self._midi(control_change=[(0, 64, 127)])  # cc_sustain
        result = merge_midi_dimensions(base, left, right, [], "song.mid")
        assert result is not None
        merged, _ = result
        ccs = dict(_midi_bytes_to_ccs(merged))
        assert ccs.get(7) == 100
        assert ccs.get(64) == 127

    def test_one_side_only_changed_notes(self) -> None:
        base = self._midi()
        left = self._midi(notes=[(0, 64, 80)])
        result = merge_midi_dimensions(base, left, self._midi(), [], "song.mid")
        assert result is not None
        merged, _ = result
        assert _midi_bytes_to_notes(merged) == {64}

    def test_unchanged_both_sides_preserved(self) -> None:
        base = self._midi(notes=[(0, 60, 80)])
        result = merge_midi_dimensions(base, base, base, [], "song.mid")
        assert result is not None
        merged, _ = result
        assert _midi_bytes_to_notes(merged) == {60}

    # --- Strategy override via AttributeRule --------------------------------

    def test_notes_conflict_resolved_by_ours_rule(self) -> None:
        base = self._midi()
        left = self._midi(notes=[(0, 60, 80)])
        right = self._midi(notes=[(0, 64, 80)])
        rules = self._rules(("*", "notes", "ours"))
        result = merge_midi_dimensions(base, left, right, rules, "song.mid")
        assert result is not None
        merged, report = result
        assert _midi_bytes_to_notes(merged) == {60}
        assert "ours" in report["notes"]

    def test_notes_conflict_resolved_by_theirs_rule(self) -> None:
        base = self._midi()
        left = self._midi(notes=[(0, 60, 80)])
        right = self._midi(notes=[(0, 64, 80)])
        rules = self._rules(("*", "notes", "theirs"))
        result = merge_midi_dimensions(base, left, right, rules, "song.mid")
        assert result is not None
        merged, _ = result
        assert _midi_bytes_to_notes(merged) == {64}

    def test_pitch_bend_conflict_resolved_by_theirs(self) -> None:
        base = self._midi()
        left = self._midi(pitchwheel=[(0, 200)])
        right = self._midi(pitchwheel=[(0, -200)])
        rules = self._rules(("*", "pitch_bend", "theirs"))
        result = merge_midi_dimensions(base, left, right, rules, "song.mid")
        assert result is not None
        merged, _ = result
        assert _midi_bytes_to_pitchwheels(merged) == [-200]

    def test_wildcard_dim_rule_resolves_all(self) -> None:
        base = self._midi()
        left = self._midi(notes=[(0, 60, 80)], pitchwheel=[(0, 200)])
        right = self._midi(notes=[(0, 64, 80)], pitchwheel=[(0, -200)])
        rules = self._rules(("*", "*", "ours"))
        result = merge_midi_dimensions(base, left, right, rules, "song.mid")
        assert result is not None
        merged, _ = result
        assert _midi_bytes_to_notes(merged) == {60}
        assert 200 in _midi_bytes_to_pitchwheels(merged)

    def test_notes_conflict_no_rule_returns_none(self) -> None:
        """Both sides changed notes, no matching rule → conflict → None."""
        base = self._midi()
        left = self._midi(notes=[(0, 60, 80)])
        right = self._midi(notes=[(0, 64, 80)])
        assert merge_midi_dimensions(base, left, right, [], "song.mid") is None

    def test_manual_strategy_returns_none(self) -> None:
        base = self._midi()
        left = self._midi(notes=[(0, 60, 80)])
        right = self._midi(notes=[(0, 64, 80)])
        rules = self._rules(("*", "notes", "manual"))
        assert merge_midi_dimensions(base, left, right, rules, "song.mid") is None

    # --- Report content -----------------------------------------------------

    def test_report_shows_left_right_labels(self) -> None:
        base = self._midi()
        left = self._midi(notes=[(0, 60, 80)])
        right = self._midi(pitchwheel=[(0, 100)])
        result = merge_midi_dimensions(base, left, right, [], "song.mid")
        assert result is not None
        _, report = result
        assert report["notes"] == "left"
        assert report["pitch_bend"] == "right"

    # --- Output is valid MIDI -----------------------------------------------

    def test_merged_bytes_parseable(self) -> None:
        base = self._midi()
        left = self._midi(notes=[(0, 60, 80)])
        right = self._midi(pitchwheel=[(0, 100)])
        result = merge_midi_dimensions(base, left, right, [], "song.mid")
        assert result is not None
        merged, _ = result
        parsed = mido.MidiFile(file=io.BytesIO(merged))
        assert parsed.ticks_per_beat == 480

    def test_merged_bytes_preserve_ticks_per_beat(self) -> None:
        base = _make_midi(ticks_per_beat=960)
        left = _make_midi(notes=[(0, 60, 80)], ticks_per_beat=960)
        right = _make_midi(pitchwheel=[(0, 100)], ticks_per_beat=960)
        result = merge_midi_dimensions(base, left, right, [], "song.mid")
        assert result is not None
        merged, _ = result
        assert mido.MidiFile(file=io.BytesIO(merged)).ticks_per_beat == 960

    # --- Path-pattern matching in rules ------------------------------------

    def test_path_specific_rule_respected(self) -> None:
        base = self._midi()
        left = self._midi(pitchwheel=[(0, 200)])
        right = self._midi(pitchwheel=[(0, -200)])
        rules = self._rules(("keys/*", "pitch_bend", "theirs"))

        result_keys = merge_midi_dimensions(base, left, right, rules, "keys/piano.mid")
        assert result_keys is not None
        merged_keys, _ = result_keys
        assert _midi_bytes_to_pitchwheels(merged_keys) == [-200]

        result_other = merge_midi_dimensions(base, left, right, rules, "other/bass.mid")
        assert result_other is None   # rule doesn't match this path

    def test_multi_rule_priority_order(self) -> None:
        """Lower-priority rule does not override higher-priority one."""
        base = self._midi()
        left = self._midi(control_change=[(0, 7, 100)])   # cc_volume
        right = self._midi(control_change=[(0, 7, 50)])
        rules = self._rules(
            ("*", "cc_volume", "ours"),    # priority 1
            ("*", "cc_volume", "theirs"),  # priority 2 — should be ignored
        )
        result = merge_midi_dimensions(base, left, right, rules, "song.mid")
        assert result is not None
        merged, _ = result
        ccs = dict(_midi_bytes_to_ccs(merged))
        assert ccs.get(7) == 100   # ours = left = 100

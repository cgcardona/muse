"""Tests for muse/plugins/music/midi_merge.py — dimension-aware MIDI merge."""
from __future__ import annotations

import hashlib
import io
import pathlib

import mido
import pytest

from muse.core.attributes import AttributeRule
from muse.plugins.music.midi_merge import (
    INTERNAL_DIMS,
    DimensionSlice,
    MidiDimensions,
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
    tempo: int = 500_000,
    ticks_per_beat: int = 480,
) -> bytes:
    """Build a minimal type-0 MIDI file in memory.

    Args:
        notes:          List of (abs_tick, note, velocity) note-on events.
                        Each note_on is followed by a note_off 120 ticks later.
        pitchwheel:     List of (abs_tick, pitch) pitchwheel events.
        control_change: List of (abs_tick, control, value) CC events.
        tempo:          Microseconds per beat (default 120 BPM).
        ticks_per_beat: MIDI resolution.
    """
    mid = mido.MidiFile(type=0, ticks_per_beat=ticks_per_beat)
    track = mido.MidiTrack()

    # Collect all events with absolute ticks, then sort and convert to delta.
    events: list[tuple[int, mido.Message]] = []
    events.append((0, mido.MetaMessage("set_tempo", tempo=tempo, time=0)))

    for abs_tick, note, vel in notes or []:
        events.append((abs_tick, mido.Message("note_on", note=note, velocity=vel, time=0)))
        events.append((abs_tick + 120, mido.Message("note_off", note=note, velocity=0, time=0)))

    for abs_tick, pitch in pitchwheel or []:
        events.append((abs_tick, mido.Message("pitchwheel", pitch=pitch, time=0)))

    for abs_tick, ctrl, val in control_change or []:
        events.append((abs_tick, mido.Message("control_change", control=ctrl, value=val, time=0)))

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
    """Return the set of note numbers present in note_on events."""
    mid = mido.MidiFile(file=io.BytesIO(midi_bytes))
    notes: set[int] = set()
    for track in mid.tracks:
        for msg in track:
            if msg.type == "note_on" and msg.velocity > 0:
                notes.add(msg.note)
    return notes


def _midi_bytes_to_pitchwheels(midi_bytes: bytes) -> list[int]:
    """Return the list of pitchwheel values in order."""
    mid = mido.MidiFile(file=io.BytesIO(midi_bytes))
    values: list[int] = []
    for track in mid.tracks:
        for msg in track:
            if msg.type == "pitchwheel":
                values.append(msg.pitch)
    return values


def _midi_bytes_to_ccs(midi_bytes: bytes) -> list[tuple[int, int]]:
    """Return list of (control, value) pairs from CC events."""
    mid = mido.MidiFile(file=io.BytesIO(midi_bytes))
    ccs: list[tuple[int, int]] = []
    for track in mid.tracks:
        for msg in track:
            if msg.type == "control_change":
                ccs.append((msg.control, msg.value))
    return ccs


# ---------------------------------------------------------------------------
# _classify_event
# ---------------------------------------------------------------------------


class TestClassifyEvent:
    def test_note_on(self) -> None:
        assert _classify_event(mido.Message("note_on", note=60)) == "notes"

    def test_note_off(self) -> None:
        assert _classify_event(mido.Message("note_off", note=60)) == "notes"

    def test_pitchwheel(self) -> None:
        assert _classify_event(mido.Message("pitchwheel", pitch=100)) == "harmonic"

    def test_control_change(self) -> None:
        assert _classify_event(mido.Message("control_change", control=7, value=100)) == "dynamic"

    def test_set_tempo(self) -> None:
        assert _classify_event(mido.MetaMessage("set_tempo", tempo=500_000)) == "structural"

    def test_time_signature(self) -> None:
        msg = mido.MetaMessage("time_signature", numerator=4, denominator=4,
                               clocks_per_click=24, notated_32nd_notes_per_beat=8)
        assert _classify_event(msg) == "structural"

    def test_end_of_track(self) -> None:
        assert _classify_event(mido.MetaMessage("end_of_track")) == "structural"


# ---------------------------------------------------------------------------
# extract_dimensions
# ---------------------------------------------------------------------------


class TestExtractDimensions:
    def test_empty_midi_has_all_dims(self) -> None:
        midi = _make_midi()
        dims = extract_dimensions(midi)
        assert set(dims.slices.keys()) == set(INTERNAL_DIMS)

    def test_notes_in_notes_bucket(self) -> None:
        midi = _make_midi(notes=[(0, 60, 80), (480, 64, 80)])
        dims = extract_dimensions(midi)
        note_events = [msg for _, msg in dims.slices["notes"].events
                       if msg.type == "note_on"]
        assert len(note_events) == 2

    def test_pitchwheel_in_harmonic(self) -> None:
        midi = _make_midi(pitchwheel=[(100, 500), (200, -500)])
        dims = extract_dimensions(midi)
        assert len(dims.slices["harmonic"].events) == 2

    def test_cc_in_dynamic(self) -> None:
        midi = _make_midi(control_change=[(0, 7, 100)])
        dims = extract_dimensions(midi)
        assert len(dims.slices["dynamic"].events) == 1

    def test_tempo_in_structural(self) -> None:
        midi = _make_midi(tempo=600_000)
        dims = extract_dimensions(midi)
        structural_types = {msg.type for _, msg in dims.slices["structural"].events}
        assert "set_tempo" in structural_types

    def test_content_hash_is_deterministic(self) -> None:
        midi = _make_midi(notes=[(0, 60, 80)])
        d1 = extract_dimensions(midi)
        d2 = extract_dimensions(midi)
        assert d1.slices["notes"].content_hash == d2.slices["notes"].content_hash

    def test_different_notes_give_different_hash(self) -> None:
        midi_a = _make_midi(notes=[(0, 60, 80)])
        midi_b = _make_midi(notes=[(0, 62, 80)])
        da = extract_dimensions(midi_a)
        db = extract_dimensions(midi_b)
        assert da.slices["notes"].content_hash != db.slices["notes"].content_hash

    def test_same_notes_same_pitchwheel_same_hash(self) -> None:
        midi_a = _make_midi(notes=[(0, 60, 80)], pitchwheel=[(50, 200)])
        midi_b = _make_midi(notes=[(0, 60, 80)], pitchwheel=[(50, 200)])
        da = extract_dimensions(midi_a)
        db = extract_dimensions(midi_b)
        assert da.slices["notes"].content_hash == db.slices["notes"].content_hash
        assert da.slices["harmonic"].content_hash == db.slices["harmonic"].content_hash

    def test_ticks_per_beat_preserved(self) -> None:
        midi = _make_midi(ticks_per_beat=960)
        dims = extract_dimensions(midi)
        assert dims.ticks_per_beat == 960

    def test_invalid_bytes_raises(self) -> None:
        with pytest.raises(ValueError, match="Failed to parse"):
            extract_dimensions(b"not a midi file")

    def test_get_via_user_alias(self) -> None:
        midi = _make_midi(notes=[(0, 60, 80)])
        dims = extract_dimensions(midi)
        # "melodic" and "rhythmic" should both map to the "notes" bucket
        assert dims.get("melodic").name == "notes"
        assert dims.get("rhythmic").name == "notes"
        assert dims.get("harmonic").name == "harmonic"


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
            notes=notes, pitchwheel=pitchwheel, control_change=control_change, tempo=tempo
        ))

    def test_unchanged_when_all_same(self) -> None:
        base = self._dims_from(notes=[(0, 60, 80)])
        detail = dimension_conflict_detail(base, base, base)
        assert all(v == "unchanged" for v in detail.values())

    def test_left_only_change(self) -> None:
        base = self._dims_from()
        left = self._dims_from(notes=[(0, 60, 80)])
        right = self._dims_from()
        detail = dimension_conflict_detail(base, left, right)
        assert detail["notes"] == "left_only"
        assert detail["harmonic"] == "unchanged"

    def test_right_only_change(self) -> None:
        base = self._dims_from()
        left = self._dims_from()
        right = self._dims_from(pitchwheel=[(0, 100)])
        detail = dimension_conflict_detail(base, left, right)
        assert detail["harmonic"] == "right_only"

    def test_both_sides_change(self) -> None:
        base = self._dims_from()
        left = self._dims_from(notes=[(0, 60, 80)])
        right = self._dims_from(notes=[(0, 64, 80)])
        detail = dimension_conflict_detail(base, left, right)
        assert detail["notes"] == "both"

    def test_independent_dimension_changes(self) -> None:
        base = self._dims_from()
        left = self._dims_from(notes=[(0, 60, 80)])         # changed notes
        right = self._dims_from(pitchwheel=[(0, 200)])       # changed harmonic
        detail = dimension_conflict_detail(base, left, right)
        assert detail["notes"] == "left_only"
        assert detail["harmonic"] == "right_only"
        assert detail["dynamic"] == "unchanged"


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

    # --- Clean auto-merge: independent dimension changes ------------------

    def test_independent_dims_auto_merge(self) -> None:
        """Left changed notes, right changed harmonic → clean merge."""
        base = self._midi()
        left = self._midi(notes=[(0, 60, 80)])
        right = self._midi(pitchwheel=[(0, 500)])
        result = merge_midi_dimensions(base, left, right, [], "song.mid")
        assert result is not None
        merged_bytes, report = result
        assert _midi_bytes_to_notes(merged_bytes) == {60}
        assert _midi_bytes_to_pitchwheels(merged_bytes) == [500]

    def test_one_side_changed_notes(self) -> None:
        """Only left changed notes → take left automatically."""
        base = self._midi()
        left = self._midi(notes=[(0, 64, 80)])
        right = self._midi()
        result = merge_midi_dimensions(base, left, right, [], "song.mid")
        assert result is not None
        merged_bytes, _ = result
        assert _midi_bytes_to_notes(merged_bytes) == {64}

    def test_unchanged_notes_kept(self) -> None:
        """No changes on either side → preserve base."""
        base = self._midi(notes=[(0, 60, 80)])
        result = merge_midi_dimensions(base, base, base, [], "song.mid")
        assert result is not None
        merged_bytes, _ = result
        assert _midi_bytes_to_notes(merged_bytes) == {60}

    # --- File-level and dimension-level strategy override -----------------

    def test_ours_rule_on_notes_conflict(self) -> None:
        """Both sides changed notes, 'ours' rule → take left notes."""
        base = self._midi()
        left = self._midi(notes=[(0, 60, 80)])
        right = self._midi(notes=[(0, 64, 80)])
        rules = self._rules(("*", "melodic", "ours"))
        result = merge_midi_dimensions(base, left, right, rules, "song.mid")
        assert result is not None
        merged_bytes, report = result
        assert _midi_bytes_to_notes(merged_bytes) == {60}
        assert "notes" in str(report)

    def test_theirs_rule_on_notes_conflict(self) -> None:
        """Both sides changed notes, 'theirs' rule → take right notes."""
        base = self._midi()
        left = self._midi(notes=[(0, 60, 80)])
        right = self._midi(notes=[(0, 64, 80)])
        rules = self._rules(("*", "rhythmic", "theirs"))  # rhythmic maps to notes
        result = merge_midi_dimensions(base, left, right, rules, "song.mid")
        assert result is not None
        merged_bytes, _ = result
        assert _midi_bytes_to_notes(merged_bytes) == {64}

    def test_theirs_harmonic_ours_notes(self) -> None:
        """Left changed notes, right changed harmonic + notes (both), theirs harmonic."""
        base = self._midi()
        left = self._midi(notes=[(0, 60, 80)])
        right = self._midi(notes=[(0, 64, 80)], pitchwheel=[(100, 300)])
        rules = self._rules(("*", "harmonic", "theirs"), ("*", "melodic", "ours"))
        result = merge_midi_dimensions(base, left, right, rules, "song.mid")
        assert result is not None
        merged_bytes, report = result
        assert _midi_bytes_to_notes(merged_bytes) == {60}       # ours melodic
        assert _midi_bytes_to_pitchwheels(merged_bytes) == [300]  # theirs harmonic

    def test_wildcard_file_strategy_resolves_all_dims(self) -> None:
        """'* * ours' resolves every dimension to ours."""
        base = self._midi()
        left = self._midi(notes=[(0, 60, 80)], pitchwheel=[(0, 200)])
        right = self._midi(notes=[(0, 64, 80)], pitchwheel=[(0, -200)])
        rules = self._rules(("*", "*", "ours"))
        result = merge_midi_dimensions(base, left, right, rules, "song.mid")
        assert result is not None
        merged_bytes, _ = result
        assert _midi_bytes_to_notes(merged_bytes) == {60}
        assert 200 in _midi_bytes_to_pitchwheels(merged_bytes)

    def test_no_resolvable_strategy_returns_none(self) -> None:
        """Both sides changed notes, no matching rule → None (file-level conflict)."""
        base = self._midi()
        left = self._midi(notes=[(0, 60, 80)])
        right = self._midi(notes=[(0, 64, 80)])
        result = merge_midi_dimensions(base, left, right, [], "song.mid")
        assert result is None

    def test_manual_strategy_returns_none(self) -> None:
        """manual strategy → cannot auto-resolve → None."""
        base = self._midi()
        left = self._midi(notes=[(0, 60, 80)])
        right = self._midi(notes=[(0, 64, 80)])
        rules = self._rules(("*", "melodic", "manual"))
        result = merge_midi_dimensions(base, left, right, rules, "song.mid")
        assert result is None

    # --- Report content ---------------------------------------------------

    def test_report_shows_winner(self) -> None:
        base = self._midi()
        left = self._midi(notes=[(0, 60, 80)])
        right = self._midi(pitchwheel=[(0, 100)])
        result = merge_midi_dimensions(base, left, right, [], "song.mid")
        assert result is not None
        _, report = result
        assert report["notes"] == "left"
        assert report["harmonic"] == "right"

    def test_report_shows_ours_theirs_labels(self) -> None:
        base = self._midi()
        left = self._midi(notes=[(0, 60, 80)])
        right = self._midi(notes=[(0, 64, 80)])
        rules = self._rules(("*", "melodic", "ours"))
        result = merge_midi_dimensions(base, left, right, rules, "song.mid")
        assert result is not None
        _, report = result
        assert "ours" in report["notes"]

    # --- Output is valid MIDI ---------------------------------------------

    def test_merged_bytes_parseable(self) -> None:
        base = self._midi()
        left = self._midi(notes=[(0, 60, 80)])
        right = self._midi(pitchwheel=[(0, 100)])
        result = merge_midi_dimensions(base, left, right, [], "song.mid")
        assert result is not None
        merged_bytes, _ = result
        # Should be parseable without raising
        parsed = mido.MidiFile(file=io.BytesIO(merged_bytes))
        assert parsed.ticks_per_beat == 480

    def test_merged_bytes_preserve_ticks_per_beat(self) -> None:
        base = _make_midi(ticks_per_beat=960)
        left = _make_midi(notes=[(0, 60, 80)], ticks_per_beat=960)
        right = _make_midi(pitchwheel=[(0, 100)], ticks_per_beat=960)
        result = merge_midi_dimensions(base, left, right, [], "song.mid")
        assert result is not None
        merged_bytes, _ = result
        parsed = mido.MidiFile(file=io.BytesIO(merged_bytes))
        assert parsed.ticks_per_beat == 960

    # --- Path-pattern matching in rules -----------------------------------

    def test_path_specific_rule_respected(self) -> None:
        """Rule 'keys/* harmonic theirs' only applies to keys/ paths."""
        base = self._midi()
        left = self._midi(pitchwheel=[(0, 200)])
        right = self._midi(pitchwheel=[(0, -200)])
        rules = self._rules(("keys/*", "harmonic", "theirs"))

        # keys/piano.mid → rule applies
        result_keys = merge_midi_dimensions(base, left, right, rules, "keys/piano.mid")
        assert result_keys is not None
        merged_keys, _ = result_keys
        assert _midi_bytes_to_pitchwheels(merged_keys) == [-200]  # theirs

        # drums/kick.mid → rule does not apply → unresolved
        result_drums = merge_midi_dimensions(base, left, right, rules, "drums/kick.mid")
        assert result_drums is None

    # --- CC events --------------------------------------------------------

    def test_dynamic_dimension_merge(self) -> None:
        base = self._midi()
        left = self._midi(control_change=[(0, 7, 100)])   # volume up
        right = self._midi(control_change=[(0, 10, 64)])  # pan center
        # Both changed dynamic — need a rule
        rules = self._rules(("*", "dynamic", "ours"))
        result = merge_midi_dimensions(base, left, right, rules, "song.mid")
        assert result is not None
        merged_bytes, _ = result
        ccs = _midi_bytes_to_ccs(merged_bytes)
        assert (7, 100) in ccs   # ours dynamic
        assert (10, 64) not in ccs  # theirs dynamic excluded

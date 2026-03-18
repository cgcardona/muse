"""Tests for muse.plugins.music.midi_diff — Myers LCS on MIDI note sequences.

Covers:
- NoteKey extraction from MIDI bytes.
- LCS edit script correctness (keep/insert/delete).
- LCS minimality (length of keep steps == LCS length).
- diff_midi_notes() produces correct StructuredDelta.
- Content IDs are deterministic and unique per note.
- Human-readable summaries and content_summary strings.
"""
from __future__ import annotations

import io
import struct

import mido
import pytest

from muse.plugins.music.midi_diff import (
    NoteKey,
    diff_midi_notes,
    extract_notes,
    lcs_edit_script,
)


# ---------------------------------------------------------------------------
# MIDI builder helpers
# ---------------------------------------------------------------------------

def _build_midi(notes: list[tuple[int, int, int, int]]) -> bytes:
    """Build a minimal type-0 MIDI file from (pitch, velocity, start, duration) tuples.

    All values use ticks_per_beat=480. Produces valid mido-parseable MIDI bytes.
    """
    mid = mido.MidiFile(type=0, ticks_per_beat=480)
    track = mido.MidiTrack()
    mid.tracks.append(track)

    # Collect all events sorted by tick.
    events: list[tuple[int, str, int, int]] = []  # (tick, type, note, velocity)
    for pitch, velocity, start, duration in notes:
        events.append((start, "note_on", pitch, velocity))
        events.append((start + duration, "note_off", pitch, 0))

    events.sort(key=lambda e: e[0])

    prev_tick = 0
    for tick, msg_type, note, vel in events:
        delta = tick - prev_tick
        track.append(mido.Message(msg_type, note=note, velocity=vel, time=delta))
        prev_tick = tick

    track.append(mido.MetaMessage("end_of_track", time=0))

    buf = io.BytesIO()
    mid.save(file=buf)
    return buf.getvalue()


def _note(pitch: int, velocity: int = 80, start: int = 0, duration: int = 480) -> NoteKey:
    return NoteKey(
        pitch=pitch, velocity=velocity, start_tick=start,
        duration_ticks=duration, channel=0,
    )


# ---------------------------------------------------------------------------
# extract_notes
# ---------------------------------------------------------------------------

class TestExtractNotes:
    def test_empty_midi_returns_no_notes(self) -> None:
        midi_bytes = _build_midi([])
        notes, tpb = extract_notes(midi_bytes)
        assert notes == []
        assert tpb == 480

    def test_single_note_extracted(self) -> None:
        midi_bytes = _build_midi([(60, 80, 0, 480)])  # C4
        notes, tpb = extract_notes(midi_bytes)
        assert len(notes) == 1
        assert notes[0]["pitch"] == 60
        assert notes[0]["velocity"] == 80
        assert notes[0]["start_tick"] == 0
        assert notes[0]["duration_ticks"] == 480

    def test_multiple_notes_extracted(self) -> None:
        midi_bytes = _build_midi([
            (60, 80, 0, 480),
            (64, 90, 480, 480),
            (67, 70, 960, 480),
        ])
        notes, _ = extract_notes(midi_bytes)
        assert len(notes) == 3

    def test_notes_sorted_by_start_tick(self) -> None:
        midi_bytes = _build_midi([
            (67, 70, 960, 240),
            (60, 80, 0, 480),
            (64, 90, 480, 480),
        ])
        notes, _ = extract_notes(midi_bytes)
        ticks = [n["start_tick"] for n in notes]
        assert ticks == sorted(ticks)

    def test_invalid_bytes_raises_value_error(self) -> None:
        with pytest.raises(ValueError):
            extract_notes(b"not a midi file")

    def test_ticks_per_beat_is_returned(self) -> None:
        midi_bytes = _build_midi([(60, 80, 0, 480)])
        _, tpb = extract_notes(midi_bytes)
        assert tpb == 480


# ---------------------------------------------------------------------------
# lcs_edit_script
# ---------------------------------------------------------------------------

class TestLCSEditScript:
    """LCS tests use start_tick=pitch so same-pitch notes always compare equal.

    NoteKey equality requires ALL five fields to match. Using start_tick=pitch
    ensures that notes with the same pitch in base and target are considered
    identical by LCS, giving intuitive edit scripts.
    """

    def _nk(self, pitch: int) -> NoteKey:
        """Make a NoteKey where start_tick equals pitch for stable matching."""
        return NoteKey(
            pitch=pitch, velocity=80,
            start_tick=pitch,  # deterministic: same pitch → same tick → same key
            duration_ticks=480, channel=0,
        )

    def _seq(self, pitches: list[int]) -> list[NoteKey]:
        return [self._nk(p) for p in pitches]

    def test_identical_sequences_keeps_all(self) -> None:
        notes = self._seq([60, 62, 64])
        steps = lcs_edit_script(notes, notes)
        kinds = [s.kind for s in steps]
        assert kinds == ["keep", "keep", "keep"]

    def test_empty_to_sequence_all_inserts(self) -> None:
        target = self._seq([60, 62])
        steps = lcs_edit_script([], target)
        assert all(s.kind == "insert" for s in steps)
        assert len(steps) == 2

    def test_sequence_to_empty_all_deletes(self) -> None:
        base = self._seq([60, 62])
        steps = lcs_edit_script(base, [])
        assert all(s.kind == "delete" for s in steps)
        assert len(steps) == 2

    def test_single_insert_at_end(self) -> None:
        # base=[60,62], target=[60,62,64] → keep 60, keep 62, insert 64
        base = self._seq([60, 62])
        target = self._seq([60, 62, 64])
        steps = lcs_edit_script(base, target)
        keeps = [s for s in steps if s.kind == "keep"]
        inserts = [s for s in steps if s.kind == "insert"]
        assert len(keeps) == 2
        assert len(inserts) == 1
        assert inserts[0].note["pitch"] == 64

    def test_single_delete_from_middle(self) -> None:
        # base=[60,62,64], target=[60,64] → keep 60, delete 62, keep 64
        # NoteKeys with start_tick=pitch ensure 64@64 matches 64@64.
        base = self._seq([60, 62, 64])
        target = self._seq([60, 64])
        steps = lcs_edit_script(base, target)
        deletes = [s for s in steps if s.kind == "delete"]
        assert len(deletes) == 1
        assert deletes[0].note["pitch"] == 62

    def test_pitch_change_is_delete_plus_insert(self) -> None:
        # A note with a different pitch → one delete + one insert.
        base = [_note(60)]
        target = [_note(62)]
        steps = lcs_edit_script(base, target)
        kinds = {s.kind for s in steps}
        assert "delete" in kinds
        assert "insert" in kinds
        assert "keep" not in kinds

    def test_lcs_is_minimal_keeps_equal_lcs_length(self) -> None:
        # LCS of [60,62,64,65] and [60,64,65,67] is [60,64,65] (length 3)
        # because 60@60, 64@64, 65@65 all have matching counterparts in target.
        base = self._seq([60, 62, 64, 65])
        target = self._seq([60, 64, 65, 67])
        steps = lcs_edit_script(base, target)
        keeps = [s for s in steps if s.kind == "keep"]
        assert len(keeps) == 3

    def test_empty_both_returns_empty(self) -> None:
        steps = lcs_edit_script([], [])
        assert steps == []

    def test_step_indices_are_consistent(self) -> None:
        base = self._seq([60, 62, 64])
        target = self._seq([60, 64])
        steps = lcs_edit_script(base, target)
        base_indices = [s.base_index for s in steps if s.kind != "insert"]
        target_indices = [s.target_index for s in steps if s.kind != "delete"]
        assert base_indices == sorted(base_indices)
        assert target_indices == sorted(target_indices)

    def test_reorder_detected_as_delete_insert(self) -> None:
        # Swapping pitches at the same positions → notes differ → no keeps.
        # Using start_tick=0 for all to guarantee tick collision is NOT the issue;
        # the pitch mismatch is what creates the delete+insert.
        base = [NoteKey(pitch=60, velocity=80, start_tick=0, duration_ticks=480, channel=0),
                NoteKey(pitch=62, velocity=80, start_tick=480, duration_ticks=480, channel=0)]
        target = [NoteKey(pitch=62, velocity=80, start_tick=0, duration_ticks=480, channel=0),
                  NoteKey(pitch=60, velocity=80, start_tick=480, duration_ticks=480, channel=0)]
        steps = lcs_edit_script(base, target)
        keeps = [s for s in steps if s.kind == "keep"]
        # No notes match exactly (same pitch at same tick is not present in both).
        assert len(keeps) == 0


# ---------------------------------------------------------------------------
# diff_midi_notes
# ---------------------------------------------------------------------------

class TestDiffMidiNotes:
    def test_no_change_returns_empty_ops(self) -> None:
        midi_bytes = _build_midi([(60, 80, 0, 480)])
        delta = diff_midi_notes(midi_bytes, midi_bytes)
        assert delta["ops"] == []

    def test_no_change_summary(self) -> None:
        midi_bytes = _build_midi([(60, 80, 0, 480)])
        delta = diff_midi_notes(midi_bytes, midi_bytes)
        assert "no note changes" in delta["summary"]

    def test_add_note_returns_insert_op(self) -> None:
        base_bytes = _build_midi([(60, 80, 0, 480)])
        target_bytes = _build_midi([(60, 80, 0, 480), (64, 80, 480, 480)])
        delta = diff_midi_notes(base_bytes, target_bytes)
        inserts = [op for op in delta["ops"] if op["op"] == "insert"]
        assert len(inserts) == 1

    def test_remove_note_returns_delete_op(self) -> None:
        base_bytes = _build_midi([(60, 80, 0, 480), (64, 80, 480, 480)])
        target_bytes = _build_midi([(60, 80, 0, 480)])
        delta = diff_midi_notes(base_bytes, target_bytes)
        deletes = [op for op in delta["ops"] if op["op"] == "delete"]
        assert len(deletes) == 1

    def test_change_pitch_produces_delete_and_insert(self) -> None:
        base_bytes = _build_midi([(60, 80, 0, 480)])
        target_bytes = _build_midi([(62, 80, 0, 480)])
        delta = diff_midi_notes(base_bytes, target_bytes)
        kinds = {op["op"] for op in delta["ops"]}
        assert "delete" in kinds
        assert "insert" in kinds

    def test_summary_mentions_added_notes(self) -> None:
        base_bytes = _build_midi([(60, 80, 0, 480)])
        target_bytes = _build_midi([(60, 80, 0, 480), (64, 80, 480, 480)])
        delta = diff_midi_notes(base_bytes, target_bytes)
        assert "added" in delta["summary"]

    def test_summary_mentions_removed_notes(self) -> None:
        base_bytes = _build_midi([(60, 80, 0, 480), (64, 80, 480, 480)])
        target_bytes = _build_midi([(60, 80, 0, 480)])
        delta = diff_midi_notes(base_bytes, target_bytes)
        assert "removed" in delta["summary"]

    def test_summary_singular_for_one_note(self) -> None:
        base_bytes = _build_midi([])
        target_bytes = _build_midi([(60, 80, 0, 480)])
        delta = diff_midi_notes(base_bytes, target_bytes)
        assert "1 note added" in delta["summary"]

    def test_summary_plural_for_multiple_notes(self) -> None:
        base_bytes = _build_midi([])
        target_bytes = _build_midi([(60, 80, 0, 480), (64, 80, 480, 480)])
        delta = diff_midi_notes(base_bytes, target_bytes)
        assert "2 notes added" in delta["summary"]

    def test_content_id_is_deterministic(self) -> None:
        midi_bytes = _build_midi([(60, 80, 0, 480)])
        empty_bytes = _build_midi([])
        delta1 = diff_midi_notes(empty_bytes, midi_bytes)
        delta2 = diff_midi_notes(empty_bytes, midi_bytes)
        ids1 = [op["content_id"] for op in delta1["ops"]]
        ids2 = [op["content_id"] for op in delta2["ops"]]
        assert ids1 == ids2

    def test_content_ids_differ_for_different_notes(self) -> None:
        empty_bytes = _build_midi([])
        midi_c4 = _build_midi([(60, 80, 0, 480)])
        midi_d4 = _build_midi([(62, 80, 0, 480)])
        delta_c4 = diff_midi_notes(empty_bytes, midi_c4)
        delta_d4 = diff_midi_notes(empty_bytes, midi_d4)
        id_c4 = delta_c4["ops"][0]["content_id"]
        id_d4 = delta_d4["ops"][0]["content_id"]
        assert id_c4 != id_d4

    def test_content_summary_is_human_readable(self) -> None:
        empty_bytes = _build_midi([])
        target_bytes = _build_midi([(60, 80, 0, 480)])  # C4
        delta = diff_midi_notes(empty_bytes, target_bytes)
        summary = delta["ops"][0]["content_summary"]
        assert "C4" in summary
        assert "vel=80" in summary

    def test_domain_is_midi_notes(self) -> None:
        midi_bytes = _build_midi([(60, 80, 0, 480)])
        empty_bytes = _build_midi([])
        delta = diff_midi_notes(empty_bytes, midi_bytes)
        assert delta["domain"] == "midi_notes"

    def test_invalid_base_raises_value_error(self) -> None:
        valid = _build_midi([(60, 80, 0, 480)])
        with pytest.raises(ValueError):
            diff_midi_notes(b"garbage", valid)

    def test_invalid_target_raises_value_error(self) -> None:
        valid = _build_midi([(60, 80, 0, 480)])
        with pytest.raises(ValueError):
            diff_midi_notes(valid, b"garbage")

    def test_file_path_appears_in_content_summary_context(self) -> None:
        # file_path is used only for logging; no crash expected.
        base_bytes = _build_midi([])
        target_bytes = _build_midi([(60, 80, 0, 480)])
        delta = diff_midi_notes(
            base_bytes, target_bytes, file_path="tracks/piano.mid"
        )
        assert len(delta["ops"]) == 1

    def test_position_reflects_sequence_index(self) -> None:
        empty = _build_midi([])
        two_notes = _build_midi([(60, 80, 0, 480), (64, 80, 480, 480)])
        delta = diff_midi_notes(empty, two_notes)
        positions = [op["position"] for op in delta["ops"]]
        assert 0 in positions
        assert 1 in positions

"""Tests for muse.plugins.music._crdt_notes — NotePosition, RGANoteEntry, MusicRGA.

Verifies all three CRDT laws:
  1. Commutativity: merge(a, b) == merge(b, a)
  2. Associativity: merge(merge(a, b), c) == merge(a, merge(b, c))
  3. Idempotency:   merge(a, a) == a
"""
from __future__ import annotations

import pytest

from muse.plugins.music._crdt_notes import (
    MusicRGA,
    NotePosition,
    RGANoteEntry,
    _pitch_to_voice_lane,
)
from muse.plugins.music.midi_diff import NoteKey


def _key(pitch: int = 60, velocity: int = 80, start_tick: int = 0,
         duration_ticks: int = 480, channel: int = 0) -> NoteKey:
    return NoteKey(pitch=pitch, velocity=velocity, start_tick=start_tick,
                   duration_ticks=duration_ticks, channel=channel)


# ---------------------------------------------------------------------------
# NotePosition ordering
# ---------------------------------------------------------------------------


class TestNotePosition:
    def test_ordered_by_measure_first(self) -> None:
        p1 = NotePosition(measure=1, beat_sub=100, voice_lane=3, op_id="zzz")
        p2 = NotePosition(measure=2, beat_sub=0, voice_lane=0, op_id="aaa")
        assert p1 < p2

    def test_ordered_by_beat_sub_within_measure(self) -> None:
        p1 = NotePosition(measure=1, beat_sub=100, voice_lane=3, op_id="zzz")
        p2 = NotePosition(measure=1, beat_sub=200, voice_lane=0, op_id="aaa")
        assert p1 < p2

    def test_ordered_by_voice_lane_at_same_beat(self) -> None:
        # Bass (lane 0) should come before soprano (lane 3).
        p_bass = NotePosition(measure=1, beat_sub=0, voice_lane=0, op_id="zzz")
        p_soprano = NotePosition(measure=1, beat_sub=0, voice_lane=3, op_id="aaa")
        assert p_bass < p_soprano

    def test_tie_broken_by_op_id(self) -> None:
        p1 = NotePosition(measure=1, beat_sub=0, voice_lane=0, op_id="aaa")
        p2 = NotePosition(measure=1, beat_sub=0, voice_lane=0, op_id="bbb")
        assert p1 < p2


# ---------------------------------------------------------------------------
# _pitch_to_voice_lane
# ---------------------------------------------------------------------------


class TestPitchToVoiceLane:
    def test_bass_range(self) -> None:
        assert _pitch_to_voice_lane(24) == 0
        assert _pitch_to_voice_lane(47) == 0

    def test_tenor_range(self) -> None:
        assert _pitch_to_voice_lane(48) == 1
        assert _pitch_to_voice_lane(59) == 1

    def test_alto_range(self) -> None:
        assert _pitch_to_voice_lane(60) == 2
        assert _pitch_to_voice_lane(71) == 2

    def test_soprano_range(self) -> None:
        assert _pitch_to_voice_lane(72) == 3
        assert _pitch_to_voice_lane(108) == 3


# ---------------------------------------------------------------------------
# MusicRGA — basic insert / delete
# ---------------------------------------------------------------------------


class TestMusicRGAInsertDelete:
    def test_single_insert_visible(self) -> None:
        seq = MusicRGA("agent-a")
        seq.insert(_key(60))
        notes = seq.to_sequence()
        assert len(notes) == 1
        assert notes[0]["pitch"] == 60

    def test_multiple_inserts_ordered_by_position(self) -> None:
        seq = MusicRGA("agent-a")
        # Insert soprano (pitch 72 = lane 3) before bass (pitch 36 = lane 0)
        # at the same beat — bass should appear first in output.
        seq.insert(_key(pitch=72, start_tick=0))
        seq.insert(_key(pitch=36, start_tick=0))
        notes = seq.to_sequence()
        assert notes[0]["pitch"] == 36   # bass first
        assert notes[1]["pitch"] == 72   # soprano second

    def test_delete_removes_note(self) -> None:
        seq = MusicRGA("agent-a")
        entry = seq.insert(_key(60))
        seq.delete(entry["op_id"])
        assert seq.to_sequence() == []

    def test_delete_nonexistent_raises(self) -> None:
        seq = MusicRGA("agent-a")
        with pytest.raises(KeyError):
            seq.delete("nonexistent-op-id")

    def test_tombstoned_entries_counted(self) -> None:
        seq = MusicRGA("agent-a")
        e = seq.insert(_key(60))
        seq.delete(e["op_id"])
        assert seq.entry_count() == 1
        assert seq.live_count() == 0


# ---------------------------------------------------------------------------
# CRDT merge — commutativity, associativity, idempotency
# ---------------------------------------------------------------------------


class TestMusicRGACRDTLaws:
    def _make_replicas(self) -> tuple[MusicRGA, MusicRGA, MusicRGA]:
        a = MusicRGA("agent-a")
        b = MusicRGA("agent-b")
        c = MusicRGA("agent-c")

        a.insert(_key(60, start_tick=0))
        a.insert(_key(64, start_tick=480))

        b.insert(_key(67, start_tick=0))
        b.insert(_key(71, start_tick=480))

        c.insert(_key(72, start_tick=0))

        # Propagate a's ops to b and c (simulating gossip).
        for entry in list(a._entries.values()):
            b._entries.setdefault(entry["op_id"], entry)
            c._entries.setdefault(entry["op_id"], entry)

        return a, b, c

    def test_commutativity(self) -> None:
        a, b, _ = self._make_replicas()
        ab = MusicRGA.merge(a, b)
        ba = MusicRGA.merge(b, a)
        assert ab.to_sequence() == ba.to_sequence()

    def test_associativity(self) -> None:
        a, b, c = self._make_replicas()
        ab_c = MusicRGA.merge(MusicRGA.merge(a, b), c)
        a_bc = MusicRGA.merge(a, MusicRGA.merge(b, c))
        assert ab_c.to_sequence() == a_bc.to_sequence()

    def test_idempotency(self) -> None:
        a, _, _ = self._make_replicas()
        aa = MusicRGA.merge(a, a)
        assert aa.to_sequence() == a.to_sequence()

    def test_merge_contains_all_inserts(self) -> None:
        a = MusicRGA("agent-a")
        b = MusicRGA("agent-b")
        for i in range(5):
            a.insert(_key(60 + i, start_tick=i * 480))
        for i in range(5):
            b.insert(_key(72 + i, start_tick=i * 480))
        merged = MusicRGA.merge(a, b)
        assert merged.live_count() == 10

    def test_tombstone_wins_in_merge(self) -> None:
        a = MusicRGA("agent-a")
        b = MusicRGA("agent-b")

        entry = a.insert(_key(60))
        # Share the insert with b.
        b._entries[entry["op_id"]] = entry
        # b deletes the shared note; a does not.
        b.delete(entry["op_id"])

        merged = MusicRGA.merge(a, b)
        # Tombstone wins — note should be absent in merged result.
        assert merged.live_count() == 0


# ---------------------------------------------------------------------------
# to_domain_ops
# ---------------------------------------------------------------------------


class TestToDomainOps:
    def test_empty_base_and_live_produces_no_ops(self) -> None:
        seq = MusicRGA("agent-a")
        ops = seq.to_domain_ops([])
        assert ops == []

    def test_added_notes_produce_insert_ops(self) -> None:
        seq = MusicRGA("agent-a")
        seq.insert(_key(60))
        seq.insert(_key(64))
        ops = seq.to_domain_ops([])
        assert len(ops) == 2
        assert all(o["op"] == "insert" for o in ops)

    def test_removed_notes_produce_delete_ops(self) -> None:
        base_notes = [_key(60), _key(64)]
        seq = MusicRGA("agent-a")
        # Add only the first note back.
        seq.insert(_key(60))
        ops = seq.to_domain_ops(base_notes)
        op_types = [o["op"] for o in ops]
        assert "delete" in op_types

    def test_unchanged_notes_produce_no_ops(self) -> None:
        note = _key(60)
        seq = MusicRGA("agent-a")
        seq.insert(note)
        ops = seq.to_domain_ops([note])
        assert ops == []

    def test_voice_ordering_preserved_in_sequence(self) -> None:
        seq = MusicRGA("agent-a")
        # Insert in reverse voice order; output should be ordered bass→soprano.
        seq.insert(_key(pitch=84, start_tick=0))  # soprano
        seq.insert(_key(pitch=60, start_tick=0))  # alto
        seq.insert(_key(pitch=48, start_tick=0))  # tenor
        seq.insert(_key(pitch=36, start_tick=0))  # bass
        notes = seq.to_sequence()
        pitches = [n["pitch"] for n in notes]
        assert pitches == sorted(pitches)  # bass < tenor < alto < soprano

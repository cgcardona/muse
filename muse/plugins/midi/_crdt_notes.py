"""Voice-aware Music RGA — experimental CRDT for live MIDI note sequences.

This module is a **research prototype** for the live collaboration foundation
described in the Muse supercharge plan.  It is NOT wired into the production
merge path.  Its purpose is to:

1. Demonstrate that concurrent note insertions can be made commutative.
2. Provide a benchmark harness for comparing voice-aware RGA against LSEQ.
3. Serve as the implementation foundation for the eventual live collaboration
   layer (Workstream 7 / 8 in the plan).

Design
------
Standard RGA (Roh 2011) orders concurrent insertions at the same position
lexicographically by op_id.  For music this produces unacceptable results:
two agents inserting bass and soprano notes at the same beat would interleave
their pitches arbitrarily, producing nonsense voice crossings.

**Music-RGA** uses a multi-key position ordering:

    NotePosition = (measure, beat_sub, voice_lane, op_id)

Concurrent insertions at the same ``(measure, beat_sub)`` are ordered by
``voice_lane`` first (bass=0 < tenor=1 < alto=2 < soprano=3), then by
``op_id`` as a tie-break.  This guarantees that bass notes always precede
treble notes in the materialised sequence regardless of insertion order.

Voice lane assignment
---------------------
Voice lane is determined from the note's pitch at insert time using a
coarse tessiture model:

    pitch < 48   → 0 (bass)
    48 ≤ pitch < 60 → 1 (tenor)
    60 ≤ pitch < 72 → 2 (alto)
    pitch ≥ 72   → 3 (soprano)

Agents that perform explicit voice separation can override ``voice_lane``
when calling :meth:`MusicRGA.insert`.

CRDT properties
---------------
The three lattice laws are demonstrated by :func:`_verify_crdt_laws` in the
test suite:

1. **Commutativity**: ``merge(a, b).to_sequence() == merge(b, a).to_sequence()``
2. **Associativity**: ``merge(merge(a, b), c) == merge(a, merge(b, c))``
3. **Idempotency**: ``merge(a, a).to_sequence() == a.to_sequence()``

Relationship to the commit DAG
-------------------------------
A live session accumulates :class:`RGANoteEntry` operations.  At commit time,
:meth:`MusicRGA.to_domain_ops` translates the CRDT state into canonical Muse
:class:`~muse.domain.DomainOp` entries for storage in the commit record.
The CRDT state itself is ephemeral — not stored in the object store.

Public API
----------
- :class:`NotePosition`  — music-aware position key (NamedTuple).
- :class:`RGANoteEntry`  — one element in the RGA (TypedDict).
- :class:`MusicRGA`      — the voice-aware ordered note sequence CRDT.
"""
from __future__ import annotations

import logging
import uuid as _uuid_mod
from typing import NamedTuple, TypedDict

from muse.domain import DeleteOp, DomainOp, InsertOp
from muse.plugins.midi.midi_diff import NoteKey, _note_content_id, _note_summary

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Music-aware position key
# ---------------------------------------------------------------------------


class NotePosition(NamedTuple):
    """Multi-key position for voice-aware ordering in the Music RGA.

    Fields are ordered by comparison priority:

    ``measure``
        1-indexed bar number.  Notes in earlier bars always precede notes
        in later bars.
    ``beat_sub``
        Tick offset within the bar.  Lower onset first within the same bar.
    ``voice_lane``
        Voice stream: 0=bass, 1=tenor, 2=alto, 3=soprano, 4+=auxiliary.
        At the same ``(measure, beat_sub)``, lower voice lane wins — bass
        notes are placed before treble notes, preventing voice crossings.
    ``op_id``
        UUID4 op identifier.  Lexicographic tie-break for concurrent
        insertions by different actors in the same voice at the same beat.
    """

    measure: int
    beat_sub: int
    voice_lane: int
    op_id: str


def _pitch_to_voice_lane(pitch: int) -> int:
    """Map a MIDI pitch to a coarse voice lane index.

    Tessiture boundaries (MIDI pitch → voice):
    - 0–47   → 0 (bass, sub-bass)
    - 48–59  → 1 (tenor, baritone)
    - 60–71  → 2 (alto, mezzo)
    - 72–127 → 3 (soprano, treble)
    """
    if pitch < 48:
        return 0
    if pitch < 60:
        return 1
    if pitch < 72:
        return 2
    return 3


# ---------------------------------------------------------------------------
# RGA entry
# ---------------------------------------------------------------------------


class RGANoteEntry(TypedDict):
    """One element in the :class:`MusicRGA` linked list.

    ``op_id``       Unique insertion operation ID (UUID4).
    ``actor_id``    The agent or human that performed this insertion.
    ``note``        The MIDI note content.
    ``position``    Music-aware position key for ordering.
    ``parent_op_id`` The ``op_id`` of the element this was inserted after
                    (``None`` for head insertions).
    ``tombstone``   ``True`` when this note has been deleted (standard RGA
                    tombstone semantics — the entry is retained so that its
                    position remains stable for other replicas).
    """

    op_id: str
    actor_id: str
    note: NoteKey
    position: NotePosition
    parent_op_id: str | None
    tombstone: bool


# ---------------------------------------------------------------------------
# MusicRGA
# ---------------------------------------------------------------------------


class MusicRGA:
    """Voice-aware Replicated Growable Array for live MIDI note sequences.

    Implements the standard RGA CRDT (Roh et al., 2011) with a music-aware
    position key (:class:`NotePosition`) that orders concurrent insertions by
    voice lane before falling back to op_id, preventing voice crossings in
    concurrent collaborative edits.

    Usage::

        seq = MusicRGA("agent-1")
        e1 = seq.insert(bass_note)
        e2 = seq.insert(soprano_note)
        seq.delete(e1["op_id"])

        # On another replica:
        seq2 = MusicRGA("agent-2")
        e3 = seq2.insert(tenor_note)

        merged = MusicRGA.merge(seq, seq2)
        notes = merged.to_sequence()  # deterministic, voice-ordered

    Args:
        actor_id: Stable identifier for the agent or human using this replica.
    """

    def __init__(self, actor_id: str) -> None:
        self._actor_id = actor_id
        self._entries: dict[str, RGANoteEntry] = {}  # op_id → entry

    # ------------------------------------------------------------------
    # Insertion
    # ------------------------------------------------------------------

    def insert(
        self,
        note: NoteKey,
        *,
        after: str | None = None,
        voice_lane: int | None = None,
        ticks_per_beat: int = 480,
        time_sig_numerator: int = 4,
    ) -> RGANoteEntry:
        """Insert *note* into the sequence, optionally after entry *after*.

        Args:
            note:                The MIDI note to insert.
            after:               ``op_id`` of the entry to insert after.
                                 ``None`` inserts at the head.
            voice_lane:          Override the automatic tessiture assignment.
            ticks_per_beat:      Used to compute measure and beat_sub.
            time_sig_numerator:  Beats per bar (default 4 for 4/4 time).

        Returns:
            The created :class:`RGANoteEntry`.
        """
        op_id = str(_uuid_mod.uuid4())

        ticks_per_bar = ticks_per_beat * time_sig_numerator
        measure = note["start_tick"] // ticks_per_bar + 1
        beat_sub = note["start_tick"] % ticks_per_bar
        lane = voice_lane if voice_lane is not None else _pitch_to_voice_lane(note["pitch"])

        position = NotePosition(
            measure=measure,
            beat_sub=beat_sub,
            voice_lane=lane,
            op_id=op_id,
        )

        entry: RGANoteEntry = RGANoteEntry(
            op_id=op_id,
            actor_id=self._actor_id,
            note=note,
            position=position,
            parent_op_id=after,
            tombstone=False,
        )
        self._entries[op_id] = entry
        logger.debug(
            "MusicRGA insert: actor=%r pitch=%d measure=%d voice=%d op=%s",
            self._actor_id,
            note["pitch"],
            measure,
            lane,
            op_id[:8],
        )
        return entry

    # ------------------------------------------------------------------
    # Deletion
    # ------------------------------------------------------------------

    def delete(self, op_id: str) -> None:
        """Mark the entry with *op_id* as tombstoned.

        The entry remains in the internal map so that its position continues
        to anchor other entries that were inserted after it.

        Args:
            op_id: The op_id of the entry to delete.

        Raises:
            KeyError: When *op_id* is not found in this replica.
        """
        if op_id not in self._entries:
            raise KeyError(f"op_id {op_id!r} not found in MusicRGA")
        entry = self._entries[op_id]
        self._entries[op_id] = RGANoteEntry(
            op_id=entry["op_id"],
            actor_id=entry["actor_id"],
            note=entry["note"],
            position=entry["position"],
            parent_op_id=entry["parent_op_id"],
            tombstone=True,
        )

    # ------------------------------------------------------------------
    # Materialisation
    # ------------------------------------------------------------------

    def to_sequence(self) -> list[NoteKey]:
        """Materialise the live note sequence (excluding tombstones).

        Entries are sorted by their :class:`NotePosition` key:
        ``(measure, beat_sub, voice_lane, op_id)``.  This guarantees a
        deterministic, voice-coherent ordering regardless of insertion order
        across replicas.

        Returns:
            Sorted list of live (non-tombstoned) :class:`NoteKey` objects.
        """
        live = [e for e in self._entries.values() if not e["tombstone"]]
        live.sort(key=lambda e: e["position"])
        return [e["note"] for e in live]

    def entry_count(self) -> int:
        """Return the total number of entries including tombstones."""
        return len(self._entries)

    def live_count(self) -> int:
        """Return the number of non-tombstoned (visible) entries."""
        return sum(1 for e in self._entries.values() if not e["tombstone"])

    # ------------------------------------------------------------------
    # CRDT merge — commutative, associative, idempotent
    # ------------------------------------------------------------------

    @staticmethod
    def merge(a: "MusicRGA", b: "MusicRGA") -> "MusicRGA":
        """Return a new MusicRGA that is the join of replicas *a* and *b*.

        The join is:
        - **Commutative**: ``merge(a, b).to_sequence() == merge(b, a).to_sequence()``
        - **Associative**: ``merge(merge(a, b), c) == merge(a, merge(b, c))``
        - **Idempotent**: ``merge(a, a).to_sequence() == a.to_sequence()``

        For entries present in both replicas, deletion wins (tombstone=True
        takes priority over tombstone=False).  This is the standard OR-Set
        / RGA semantics for concurrent delete-and-insert.

        Args:
            a: First replica.
            b: Second replica.

        Returns:
            A new :class:`MusicRGA` containing the union of all entries from
            both replicas with tombstone-wins conflict resolution.
        """
        merged = MusicRGA(actor_id=f"merge({a._actor_id},{b._actor_id})")

        all_op_ids = set(a._entries) | set(b._entries)
        for op_id in all_op_ids:
            entry_a = a._entries.get(op_id)
            entry_b = b._entries.get(op_id)

            if entry_a is not None and entry_b is not None:
                # Tombstone wins — if either replica deleted this entry, it
                # is considered deleted in the merged result.
                tombstone = entry_a["tombstone"] or entry_b["tombstone"]
                merged._entries[op_id] = RGANoteEntry(
                    op_id=entry_a["op_id"],
                    actor_id=entry_a["actor_id"],
                    note=entry_a["note"],
                    position=entry_a["position"],
                    parent_op_id=entry_a["parent_op_id"],
                    tombstone=tombstone,
                )
            elif entry_a is not None:
                merged._entries[op_id] = entry_a
            else:
                assert entry_b is not None
                merged._entries[op_id] = entry_b

        return merged

    # ------------------------------------------------------------------
    # Conversion to Muse DomainOps
    # ------------------------------------------------------------------

    def to_domain_ops(
        self,
        base_sequence: list[NoteKey],
        ticks_per_beat: int = 480,
    ) -> list[DomainOp]:
        """Convert this CRDT state to Muse DomainOps relative to a base sequence.

        Used at commit time to crystallise a live session's CRDT state into
        the canonical Muse typed delta algebra for storage in the commit record.

        The conversion computes:
        - ``InsertOp`` for notes present in the live sequence but not in base.
        - ``DeleteOp`` for notes present in base but not in the live sequence.

        Args:
            base_sequence: The committed note list at the start of the session.
            ticks_per_beat: Used for human-readable summaries.

        Returns:
            List of :class:`~muse.domain.DomainOp` entries.
        """
        live = self.to_sequence()
        base_content_ids = {_note_content_id(n) for n in base_sequence}
        live_content_ids = {_note_content_id(n) for n in live}

        ops: list[DomainOp] = []

        for i, note in enumerate(live):
            cid = _note_content_id(note)
            if cid not in base_content_ids:
                ops.append(
                    InsertOp(
                        op="insert",
                        address=f"note:{i}",
                        position=i,
                        content_id=cid,
                        content_summary=_note_summary(note, ticks_per_beat),
                    )
                )

        for i, note in enumerate(base_sequence):
            cid = _note_content_id(note)
            if cid not in live_content_ids:
                ops.append(
                    DeleteOp(
                        op="delete",
                        address=f"note:{i}",
                        position=i,
                        content_id=cid,
                        content_summary=_note_summary(note, ticks_per_beat),
                    )
                )

        return ops

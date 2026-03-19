"""MIDI note-level diff for the Muse MIDI plugin.

Produces a ``StructuredDelta`` with note-level ``InsertOp`` and ``DeleteOp``
entries from two MIDI byte strings. This is what lets ``muse show`` display
"C4 added at beat 3.5" rather than "tracks/drums.mid modified".

Algorithm
---------
1. Parse MIDI bytes and extract paired note events (note_on + note_off)
   sorted by start tick.
2. Represent each note as a ``NoteKey`` TypedDict with five fields.
3. Convert each ``NoteKey`` to its deterministic content ID (SHA-256 of the
   five fields).
4. Delegate to :func:`~muse.core.diff_algorithms.lcs.myers_ses` — the shared
   LCS implementation from the diff algorithm library — for the SES.
5. Map edit steps to typed ``DomainOp`` instances using the note's content
   ID and a human-readable summary string.
6. Wrap the ops in a ``StructuredDelta``.

Additional features
-----------------
:func:`reconstruct_midi` — the inverse of :func:`extract_notes`. Given a list
of :class:`NoteKey` objects and a ticks_per_beat value, produces raw MIDI bytes
for a Type 0 single-track file. Used by ``MidiPlugin.merge_ops()`` to
materialise a merged MIDI file after the OT engine has determined that
two branches' note-level operations commute.

Public API
----------
- :class:`NoteKey` — typed MIDI note identity.
- :func:`extract_notes` — MIDI bytes → sorted ``list[NoteKey]``.
- :func:`reconstruct_midi` — ``list[NoteKey]`` → MIDI bytes.
- :func:`diff_midi_notes` — top-level: MIDI bytes × 2 → ``StructuredDelta``.
"""

import hashlib
import io
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, TypedDict

if TYPE_CHECKING:
    from muse.plugins.midi.entity import EntityIndex

import mido

from muse.core.diff_algorithms.lcs import myers_ses
from muse.domain import (
    DeleteOp,
    DomainOp,
    InsertOp,
    StructuredDelta,
)

logger = logging.getLogger(__name__)

#: Identifies the sub-domain for note-level operations inside a PatchOp.
_CHILD_DOMAIN = "midi_notes"

_PITCH_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


# ---------------------------------------------------------------------------
# NoteKey — the unit of LCS comparison
# ---------------------------------------------------------------------------


class NoteKey(TypedDict):
    """Fully-specified MIDI note used as the LCS comparison unit.

    Two notes are considered identical in LCS iff all five fields match.
    A pitch change, velocity change, timing shift, or channel change
    counts as a delete of the old note and an insert of the new one.
    This is conservative but correct — it means the LCS finds true
    structural matches and surfaces real musical changes.
    """

    pitch: int
    velocity: int
    start_tick: int
    duration_ticks: int
    channel: int


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pitch_name(midi_pitch: int) -> str:
    """Return a human-readable pitch string, e.g. ``"C4"``, ``"F#5"``."""
    octave = midi_pitch // 12 - 1
    name = _PITCH_NAMES[midi_pitch % 12]
    return f"{name}{octave}"


def _note_content_id(note: NoteKey) -> str:
    """Return a deterministic SHA-256 for a note's five identity fields.

    This gives a stable ``content_id`` for use in ``InsertOp`` / ``DeleteOp``
    without requiring the note to be stored as a separate blob in the object
    store. The hash uniquely identifies "this specific note event".
    """
    payload = (
        f"{note['pitch']}:{note['velocity']}:"
        f"{note['start_tick']}:{note['duration_ticks']}:{note['channel']}"
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _note_summary(note: NoteKey, ticks_per_beat: int) -> str:
    """Return a human-readable one-liner for a note, e.g. ``"C4 vel=80 @beat=1.00"``."""
    beat = note["start_tick"] / max(ticks_per_beat, 1)
    dur = note["duration_ticks"] / max(ticks_per_beat, 1)
    return (
        f"{_pitch_name(note['pitch'])} "
        f"vel={note['velocity']} "
        f"@beat={beat:.2f} "
        f"dur={dur:.2f}"
    )


# ---------------------------------------------------------------------------
# Note extraction
# ---------------------------------------------------------------------------


def extract_notes(midi_bytes: bytes) -> tuple[list[NoteKey], int]:
    """Parse *midi_bytes* and return ``(notes, ticks_per_beat)``.

    Notes are paired note_on / note_off events. A note_on with velocity=0
    is treated as note_off. Notes are sorted by start_tick then pitch for
    deterministic ordering.

    Args:
        midi_bytes: Raw bytes of a ``.mid`` file.

    Returns:
        A tuple of (sorted NoteKey list, ticks_per_beat integer).

    Raises:
        ValueError: When *midi_bytes* cannot be parsed as a MIDI file.
    """
    try:
        mid = mido.MidiFile(file=io.BytesIO(midi_bytes))
    except Exception as exc:
        raise ValueError(f"Cannot parse MIDI bytes: {exc}") from exc

    ticks_per_beat: int = int(mid.ticks_per_beat)
    # (channel, pitch) → (start_tick, velocity)
    active: dict[tuple[int, int], tuple[int, int]] = {}
    notes: list[NoteKey] = []

    for track in mid.tracks:
        abs_tick = 0
        for msg in track:
            abs_tick += msg.time
            if msg.type == "note_on" and msg.velocity > 0:
                active[(msg.channel, msg.note)] = (abs_tick, msg.velocity)
            elif msg.type == "note_off" or (
                msg.type == "note_on" and msg.velocity == 0
            ):
                key = (msg.channel, msg.note)
                if key in active:
                    start, vel = active.pop(key)
                    notes.append(
                        NoteKey(
                            pitch=msg.note,
                            velocity=vel,
                            start_tick=start,
                            duration_ticks=max(abs_tick - start, 1),
                            channel=msg.channel,
                        )
                    )

    # Close any notes still open at end of file with duration 1.
    for (ch, pitch), (start, vel) in active.items():
        notes.append(
            NoteKey(
                pitch=pitch,
                velocity=vel,
                start_tick=start,
                duration_ticks=1,
                channel=ch,
            )
        )

    notes.sort(key=lambda n: (n["start_tick"], n["pitch"], n["channel"]))
    return notes, ticks_per_beat


# ---------------------------------------------------------------------------
# NoteKey-level edit script — adapter over the core LCS
# ---------------------------------------------------------------------------

EditKind = Literal["keep", "insert", "delete"]


@dataclass(frozen=True)
class EditStep:
    """One step in the note-level edit script produced by :func:`lcs_edit_script`."""

    kind: EditKind
    base_index: int
    target_index: int
    note: NoteKey


def lcs_edit_script(
    base: list[NoteKey],
    target: list[NoteKey],
) -> list[EditStep]:
    """Compute the shortest edit script transforming *base* into *target*.

    Converts each ``NoteKey`` to its content ID, delegates to
    :func:`~muse.core.diff_algorithms.lcs.myers_ses` for the SES, then maps
    the result back to :class:`EditStep` entries carrying the original
    ``NoteKey`` values.

    Two notes are matched iff all five ``NoteKey`` fields are equal. This is
    correct: a pitch change, velocity change, or timing shift is a delete of
    the old note and an insert of the new one.

    Args:
        base:   The base (ancestor) note sequence.
        target: The target (newer) note sequence.

    Returns:
        A list of :class:`EditStep` entries (keep / insert / delete).
    """
    base_ids = [_note_content_id(n) for n in base]
    target_ids = [_note_content_id(n) for n in target]
    raw_steps = myers_ses(base_ids, target_ids)

    result: list[EditStep] = []
    for step in raw_steps:
        if step.kind == "keep":
            result.append(EditStep("keep", step.base_index, step.target_index, base[step.base_index]))
        elif step.kind == "insert":
            result.append(EditStep("insert", step.base_index, step.target_index, target[step.target_index]))
        else:
            result.append(EditStep("delete", step.base_index, step.target_index, base[step.base_index]))
    return result


# ---------------------------------------------------------------------------
# Public diff entry point
# ---------------------------------------------------------------------------


def diff_midi_notes(
    base_bytes: bytes,
    target_bytes: bytes,
    *,
    file_path: str = "",
) -> StructuredDelta:
    """Compute a note-level ``StructuredDelta`` between two MIDI files.

    Parses both files, converts each note to its content ID, delegates to the
    core :func:`~muse.core.diff_algorithms.lcs.myers_ses` for the SES, then
    maps the edit steps to typed ``DomainOp`` instances.

    Args:
        base_bytes:   Raw bytes of the base (ancestor) MIDI file.
        target_bytes: Raw bytes of the target (newer) MIDI file.
        file_path:    Workspace-relative path of the file being diffed (used
                      only in log messages and ``content_summary`` strings).

    Returns:
        A ``StructuredDelta`` with ``InsertOp`` and ``DeleteOp`` entries for
        each note added or removed. The ``summary`` field is human-readable,
        e.g. ``"3 notes added, 1 note removed"``.

    Raises:
        ValueError: When either byte string cannot be parsed as MIDI.
    """
    base_notes, base_tpb = extract_notes(base_bytes)
    target_notes, target_tpb = extract_notes(target_bytes)
    tpb = base_tpb  # use base ticks_per_beat for human-readable summaries

    # Convert NoteKey → content ID, then delegate LCS to the core algorithm.
    base_ids = [_note_content_id(n) for n in base_notes]
    target_ids = [_note_content_id(n) for n in target_notes]
    steps = myers_ses(base_ids, target_ids)

    # Build a content-ID → NoteKey lookup so we can produce rich summaries.
    base_by_id = {_note_content_id(n): n for n in base_notes}
    target_by_id = {_note_content_id(n): n for n in target_notes}

    child_ops: list[DomainOp] = []
    inserts = 0
    deletes = 0

    for step in steps:
        if step.kind == "insert":
            note = target_by_id.get(step.item)
            summary = _note_summary(note, tpb) if note else step.item[:12]
            child_ops.append(
                InsertOp(
                    op="insert",
                    address=f"note:{step.target_index}",
                    position=step.target_index,
                    content_id=step.item,
                    content_summary=summary,
                )
            )
            inserts += 1
        elif step.kind == "delete":
            note = base_by_id.get(step.item)
            summary = _note_summary(note, tpb) if note else step.item[:12]
            child_ops.append(
                DeleteOp(
                    op="delete",
                    address=f"note:{step.base_index}",
                    position=step.base_index,
                    content_id=step.item,
                    content_summary=summary,
                )
            )
            deletes += 1
        # "keep" steps produce no ops — the note is unchanged.

    parts: list[str] = []
    if inserts:
        parts.append(f"{inserts} note{'s' if inserts != 1 else ''} added")
    if deletes:
        parts.append(f"{deletes} note{'s' if deletes != 1 else ''} removed")
    child_summary = ", ".join(parts) if parts else "no note changes"

    logger.debug(
        "✅ MIDI diff %r: +%d -%d notes (%d SES steps)",
        file_path,
        inserts,
        deletes,
        len(steps),
    )

    return StructuredDelta(
        domain=_CHILD_DOMAIN,
        ops=child_ops,
        summary=child_summary,
    )


# ---------------------------------------------------------------------------
# Entity-aware diff — wrapper that produces MutateOp for field-level mutations
# ---------------------------------------------------------------------------


def diff_midi_notes_with_entities(
    base_bytes: bytes,
    target_bytes: bytes,
    *,
    prior_index: "EntityIndex | None" = None,
    commit_id: str = "",
    op_id: str = "",
    file_path: str = "",
    mutation_threshold_ticks: int = 10,
    mutation_threshold_velocity: int = 20,
) -> StructuredDelta:
    """Compute a note-level ``StructuredDelta`` with stable entity identity.

    Unlike :func:`diff_midi_notes` which maps every field-level change to a
    ``DeleteOp + InsertOp`` pair, this function uses the entity index from the
    parent commit to detect *mutations* — notes that are logically the same
    entity with changed properties — and emits ``MutateOp`` entries for them.

    When ``prior_index`` is ``None`` or entity tracking is unavailable for a
    note, this function falls back to the content-hash-only diff for that note
    (same semantics as :func:`diff_midi_notes`).

    The returned ``StructuredDelta`` also includes updated entity tracking
    metadata in the ``domain`` field tag so consumers know which delta type
    they are receiving.

    Args:
        base_bytes:   Raw bytes of the base (ancestor) MIDI file.
        target_bytes: Raw bytes of the target (newer) MIDI file.
        prior_index:  Entity index from the parent commit for *file_path*.
                      ``None`` for first-commit or untracked tracks.
        commit_id:    Current commit ID for provenance metadata.
        op_id:        Op log entry ID that produced this diff.
        file_path:    Workspace-relative path for log messages.
        mutation_threshold_ticks:    Max |Δtick| for fuzzy entity matching.
        mutation_threshold_velocity: Max |Δvelocity| for fuzzy entity matching.

    Returns:
        A ``StructuredDelta`` with ``InsertOp``, ``DeleteOp``, and ``MutateOp``
        entries.  Domain tag is ``"midi_notes_tracked"`` to distinguish from
        the plain content-hash diff.

    Raises:
        ValueError: When either byte string cannot be parsed as MIDI.
    """
    from muse.plugins.midi.entity import assign_entity_ids, diff_with_entity_ids

    base_notes, base_tpb = extract_notes(base_bytes)
    target_notes, _ = extract_notes(target_bytes)
    tpb = base_tpb

    base_entities = assign_entity_ids(
        base_notes,
        prior_index,
        commit_id=commit_id or "base",
        op_id=op_id or "",
        mutation_threshold_ticks=mutation_threshold_ticks,
        mutation_threshold_velocity=mutation_threshold_velocity,
    )
    target_entities = assign_entity_ids(
        target_notes,
        prior_index,
        commit_id=commit_id,
        op_id=op_id,
        mutation_threshold_ticks=mutation_threshold_ticks,
        mutation_threshold_velocity=mutation_threshold_velocity,
    )

    ops = diff_with_entity_ids(base_entities, target_entities, tpb)

    inserts = sum(1 for op in ops if op["op"] == "insert")
    deletes = sum(1 for op in ops if op["op"] == "delete")
    mutates = sum(1 for op in ops if op["op"] == "mutate")

    parts: list[str] = []
    if inserts:
        parts.append(f"{inserts} note{'s' if inserts != 1 else ''} added")
    if deletes:
        parts.append(f"{deletes} note{'s' if deletes != 1 else ''} removed")
    if mutates:
        parts.append(f"{mutates} note{'s' if mutates != 1 else ''} mutated")
    summary = ", ".join(parts) if parts else "no note changes"

    logger.debug(
        "✅ Entity-aware MIDI diff %r: +%d -%d ~%d (%d ops)",
        file_path,
        inserts,
        deletes,
        mutates,
        len(ops),
    )

    return StructuredDelta(
        domain="midi_notes_tracked",
        ops=ops,
        summary=summary,
    )


# ---------------------------------------------------------------------------
# MIDI reconstruction — inverse of extract_notes
# ---------------------------------------------------------------------------


def reconstruct_midi(
    notes: list[NoteKey],
    *,
    ticks_per_beat: int = 480,
) -> bytes:
    """Produce raw MIDI bytes from a list of :class:`NoteKey` objects.

    Creates a Type 0 (single-track) MIDI file.  One ``note_on`` and one
    ``note_off`` event are emitted per note.  Events are sorted by absolute
    tick time so the output is a valid MIDI stream regardless of the input
    order.

    This is the inverse of :func:`extract_notes`.  Used by
    :func:`~muse.plugins.midi.plugin._merge_patch_ops` after the OT
    engine has confirmed that two branches' note sequences commute, allowing
    the merged note list to be materialised as actual MIDI bytes.

    Args:
        notes:          Note events to write.  May be in any order; the
                        function sorts by ``start_tick`` before writing.
        ticks_per_beat: Timing resolution.  Preserve the base file's value so
                        that beat positions remain meaningful.

    Returns:
        Raw MIDI bytes ready to be written to the object store.
    """
    mid = mido.MidiFile(ticks_per_beat=ticks_per_beat, type=0)
    track = mido.MidiTrack()
    mid.tracks.append(track)

    # Build flat (abs_tick, note_on, channel, pitch, velocity) event tuples.
    raw_events: list[tuple[int, bool, int, int, int]] = []
    for note in notes:
        raw_events.append(
            (note["start_tick"], True, note["channel"], note["pitch"], note["velocity"])
        )
        raw_events.append(
            (
                note["start_tick"] + note["duration_ticks"],
                False,
                note["channel"],
                note["pitch"],
                0,
            )
        )

    # Sort: by tick, with note_off (False) before note_on (True) at the same
    # tick so that retriggered notes are handled correctly.
    raw_events.sort(key=lambda e: (e[0], e[1]))

    prev_tick = 0
    for abs_tick, is_on, channel, pitch, velocity in raw_events:
        delta = abs_tick - prev_tick
        if is_on:
            track.append(
                mido.Message("note_on", channel=channel, note=pitch, velocity=velocity, time=delta)
            )
        else:
            track.append(
                mido.Message("note_off", channel=channel, note=pitch, velocity=0, time=delta)
            )
        prev_tick = abs_tick

    buf = io.BytesIO()
    mid.save(file=buf)
    return buf.getvalue()



"""MIDI note-level diff for the Muse music plugin.

Implements the Myers / LCS shortest-edit-script algorithm on MIDI note
sequences, producing a ``StructuredDelta`` with note-level ``InsertOp``,
``DeleteOp``, and ``ReplaceOp`` entries inside a ``PatchOp``.

This is what lets ``muse show`` display "C4 added at beat 3.5" rather than
"tracks/drums.mid modified".

Algorithm
---------
1. Parse MIDI bytes and extract paired note events (note_on + note_off)
   sorted by start tick.
2. Represent each note as a ``NoteKey`` TypedDict with five fields.
3. Run the O(nm) LCS dynamic-programming algorithm on the two note sequences.
4. Traceback to produce a shortest edit script of keep / insert / delete steps.
5. Map edit steps to typed ``DomainOp`` instances.
6. Wrap the ops in a ``StructuredDelta`` with a human-readable summary.

Public API
----------
- :class:`NoteKey` — hashable note identity.
- :func:`extract_notes` — MIDI bytes → sorted ``list[NoteKey]``.
- :func:`lcs_edit_script` — LCS shortest edit script on two note lists.
- :func:`diff_midi_notes` — top-level: MIDI bytes × 2 → ``StructuredDelta``.
"""
from __future__ import annotations

import hashlib
import io
import logging
from dataclasses import dataclass
from typing import Literal, TypedDict

import mido

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
# Edit step — output of the LCS traceback
# ---------------------------------------------------------------------------

EditKind = Literal["keep", "insert", "delete"]


@dataclass(frozen=True)
class EditStep:
    """One step in the shortest edit script."""

    kind: EditKind
    base_index: int    # index in the base note sequence
    target_index: int  # index in the target note sequence
    note: NoteKey


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
# LCS / Myers algorithm
# ---------------------------------------------------------------------------


def lcs_edit_script(
    base: list[NoteKey],
    target: list[NoteKey],
) -> list[EditStep]:
    """Compute the shortest edit script transforming *base* into *target*.

    Uses the standard O(n·m) LCS dynamic-programming algorithm followed by
    linear-time traceback. Two notes are matched iff all five ``NoteKey``
    fields are equal.

    Args:
        base:   The base (ancestor) note sequence.
        target: The target (newer) note sequence.

    Returns:
        A list of ``EditStep`` with kind ``"keep"``, ``"insert"``, or
        ``"delete"`` that transforms *base* into *target* in order.
        The list is minimal: ``len(keep steps) == LCS length``.
    """
    n, m = len(base), len(target)

    # dp[i][j] = length of LCS of base[i:] and target[j:]
    dp: list[list[int]] = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            if base[i] == target[j]:
                dp[i][j] = dp[i + 1][j + 1] + 1
            else:
                dp[i][j] = max(dp[i + 1][j], dp[i][j + 1])

    # Traceback: reconstruct the edit script.
    steps: list[EditStep] = []
    i, j = 0, 0
    while i < n or j < m:
        if i < n and j < m and base[i] == target[j]:
            steps.append(EditStep("keep", i, j, base[i]))
            i += 1
            j += 1
        elif j < m and (i >= n or dp[i][j + 1] >= dp[i + 1][j]):
            steps.append(EditStep("insert", i, j, target[j]))
            j += 1
        else:
            steps.append(EditStep("delete", i, j, base[i]))
            i += 1

    return steps


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

    Parses both files, runs LCS on their note sequences, and returns a
    ``StructuredDelta`` suitable for embedding in a ``PatchOp.child_ops``
    list or storing directly as a commit's ``structured_delta``.

    Args:
        base_bytes:  Raw bytes of the base (ancestor) MIDI file.
        target_bytes: Raw bytes of the target (newer) MIDI file.
        file_path:   Workspace-relative path of the file being diffed.
                     Used only in log messages and ``content_summary`` strings.

    Returns:
        A ``StructuredDelta`` with ``InsertOp`` and ``DeleteOp`` entries for
        each note added or removed. The ``summary`` field is human-readable,
        e.g. ``"3 notes added, 1 note removed"``.

    Raises:
        ValueError: When either byte string cannot be parsed as MIDI.
    """
    base_notes, base_tpb = extract_notes(base_bytes)
    target_notes, target_tpb = extract_notes(target_bytes)
    tpb = base_tpb  # use base ticks_per_beat for summary formatting

    steps = lcs_edit_script(base_notes, target_notes)

    child_ops: list[DomainOp] = []
    inserts = 0
    deletes = 0

    for step in steps:
        if step.kind == "insert":
            child_ops.append(
                InsertOp(
                    op="insert",
                    address=f"note:{step.target_index}",
                    position=step.target_index,
                    content_id=_note_content_id(step.note),
                    content_summary=_note_summary(step.note, tpb),
                )
            )
            inserts += 1
        elif step.kind == "delete":
            child_ops.append(
                DeleteOp(
                    op="delete",
                    address=f"note:{step.base_index}",
                    position=step.base_index,
                    content_id=_note_content_id(step.note),
                    content_summary=_note_summary(step.note, tpb),
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
        "✅ MIDI diff %r: +%d -%d notes (%d LCS steps)",
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



"""Shared music-domain query helpers for the Muse CLI.

Provides the low-level primitives that music-domain commands share:
note extraction from the object store, bar-level grouping, chord detection,
and commit-graph walking specific to MIDI tracks.

Nothing here belongs in the public ``MidiPlugin`` API.  These are CLI-layer
helpers — thin adapters over ``midi_diff.extract_notes`` and the core store.
"""

from __future__ import annotations

import logging
import pathlib
from typing import NamedTuple

from muse.core.object_store import read_object
from muse.core.store import CommitRecord, read_commit, get_commit_snapshot_manifest
from muse.plugins.midi.midi_diff import NoteKey, _pitch_name, extract_notes  # noqa: PLC2701

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pitch / music-theory constants
# ---------------------------------------------------------------------------

_PITCH_CLASSES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

# Chord templates: frozenset of pitch-class offsets (root = 0).
_CHORD_TEMPLATES: list[tuple[str, frozenset[int]]] = [
    ("maj",  frozenset({0, 4, 7})),
    ("min",  frozenset({0, 3, 7})),
    ("dim",  frozenset({0, 3, 6})),
    ("aug",  frozenset({0, 4, 8})),
    ("sus2", frozenset({0, 2, 7})),
    ("sus4", frozenset({0, 5, 7})),
    ("dom7", frozenset({0, 4, 7, 10})),
    ("maj7", frozenset({0, 4, 7, 11})),
    ("min7", frozenset({0, 3, 7, 10})),
    ("dim7", frozenset({0, 3, 6, 9})),
    ("5",    frozenset({0, 7})),          # power chord
]

# ---------------------------------------------------------------------------
# NoteInfo — enriched note for display
# ---------------------------------------------------------------------------


class NoteInfo(NamedTuple):
    """A ``NoteKey`` with derived musical fields for display."""

    pitch: int
    velocity: int
    start_tick: int
    duration_ticks: int
    channel: int
    ticks_per_beat: int

    @property
    def pitch_name(self) -> str:
        return _pitch_name(self.pitch)

    @property
    def beat(self) -> float:
        return self.start_tick / max(self.ticks_per_beat, 1)

    @property
    def beat_duration(self) -> float:
        return self.duration_ticks / max(self.ticks_per_beat, 1)

    @property
    def bar(self) -> int:
        """1-indexed bar number (assumes 4/4 time)."""
        return int(self.start_tick // (4 * max(self.ticks_per_beat, 1))) + 1

    @property
    def beat_in_bar(self) -> float:
        """Beat position within the bar (1-indexed)."""
        tpb = max(self.ticks_per_beat, 1)
        bar_tick = (self.bar - 1) * 4 * tpb
        return (self.start_tick - bar_tick) / tpb + 1

    @property
    def pitch_class(self) -> int:
        return self.pitch % 12

    @property
    def pitch_class_name(self) -> str:
        return _PITCH_CLASSES[self.pitch_class]

    @classmethod
    def from_note_key(cls, note: NoteKey, ticks_per_beat: int) -> "NoteInfo":
        return cls(
            pitch=note["pitch"],
            velocity=note["velocity"],
            start_tick=note["start_tick"],
            duration_ticks=note["duration_ticks"],
            channel=note["channel"],
            ticks_per_beat=ticks_per_beat,
        )


# ---------------------------------------------------------------------------
# Track loading from the object store
# ---------------------------------------------------------------------------


def load_track(
    root: pathlib.Path,
    commit_id: str,
    track_path: str,
) -> tuple[list[NoteInfo], int] | None:
    """Load notes for *track_path* from the snapshot at *commit_id*.

    Args:
        root:       Repository root.
        commit_id:  SHA-256 commit ID.
        track_path: Workspace-relative path to the ``.mid`` file.

    Returns:
        ``(notes, ticks_per_beat)`` on success, ``None`` when the track is
        not in the snapshot or the object is missing / unparseable.
    """
    manifest: dict[str, str] = get_commit_snapshot_manifest(root, commit_id) or {}
    object_id = manifest.get(track_path)
    if object_id is None:
        return None
    raw = read_object(root, object_id)
    if raw is None:
        return None
    try:
        keys, tpb = extract_notes(raw)
    except ValueError as exc:
        logger.debug("Cannot parse MIDI %r from commit %s: %s", track_path, commit_id[:8], exc)
        return None
    notes = [NoteInfo.from_note_key(k, tpb) for k in keys]
    return notes, tpb


def load_track_from_workdir(
    root: pathlib.Path,
    track_path: str,
) -> tuple[list[NoteInfo], int] | None:
    """Load notes for *track_path* from ``muse-work/`` (live working tree).

    Args:
        root:       Repository root.
        track_path: Workspace-relative path to the ``.mid`` file.

    Returns:
        ``(notes, ticks_per_beat)`` on success, ``None`` when unreadable.
    """
    work_path = root / "muse-work" / track_path
    if not work_path.exists():
        work_path = root / track_path
    if not work_path.exists():
        return None
    raw = work_path.read_bytes()
    try:
        keys, tpb = extract_notes(raw)
    except ValueError as exc:
        logger.debug("Cannot parse MIDI %r from workdir: %s", track_path, exc)
        return None
    notes = [NoteInfo.from_note_key(k, tpb) for k in keys]
    return notes, tpb


# ---------------------------------------------------------------------------
# Musical analysis helpers
# ---------------------------------------------------------------------------


def notes_by_bar(notes: list[NoteInfo]) -> dict[int, list[NoteInfo]]:
    """Group *notes* by 1-indexed bar number (assumes 4/4 time)."""
    bars: dict[int, list[NoteInfo]] = {}
    for note in sorted(notes, key=lambda n: (n.start_tick, n.pitch)):
        bars.setdefault(note.bar, []).append(note)
    return bars


def detect_chord(pitch_classes: frozenset[int]) -> str:
    """Return the best chord name for a set of pitch classes.

    Tries every chromatic root and every chord template.  Returns the
    name of the best match (most pitch classes covered) as ``"RootQuality"``
    e.g. ``"Cmaj"``, ``"Fmin7"``.  Returns ``"??"`` when fewer than two
    distinct pitch classes are present.
    """
    if len(pitch_classes) < 2:
        return "??"
    best_name = "??"
    best_score = 0
    for root in range(12):
        normalized = frozenset((pc - root) % 12 for pc in pitch_classes)
        for quality, template in _CHORD_TEMPLATES:
            overlap = len(normalized & template)
            if overlap > best_score or (
                overlap == best_score and overlap == len(template)
            ):
                best_score = overlap
                root_name = _PITCH_CLASSES[root]
                best_name = f"{root_name}{quality}"
    return best_name


def key_signature_guess(notes: list[NoteInfo]) -> str:
    """Guess the key signature from pitch class frequencies.

    Uses the Krumhansl-Schmuckler key-finding algorithm with simplified
    major and minor profiles.  Returns a string like ``"G major"`` or
    ``"D minor"``.
    """
    if not notes:
        return "unknown"

    # Build pitch class histogram.
    histogram = [0] * 12
    for note in notes:
        histogram[note.pitch_class] += 1

    # Krumhansl-Schmuckler major and minor profiles (normalized).
    major_profile = [
        6.35, 2.23, 3.48, 2.33, 4.38, 4.09,
        2.52, 5.19, 2.39, 3.66, 2.29, 2.88,
    ]
    minor_profile = [
        6.33, 2.68, 3.52, 5.38, 2.60, 3.53,
        2.54, 4.75, 3.98, 2.69, 3.34, 3.17,
    ]

    total = max(sum(histogram), 1)
    h_norm = [v / total for v in histogram]

    best_key = ""
    best_score = -999.0

    for root in range(12):
        for mode, profile in [("major", major_profile), ("minor", minor_profile)]:
            # Rotate profile to this root.
            score = sum(
                h_norm[(root + i) % 12] * profile[i] for i in range(12)
            )
            if score > best_score:
                best_score = score
                best_key = f"{_PITCH_CLASSES[root]} {mode}"

    return best_key


# ---------------------------------------------------------------------------
# Commit-graph walking (music-domain specific)
# ---------------------------------------------------------------------------


def walk_commits_for_track(
    root: pathlib.Path,
    start_commit_id: str,
    track_path: str,
    max_commits: int = 10_000,
) -> list[tuple[CommitRecord, dict[str, str] | None]]:
    """Walk the parent chain from *start_commit_id*, collecting snapshot manifests.

    Returns ``(commit, manifest)`` pairs where ``manifest`` may be ``None``
    when the commit has no snapshot.  Only commits where the track appears
    in the manifest (or in its parent's manifest) are useful for note-level
    queries, but we return all so callers can filter.
    """
    result: list[tuple[CommitRecord, dict[str, str] | None]] = []
    seen: set[str] = set()
    current_id: str | None = start_commit_id
    while current_id and current_id not in seen and len(result) < max_commits:
        seen.add(current_id)
        commit = read_commit(root, current_id)
        if commit is None:
            break
        manifest = get_commit_snapshot_manifest(root, commit.commit_id) or None
        result.append((commit, manifest))
        current_id = commit.parent_commit_id
    return result


# ---------------------------------------------------------------------------
# MIDI reconstruction helper (for transpose / mix)
# ---------------------------------------------------------------------------


def notes_to_midi_bytes(notes: list[NoteInfo], ticks_per_beat: int) -> bytes:
    """Reconstruct a MIDI file from a list of ``NoteInfo`` objects.

    Produces a Type-0 single-track MIDI file with one note_on / note_off
    pair per note.  Delegates to
    :func:`~muse.plugins.midi.midi_diff.reconstruct_midi`.
    """
    from muse.plugins.midi.midi_diff import NoteKey, reconstruct_midi

    keys = [
        NoteKey(
            pitch=n.pitch,
            velocity=n.velocity,
            start_tick=n.start_tick,
            duration_ticks=n.duration_ticks,
            channel=n.channel,
        )
        for n in notes
    ]
    return reconstruct_midi(keys, ticks_per_beat=ticks_per_beat)

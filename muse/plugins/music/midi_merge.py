"""MIDI dimension-aware merge for the Muse music plugin.

This module implements the multidimensional merge that makes Muse meaningfully
different from git.  Git treats every file as an opaque byte sequence: any
two-branch change to the same file is a conflict.  Muse understands that a
MIDI file has *independent orthogonal axes*, and two collaborators can touch
different axes of the same file without conflicting.

Dimensions
----------

+---------------+----------------------------------------------------+
| Dimension     | MIDI event types                                   |
+===============+====================================================+
| ``melodic``   | ``note_on`` / ``note_off`` (pitch + timing)        |
+---------------+----------------------------------------------------+
| ``rhythmic``  | Alias for ``melodic`` — timing is inseparable from |
|               | pitch in the MIDI event model; provided as a       |
|               | user-facing label in ``.museattributes`` rules.    |
+---------------+----------------------------------------------------+
| ``harmonic``  | ``pitchwheel`` events                              |
+---------------+----------------------------------------------------+
| ``dynamic``   | ``control_change`` events                          |
+---------------+----------------------------------------------------+
| ``structural``| ``set_tempo``, ``time_signature``, ``key_signature``,|
|               | ``program_change``, text/sysex meta events         |
+---------------+----------------------------------------------------+

Merge algorithm
---------------

1. Parse ``base``, ``left``, and ``right`` MIDI bytes into event streams.
2. Convert to absolute-tick representation and bucket by dimension.
3. Hash each bucket; compare ``base ↔ left`` and ``base ↔ right`` to detect
   per-dimension changes.
4. For each dimension apply the winning side determined by ``.museattributes``
   strategy (or the standard one-sided-change rule when no conflict exists).
5. Reconstruct a valid MIDI file by merging winning dimension slices, sorting
   by absolute tick, converting back to delta-time, and writing to bytes.

Public API
----------

- :func:`extract_dimensions` — parse MIDI bytes → ``dict[dim, DimensionSlice]``
- :func:`merge_midi_dimensions` — three-way dimension merge → bytes or ``None``
- :func:`dimension_conflict_detail` — per-dimension change report for logging
"""
from __future__ import annotations

import hashlib
import io
import json
from dataclasses import dataclass, field

import mido

from muse.core.attributes import AttributeRule, resolve_strategy

# ---------------------------------------------------------------------------
# Dimension constants
# ---------------------------------------------------------------------------

#: Internal dimension names used as dict keys throughout this module.
INTERNAL_DIMS: list[str] = ["notes", "harmonic", "dynamic", "structural"]

#: User-facing dimension names from .museattributes mapped to internal buckets.
#: Both "melodic" and "rhythmic" map to the same "notes" bucket because MIDI
#: event timing and pitch are carried in the same event structure.
DIM_ALIAS: dict[str, str] = {
    "melodic": "notes",
    "rhythmic": "notes",
    "harmonic": "harmonic",
    "dynamic": "dynamic",
    "structural": "structural",
}

#: Canonical alias → internal dim name, with internal names as pass-throughs.
_CANONICAL: dict[str, str] = {**DIM_ALIAS, "notes": "notes"}


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class DimensionSlice:
    """Events belonging to one dimension of a MIDI file.

    ``events`` is a list of ``(abs_tick, mido.Message)`` pairs sorted by
    ascending absolute tick.  ``content_hash`` is the SHA-256 digest of the
    canonical JSON serialisation of the event list (used for change detection
    without loading file bytes).
    """

    name: str
    events: list[tuple[int, mido.Message]] = field(default_factory=list)
    content_hash: str = ""

    def __post_init__(self) -> None:
        if not self.content_hash:
            self.content_hash = _hash_events(self.events)


@dataclass
class MidiDimensions:
    """All dimension slices extracted from one MIDI file."""

    ticks_per_beat: int
    file_type: int
    slices: dict[str, DimensionSlice]  # internal dim name → slice

    def get(self, user_dim: str) -> DimensionSlice:
        """Return the slice for a user-facing or internal dimension name."""
        internal = _CANONICAL.get(user_dim, user_dim)
        return self.slices[internal]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _classify_event(msg: mido.Message) -> str | None:
    """Map a mido Message to an internal dimension bucket, or ``None`` to skip."""
    t = msg.type
    if t in ("note_on", "note_off"):
        return "notes"
    if t == "pitchwheel":
        return "harmonic"
    if t == "control_change":
        return "dynamic"
    if t in (
        "set_tempo",
        "time_signature",
        "key_signature",
        "program_change",
        "sysex",
        "text",
        "copyright",
        "track_name",
        "instrument_name",
        "lyrics",
        "marker",
        "cue_marker",
        "sequencer_specific",
        "end_of_track",
    ):
        return "structural"
    # Unrecognised meta events → structural bucket as a safe default.
    if getattr(msg, "is_meta", False):
        return "structural"
    return None


type _MsgVal = int | str | list[int]


def _msg_to_dict(msg: mido.Message) -> dict[str, _MsgVal]:
    """Serialise a mido Message to a JSON-compatible dict."""
    d: dict[str, _MsgVal] = {"type": msg.type}
    for attr in ("channel", "note", "velocity", "control", "value",
                 "pitch", "program", "numerator", "denominator",
                 "clocks_per_click", "notated_32nd_notes_per_beat",
                 "tempo", "key", "scale", "text", "data"):
        if hasattr(msg, attr):
            raw = getattr(msg, attr)
            if isinstance(raw, (bytes, bytearray)):
                d[attr] = list(raw)
            elif isinstance(raw, str):
                d[attr] = raw
            elif isinstance(raw, int):
                d[attr] = raw
            # Other types (float, etc.) are skipped — not present in standard MIDI
    return d


def _hash_events(events: list[tuple[int, mido.Message]]) -> str:
    """SHA-256 of the canonical JSON representation of an event list."""
    payload = json.dumps(
        [(tick, _msg_to_dict(msg)) for tick, msg in events],
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def _to_absolute(track: mido.MidiTrack) -> list[tuple[int, mido.Message]]:
    """Convert a delta-time track to a list of ``(abs_tick, msg)`` pairs."""
    result: list[tuple[int, mido.Message]] = []
    abs_tick = 0
    for msg in track:
        abs_tick += msg.time
        result.append((abs_tick, msg))
    return result


# ---------------------------------------------------------------------------
# Public: extract_dimensions
# ---------------------------------------------------------------------------


def extract_dimensions(midi_bytes: bytes) -> MidiDimensions:
    """Parse *midi_bytes* and bucket events by dimension.

    Args:
        midi_bytes: Raw bytes of a ``.mid`` file.

    Returns:
        A :class:`MidiDimensions` with one :class:`DimensionSlice` per
        internal dimension.  Events are sorted by ascending absolute tick.

    Raises:
        ValueError: If *midi_bytes* cannot be parsed as a MIDI file.
    """
    try:
        mid = mido.MidiFile(file=io.BytesIO(midi_bytes))
    except Exception as exc:
        raise ValueError(f"Failed to parse MIDI data: {exc}") from exc

    buckets: dict[str, list[tuple[int, mido.Message]]] = {
        dim: [] for dim in INTERNAL_DIMS
    }

    for track in mid.tracks:
        for abs_tick, msg in _to_absolute(track):
            bucket = _classify_event(msg)
            if bucket is not None:
                buckets[bucket].append((abs_tick, msg))

    # Sort each bucket by ascending absolute tick, then by event type for
    # determinism when multiple events share the same tick.
    for dim in INTERNAL_DIMS:
        buckets[dim].sort(key=lambda x: (x[0], x[1].type))

    slices = {dim: DimensionSlice(name=dim, events=events)
               for dim, events in buckets.items()}
    return MidiDimensions(
        ticks_per_beat=mid.ticks_per_beat,
        file_type=mid.type,
        slices=slices,
    )


# ---------------------------------------------------------------------------
# Public: dimension_conflict_detail
# ---------------------------------------------------------------------------


def dimension_conflict_detail(
    base: MidiDimensions,
    left: MidiDimensions,
    right: MidiDimensions,
) -> dict[str, str]:
    """Return a per-dimension change report for a conflicting file.

    Returns a dict mapping internal dimension name to one of:

    - ``"unchanged"`` — neither side changed this dimension.
    - ``"left_only"`` — only the left (ours) side changed.
    - ``"right_only"`` — only the right (theirs) side changed.
    - ``"both"`` — both sides changed; a dimension-level conflict.

    This is used by :func:`merge_midi_dimensions` and can also be surfaced
    in ``muse merge`` output for human-readable conflict diagnostics.
    """
    report: dict[str, str] = {}
    for dim in INTERNAL_DIMS:
        base_hash = base.slices[dim].content_hash
        left_hash = left.slices[dim].content_hash
        right_hash = right.slices[dim].content_hash
        left_changed = base_hash != left_hash
        right_changed = base_hash != right_hash
        if left_changed and right_changed:
            report[dim] = "both"
        elif left_changed:
            report[dim] = "left_only"
        elif right_changed:
            report[dim] = "right_only"
        else:
            report[dim] = "unchanged"
    return report


# ---------------------------------------------------------------------------
# Reconstruction helpers
# ---------------------------------------------------------------------------


def _events_to_track(
    events: list[tuple[int, mido.Message]],
) -> mido.MidiTrack:
    """Convert absolute-tick events to a mido MidiTrack with delta times."""
    track = mido.MidiTrack()
    prev_tick = 0
    for abs_tick, msg in sorted(events, key=lambda x: (x[0], x[1].type)):
        delta = abs_tick - prev_tick
        # mido Message objects are immutable; copy() gives us a mutable clone.
        new_msg = msg.copy(time=delta)
        track.append(new_msg)
        prev_tick = abs_tick
    # Ensure every track ends with end_of_track.
    if not track or track[-1].type != "end_of_track":
        track.append(mido.MetaMessage("end_of_track", time=0))
    return track


def _reconstruct(
    ticks_per_beat: int,
    winning_slices: dict[str, list[tuple[int, mido.Message]]],
) -> bytes:
    """Build a type-0 MIDI file from winning dimension event lists.

    All dimension events are merged into a single track (type-0) for
    maximum compatibility.  The absolute-tick ordering is preserved.
    """
    all_events: list[tuple[int, mido.Message]] = []
    for events in winning_slices.values():
        all_events.extend(events)

    # Remove duplicate end_of_track messages; add exactly one at the end.
    all_events = [
        (tick, msg) for tick, msg in all_events
        if msg.type != "end_of_track"
    ]
    all_events.sort(key=lambda x: (x[0], x[1].type))

    track = _events_to_track(all_events)
    mid = mido.MidiFile(type=0, ticks_per_beat=ticks_per_beat)
    mid.tracks.append(track)

    buf = io.BytesIO()
    mid.save(file=buf)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Public: merge_midi_dimensions
# ---------------------------------------------------------------------------


def merge_midi_dimensions(
    base_bytes: bytes,
    left_bytes: bytes,
    right_bytes: bytes,
    attrs_rules: list[AttributeRule],
    path: str,
) -> tuple[bytes, dict[str, str]] | None:
    """Attempt a dimension-level three-way merge of a MIDI file.

    For each internal dimension:

    - If neither side changed → keep base.
    - If only one side changed → take that side (clean auto-merge).
    - If both sides changed → consult ``.museattributes`` strategy:

      * ``ours`` / ``theirs`` → take the specified side; record in report.
      * ``manual`` / ``auto`` / ``union`` → unresolvable; return ``None``.

    Args:
        base_bytes:  MIDI bytes for the common ancestor.
        left_bytes:  MIDI bytes for the ours (left) branch.
        right_bytes: MIDI bytes for the theirs (right) branch.
        attrs_rules: Rule list from :func:`muse.core.attributes.load_attributes`.
        path:        Workspace-relative POSIX path (used for strategy lookup).

    Returns:
        A ``(merged_bytes, dimension_report)`` tuple when all dimension
        conflicts can be resolved, or ``None`` when at least one dimension
        conflict has no resolvable strategy.

        *dimension_report* maps each internal dimension name to the side
        chosen: ``"base"``, ``"left"``, ``"right"``, or the strategy string.

    Raises:
        ValueError: If any of the byte strings cannot be parsed as MIDI.
    """
    base_dims = extract_dimensions(base_bytes)
    left_dims = extract_dimensions(left_bytes)
    right_dims = extract_dimensions(right_bytes)

    detail = dimension_conflict_detail(base_dims, left_dims, right_dims)

    winning_slices: dict[str, list[tuple[int, mido.Message]]] = {}
    dimension_report: dict[str, str] = {}

    for dim in INTERNAL_DIMS:
        change = detail[dim]

        if change == "unchanged":
            winning_slices[dim] = base_dims.slices[dim].events
            dimension_report[dim] = "base"

        elif change == "left_only":
            winning_slices[dim] = left_dims.slices[dim].events
            dimension_report[dim] = "left"

        elif change == "right_only":
            winning_slices[dim] = right_dims.slices[dim].events
            dimension_report[dim] = "right"

        else:
            # Both sides changed — consult .museattributes for this dimension.
            # Try user-facing aliases first, then internal name.
            user_dim_names = [k for k, v in DIM_ALIAS.items() if v == dim] + [dim]
            strategy = "auto"
            for user_dim in user_dim_names:
                s = resolve_strategy(attrs_rules, path, user_dim)
                if s != "auto":
                    strategy = s
                    break
            # Also try dimension wildcard ("*")
            if strategy == "auto":
                strategy = resolve_strategy(attrs_rules, path, "*")

            if strategy == "ours":
                winning_slices[dim] = left_dims.slices[dim].events
                dimension_report[dim] = f"ours ({dim})"
            elif strategy == "theirs":
                winning_slices[dim] = right_dims.slices[dim].events
                dimension_report[dim] = f"theirs ({dim})"
            else:
                # "auto", "union", "manual" — cannot resolve this dimension.
                return None

    merged_bytes = _reconstruct(base_dims.ticks_per_beat, winning_slices)
    return merged_bytes, dimension_report

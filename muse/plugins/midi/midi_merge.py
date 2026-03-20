"""MIDI dimension-aware merge for the Muse MIDI plugin.

This module implements the multidimensional merge that makes Muse meaningfully
different from git.  Git treats every file as an opaque byte sequence: any
two-branch change to the same file is a conflict.  Muse understands that a
MIDI file has *independent orthogonal axes*, and two collaborators can touch
different axes of the same file without conflicting.

Dimensions
----------

MIDI carries far more independent axes than a naive "notes vs. everything else"
split.  The full dimension taxonomy maps every MIDI event type to exactly one
internal bucket:

+----------------------+--------------------------------------------------+
| Internal dimension   | MIDI event types / CC numbers                    |
+======================+==================================================+
| ``notes``            | ``note_on`` / ``note_off``                       |
+----------------------+--------------------------------------------------+
| ``pitch_bend``       | ``pitchwheel``                                   |
+----------------------+--------------------------------------------------+
| ``channel_pressure`` | ``aftertouch`` (mono channel pressure)           |
+----------------------+--------------------------------------------------+
| ``poly_pressure``    | ``polytouch`` (per-note polyphonic aftertouch)   |
+----------------------+--------------------------------------------------+
| ``cc_modulation``    | CC 1 — modulation wheel                         |
+----------------------+--------------------------------------------------+
| ``cc_volume``        | CC 7 — channel volume                           |
+----------------------+--------------------------------------------------+
| ``cc_pan``           | CC 10 — stereo pan                              |
+----------------------+--------------------------------------------------+
| ``cc_expression``    | CC 11 — expression controller                   |
+----------------------+--------------------------------------------------+
| ``cc_sustain``       | CC 64 — damper / sustain pedal                  |
+----------------------+--------------------------------------------------+
| ``cc_sostenuto``     | CC 66 — sostenuto pedal                         |
+----------------------+--------------------------------------------------+
| ``cc_soft_pedal``    | CC 67 — soft pedal (una corda)                  |
+----------------------+--------------------------------------------------+
| ``cc_portamento``    | CC 65 — portamento on/off                       |
+----------------------+--------------------------------------------------+
| ``cc_reverb``        | CC 91 — reverb send level                       |
+----------------------+--------------------------------------------------+
| ``cc_chorus``        | CC 93 — chorus send level                       |
+----------------------+--------------------------------------------------+
| ``cc_other``         | All other CC events (numbered controllers)       |
+----------------------+--------------------------------------------------+
| ``program_change``   | ``program_change`` (patch / instrument select)   |
+----------------------+--------------------------------------------------+
| ``tempo_map``        | ``set_tempo`` meta events                        |
+----------------------+--------------------------------------------------+
| ``time_signatures``  | ``time_signature`` meta events                   |
+----------------------+--------------------------------------------------+
| ``key_signatures``   | ``key_signature`` meta events                    |
+----------------------+--------------------------------------------------+
| ``markers``          | ``marker``, ``cue_marker``, ``text``,            |
|                      | ``lyrics``, ``copyright`` meta events            |
+----------------------+--------------------------------------------------+
| ``track_structure``  | ``track_name``, ``instrument_name``, ``sysex``,  |
|                      | ``sequencer_specific`` and unknown meta events   |
+----------------------+--------------------------------------------------+

Why fine-grained dimensions matter
-----------------------------------
With the old 4-bucket model, changing sustain pedal (CC64) and changing channel
volume (CC7) were the same dimension: they always conflicted.  With 21 internal
dimensions they are independent — two agents can edit different aspects of the
same MIDI file without ever conflicting.

Independence rules
------------------
- **Independent** (``independent_merge=True``): notes, pitch_bend, all CC
  dimensions, channel_pressure, poly_pressure, program_change, key_signatures,
  markers.  Conflicts in these dimensions never block merging others.
- **Non-independent** (``independent_merge=False``): tempo_map, time_signatures,
  track_structure.  A conflict here blocks merging other dimensions until
  resolved, because a tempo change shifts the musical meaning of every subsequent
  tick position, and track structure changes affect routing.

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
- :func:`extract_dimensions` — parse MIDI bytes → ``MidiDimensions``
- :func:`merge_midi_dimensions` — three-way dimension merge → bytes or ``None``
- :func:`dimension_conflict_detail` — per-dimension change report for logging
- :data:`INTERNAL_DIMS` — ordered list of all internal dimension names
- :data:`DIM_ALIAS` — user-facing ``.museattributes`` name → internal bucket
- :data:`NON_INDEPENDENT_DIMS` — dimensions that block others on conflict
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
from dataclasses import dataclass, field

import mido

logger = logging.getLogger(__name__)

from muse.core.attributes import AttributeRule, resolve_strategy

# ---------------------------------------------------------------------------
# Dimension constants — the complete MIDI dimension taxonomy
# ---------------------------------------------------------------------------

#: Internal dimension names, ordered canonically.
#: Each MIDI event type maps to exactly one of these buckets.
INTERNAL_DIMS: list[str] = [
    # --- Expressive note content ---
    "notes",            # note_on / note_off
    "pitch_bend",       # pitchwheel
    "channel_pressure", # aftertouch (mono)
    "poly_pressure",    # polytouch (per-note)
    # --- Named CC controllers (individually mergeable) ---
    "cc_modulation",    # CC 1
    "cc_volume",        # CC 7
    "cc_pan",           # CC 10
    "cc_expression",    # CC 11
    "cc_sustain",       # CC 64
    "cc_portamento",    # CC 65
    "cc_sostenuto",     # CC 66
    "cc_soft_pedal",    # CC 67
    "cc_reverb",        # CC 91
    "cc_chorus",        # CC 93
    "cc_other",         # all remaining CC numbers
    # --- Patch / program selection ---
    "program_change",
    # --- Timeline / notation metadata (non-independent) ---
    "tempo_map",        # set_tempo — non-independent: affects all tick positions
    "time_signatures",  # time_signature — non-independent: affects bar structure
    # --- Tonal context and notation ---
    "key_signatures",   # key_signature
    "markers",          # marker, cue_marker, text, lyrics, copyright
    # --- Track structure (non-independent) ---
    "track_structure",  # track_name, instrument_name, sysex, unknown meta
]

#: Dimensions whose conflicts block merging all other dimensions until resolved.
#: All other dimensions are merged in parallel regardless of conflicts here.
NON_INDEPENDENT_DIMS: frozenset[str] = frozenset({
    "tempo_map",
    "time_signatures",
    "track_structure",
})

#: User-facing dimension names from .museattributes mapped to internal buckets.
#: Agents and humans use these names in merge strategy declarations.
DIM_ALIAS: dict[str, str] = {
    "pitch_bend":       "pitch_bend",
    "aftertouch":       "channel_pressure",
    "poly_aftertouch":  "poly_pressure",
    "modulation":       "cc_modulation",
    "volume":           "cc_volume",
    "pan":              "cc_pan",
    "expression":       "cc_expression",
    "sustain":          "cc_sustain",
    "portamento":       "cc_portamento",
    "sostenuto":        "cc_sostenuto",
    "soft_pedal":       "cc_soft_pedal",
    "reverb":           "cc_reverb",
    "chorus":           "cc_chorus",
    "automation":       "cc_other",
    "program":          "program_change",
    "tempo":            "tempo_map",
    "time_sig":         "time_signatures",
    "key_sig":          "key_signatures",
    "markers":          "markers",
    "track_structure":  "track_structure",
}

#: All valid names (aliases + internal) → internal bucket.
_CANONICAL: dict[str, str] = {**DIM_ALIAS, **{d: d for d in INTERNAL_DIMS}}

#: CC number → internal dimension name for named controllers.
_CC_DIM: dict[int, str] = {
    1:  "cc_modulation",
    7:  "cc_volume",
    10: "cc_pan",
    11: "cc_expression",
    64: "cc_sustain",
    65: "cc_portamento",
    66: "cc_sostenuto",
    67: "cc_soft_pedal",
    91: "cc_reverb",
    93: "cc_chorus",
}


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
    """All dimension slices extracted from one MIDI file.

    ``slices`` maps internal dimension name → :class:`DimensionSlice`.
    Every internal dimension in :data:`INTERNAL_DIMS` has an entry, even if
    the corresponding event list is empty (hash of empty list is stable).
    """

    ticks_per_beat: int
    file_type: int
    slices: dict[str, DimensionSlice]

    def get(self, dim: str) -> DimensionSlice:
        """Return the slice for a user-facing or internal dimension name."""
        internal = _CANONICAL.get(dim, dim)
        return self.slices[internal]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _classify_event(msg: mido.Message) -> str | None:
    """Map a mido Message to an internal dimension bucket.

    Returns ``None`` for events that should be excluded from all buckets
    (e.g. ``end_of_track`` is handled during reconstruction, not stored here).
    Unknown messages that are meta events fall back to ``"track_structure"``.
    True unknowns (no ``is_meta`` attribute) are discarded.
    """
    t = msg.type

    # --- Note events ---
    if t in ("note_on", "note_off"):
        return "notes"

    # --- Pitch / pressure ---
    if t == "pitchwheel":
        return "pitch_bend"
    if t == "aftertouch":
        return "channel_pressure"
    if t == "polytouch":
        return "poly_pressure"

    # --- CC — split by controller number ---
    if t == "control_change":
        return _CC_DIM.get(msg.control, "cc_other")

    # --- Program change ---
    if t == "program_change":
        return "program_change"

    # --- Timeline metadata ---
    if t == "set_tempo":
        return "tempo_map"
    if t == "time_signature":
        return "time_signatures"
    if t == "key_signature":
        return "key_signatures"

    # --- Section markers and text annotations ---
    if t in ("marker", "cue_marker", "text", "lyrics", "copyright"):
        return "markers"

    # --- Track structure and routing ---
    if t in ("track_name", "instrument_name", "sysex", "sequencer_specific"):
        return "track_structure"

    # --- End-of-track is reconstructed, not stored ---
    if t == "end_of_track":
        return None

    # --- Unknown meta events → track structure (safe default) ---
    if getattr(msg, "is_meta", False):
        return "track_structure"

    return None


type _MsgVal = int | str | list[int]


def _msg_to_dict(msg: mido.Message) -> dict[str, _MsgVal]:
    """Serialise a mido Message to a JSON-compatible dict."""
    from muse.core.validation import MAX_SYSEX_BYTES

    d: dict[str, _MsgVal] = {"type": msg.type}
    for attr in (
        "channel", "note", "velocity", "control", "value",
        "pitch", "program", "numerator", "denominator",
        "clocks_per_click", "notated_32nd_notes_per_beat",
        "tempo", "key", "scale", "text", "data",
    ):
        if hasattr(msg, attr):
            raw = getattr(msg, attr)
            if isinstance(raw, (bytes, bytearray)):
                # Cap sysex / large byte payloads to prevent memory exhaustion
                # when a crafted MIDI contains a giant sysex blob.
                if len(raw) > MAX_SYSEX_BYTES:
                    logger.warning(
                        "⚠️ Sysex payload %d bytes exceeds cap (%d) — truncating",
                        len(raw), MAX_SYSEX_BYTES,
                    )
                    raw = raw[:MAX_SYSEX_BYTES]
                d[attr] = list(raw)
            elif isinstance(raw, str):
                d[attr] = raw
            elif isinstance(raw, int):
                d[attr] = raw
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

    Every event type in the MIDI spec maps to exactly one of the
    :data:`INTERNAL_DIMS` buckets.  Empty buckets are present with an empty
    event list so that callers can always index by dimension name.

    Args:
        midi_bytes: Raw bytes of a ``.mid`` file.

    Returns:
        A :class:`MidiDimensions` with one :class:`DimensionSlice` per
        internal dimension.  Events within each slice are sorted by ascending
        absolute tick, then by event type for determinism when multiple events
        share the same tick.

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

    for dim in INTERNAL_DIMS:
        buckets[dim].sort(key=lambda x: (x[0], x[1].type))

    slices = {
        dim: DimensionSlice(name=dim, events=events)
        for dim, events in buckets.items()
    }
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

    This is used by :func:`merge_midi_dimensions` and surfaced in
    ``muse merge`` output for human-readable conflict diagnostics.
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
        new_msg = msg.copy(time=delta)
        track.append(new_msg)
        prev_tick = abs_tick
    if not track or track[-1].type != "end_of_track":
        track.append(mido.MetaMessage("end_of_track", time=0))
    return track


def _reconstruct(
    ticks_per_beat: int,
    winning_slices: dict[str, list[tuple[int, mido.Message]]],
) -> bytes:
    """Build a type-0 MIDI file from winning dimension event lists.

    All dimension events are merged into a single track (type-0) for
    maximum compatibility.  The absolute-tick ordering is preserved and
    duplicate end_of_track messages are removed.
    """
    all_events: list[tuple[int, mido.Message]] = []
    for events in winning_slices.values():
        all_events.extend(events)

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

    For each internal dimension (all 21 of them):

    - If neither side changed → keep base.
    - If only one side changed → take that side (clean auto-merge).
    - If both sides changed → consult ``.museattributes`` strategy:

      * ``ours`` / ``theirs`` → take the specified side; record in report.
      * ``manual`` / ``auto`` / ``union`` → unresolvable; return ``None``.

    Non-independent dimensions (``tempo_map``, ``time_signatures``,
    ``track_structure``) that have bilateral conflicts cause an immediate
    ``None`` return — their conflicts cannot be auto-resolved because they
    affect the semantic meaning of all other dimensions.

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
        Only dimensions with non-empty event lists or conflicts are included.

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
            if base_dims.slices[dim].events:
                dimension_report[dim] = "base"

        elif change == "left_only":
            winning_slices[dim] = left_dims.slices[dim].events
            dimension_report[dim] = "left"

        elif change == "right_only":
            winning_slices[dim] = right_dims.slices[dim].events
            dimension_report[dim] = "right"

        else:
            # Both sides changed — resolve via .museattributes strategy.
            # Look up by user-facing aliases first, then internal name.
            user_dim_names = [k for k, v in DIM_ALIAS.items() if v == dim]
            user_dim_names.append(dim)  # internal name is also a valid alias

            strategy = "auto"
            for user_dim in user_dim_names:
                s = resolve_strategy(attrs_rules, path, user_dim)
                if s != "auto":
                    strategy = s
                    break
            if strategy == "auto":
                strategy = resolve_strategy(attrs_rules, path, "*")

            if strategy == "ours":
                winning_slices[dim] = left_dims.slices[dim].events
                dimension_report[dim] = f"ours ({dim})"
            elif strategy == "theirs":
                winning_slices[dim] = right_dims.slices[dim].events
                dimension_report[dim] = f"theirs ({dim})"
            else:
                # Unresolvable conflict.  Non-independent dims fail fast.
                return None

    merged_bytes = _reconstruct(base_dims.ticks_per_beat, winning_slices)
    return merged_bytes, dimension_report

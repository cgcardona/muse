"""Hierarchical chunk manifests for the Muse music plugin.

Evolves the flat ``{"files": {"track.mid": "<sha256>"}}`` snapshot beyond
a single content hash per file to a rich, per-bar, per-track manifest that
enables:

- **Partial diff** — compare only the bars that changed.
- **Query acceleration** — answer note queries without reading full MIDI blobs.
- **Targeted merge** — attempt to merge only the bars with conflicts.
- **Historical analytics** — aggregate statistics over commit history without
  re-parsing MIDI bytes on every query.

Backward compatibility
----------------------
The ``MusicManifest.files`` field is identical to the standard
``SnapshotManifest.files`` so that the core Muse engine and all existing
commands continue to work unchanged.  The ``tracks`` field is additive
metadata stored as a sidecar under ``.muse/music_manifests/`` — never
replacing the canonical flat manifest.

Storage layout::

    .muse/music_manifests/
        <snapshot_id>.json     — full MusicManifest for this snapshot
        (rebuildable from history; add to .museignore in CI)

Public API
----------
- :class:`BarChunk`         — per-bar chunk descriptor.
- :class:`TrackManifest`    — rich metadata for one MIDI track.
- :class:`MusicManifest`    — top-level sidecar manifest.
- :func:`build_bar_chunk`   — build a :class:`BarChunk` from a bar's notes.
- :func:`build_track_manifest` — build a :class:`TrackManifest`.
- :func:`build_music_manifest` — build the full :class:`MusicManifest`.
- :func:`write_music_manifest` — persist to ``.muse/music_manifests/``.
- :func:`read_music_manifest`  — load from the sidecar store.
"""

import hashlib
import json
import logging
import pathlib
from typing import Literal, TypedDict

from muse.plugins.midi._query import (
    NoteInfo,
    detect_chord,
    key_signature_guess,
    notes_by_bar,
)
from muse.plugins.midi.midi_diff import extract_notes

logger = logging.getLogger(__name__)

_MANIFEST_DIR = ".muse/music_manifests"


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


class BarChunk(TypedDict):
    """Descriptor for one bar's worth of note events in a MIDI track.

    ``bar``         1-indexed bar number (assumes 4/4 time).
    ``chunk_hash``  SHA-256 of the canonical JSON of all notes in this bar.
                    Used for per-bar change detection without re-parsing MIDI.
    ``note_count``  Number of notes in this bar.
    ``chord``       Best-guess chord name for this bar (e.g. ``"Cmaj"``).
    ``pitch_range`` ``[min_pitch, max_pitch]`` MIDI pitch values in this bar.
    """

    bar: int
    chunk_hash: str
    note_count: int
    chord: str
    pitch_range: list[int]


class TrackManifest(TypedDict):
    """Rich metadata descriptor for one MIDI track at a specific snapshot.

    ``track_id``     Stable identifier for this track (SHA-256 of file path).
                     Stable across renames if you track by content; changes
                     on rename.  Use entity IDs in the entity index for
                     true cross-rename continuity.
    ``file_path``    Workspace-relative MIDI file path.
    ``content_hash`` SHA-256 of the full MIDI file bytes (same as the flat
                     manifest entry — the canonical content address).
    ``bars``         Mapping from ``str(bar_number)`` → :class:`BarChunk`.
                     JSON keys are always strings; callers convert to int.
    ``ticks_per_beat`` MIDI ticks per quarter note for this file.
    ``note_count``   Total note count across all bars.
    ``key_guess``    Krumhansl-Schmuckler key estimate (e.g. ``"G major"``).
    ``bar_count``    Number of bars with at least one note.
    """

    track_id: str
    file_path: str
    content_hash: str
    bars: dict[str, BarChunk]
    ticks_per_beat: int
    note_count: int
    key_guess: str
    bar_count: int


class MusicManifest(TypedDict):
    """Top-level hierarchical manifest for a music snapshot.

    This is the sidecar companion to the standard :class:`~muse.domain.SnapshotManifest`.
    The ``files`` field is identical to the flat manifest — the core engine
    reads only ``files`` for content addressing.  The ``tracks`` field is
    additive richness for music-domain queries, diff, and merge.

    ``schema_version``  Always ``2`` for this format.
    ``snapshot_id``     The snapshot this manifest belongs to.
    ``files``           Standard flat ``{path: sha256}`` manifest (compat layer).
    ``tracks``          ``{path: TrackManifest}`` for each MIDI file.
    """

    domain: Literal["midi"]
    schema_version: Literal[2]
    snapshot_id: str
    files: dict[str, str]
    tracks: dict[str, TrackManifest]


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _bar_chunk_hash(notes: list[NoteInfo]) -> str:
    """Return a SHA-256 of the canonical JSON of a bar's notes."""
    payload = json.dumps(
        [
            {
                "pitch": n.pitch,
                "velocity": n.velocity,
                "start_tick": n.start_tick,
                "duration_ticks": n.duration_ticks,
                "channel": n.channel,
            }
            for n in sorted(notes, key=lambda n: (n.start_tick, n.pitch))
        ],
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def build_bar_chunk(bar_num: int, notes: list[NoteInfo]) -> BarChunk:
    """Build a :class:`BarChunk` descriptor for *bar_num*.

    Args:
        bar_num: 1-indexed bar number.
        notes:   All notes in this bar.

    Returns:
        A populated :class:`BarChunk`.
    """
    pcs = frozenset(n.pitch_class for n in notes)
    chord = detect_chord(pcs)
    pitches = [n.pitch for n in notes]
    pitch_range: list[int] = [min(pitches), max(pitches)] if pitches else [0, 0]
    return BarChunk(
        bar=bar_num,
        chunk_hash=_bar_chunk_hash(notes),
        note_count=len(notes),
        chord=chord,
        pitch_range=pitch_range,
    )


def build_track_manifest(
    notes: list[NoteInfo],
    file_path: str,
    content_hash: str,
    ticks_per_beat: int,
) -> TrackManifest:
    """Build a :class:`TrackManifest` from a parsed note list.

    Args:
        notes:         All notes extracted from the MIDI file.
        file_path:     Workspace-relative MIDI file path.
        content_hash:  SHA-256 of the MIDI file bytes (from the flat manifest).
        ticks_per_beat: MIDI timing resolution.

    Returns:
        A populated :class:`TrackManifest`.
    """
    track_id = hashlib.sha256(file_path.encode()).hexdigest()
    bars_map = notes_by_bar(notes)
    bars: dict[str, BarChunk] = {}
    for bar_num, bar_notes in sorted(bars_map.items()):
        bars[str(bar_num)] = build_bar_chunk(bar_num, bar_notes)

    key_guess = key_signature_guess(notes)

    return TrackManifest(
        track_id=track_id,
        file_path=file_path,
        content_hash=content_hash,
        bars=bars,
        ticks_per_beat=ticks_per_beat,
        note_count=len(notes),
        key_guess=key_guess,
        bar_count=len(bars),
    )


def build_music_manifest(
    file_manifest: dict[str, str],
    root: "pathlib.Path",
    snapshot_id: str = "",
) -> MusicManifest:
    """Build a :class:`MusicManifest` from a flat file manifest.

    For each ``.mid`` file in *file_manifest*, loads the MIDI bytes from the
    object store, parses notes, and builds a :class:`TrackManifest`.

    Non-MIDI files appear only in ``files`` — they have no ``TrackManifest``.

    Args:
        file_manifest: Standard ``{path: sha256}`` manifest.
        root:          Repository root for object store access.
        snapshot_id:   The snapshot ID this manifest belongs to.

    Returns:
        A populated :class:`MusicManifest`.  Tracks whose MIDI bytes are
        unreadable or unparseable are silently omitted from ``tracks`` but
        still appear in ``files``.
    """
    import pathlib as _pathlib
    from muse.core.object_store import read_object

    tracks: dict[str, TrackManifest] = {}

    for path, content_hash in sorted(file_manifest.items()):
        if not path.lower().endswith(".mid"):
            continue
        raw = read_object(root, content_hash)
        if raw is None:
            logger.debug("⚠️ Object %s for %r not in store — skipping manifest", content_hash[:8], path)
            continue
        try:
            keys, tpb = extract_notes(raw)
        except ValueError as exc:
            logger.debug("⚠️ Cannot parse MIDI %r: %s — skipping manifest", path, exc)
            continue
        notes = [NoteInfo.from_note_key(k, tpb) for k in keys]
        tracks[path] = build_track_manifest(notes, path, content_hash, tpb)

    return MusicManifest(
        domain="midi",
        schema_version=2,
        snapshot_id=snapshot_id,
        files=dict(file_manifest),
        tracks=tracks,
    )


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def _manifest_path(repo_root: pathlib.Path, snapshot_id: str) -> pathlib.Path:
    return repo_root / _MANIFEST_DIR / f"{snapshot_id}.json"


def write_music_manifest(
    repo_root: pathlib.Path,
    manifest: MusicManifest,
) -> pathlib.Path:
    """Persist *manifest* to ``.muse/music_manifests/<snapshot_id>.json``.

    Args:
        repo_root: Repository root.
        manifest:  The manifest to write.

    Returns:
        Path to the written file.
    """
    snapshot_id = manifest.get("snapshot_id", "")
    if not snapshot_id:
        raise ValueError("MusicManifest.snapshot_id must be non-empty")
    path = _manifest_path(repo_root, snapshot_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2) + "\n")
    logger.debug(
        "✅ Music manifest written: %s (%d tracks)",
        snapshot_id[:8],
        len(manifest["tracks"]),
    )
    return path


def read_music_manifest(
    repo_root: pathlib.Path,
    snapshot_id: str,
) -> MusicManifest | None:
    """Load the music manifest for *snapshot_id*, or ``None`` if absent.

    Args:
        repo_root:   Repository root.
        snapshot_id: Snapshot ID.

    Returns:
        The :class:`MusicManifest`, or ``None`` when the sidecar file does
        not exist.
    """
    path = _manifest_path(repo_root, snapshot_id)
    if not path.exists():
        return None
    try:
        raw: MusicManifest = json.loads(path.read_text())
        return raw
    except (json.JSONDecodeError, KeyError) as exc:
        logger.warning("⚠️ Corrupt music manifest %s: %s", path, exc)
        return None


# ---------------------------------------------------------------------------
# Partial diff helper
# ---------------------------------------------------------------------------


def diff_manifests_by_bar(
    base: MusicManifest,
    target: MusicManifest,
) -> dict[str, list[int]]:
    """Return a per-track list of bars that changed between two manifests.

    Uses the per-bar ``chunk_hash`` values to detect changes without
    loading any MIDI bytes.

    Args:
        base:   Ancestor manifest.
        target: Newer manifest.

    Returns:
        ``{track_path: [changed_bar_numbers]}`` for all tracks where at
        least one bar differs.  Tracks added or removed appear with ``[-1]``
        as a sentinel indicating the whole track changed.
    """
    changed: dict[str, list[int]] = {}

    all_tracks = set(base["tracks"]) | set(target["tracks"])

    for track in sorted(all_tracks):
        base_track = base["tracks"].get(track)
        target_track = target["tracks"].get(track)

        if base_track is None or target_track is None:
            changed[track] = [-1]
            continue

        if base_track["content_hash"] == target_track["content_hash"]:
            continue

        # Content changed — find which bars.
        base_bars = base_track["bars"]
        target_bars = target_track["bars"]
        all_bar_keys = set(base_bars) | set(target_bars)

        changed_bars: list[int] = []
        for bar_key in sorted(all_bar_keys, key=lambda k: int(k)):
            base_chunk = base_bars.get(bar_key)
            target_chunk = target_bars.get(bar_key)
            if base_chunk is None or target_chunk is None:
                changed_bars.append(int(bar_key))
            elif base_chunk["chunk_hash"] != target_chunk["chunk_hash"]:
                changed_bars.append(int(bar_key))

        if changed_bars:
            changed[track] = sorted(changed_bars)

    return changed

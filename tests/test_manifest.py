"""Tests for muse.plugins.midi.manifest — BarChunk, TrackManifest, MusicManifest."""

import io
import pathlib

import mido
import pytest

from muse._version import __version__
from muse.plugins.midi._query import NoteInfo
from muse.plugins.midi.manifest import (
    BarChunk,
    MusicManifest,
    TrackManifest,
    build_bar_chunk,
    build_music_manifest,
    build_track_manifest,
    diff_manifests_by_bar,
    read_music_manifest,
    write_music_manifest,
)
from muse.plugins.midi.midi_diff import NoteKey


def _note(pitch: int, start_tick: int = 0, duration_ticks: int = 480,
          velocity: int = 80, channel: int = 0) -> NoteInfo:
    return NoteInfo.from_note_key(
        NoteKey(pitch=pitch, velocity=velocity, start_tick=start_tick,
                duration_ticks=duration_ticks, channel=channel),
        ticks_per_beat=480,
    )


def _build_midi_bytes(notes: list[tuple[int, int, int]], ticks_per_beat: int = 480) -> bytes:
    """Build a minimal MIDI file from (pitch, start_tick, duration_ticks) tuples."""
    events: list[tuple[int, mido.Message]] = []
    for pitch, start, dur in notes:
        events.append((start, mido.Message("note_on", note=pitch, velocity=80, channel=0, time=0)))
        events.append((start + dur, mido.Message("note_off", note=pitch, velocity=0, channel=0, time=0)))
    events.sort(key=lambda e: (e[0], e[1].type))

    track = mido.MidiTrack()
    prev = 0
    for abs_tick, msg in events:
        track.append(msg.copy(time=abs_tick - prev))
        prev = abs_tick
    track.append(mido.MetaMessage("end_of_track", time=0))

    mid = mido.MidiFile(type=0, ticks_per_beat=ticks_per_beat)
    mid.tracks.append(track)
    buf = io.BytesIO()
    mid.save(file=buf)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# build_bar_chunk
# ---------------------------------------------------------------------------


class TestBuildBarChunk:
    def test_fields_populated(self) -> None:
        notes = [_note(60), _note(64), _note(67)]
        chunk = build_bar_chunk(1, notes)
        assert chunk["bar"] == 1
        assert chunk["note_count"] == 3
        assert isinstance(chunk["chunk_hash"], str)
        assert len(chunk["chunk_hash"]) == 64

    def test_pitch_range_correct(self) -> None:
        notes = [_note(60), _note(72), _note(55)]
        chunk = build_bar_chunk(1, notes)
        assert chunk["pitch_range"] == [55, 72]

    def test_same_notes_same_hash(self) -> None:
        notes = [_note(60), _note(64)]
        c1 = build_bar_chunk(1, notes)
        c2 = build_bar_chunk(1, notes)
        assert c1["chunk_hash"] == c2["chunk_hash"]

    def test_different_notes_different_hash(self) -> None:
        c1 = build_bar_chunk(1, [_note(60)])
        c2 = build_bar_chunk(1, [_note(62)])
        assert c1["chunk_hash"] != c2["chunk_hash"]

    def test_empty_bar_has_pitch_range_zero(self) -> None:
        chunk = build_bar_chunk(1, [])
        assert chunk["pitch_range"] == [0, 0]
        assert chunk["note_count"] == 0


# ---------------------------------------------------------------------------
# build_track_manifest
# ---------------------------------------------------------------------------


class TestBuildTrackManifest:
    def test_basic_fields(self) -> None:
        notes = [_note(60), _note(64), _note(67)]
        tm = build_track_manifest(notes, "piano.mid", "abc123", 480)
        assert tm["file_path"] == "piano.mid"
        assert tm["content_hash"] == "abc123"
        assert tm["ticks_per_beat"] == 480
        assert tm["note_count"] == 3
        assert isinstance(tm["track_id"], str)

    def test_bars_dict_has_string_keys(self) -> None:
        notes = [_note(60)]
        tm = build_track_manifest(notes, "t.mid", "h1", 480)
        for key in tm["bars"].keys():
            assert isinstance(key, str)

    def test_bar_count_matches_unique_bars(self) -> None:
        # Two notes in bar 1, two in bar 2.
        tpb = 480
        bar_ticks = tpb * 4
        notes = [
            _note(60, start_tick=0),
            _note(64, start_tick=tpb),
            _note(60, start_tick=bar_ticks),
            _note(67, start_tick=bar_ticks + tpb),
        ]
        tm = build_track_manifest(notes, "t.mid", "h1", 480)
        assert tm["bar_count"] == 2

    def test_key_guess_is_string(self) -> None:
        notes = [_note(60), _note(62), _note(64), _note(65), _note(67)]
        tm = build_track_manifest(notes, "t.mid", "h1", 480)
        assert isinstance(tm["key_guess"], str)
        assert len(tm["key_guess"]) > 0


# ---------------------------------------------------------------------------
# MusicManifest I/O
# ---------------------------------------------------------------------------


class TestMusicManifestIO:
    def _make_manifest(self, tmp_path: pathlib.Path) -> MusicManifest:
        notes = [_note(60), _note(64)]
        track_manifest = build_track_manifest(notes, "t.mid", "fakehash123", 480)
        return MusicManifest(
            domain="midi",
            schema_version=__version__,
            snapshot_id="snap-abc123",
            files={"t.mid": "fakehash123"},
            tracks={"t.mid": track_manifest},
        )

    def test_write_and_read_roundtrip(self, tmp_path: pathlib.Path) -> None:
        manifest = self._make_manifest(tmp_path)
        write_music_manifest(tmp_path, manifest)
        recovered = read_music_manifest(tmp_path, "snap-abc123")
        assert recovered is not None
        assert recovered["snapshot_id"] == "snap-abc123"
        assert "t.mid" in recovered["tracks"]

    def test_read_missing_returns_none(self, tmp_path: pathlib.Path) -> None:
        result = read_music_manifest(tmp_path, "nonexistent-snap")
        assert result is None

    def test_write_requires_snapshot_id(self, tmp_path: pathlib.Path) -> None:
        manifest = MusicManifest(
            domain="midi",
            schema_version=__version__,
            snapshot_id="",
            files={},
            tracks={},
        )
        with pytest.raises(ValueError, match="snapshot_id"):
            write_music_manifest(tmp_path, manifest)


# ---------------------------------------------------------------------------
# diff_manifests_by_bar
# ---------------------------------------------------------------------------


class TestDiffManifestsByBar:
    def _make_pair_manifests(self) -> tuple[MusicManifest, MusicManifest]:
        tpb = 480
        bar_ticks = tpb * 4
        notes1 = [_note(60, start_tick=0), _note(64, start_tick=bar_ticks)]
        notes2 = [_note(60, start_tick=0), _note(67, start_tick=bar_ticks)]  # bar 2 differs

        tm1 = build_track_manifest(notes1, "t.mid", "hash1", tpb)
        tm2 = build_track_manifest(notes2, "t.mid", "hash2", tpb)

        base = MusicManifest(domain="midi", schema_version=__version__, snapshot_id="1" * 64,
                              files={"t.mid": "hash1"}, tracks={"t.mid": tm1})
        target = MusicManifest(domain="midi", schema_version=__version__, snapshot_id="s2",
                                files={"t.mid": "hash2"}, tracks={"t.mid": tm2})
        return base, target

    def test_no_change_produces_empty_result(self) -> None:
        notes = [_note(60)]
        tm = build_track_manifest(notes, "t.mid", "hash1", 480)
        base = MusicManifest(domain="midi", schema_version=__version__, snapshot_id="1" * 64,
                              files={"t.mid": "hash1"}, tracks={"t.mid": tm})
        changed = diff_manifests_by_bar(base, base)
        assert changed == {}

    def test_changed_bar_detected(self) -> None:
        base, target = self._make_pair_manifests()
        changed = diff_manifests_by_bar(base, target)
        assert "t.mid" in changed
        # Bar 2 changed.
        assert 2 in changed["t.mid"]

    def test_unchanged_bar_not_in_changed(self) -> None:
        base, target = self._make_pair_manifests()
        changed = diff_manifests_by_bar(base, target)
        # Bar 1 is unchanged.
        if "t.mid" in changed:
            assert 1 not in changed["t.mid"]

    def test_added_track_reported_with_sentinel(self) -> None:
        notes = [_note(60)]
        tm = build_track_manifest(notes, "new.mid", "hashN", 480)
        base = MusicManifest(domain="midi", schema_version=__version__, snapshot_id="1" * 64,
                              files={}, tracks={})
        target = MusicManifest(domain="midi", schema_version=__version__, snapshot_id="s2",
                                files={"new.mid": "hashN"}, tracks={"new.mid": tm})
        changed = diff_manifests_by_bar(base, target)
        assert "new.mid" in changed
        assert changed["new.mid"] == [-1]

"""muse mix — merge notes from two MIDI tracks into a single output track.

Reads two MIDI files, combines their note sequences, sorts by time, and
writes the result to an output path.  Timing collisions are preserved —
if both tracks have notes at the same tick, both appear in the output.

This is the music-domain equivalent of ``muse mix``: a compositional
assembly operation that an AI agent can use to layer tracks without
creating a merge conflict.

Usage::

    muse mix tracks/melody.mid tracks/harmony.mid --output tracks/full.mid
    muse mix tracks/piano.mid tracks/strings.mid --output tracks/ensemble.mid
    muse mix tracks/drums.mid tracks/bass.mid --output tracks/rhythm.mid --dry-run

Output::

    ✅ Mixed tracks/melody.mid + tracks/harmony.mid → tracks/full.mid
       melody.mid:   23 notes  (C3–G5)
       harmony.mid:  18 notes  (C2–B4)
       full.mid:     41 notes  (C2–G5)
       Run `muse status` to review, then `muse commit`
"""

from __future__ import annotations

import json
import logging
import pathlib

import typer

from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.plugins.midi._query import (
    NoteInfo,
    load_track_from_workdir,
    notes_to_midi_bytes,
)
from muse.plugins.midi.midi_diff import _pitch_name

logger = logging.getLogger(__name__)

app = typer.Typer()


@app.callback(invoke_without_command=True)
def mix(
    ctx: typer.Context,
    track_a: str = typer.Argument(..., metavar="TRACK-A", help="First source .mid file."),
    track_b: str = typer.Argument(..., metavar="TRACK-B", help="Second source .mid file."),
    output: str = typer.Option(
        ..., "--output", "-o", metavar="OUTPUT",
        help="Destination .mid file path (workspace-relative).",
    ),
    channel_a: int | None = typer.Option(
        None, "--channel-a", metavar="N",
        help="Remap all notes from TRACK-A to this MIDI channel.",
    ),
    channel_b: int | None = typer.Option(
        None, "--channel-b", metavar="N",
        help="Remap all notes from TRACK-B to this MIDI channel.",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", "-n",
        help="Preview the operation without writing to disk.",
    ),
) -> None:
    """Combine notes from two MIDI tracks into a single output track.

    ``muse mix`` reads two MIDI files, merges their note sequences sorted
    by start tick, and writes the result to *--output*.  Both source
    files are preserved unchanged.

    Use ``--channel-a`` / ``--channel-b`` to assign distinct MIDI channels
    to each source so instruments can be differentiated in the output.

    This is a compositional assembly command for AI agents: layer a melody
    over a harmony, combine drums with bass, or stack multiple instrument
    parts — all without a merge conflict.  The structured delta captured
    on commit will record every note inserted into the output track.
    """
    root = require_repo()

    result_a = load_track_from_workdir(root, track_a)
    if result_a is None:
        typer.echo(f"❌ Track '{track_a}' not found or not a valid MIDI file.", err=True)
        raise typer.Exit(code=ExitCode.USER_ERROR)

    result_b = load_track_from_workdir(root, track_b)
    if result_b is None:
        typer.echo(f"❌ Track '{track_b}' not found or not a valid MIDI file.", err=True)
        raise typer.Exit(code=ExitCode.USER_ERROR)

    notes_a, tpb_a = result_a
    notes_b, tpb_b = result_b
    tpb = max(tpb_a, tpb_b)

    # Optionally remap channels.
    def _maybe_remap(notes: list[NoteInfo], channel: int | None) -> list[NoteInfo]:
        if channel is None:
            return notes
        return [
            NoteInfo(
                pitch=n.pitch, velocity=n.velocity,
                start_tick=n.start_tick, duration_ticks=n.duration_ticks,
                channel=channel, ticks_per_beat=n.ticks_per_beat,
            )
            for n in notes
        ]

    notes_a = _maybe_remap(notes_a, channel_a)
    notes_b = _maybe_remap(notes_b, channel_b)

    mixed = sorted(notes_a + notes_b, key=lambda n: (n.start_tick, n.pitch))

    # Stats.
    def _range_str(notes: list[NoteInfo]) -> str:
        if not notes:
            return "(empty)"
        lo = min(n.pitch for n in notes)
        hi = max(n.pitch for n in notes)
        return f"{_pitch_name(lo)}–{_pitch_name(hi)}"

    if dry_run:
        typer.echo(f"\n[dry-run] Would mix {track_a} + {track_b} → {output}")
        typer.echo(f"  {track_a}:  {len(notes_a)} notes  ({_range_str(notes_a)})")
        typer.echo(f"  {track_b}:  {len(notes_b)} notes  ({_range_str(notes_b)})")
        typer.echo(f"  {output}:   {len(mixed)} notes  ({_range_str(mixed)})")
        typer.echo("  No changes written (--dry-run).")
        return

    midi_bytes = notes_to_midi_bytes(mixed, tpb)

    out_path = root / "muse-work" / output
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if not (root / "muse-work").exists():
        out_path = root / output
        out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(midi_bytes)

    typer.echo(f"\n✅ Mixed {track_a} + {track_b} → {output}")
    typer.echo(f"   {track_a}:  {len(notes_a)} notes  ({_range_str(notes_a)})")
    typer.echo(f"   {track_b}:  {len(notes_b)} notes  ({_range_str(notes_b)})")
    typer.echo(f"   {output}:   {len(mixed)} notes  ({_range_str(mixed)})")
    typer.echo("   Run `muse status` to review, then `muse commit`")

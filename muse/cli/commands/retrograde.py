"""muse retrograde — reverse the note order of a MIDI track.

Plays the melody backward: the last note becomes the first.  A classical
transformation used in canon, fugue, and serial music.  Agents composing
palindromic or mirror structures can apply this automatically.

Usage::

    muse retrograde tracks/melody.mid
    muse retrograde tracks/melody.mid --dry-run

Output::

    ✅ Retrograded tracks/melody.mid
       23 notes reversed  (C4 → was last, now first)
       Duration preserved  ·  original span: 8.0 beats
       Run `muse status` to review, then `muse commit`
"""

from __future__ import annotations

import logging
import pathlib

import typer

from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.plugins.midi._query import NoteInfo, load_track_from_workdir, notes_to_midi_bytes
from muse.plugins.midi.midi_diff import _pitch_name

logger = logging.getLogger(__name__)
app = typer.Typer()


@app.callback(invoke_without_command=True)
def retrograde(
    ctx: typer.Context,
    track: str = typer.Argument(..., metavar="TRACK", help="Workspace-relative path to a .mid file."),
    dry_run: bool = typer.Option(False, "--dry-run", "-n", help="Preview without writing."),
) -> None:
    """Reverse the pitch order of all notes (retrograde transformation).

    ``muse retrograde`` maps note positions: the note that was at tick T from
    the end is placed at tick T from the start, so the last note plays first.
    Durations and velocities are preserved; only pitch and position are swapped.

    This is a foundational operation in serial/twelve-tone composition and is
    impossible to describe meaningfully in Git's binary-blob model.
    """
    root = require_repo()
    result = load_track_from_workdir(root, track)
    if result is None:
        typer.echo(f"❌ Track '{track}' not found or not a valid MIDI file.", err=True)
        raise typer.Exit(code=ExitCode.USER_ERROR)

    notes, tpb = result
    if not notes:
        typer.echo(f"  (track '{track}' contains no notes — nothing to retrograde)")
        return

    # Sort by start time to get the temporal order, then reverse pitches.
    by_time = sorted(notes, key=lambda n: n.start_tick)
    reversed_pitches = [n.pitch for n in reversed(by_time)]

    total_ticks = max(n.start_tick + n.duration_ticks for n in notes)
    retro: list[NoteInfo] = [
        NoteInfo(
            pitch=reversed_pitches[i],
            velocity=by_time[i].velocity,
            start_tick=by_time[i].start_tick,
            duration_ticks=by_time[i].duration_ticks,
            channel=by_time[i].channel,
            ticks_per_beat=by_time[i].ticks_per_beat,
        )
        for i in range(len(by_time))
    ]

    span_beats = total_ticks / max(tpb, 1)
    first_orig = _pitch_name(by_time[0].pitch)
    first_retro = _pitch_name(retro[0].pitch)

    if dry_run:
        typer.echo(f"\n[dry-run] Would retrograde {track}")
        typer.echo(f"  Notes:   {len(notes)}")
        typer.echo(f"  Was:     first note = {first_orig}")
        typer.echo(f"  Would:   first note = {first_retro}")
        typer.echo(f"  Span:    {span_beats:.2f} beats (unchanged)")
        typer.echo("  No changes written (--dry-run).")
        return

    midi_bytes = notes_to_midi_bytes(retro, tpb)
    work_path = root / "muse-work" / track
    if not work_path.parent.exists():
        work_path = root / track
    work_path.write_bytes(midi_bytes)

    typer.echo(f"\n✅ Retrograded {track}")
    typer.echo(f"   {len(retro)} notes reversed  ({first_orig} → was last, now first)")
    typer.echo(f"   Duration preserved  ·  original span: {span_beats:.2f} beats")
    typer.echo("   Run `muse status` to review, then `muse commit`")

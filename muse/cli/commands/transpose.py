"""muse transpose — transpose a MIDI track by N semitones.

Reads the MIDI file from the working tree, shifts every note's pitch by
the specified number of semitones, and writes the result back in-place.

This is a surgical agent command: the content hash changes (Muse treats the
transposed version as a distinct composition), but every note's timing and
velocity are preserved exactly.  Run ``muse status`` and ``muse commit`` to
record the transposition in the structured delta.

Usage::

    muse transpose tracks/melody.mid --semitones 2   # up a major second
    muse transpose tracks/bass.mid --semitones -7    # down a fifth
    muse transpose tracks/piano.mid --semitones 12   # up an octave
    muse transpose tracks/melody.mid --semitones 5 --dry-run

Output::

    ✅ Transposed tracks/melody.mid  +2 semitones
       23 notes shifted  (C4 → D4, G5 → A5, …)
       Pitch range: C3–A5  (was A2–G5)
       Run `muse status` to review, then `muse commit`
"""
from __future__ import annotations

import json
import logging
import pathlib

import typer

from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.plugins.music._query import (
    NoteInfo,
    load_track_from_workdir,
    notes_to_midi_bytes,
)
from muse.plugins.music.midi_diff import _pitch_name

logger = logging.getLogger(__name__)

app = typer.Typer()

_MIDI_MIN = 0
_MIDI_MAX = 127


@app.callback(invoke_without_command=True)
def transpose(
    ctx: typer.Context,
    track: str = typer.Argument(..., metavar="TRACK", help="Workspace-relative path to a .mid file."),
    semitones: int = typer.Option(
        ..., "--semitones", "-s", metavar="N",
        help="Number of semitones to shift (positive = up, negative = down).",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", "-n",
        help="Preview what would change without writing to disk.",
    ),
    clamp: bool = typer.Option(
        False, "--clamp",
        help="Clamp pitches to 0–127 instead of failing on out-of-range notes.",
    ),
) -> None:
    """Transpose all notes in a MIDI track by N semitones.

    ``muse transpose`` reads the MIDI file from the working tree, shifts
    every note's pitch by *--semitones*, and writes the result back.
    Timing and velocity are preserved exactly.

    After transposing, run ``muse status`` to see the structured delta
    (note-level insertions and deletions), then ``muse commit`` to record
    the transposition with full musical attribution.

    For AI agents: this is the equivalent of ``muse patch`` for music —
    a single command that applies a well-defined musical transformation
    without touching anything else.

    Use ``--dry-run`` to preview the operation without writing.
    Use ``--clamp`` to clip pitches to the valid MIDI range (0–127)
    instead of raising an error.
    """
    root = require_repo()

    result = load_track_from_workdir(root, track)
    if result is None:
        typer.echo(f"❌ Track '{track}' not found or not a valid MIDI file.", err=True)
        raise typer.Exit(code=ExitCode.USER_ERROR)

    original_notes, tpb = result

    if not original_notes:
        typer.echo(f"  (track '{track}' contains no notes — nothing to transpose)")
        return

    # Validate pitch range.
    new_pitches = [n.pitch + semitones for n in original_notes]
    out_of_range = [p for p in new_pitches if p < _MIDI_MIN or p > _MIDI_MAX]
    if out_of_range and not clamp:
        lo = min(out_of_range)
        hi = max(out_of_range)
        typer.echo(
            f"❌ Transposing by {semitones:+d} semitones would produce "
            f"out-of-range MIDI pitches ({lo}–{hi}).  "
            f"Use --clamp to clip to 0–127.",
            err=True,
        )
        raise typer.Exit(code=ExitCode.USER_ERROR)

    # Build transposed notes.
    transposed: list[NoteInfo] = []
    for note in original_notes:
        new_pitch = max(_MIDI_MIN, min(_MIDI_MAX, note.pitch + semitones))
        transposed.append(NoteInfo(
            pitch=new_pitch,
            velocity=note.velocity,
            start_tick=note.start_tick,
            duration_ticks=note.duration_ticks,
            channel=note.channel,
            ticks_per_beat=note.ticks_per_beat,
        ))

    old_lo = min(n.pitch for n in original_notes)
    old_hi = max(n.pitch for n in original_notes)
    new_lo = min(n.pitch for n in transposed)
    new_hi = max(n.pitch for n in transposed)

    sign = "+" if semitones >= 0 else ""
    sample_pairs = [
        f"{_pitch_name(original_notes[i].pitch)} → {_pitch_name(transposed[i].pitch)}"
        for i in range(min(3, len(original_notes)))
    ]

    if dry_run:
        typer.echo(f"\n[dry-run] Would transpose {track}  {sign}{semitones} semitones")
        typer.echo(f"  Notes:       {len(original_notes)}")
        typer.echo(f"  Shifts:      {', '.join(sample_pairs)}, …")
        typer.echo(f"  Pitch range: {_pitch_name(new_lo)}–{_pitch_name(new_hi)}  "
                   f"(was {_pitch_name(old_lo)}–{_pitch_name(old_hi)})")
        typer.echo("  No changes written (--dry-run).")
        return

    midi_bytes = notes_to_midi_bytes(transposed, tpb)

    # Write back to the working tree.
    work_path = root / "muse-work" / track
    if not work_path.parent.exists():
        work_path = root / track
    work_path.write_bytes(midi_bytes)

    typer.echo(f"\n✅ Transposed {track}  {sign}{semitones} semitones")
    typer.echo(f"   {len(transposed)} notes shifted  ({', '.join(sample_pairs)}, …)")
    typer.echo(f"   Pitch range: {_pitch_name(new_lo)}–{_pitch_name(new_hi)}"
               f"  (was {_pitch_name(old_lo)}–{_pitch_name(old_hi)})")
    typer.echo("   Run `muse status` to review, then `muse commit`")

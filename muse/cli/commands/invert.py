"""muse invert — melodic inversion (flip intervals around a pivot pitch).

Reflects every interval in the melody around a pivot pitch.  If the melody
goes up 2 semitones, the inversion goes down 2 semitones.  A classic
contrapuntal transformation — Bach used it in every fugue.  Agents exploring
the musical space around a theme can generate invertible counterpoint
automatically.

Usage::

    muse invert tracks/melody.mid
    muse invert tracks/melody.mid --pivot C4
    muse invert tracks/melody.mid --pivot 60 --dry-run

Pivot defaults to the first note of the track.

Output::

    ✅ Inverted tracks/melody.mid  (pivot: C4 / MIDI 60)
       23 notes transformed  (D4 → B3, E4 → A3, …)
       New range: G2–C5  (was C4–A5)
       Run `muse status` to review, then `muse commit`
"""

from __future__ import annotations

import logging
import pathlib

import typer

from muse.core.errors import ExitCode
from muse.core.validation import contain_path
from muse.core.repo import require_repo
from muse.plugins.midi._query import NoteInfo, load_track_from_workdir, notes_to_midi_bytes
from muse.plugins.midi.midi_diff import _pitch_name

logger = logging.getLogger(__name__)
app = typer.Typer()

_MIDI_MIN = 0
_MIDI_MAX = 127

_NOTE_NAMES: dict[str, int] = {
    "C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11,
}


def _parse_pivot(pivot_str: str) -> int | None:
    """Parse a pivot like 'C4', 'A#3', or '60' into a MIDI number."""
    pivot_str = pivot_str.strip()
    if pivot_str.isdigit():
        return int(pivot_str)
    if not pivot_str:
        return None
    note_letter = pivot_str[0].upper()
    if note_letter not in _NOTE_NAMES:
        return None
    rest = pivot_str[1:]
    sharp = rest.startswith("#")
    if sharp:
        rest = rest[1:]
    if not rest.lstrip("-").isdigit():
        return None
    octave = int(rest)
    return _NOTE_NAMES[note_letter] + (1 if sharp else 0) + (octave + 1) * 12


@app.callback(invoke_without_command=True)
def invert(
    ctx: typer.Context,
    track: str = typer.Argument(..., metavar="TRACK", help="Workspace-relative path to a .mid file."),
    pivot: str | None = typer.Option(
        None, "--pivot", "-p", metavar="PITCH",
        help="Pivot pitch as note name (C4, A#3) or MIDI number (0–127). Defaults to the first note.",
    ),
    clamp: bool = typer.Option(False, "--clamp", help="Clamp out-of-range pitches to 0–127."),
    dry_run: bool = typer.Option(False, "--dry-run", "-n", help="Preview without writing."),
) -> None:
    """Apply melodic inversion: reflect all intervals around a pivot pitch.

    ``muse invert`` transforms the melody so that upward intervals become
    downward and vice versa, mirrored around *--pivot*.  Timing, velocity,
    and duration are preserved exactly.

    In counterpoint and fugue, the inverted subject can be combined with
    the original to create invertible counterpoint.  In agent workflows,
    use this to auto-generate contrast material from an existing melody.
    """
    root = require_repo()
    result = load_track_from_workdir(root, track)
    if result is None:
        typer.echo(f"❌ Track '{track}' not found or not a valid MIDI file.", err=True)
        raise typer.Exit(code=ExitCode.USER_ERROR)

    notes, tpb = result
    if not notes:
        typer.echo(f"  (track '{track}' contains no notes — nothing to invert)")
        return

    # Determine pivot pitch
    if pivot is not None:
        pivot_midi = _parse_pivot(pivot)
        if pivot_midi is None:
            typer.echo(f"❌ Cannot parse pivot '{pivot}'. Use C4, A#3, or a MIDI number.", err=True)
            raise typer.Exit(code=ExitCode.USER_ERROR)
        if not 0 <= pivot_midi <= 127:
            typer.echo(f"❌ Pivot MIDI value {pivot_midi} is out of range [0, 127].", err=True)
            raise typer.Exit(code=ExitCode.USER_ERROR)
    else:
        pivot_midi = sorted(notes, key=lambda n: n.start_tick)[0].pitch

    inverted_pitches = [2 * pivot_midi - n.pitch for n in notes]
    out_of_range = [p for p in inverted_pitches if p < _MIDI_MIN or p > _MIDI_MAX]
    if out_of_range and not clamp:
        typer.echo(
            f"❌ Inversion around MIDI {pivot_midi} produces out-of-range pitches "
            f"({min(out_of_range)}–{max(out_of_range)}).  Use --clamp.",
            err=True,
        )
        raise typer.Exit(code=ExitCode.USER_ERROR)

    inverted: list[NoteInfo] = [
        NoteInfo(
            pitch=max(_MIDI_MIN, min(_MIDI_MAX, 2 * pivot_midi - n.pitch)),
            velocity=n.velocity,
            start_tick=n.start_tick,
            duration_ticks=n.duration_ticks,
            channel=n.channel,
            ticks_per_beat=n.ticks_per_beat,
        )
        for n in notes
    ]

    old_lo = min(n.pitch for n in notes)
    old_hi = max(n.pitch for n in notes)
    new_lo = min(n.pitch for n in inverted)
    new_hi = max(n.pitch for n in inverted)

    sorted_orig = sorted(notes, key=lambda n: n.start_tick)
    sorted_inv  = sorted(inverted, key=lambda n: n.start_tick)
    sample_pairs = [
        f"{_pitch_name(sorted_orig[i].pitch)} → {_pitch_name(sorted_inv[i].pitch)}"
        for i in range(min(3, len(sorted_orig)))
    ]

    if dry_run:
        typer.echo(f"\n[dry-run] Would invert {track}  (pivot: {_pitch_name(pivot_midi)} / MIDI {pivot_midi})")
        typer.echo(f"  Notes:      {len(notes)}")
        typer.echo(f"  Transforms: {', '.join(sample_pairs)}, …")
        typer.echo(f"  New range:  {_pitch_name(new_lo)}–{_pitch_name(new_hi)}  "
                   f"(was {_pitch_name(old_lo)}–{_pitch_name(old_hi)})")
        typer.echo("  No changes written (--dry-run).")
        return

    midi_bytes = notes_to_midi_bytes(inverted, tpb)
    workdir = root / "muse-work"
    try:
        work_path = contain_path(workdir, track)
    except ValueError as exc:
        typer.echo(f"❌ Invalid track path: {exc}")
        raise typer.Exit(code=ExitCode.USER_ERROR)
    work_path.parent.mkdir(parents=True, exist_ok=True)
    work_path.write_bytes(midi_bytes)

    typer.echo(f"\n✅ Inverted {track}  (pivot: {_pitch_name(pivot_midi)} / MIDI {pivot_midi})")
    typer.echo(f"   {len(inverted)} notes transformed  ({', '.join(sample_pairs)}, …)")
    typer.echo(f"   New range: {_pitch_name(new_lo)}–{_pitch_name(new_hi)}"
               f"  (was {_pitch_name(old_lo)}–{_pitch_name(old_hi)})")
    typer.echo("   Run `muse status` to review, then `muse commit`")

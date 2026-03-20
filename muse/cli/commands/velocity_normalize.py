"""muse normalize — normalize MIDI velocities to a target range.

Rescales all note velocities so the softest note maps to --min and the loudest
to --max.  Preserves the relative dynamics while adjusting the overall level.
Essential when merging tracks from multiple agents that were recorded at
different volumes.

Usage::

    muse normalize tracks/melody.mid
    muse normalize tracks/drums.mid --min 50 --max 110
    muse normalize tracks/piano.mid --target-mean 80
    muse normalize tracks/bass.mid --dry-run

Output::

    ✅ Normalised tracks/melody.mid
       48 notes rescaled  ·  range: 32–78 → 40–100
       Mean velocity: 61.3 → 72.0
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

logger = logging.getLogger(__name__)
app = typer.Typer()

_MIDI_MIN = 1
_MIDI_MAX = 127


def _rescale(velocity: int, src_lo: int, src_hi: int, dst_lo: int, dst_hi: int) -> int:
    if src_hi == src_lo:
        return (dst_lo + dst_hi) // 2
    ratio = (velocity - src_lo) / (src_hi - src_lo)
    return round(dst_lo + ratio * (dst_hi - dst_lo))


@app.callback(invoke_without_command=True)
def normalize(
    ctx: typer.Context,
    track: str = typer.Argument(..., metavar="TRACK", help="Workspace-relative path to a .mid file."),
    min_vel: int = typer.Option(
        40, "--min", metavar="VEL",
        help="Target minimum velocity (default 40).",
    ),
    max_vel: int = typer.Option(
        110, "--max", metavar="VEL",
        help="Target maximum velocity (default 110).",
    ),
    dry_run: bool = typer.Option(False, "--dry-run", "-n", help="Preview without writing."),
) -> None:
    """Rescale note velocities to a target dynamic range.

    ``muse normalize`` linearly maps the existing velocity range to
    [--min, --max], preserving the relative dynamic contour while adjusting
    the absolute level.  This is the standard first step when integrating
    tracks from multiple agents that were written at different volume levels.

    Use ``--min 64 --max 96`` for a narrow, compressed dynamic range;
    use ``--min 20 --max 127`` for the full MIDI spectrum.
    """
    if not _MIDI_MIN <= min_vel <= _MIDI_MAX:
        typer.echo(f"❌ --min must be between {_MIDI_MIN} and {_MIDI_MAX}.", err=True)
        raise typer.Exit(code=ExitCode.USER_ERROR)
    if not _MIDI_MIN <= max_vel <= _MIDI_MAX:
        typer.echo(f"❌ --max must be between {_MIDI_MIN} and {_MIDI_MAX}.", err=True)
        raise typer.Exit(code=ExitCode.USER_ERROR)
    if min_vel >= max_vel:
        typer.echo("❌ --min must be less than --max.", err=True)
        raise typer.Exit(code=ExitCode.USER_ERROR)

    root = require_repo()
    result = load_track_from_workdir(root, track)
    if result is None:
        typer.echo(f"❌ Track '{track}' not found or not a valid MIDI file.", err=True)
        raise typer.Exit(code=ExitCode.USER_ERROR)

    notes, tpb = result
    if not notes:
        typer.echo(f"  (track '{track}' contains no notes — nothing to normalise)")
        return

    vels = [n.velocity for n in notes]
    src_lo, src_hi = min(vels), max(vels)
    old_mean = sum(vels) / len(vels)

    normalised: list[NoteInfo] = [
        NoteInfo(
            pitch=n.pitch,
            velocity=_rescale(n.velocity, src_lo, src_hi, min_vel, max_vel),
            start_tick=n.start_tick,
            duration_ticks=n.duration_ticks,
            channel=n.channel,
            ticks_per_beat=n.ticks_per_beat,
        )
        for n in notes
    ]
    new_mean = sum(n.velocity for n in normalised) / len(normalised)

    if dry_run:
        typer.echo(f"\n[dry-run] Would normalise {track}")
        typer.echo(f"  Notes:         {len(notes)}")
        typer.echo(f"  Range:         {src_lo}–{src_hi} → {min_vel}–{max_vel}")
        typer.echo(f"  Mean velocity: {old_mean:.1f} → {new_mean:.1f}")
        typer.echo("  No changes written (--dry-run).")
        return

    midi_bytes = notes_to_midi_bytes(normalised, tpb)
    workdir = root
    try:
        work_path = contain_path(workdir, track)
    except ValueError as exc:
        typer.echo(f"❌ Invalid track path: {exc}")
        raise typer.Exit(code=ExitCode.USER_ERROR)
    work_path.parent.mkdir(parents=True, exist_ok=True)
    work_path.write_bytes(midi_bytes)

    typer.echo(f"\n✅ Normalised {track}")
    typer.echo(f"   {len(normalised)} notes rescaled  ·  range: {src_lo}–{src_hi} → {min_vel}–{max_vel}")
    typer.echo(f"   Mean velocity: {old_mean:.1f} → {new_mean:.1f}")
    typer.echo("   Run `muse status` to review, then `muse commit`")

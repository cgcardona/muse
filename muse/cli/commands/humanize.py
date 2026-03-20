"""muse humanize — add subtle timing and velocity variation to MIDI.

Applies controlled randomness to note onset times and velocities — giving
machine-quantised MIDI the feel of a human performance.  An indispensable
post-processing step when agent-generated music sounds too mechanical.

Usage::

    muse humanize tracks/piano.mid
    muse humanize tracks/drums.mid --timing 0.02 --velocity 8
    muse humanize tracks/melody.mid --seed 42
    muse humanize tracks/bass.mid --dry-run

Output::

    ✅ Humanised tracks/piano.mid
       64 notes adjusted
       Timing jitter: ±0.010 beats  ·  Velocity jitter: ±6
       Run `muse status` to review, then `muse commit`
"""

from __future__ import annotations

import logging
import pathlib
import random

import typer

from muse.core.errors import ExitCode
from muse.core.validation import contain_path
from muse.core.repo import require_repo
from muse.plugins.midi._query import NoteInfo, load_track_from_workdir, notes_to_midi_bytes

logger = logging.getLogger(__name__)
app = typer.Typer()

_MIDI_VEL_MAX = 127
_MIDI_VEL_MIN = 1


@app.callback(invoke_without_command=True)
def humanize(
    ctx: typer.Context,
    track: str = typer.Argument(..., metavar="TRACK", help="Workspace-relative path to a .mid file."),
    timing: float = typer.Option(
        0.01, "--timing", "-t", metavar="BEATS",
        help="Max timing jitter in beats (default 0.01 = 1% of a beat).",
    ),
    velocity: int = typer.Option(
        6, "--velocity", "-v", metavar="VEL",
        help="Max velocity jitter in MIDI units (default 6).",
    ),
    seed: int | None = typer.Option(
        None, "--seed", metavar="INT",
        help="Random seed for reproducible humanisation.",
    ),
    dry_run: bool = typer.Option(False, "--dry-run", "-n", help="Preview without writing."),
) -> None:
    """Add subtle timing and velocity variation to quantised MIDI.

    ``muse humanize`` applies small random perturbations drawn from a
    uniform distribution to each note's onset time and velocity.  The
    ``--timing`` amount is in beats; the ``--velocity`` amount is in raw
    MIDI units (0–127).

    Use ``--seed`` for reproducible results — important for CI pipelines
    that need deterministic audio output.  After humanising, commit with
    ``muse commit`` to record the transformation with full attribution.
    """
    if timing < 0:
        typer.echo("❌ --timing must be ≥ 0.", err=True)
        raise typer.Exit(code=ExitCode.USER_ERROR)
    if timing > 1.0:
        typer.echo("❌ --timing must be ≤ 1.0 beat (to prevent degenerate output).", err=True)
        raise typer.Exit(code=ExitCode.USER_ERROR)
    if velocity < 0:
        typer.echo("❌ --velocity must be ≥ 0.", err=True)
        raise typer.Exit(code=ExitCode.USER_ERROR)
    if velocity > 127:
        typer.echo("❌ --velocity must be ≤ 127 (MIDI max).", err=True)
        raise typer.Exit(code=ExitCode.USER_ERROR)

    root = require_repo()
    result = load_track_from_workdir(root, track)
    if result is None:
        typer.echo(f"❌ Track '{track}' not found or not a valid MIDI file.", err=True)
        raise typer.Exit(code=ExitCode.USER_ERROR)

    notes, tpb = result
    if not notes:
        typer.echo(f"  (track '{track}' contains no notes — nothing to humanise)")
        return

    rng = random.Random(seed)
    timing_ticks = int(timing * tpb)
    humanised: list[NoteInfo] = []

    for n in notes:
        tick_jitter = rng.randint(-timing_ticks, timing_ticks)
        vel_jitter = rng.randint(-velocity, velocity)
        new_tick = max(0, n.start_tick + tick_jitter)
        new_vel = max(_MIDI_VEL_MIN, min(_MIDI_VEL_MAX, n.velocity + vel_jitter))
        humanised.append(NoteInfo(
            pitch=n.pitch,
            velocity=new_vel,
            start_tick=new_tick,
            duration_ticks=n.duration_ticks,
            channel=n.channel,
            ticks_per_beat=n.ticks_per_beat,
        ))

    if dry_run:
        typer.echo(f"\n[dry-run] Would humanise {track}")
        typer.echo(f"  Notes:            {len(notes)}")
        typer.echo(f"  Timing jitter:    ±{timing} beats  (±{timing_ticks} ticks)")
        typer.echo(f"  Velocity jitter:  ±{velocity}")
        typer.echo(f"  Seed:             {seed!r}")
        typer.echo("  No changes written (--dry-run).")
        return

    midi_bytes = notes_to_midi_bytes(humanised, tpb)
    workdir = root
    try:
        work_path = contain_path(workdir, track)
    except ValueError as exc:
        typer.echo(f"❌ Invalid track path: {exc}")
        raise typer.Exit(code=ExitCode.USER_ERROR)
    work_path.parent.mkdir(parents=True, exist_ok=True)
    work_path.write_bytes(midi_bytes)

    typer.echo(f"\n✅ Humanised {track}")
    typer.echo(f"   {len(humanised)} notes adjusted")
    typer.echo(f"   Timing jitter: ±{timing} beats  ·  Velocity jitter: ±{velocity}")
    typer.echo("   Run `muse status` to review, then `muse commit`")

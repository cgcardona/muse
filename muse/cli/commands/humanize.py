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

import argparse
import logging
import pathlib
import random
import sys

from muse.core.errors import ExitCode
from muse.core.validation import contain_path
from muse.core.repo import require_repo
from muse.plugins.midi._query import NoteInfo, load_track_from_workdir, notes_to_midi_bytes

logger = logging.getLogger(__name__)

_MIDI_VEL_MAX = 127
_MIDI_VEL_MIN = 1


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the humanize subcommand."""
    parser = subparsers.add_parser("humanize", help="Add subtle timing and velocity variation to quantised MIDI.", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("track", metavar="TRACK", help="Workspace-relative path to a .mid file.")
    parser.add_argument("--timing", "-t", metavar="BEATS", type=float, default=0.01, help="Max timing jitter in beats (default 0.01 = 1%% of a beat).")
    parser.add_argument("--velocity", "-v", metavar="VEL", type=int, default=6, help="Max velocity jitter in MIDI units (default 6).")
    parser.add_argument("--seed", metavar="INT", type=int, default=None, help="Random seed for reproducible humanisation.")
    parser.add_argument("--dry-run", "-n", action="store_true", help="Preview without writing.")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> None:
    """Add subtle timing and velocity variation to quantised MIDI.

    ``muse humanize`` applies small random perturbations drawn from a
    uniform distribution to each note's onset time and velocity.  The
    ``--timing`` amount is in beats; the ``--velocity`` amount is in raw
    MIDI units (0–127).

    Use ``--seed`` for reproducible results — important for CI pipelines
    that need deterministic audio output.  After humanising, commit with
    ``muse commit`` to record the transformation with full attribution.
    """
    track: str = args.track
    timing: float = args.timing
    velocity: int = args.velocity
    seed: int | None = args.seed
    dry_run: bool = args.dry_run

    if timing < 0:
        print("❌ --timing must be ≥ 0.", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)
    if timing > 1.0:
        print("❌ --timing must be ≤ 1.0 beat (to prevent degenerate output).", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)
    if velocity < 0:
        print("❌ --velocity must be ≥ 0.", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)
    if velocity > 127:
        print("❌ --velocity must be ≤ 127 (MIDI max).", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)

    root = require_repo()
    result = load_track_from_workdir(root, track)
    if result is None:
        print(f"❌ Track '{track}' not found or not a valid MIDI file.", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)

    notes, tpb = result
    if not notes:
        print(f"  (track '{track}' contains no notes — nothing to humanise)")
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
        print(f"\n[dry-run] Would humanise {track}")
        print(f"  Notes:            {len(notes)}")
        print(f"  Timing jitter:    ±{timing} beats  (±{timing_ticks} ticks)")
        print(f"  Velocity jitter:  ±{velocity}")
        print(f"  Seed:             {seed!r}")
        print("  No changes written (--dry-run).")
        return

    midi_bytes = notes_to_midi_bytes(humanised, tpb)
    workdir = root
    try:
        work_path = contain_path(workdir, track)
    except ValueError as exc:
        print(f"❌ Invalid track path: {exc}")
        raise SystemExit(ExitCode.USER_ERROR)
    work_path.parent.mkdir(parents=True, exist_ok=True)
    work_path.write_bytes(midi_bytes)

    print(f"\n✅ Humanised {track}")
    print(f"   {len(humanised)} notes adjusted")
    print(f"   Timing jitter: ±{timing} beats  ·  Velocity jitter: ±{velocity}")
    print("   Run `muse status` to review, then `muse commit`")

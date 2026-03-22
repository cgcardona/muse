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

import argparse
import logging
import pathlib
import sys

from muse.core.errors import ExitCode
from muse.core.validation import contain_path
from muse.core.repo import require_repo
from muse.plugins.midi._query import NoteInfo, load_track_from_workdir, notes_to_midi_bytes

logger = logging.getLogger(__name__)

_MIDI_MIN = 1
_MIDI_MAX = 127


def _rescale(velocity: int, src_lo: int, src_hi: int, dst_lo: int, dst_hi: int) -> int:
    if src_hi == src_lo:
        return (dst_lo + dst_hi) // 2
    ratio = (velocity - src_lo) / (src_hi - src_lo)
    return round(dst_lo + ratio * (dst_hi - dst_lo))


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the normalize subcommand."""
    parser = subparsers.add_parser("normalize", help="Rescale note velocities to a target dynamic range.", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("track", metavar="TRACK", help="Workspace-relative path to a .mid file.")
    parser.add_argument("--min", metavar="VEL", type=int, default=40, dest="min_vel", help="Target minimum velocity (default 40).")
    parser.add_argument("--max", metavar="VEL", type=int, default=110, dest="max_vel", help="Target maximum velocity (default 110).")
    parser.add_argument("--dry-run", "-n", action="store_true", help="Preview without writing.")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> None:
    """Rescale note velocities to a target dynamic range.

    ``muse normalize`` linearly maps the existing velocity range to
    [--min, --max], preserving the relative dynamic contour while adjusting
    the absolute level.  This is the standard first step when integrating
    tracks from multiple agents that were written at different volume levels.

    Use ``--min 64 --max 96`` for a narrow, compressed dynamic range;
    use ``--min 20 --max 127`` for the full MIDI spectrum.
    """
    track: str = args.track
    min_vel: int = args.min_vel
    max_vel: int = args.max_vel
    dry_run: bool = args.dry_run

    if not _MIDI_MIN <= min_vel <= _MIDI_MAX:
        print(f"❌ --min must be between {_MIDI_MIN} and {_MIDI_MAX}.", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)
    if not _MIDI_MIN <= max_vel <= _MIDI_MAX:
        print(f"❌ --max must be between {_MIDI_MIN} and {_MIDI_MAX}.", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)
    if min_vel >= max_vel:
        print("❌ --min must be less than --max.", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)

    root = require_repo()
    result = load_track_from_workdir(root, track)
    if result is None:
        print(f"❌ Track '{track}' not found or not a valid MIDI file.", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)

    notes, tpb = result
    if not notes:
        print(f"  (track '{track}' contains no notes — nothing to normalise)")
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
        print(f"\n[dry-run] Would normalise {track}")
        print(f"  Notes:         {len(notes)}")
        print(f"  Range:         {src_lo}–{src_hi} → {min_vel}–{max_vel}")
        print(f"  Mean velocity: {old_mean:.1f} → {new_mean:.1f}")
        print("  No changes written (--dry-run).")
        return

    midi_bytes = notes_to_midi_bytes(normalised, tpb)
    workdir = root
    try:
        work_path = contain_path(workdir, track)
    except ValueError as exc:
        print(f"❌ Invalid track path: {exc}")
        raise SystemExit(ExitCode.USER_ERROR)
    work_path.parent.mkdir(parents=True, exist_ok=True)
    work_path.write_bytes(midi_bytes)

    print(f"\n✅ Normalised {track}")
    print(f"   {len(normalised)} notes rescaled  ·  range: {src_lo}–{src_hi} → {min_vel}–{max_vel}")
    print(f"   Mean velocity: {old_mean:.1f} → {new_mean:.1f}")
    print("   Run `muse status` to review, then `muse commit`")

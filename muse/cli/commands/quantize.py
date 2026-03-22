"""muse quantize — snap note onsets to a rhythmic grid.

Moves every note's start tick to the nearest multiple of the chosen
subdivision (16th, 8th, quarter, etc.).  Duration is preserved.  An
essential post-processing step after human-recorded or agent-generated
MIDI that needs to be grid-aligned before mixing.

Usage::

    muse quantize tracks/piano.mid --grid 16th
    muse quantize tracks/bass.mid --grid 8th --strength 0.5
    muse quantize tracks/melody.mid --dry-run
    muse quantize tracks/drums.mid --grid 32nd

Grid values: whole, half, quarter, 8th, 16th, 32nd, triplet-8th, triplet-16th

Output::

    ✅ Quantised tracks/piano.mid  →  16th-note grid
       64 notes adjusted  ·  avg shift: 14 ticks  ·  max shift: 58 ticks
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

_GRID_FRACTIONS: dict[str, float] = {
    "whole":        4.0,
    "half":         2.0,
    "quarter":      1.0,
    "8th":          0.5,
    "16th":         0.25,
    "32nd":         0.125,
    "triplet-8th":  1 / 3,
    "triplet-16th": 1 / 6,
}


def _grid_ticks(tpb: int, grid_name: str) -> int:
    fraction = _GRID_FRACTIONS.get(grid_name, 0.25)
    return max(1, round(tpb * fraction))


def _snap(tick: int, grid: int, strength: float) -> int:
    """Snap *tick* toward the nearest grid point with *strength* [0, 1]."""
    nearest = round(tick / grid) * grid
    return round(tick + (nearest - tick) * strength)


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the quantize subcommand."""
    parser = subparsers.add_parser("quantize", help="Snap note onsets to a rhythmic grid.", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("track", metavar="TRACK", help="Workspace-relative path to a .mid file.")
    parser.add_argument("--grid", "-g", metavar="GRID", default="16th", help="Quantisation grid: whole, half, quarter, 8th, 16th, 32nd, triplet-8th, triplet-16th.")
    parser.add_argument("--strength", "-s", metavar="S", type=float, default=1.0, help="Quantisation strength 0.0 (no change) – 1.0 (full snap). Default 1.0.")
    parser.add_argument("--dry-run", "-n", action="store_true", help="Preview without writing.")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> None:
    """Snap note onsets to a rhythmic grid.

    ``muse quantize`` moves each note's start tick to the nearest multiple of
    the chosen subdivision.  Use ``--strength`` < 1.0 for partial quantisation
    that preserves some human feel while tightening the groove.

    After quantising, run ``muse status`` to inspect the structured delta
    (which notes moved) and ``muse commit`` to record the operation.
    """
    track: str = args.track
    grid: str = args.grid
    strength: float = args.strength
    dry_run: bool = args.dry_run

    if grid not in _GRID_FRACTIONS:
        print(
            f"❌ Unknown grid '{grid}'.  "
            f"Valid: {', '.join(_GRID_FRACTIONS)}",
            file=sys.stderr,
        )
        raise SystemExit(ExitCode.USER_ERROR)

    if not 0.0 <= strength <= 1.0:
        print("❌ --strength must be between 0.0 and 1.0.", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)

    root = require_repo()
    result = load_track_from_workdir(root, track)
    if result is None:
        print(f"❌ Track '{track}' not found or not a valid MIDI file.", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)

    notes, tpb = result
    if not notes:
        print(f"  (track '{track}' contains no notes — nothing to quantise)")
        return

    grid_t = _grid_ticks(tpb, grid)
    quantised: list[NoteInfo] = []
    shifts: list[int] = []

    for n in notes:
        new_tick = _snap(n.start_tick, grid_t, strength)
        shifts.append(abs(new_tick - n.start_tick))
        quantised.append(NoteInfo(
            pitch=n.pitch,
            velocity=n.velocity,
            start_tick=new_tick,
            duration_ticks=n.duration_ticks,
            channel=n.channel,
            ticks_per_beat=n.ticks_per_beat,
        ))

    avg_shift = sum(shifts) / max(len(shifts), 1)
    max_shift = max(shifts) if shifts else 0
    moved = sum(1 for s in shifts if s > 0)

    if dry_run:
        print(f"\n[dry-run] Would quantise {track}  →  {grid}-note grid  (strength={strength:.2f})")
        print(f"  Notes adjusted:  {moved} / {len(notes)}")
        print(f"  Avg tick shift:  {avg_shift:.1f}  ·  Max: {max_shift}")
        print("  No changes written (--dry-run).")
        return

    midi_bytes = notes_to_midi_bytes(quantised, tpb)
    workdir = root
    try:
        work_path = contain_path(workdir, track)
    except ValueError as exc:
        print(f"❌ Invalid track path: {exc}")
        raise SystemExit(ExitCode.USER_ERROR)
    work_path.parent.mkdir(parents=True, exist_ok=True)
    work_path.write_bytes(midi_bytes)

    print(f"\n✅ Quantised {track}  →  {grid}-note grid")
    print(f"   {moved} notes adjusted  ·  avg shift: {avg_shift:.1f} ticks  ·  max shift: {max_shift}")
    print("   Run `muse status` to review, then `muse commit`")

"""muse shard — partition a MIDI composition into bar-range shards for parallel agents.

Splits a track into N non-overlapping bar-range segments and writes each
shard as a separate MIDI file.  An agent swarm can then work on shards in
parallel with zero risk of note-level conflicts, merging the shards back
together with ``muse mix``.

Usage::

    muse shard tracks/full.mid --shards 4
    muse shard tracks/full.mid --shards 8 --output-dir shards/
    muse shard tracks/full.mid --bars-per-shard 16
    muse shard tracks/full.mid --shards 4 --dry-run

Output::

    Shard plan: tracks/full.mid  →  4 shards
    Total bars: 32  ·  ~8 bars per shard

    Shard 0  bars  1– 8  →  shards/full_shard_0.mid  (28 notes)
    Shard 1  bars  9–16  →  shards/full_shard_1.mid  (31 notes)
    Shard 2  bars 17–24  →  shards/full_shard_2.mid  (24 notes)
    Shard 3  bars 25–32  →  shards/full_shard_3.mid  (19 notes)

    ✅ 4 shards written to shards/
"""

from __future__ import annotations

import argparse
import logging
import pathlib
import sys

from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.validation import contain_path
from muse.plugins.midi._query import NoteInfo, load_track_from_workdir, notes_by_bar, notes_to_midi_bytes

logger = logging.getLogger(__name__)


def _shard_notes(
    notes: list[NoteInfo],
    bar_ranges: list[tuple[int, int]],
) -> list[list[NoteInfo]]:
    """Partition notes into groups by bar range, rebasing start ticks to 0."""
    bars = notes_by_bar(notes)
    if not notes:
        return [[] for _ in bar_ranges]

    tpb = notes[0].ticks_per_beat

    shards: list[list[NoteInfo]] = []
    for lo_bar, hi_bar in bar_ranges:
        shard_notes: list[NoteInfo] = []
        bar_offset = (lo_bar - 1) * 4 * tpb
        for bar_num in range(lo_bar, hi_bar + 1):
            for note in bars.get(bar_num, []):
                rebased_tick = max(0, note.start_tick - bar_offset)
                shard_notes.append(NoteInfo(
                    pitch=note.pitch,
                    velocity=note.velocity,
                    start_tick=rebased_tick,
                    duration_ticks=note.duration_ticks,
                    channel=note.channel,
                    ticks_per_beat=note.ticks_per_beat,
                ))
        shards.append(shard_notes)
    return shards


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the shard subcommand."""
    parser = subparsers.add_parser("shard", help="Split a MIDI track into N bar-range shards for parallel agent work.", description=__doc__)
    parser.add_argument("track", metavar="TRACK", help="Workspace-relative path to a .mid file.")
    parser.add_argument("--shards", "-n", metavar="N", type=int, default=None, dest="num_shards", help="Number of shards to split into (mutually exclusive with --bars-per-shard).")
    parser.add_argument("--bars-per-shard", "-b", metavar="N", type=int, default=None, help="Bars per shard (mutually exclusive with --shards).")
    parser.add_argument("--output-dir", "-o", metavar="DIR", default="shards", help="Directory to write shard files (default: shards/).")
    parser.add_argument("--dry-run", action="store_true", help="Preview shard plan without writing.")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> None:
    """Split a MIDI track into N bar-range shards for parallel agent work.

    ``muse shard`` is the musical equivalent of partitioning a codebase for
    a parallelised agent swarm.  Each shard is a valid MIDI file covering a
    non-overlapping bar range.  Agents work on shards independently, then
    the shards are recombined with ``muse mix``.

    Specify either ``--shards N`` (divide evenly) or ``--bars-per-shard N``
    (fixed shard size with a remainder shard at the end).
    """
    track: str = args.track
    num_shards: int | None = args.num_shards
    bars_per_shard: int | None = args.bars_per_shard
    output_dir: str = args.output_dir
    dry_run: bool = args.dry_run

    if num_shards is not None and bars_per_shard is not None:
        print("❌ --shards and --bars-per-shard are mutually exclusive.", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)
    if num_shards is None and bars_per_shard is None:
        num_shards = 4

    root = require_repo()
    result = load_track_from_workdir(root, track)
    if result is None:
        print(f"❌ Track '{track}' not found or not a valid MIDI file.", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)

    notes, tpb = result
    if not notes:
        print(f"  (track '{track}' contains no notes — nothing to shard)")
        return

    bars = notes_by_bar(notes)
    all_bars = sorted(bars.keys())
    total_bars = len(all_bars)
    if total_bars == 0:
        print("  (no bars detected)")
        return

    first_bar = all_bars[0]
    last_bar = all_bars[-1]
    bar_span = last_bar - first_bar + 1

    # Determine bar-range splits
    if num_shards is not None:
        n = max(1, num_shards)
        bps = max(1, bar_span // n)
    else:
        bps = max(1, bars_per_shard or 1)
        n = (bar_span + bps - 1) // bps

    bar_ranges: list[tuple[int, int]] = []
    cur = first_bar
    for i in range(n):
        lo = cur
        hi = lo + bps - 1 if i < n - 1 else last_bar
        bar_ranges.append((lo, hi))
        cur = hi + 1
        if cur > last_bar:
            break

    track_stem = pathlib.Path(track).stem

    print(f"\nShard plan: {track}  →  {len(bar_ranges)} shards")
    print(f"Total bars: {total_bars}  ·  ~{bps} bars per shard\n")

    shard_notes_list = _shard_notes(notes, bar_ranges)

    try:
        out_dir = contain_path(root, output_dir)
    except ValueError as exc:
        print(f"❌ Invalid --output-dir: {exc}")
        raise SystemExit(ExitCode.USER_ERROR)
    for idx, ((lo, hi), shard_notes) in enumerate(zip(bar_ranges, shard_notes_list)):
        out_name = f"{track_stem}_shard_{idx}.mid"
        out_path = out_dir / out_name
        print(
            f"  Shard {idx}  bars {lo:>3}–{hi:>3}  →  {output_dir}/{out_name}"
            f"  ({len(shard_notes)} notes)"
        )
        if not dry_run:
            out_dir.mkdir(parents=True, exist_ok=True)
            midi_bytes = notes_to_midi_bytes(shard_notes, tpb) if shard_notes else notes_to_midi_bytes([], tpb)
            out_path.write_bytes(midi_bytes)

    if dry_run:
        print("\n  No files written (--dry-run).")
    else:
        print(f"\n✅ {len(bar_ranges)} shards written to {output_dir}/")

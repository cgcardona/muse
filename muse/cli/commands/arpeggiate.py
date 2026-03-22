"""muse arpeggiate — convert simultaneous chord notes into a sequential arpeggio.

Takes notes that overlap in time (chord voicings) and spreads them out
sequentially at a specified rhythmic rate.  Agents that receive a chord-pad
track and want to convert it into a rolling arpeggio pattern can do this in
one command.

Usage::

    muse arpeggiate tracks/chords.mid --rate 16th
    muse arpeggiate tracks/pads.mid --rate 8th --order up
    muse arpeggiate tracks/piano.mid --rate 8th --order down
    muse arpeggiate tracks/chords.mid --rate 16th --order random --seed 7
    muse arpeggiate tracks/chords.mid --rate 16th --dry-run

Order values: up (low→high), down (high→low), up-down, random

Output::

    ✅ Arpeggiated tracks/chords.mid  (16th-note rate, up order)
       12 chord clusters → 48 arpeggio notes
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

_RATE_FRACTIONS: dict[str, float] = {
    "quarter": 1.0,
    "8th":     0.5,
    "16th":    0.25,
    "32nd":    0.125,
}

_VALID_ORDERS = ("up", "down", "up-down", "random")


def _cluster_notes(notes: list[NoteInfo]) -> list[list[NoteInfo]]:
    """Group notes into time-overlapping clusters (chords)."""
    by_time = sorted(notes, key=lambda n: n.start_tick)
    clusters: list[list[NoteInfo]] = []
    current: list[NoteInfo] = []
    window = max(n.ticks_per_beat // 8 for n in notes) if notes else 1

    for note in by_time:
        if current and note.start_tick > current[0].start_tick + window:
            clusters.append(current)
            current = [note]
        else:
            current.append(note)
    if current:
        clusters.append(current)
    return clusters


def _order_cluster(cluster: list[NoteInfo], order: str, rng: random.Random) -> list[NoteInfo]:
    s = sorted(cluster, key=lambda n: n.pitch)
    if order == "up":
        return s
    if order == "down":
        return list(reversed(s))
    if order == "up-down":
        return s + list(reversed(s[1:-1]))
    # random
    shuffled = list(s)
    rng.shuffle(shuffled)
    return shuffled


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the arpeggiate subcommand."""
    parser = subparsers.add_parser("arpeggiate", help="Spread chord voicings into a sequential arpeggio pattern.", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("track", metavar="TRACK", help="Workspace-relative path to a .mid file.")
    parser.add_argument("--rate", "-r", metavar="RATE", default="16th", help="Arpeggio note rate: quarter, 8th, 16th, 32nd.")
    parser.add_argument("--order", "-o", metavar="ORDER", default="up", help="Arpeggio order: up, down, up-down, random.")
    parser.add_argument("--seed", metavar="INT", type=int, default=None, help="Random seed (for --order random).")
    parser.add_argument("--dry-run", "-n", action="store_true", help="Preview without writing.")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> None:
    """Spread chord voicings into a sequential arpeggio pattern.

    ``muse arpeggiate`` groups overlapping notes into chord clusters, then
    replaces each cluster with an arpeggio — sequential notes at the specified
    rhythmic rate in the specified pitch order.

    Durations are set to one grid step; original velocities are preserved.
    Use ``--order up-down`` for a ping-pong arpeggio.
    """
    track: str = args.track
    rate: str = args.rate
    order: str = args.order
    seed: int | None = args.seed
    dry_run: bool = args.dry_run

    if rate not in _RATE_FRACTIONS:
        print(f"❌ Unknown rate '{rate}'.  Valid: {', '.join(_RATE_FRACTIONS)}", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)
    if order not in _VALID_ORDERS:
        print(f"❌ Unknown order '{order}'.  Valid: {', '.join(_VALID_ORDERS)}", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)

    root = require_repo()
    result = load_track_from_workdir(root, track)
    if result is None:
        print(f"❌ Track '{track}' not found or not a valid MIDI file.", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)

    notes, tpb = result
    if not notes:
        print(f"  (track '{track}' contains no notes — nothing to arpeggiate)")
        return

    rng = random.Random(seed)
    step = max(1, round(tpb * _RATE_FRACTIONS[rate]))
    clusters = _cluster_notes(notes)
    arpeggiated: list[NoteInfo] = []

    for cluster in clusters:
        ordered = _order_cluster(cluster, order, rng)
        base_tick = ordered[0].start_tick
        for i, note in enumerate(ordered):
            arpeggiated.append(NoteInfo(
                pitch=note.pitch,
                velocity=note.velocity,
                start_tick=base_tick + i * step,
                duration_ticks=step,
                channel=note.channel,
                ticks_per_beat=note.ticks_per_beat,
            ))

    if dry_run:
        print(f"\n[dry-run] Would arpeggiate {track}  ({rate}-note rate, {order} order)")
        print(f"  Chord clusters:   {len(clusters)}")
        print(f"  Output notes:     {len(arpeggiated)}")
        print("  No changes written (--dry-run).")
        return

    midi_bytes = notes_to_midi_bytes(arpeggiated, tpb)
    workdir = root
    try:
        work_path = contain_path(workdir, track)
    except ValueError as exc:
        print(f"❌ Invalid track path: {exc}")
        raise SystemExit(ExitCode.USER_ERROR)
    work_path.parent.mkdir(parents=True, exist_ok=True)
    work_path.write_bytes(midi_bytes)

    print(f"\n✅ Arpeggiated {track}  ({rate}-note rate, {order} order)")
    print(f"   {len(clusters)} chord clusters → {len(arpeggiated)} arpeggio notes")
    print("   Run `muse status` to review, then `muse commit`")

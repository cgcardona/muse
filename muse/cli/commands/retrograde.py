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

import argparse
import logging
import pathlib
import sys

from muse.core.errors import ExitCode
from muse.core.validation import contain_path
from muse.core.repo import require_repo
from muse.plugins.midi._query import NoteInfo, load_track_from_workdir, notes_to_midi_bytes
from muse.plugins.midi.midi_diff import _pitch_name

logger = logging.getLogger(__name__)


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the retrograde subcommand."""
    parser = subparsers.add_parser("retrograde", help="Reverse the pitch order of all notes (retrograde transformation).", description=__doc__)
    parser.add_argument("track", metavar="TRACK", help="Workspace-relative path to a .mid file.")
    parser.add_argument("--dry-run", "-n", action="store_true", help="Preview without writing.")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> None:
    """Reverse the pitch order of all notes (retrograde transformation).

    ``muse retrograde`` maps note positions: the note that was at tick T from
    the end is placed at tick T from the start, so the last note plays first.
    Durations and velocities are preserved; only pitch and position are swapped.

    This is a foundational operation in serial/twelve-tone composition and is
    impossible to describe meaningfully in Git's binary-blob model.
    """
    track: str = args.track
    dry_run: bool = args.dry_run

    root = require_repo()
    result = load_track_from_workdir(root, track)
    if result is None:
        print(f"❌ Track '{track}' not found or not a valid MIDI file.", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)

    notes, tpb = result
    if not notes:
        print(f"  (track '{track}' contains no notes — nothing to retrograde)")
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
        print(f"\n[dry-run] Would retrograde {track}")
        print(f"  Notes:   {len(notes)}")
        print(f"  Was:     first note = {first_orig}")
        print(f"  Would:   first note = {first_retro}")
        print(f"  Span:    {span_beats:.2f} beats (unchanged)")
        print("  No changes written (--dry-run).")
        return

    midi_bytes = notes_to_midi_bytes(retro, tpb)
    workdir = root
    try:
        work_path = contain_path(workdir, track)
    except ValueError as exc:
        print(f"❌ Invalid track path: {exc}")
        raise SystemExit(ExitCode.USER_ERROR)
    work_path.parent.mkdir(parents=True, exist_ok=True)
    work_path.write_bytes(midi_bytes)

    print(f"\n✅ Retrograded {track}")
    print(f"   {len(retro)} notes reversed  ({first_orig} → was last, now first)")
    print(f"   Duration preserved  ·  original span: {span_beats:.2f} beats")
    print("   Run `muse status` to review, then `muse commit`")

"""muse harmony — chord analysis and key detection for a MIDI track.

Analyses the harmonic content of a MIDI file — detects implied chords per
bar, estimates the key signature, and reports pitch-class distribution.

Usage::

    muse harmony tracks/melody.mid
    muse harmony tracks/chords.mid --commit HEAD~5
    muse harmony tracks/piano.mid --json

Output::

    Harmonic analysis: tracks/melody.mid — commit cb4afaed
    Key signature (estimated): G major
    Total notes: 48  ·  Bars: 16

    Bar   Chord      Notes    Pitch classes
    ────────────────────────────────────────────────────────
      1   Gmaj       4        G, B, D
      2   Cmaj       4        C, E, G
      3   Amin       3        A, C, E
      4   D7         5        D, F#, A, C
    ...

    Pitch class distribution:
      G   ████████████ 12  (25.0%)
      B   ██████        6  (12.5%)
      D   ████████      8  (16.7%)
      ...
"""

from __future__ import annotations

import argparse
import json
import logging
import pathlib
import sys
from collections import Counter

from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.store import read_current_branch, resolve_commit_ref
from muse.plugins.midi._query import (
    NoteInfo,
    _PITCH_CLASSES,
    detect_chord,
    key_signature_guess,
    load_track,
    load_track_from_workdir,
    notes_by_bar,
)

logger = logging.getLogger(__name__)


def _read_repo_id(root: pathlib.Path) -> str:
    return str(json.loads((root / ".muse" / "repo.json").read_text())["repo_id"])


def _read_branch(root: pathlib.Path) -> str:
    return read_current_branch(root)


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the harmony subcommand."""
    parser = subparsers.add_parser("harmony", help="Detect chords and key signature from a MIDI track's note content.", description=__doc__)
    parser.add_argument("track", metavar="TRACK", help="Workspace-relative path to a .mid file.")
    parser.add_argument("--commit", "-c", metavar="REF", default=None, dest="ref", help="Analyse a historical snapshot instead of the working tree.")
    parser.add_argument("--json", action="store_true", dest="as_json", help="Emit results as JSON.")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> None:
    """Detect chords and key signature from a MIDI track's note content.

    ``muse harmony`` groups notes by bar, detects implied chords using a
    template-matching approach, and estimates the overall key signature
    using the Krumhansl-Schmuckler algorithm.

    This is fundamentally impossible in Git: Git has no model of what a MIDI
    file contains.  Muse stores notes as content-addressed semantic data,
    enabling musical analysis at any point in history.

    Use ``--commit`` to analyse a historical snapshot.  Use ``--json`` for
    agent-readable output suitable for further harmonic reasoning.
    """
    track: str = args.track
    ref: str | None = args.ref
    as_json: bool = args.as_json

    root = require_repo()

    result: tuple[list[NoteInfo], int] | None
    commit_label = "working tree"

    if ref is not None:
        repo_id = _read_repo_id(root)
        branch = _read_branch(root)
        commit = resolve_commit_ref(root, repo_id, branch, ref)
        if commit is None:
            print(f"❌ Commit '{ref}' not found.", file=sys.stderr)
            raise SystemExit(ExitCode.USER_ERROR)
        result = load_track(root, commit.commit_id, track)
        commit_label = commit.commit_id[:8]
    else:
        result = load_track_from_workdir(root, track)

    if result is None:
        print(f"❌ Track '{track}' not found or not a valid MIDI file.", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)

    note_list, _tpb = result
    if not note_list:
        print(f"  (no notes found in '{track}')")
        return

    key = key_signature_guess(note_list)
    bars = notes_by_bar(note_list)

    # Pitch class distribution.
    pc_counter: Counter[int] = Counter()
    for note in note_list:
        pc_counter[note.pitch_class] += 1

    # Per-bar chord analysis.
    bar_chords: list[tuple[int, str, int, list[str]]] = []
    for bar_num in sorted(bars):
        bar_notes = bars[bar_num]
        pcs = frozenset(n.pitch_class for n in bar_notes)
        chord = detect_chord(pcs)
        pc_names = sorted(set(_PITCH_CLASSES[pc] for pc in pcs))
        bar_chords.append((bar_num, chord, len(bar_notes), pc_names))

    if as_json:
        total_notes = len(note_list)
        print(json.dumps(
            {
                "track": track,
                "commit": commit_label,
                "key": key,
                "total_notes": total_notes,
                "bars": [
                    {
                        "bar": bar_num,
                        "chord": chord_name,
                        "note_count": n_count,
                        "pitch_classes": pc_name_list,
                    }
                    for bar_num, chord_name, n_count, pc_name_list in bar_chords
                ],
                "pitch_class_distribution": {
                    _PITCH_CLASSES[pc]: count
                    for pc, count in sorted(pc_counter.items())
                },
            },
            indent=2,
        ))
        return

    print(f"\nHarmonic analysis: {track} — {commit_label}")
    print(f"Key signature (estimated): {key}")
    print(f"Total notes: {len(note_list)}  ·  Bars: {len(bars)}")
    print("")
    print(f"  {'Bar':>4}  {'Chord':<10}  {'Notes':>5}  Pitch classes")
    print("  " + "─" * 54)

    for bar_num, chord_name, n_count, pc_name_list in bar_chords:
        pc_str = ", ".join(pc_name_list)
        print(f"  {bar_num:>4}  {chord_name:<10}  {n_count:>5}  {pc_str}")

    print("\nPitch class distribution:")
    total = max(sum(pc_counter.values()), 1)
    for pc in range(12):
        count = pc_counter.get(pc, 0)
        if count == 0:
            continue
        bar_len = min(int(count / total * 40), 40)
        bar_str = "█" * bar_len
        pct = count / total * 100
        print(f"  {_PITCH_CLASSES[pc]:<3}  {bar_str:<40}  {count:>3}  ({pct:.1f}%)")

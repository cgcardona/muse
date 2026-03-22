"""muse contour — melodic contour analysis for a MIDI track.

Classifies the overall melodic shape (arch, ascending, wave, …), computes
the pitch range, counts direction changes, and shows the full interval
sequence.  Agents use contour to compare melodic variation across branches
without listening to audio.

Usage::

    muse contour tracks/melody.mid
    muse contour tracks/lead.mid --commit HEAD~1
    muse contour tracks/violin.mid --json

Output::

    Melodic contour: tracks/melody.mid — working tree
    Shape:             arch
    Pitch range:       E3 – C6  (32 semitones)
    Direction changes: 7
    Avg interval size: 2.14 semitones

    Interval sequence (semitones):
    +2 +2 +3 +2 -1 -2 -2 +4 -3 -2 -1 +1 ...
"""

from __future__ import annotations

import argparse
import json
import logging
import pathlib
import sys

from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.store import read_current_branch, resolve_commit_ref
from muse.plugins.midi._analysis import analyze_contour
from muse.plugins.midi._query import load_track, load_track_from_workdir

logger = logging.getLogger(__name__)


def _read_repo_id(root: pathlib.Path) -> str:
    import json as _json

    return str(_json.loads((root / ".muse" / "repo.json").read_text())["repo_id"])


def _read_branch(root: pathlib.Path) -> str:
    return read_current_branch(root)


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the contour subcommand."""
    parser = subparsers.add_parser("contour", help="Analyse the melodic contour (shape) of a MIDI track.", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("track", metavar="TRACK", help="Workspace-relative path to a .mid file.")
    parser.add_argument("--commit", "-c", metavar="REF", default=None, dest="ref", help="Analyse a historical snapshot instead of the working tree.")
    parser.add_argument("--json", action="store_true", dest="as_json", help="Emit results as JSON.")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> None:
    """Analyse the melodic contour (shape) of a MIDI track.

    ``muse contour`` classifies the overall pitch trajectory — ascending,
    descending, arch, valley, wave, or flat — and reports pitch range,
    interval sequence, and directional complexity.

    For agents: contour is a fast structural fingerprint.  Use it to detect
    when a branch has inadvertently flattened or inverted a melody, or to
    verify that a transposition preserved the intended shape.
    """
    track: str = args.track
    ref: str | None = args.ref
    as_json: bool = args.as_json

    root = require_repo()
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

    notes, _tpb = result
    if not notes:
        print(f"  (no notes found in '{track}')")
        return

    analysis = analyze_contour(notes)

    if as_json:
        print(json.dumps({"track": track, "commit": commit_label, **analysis}, indent=2))
        return

    print(f"\nMelodic contour: {track} — {commit_label}")
    print(f"Shape:             {analysis['shape']}")
    print(
        f"Pitch range:       {analysis['lowest_pitch']} – {analysis['highest_pitch']}"
        f"  ({analysis['range_semitones']} semitones)"
    )
    print(f"Direction changes: {analysis['direction_changes']}")
    print(f"Avg interval size: {analysis['avg_interval_size']} semitones")

    intervals = analysis["intervals"]
    if intervals:
        print("\nInterval sequence (semitones):")
        parts = [f"{iv:+d}" for iv in intervals]
        print("  " + " ".join(parts[:32]) + (" …" if len(intervals) > 32 else ""))

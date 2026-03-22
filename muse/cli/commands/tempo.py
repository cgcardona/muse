"""muse tempo — estimate and report the tempo of a MIDI track.

Estimates BPM from inter-onset intervals and reports the ticks-per-beat
metadata.  For agent workflows that need to match tempo across branches or
verify that time-stretching operations preserved the rhythmic grid.

Usage::

    muse tempo tracks/drums.mid
    muse tempo tracks/bass.mid --commit HEAD~2
    muse tempo tracks/melody.mid --json

Output::

    Tempo analysis: tracks/drums.mid — working tree
    Estimated BPM:    120.0
    Ticks per beat:   480
    Confidence:       high  (ioi_voting method)

    Note: BPM is estimated from inter-onset intervals.
    For authoritative BPM, embed a MIDI tempo event at tick 0.
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
from muse.plugins.midi._analysis import estimate_tempo
from muse.plugins.midi._query import load_track, load_track_from_workdir

logger = logging.getLogger(__name__)


def _read_repo_id(root: pathlib.Path) -> str:
    import json as _json

    return str(_json.loads((root / ".muse" / "repo.json").read_text())["repo_id"])


def _read_branch(root: pathlib.Path) -> str:
    return read_current_branch(root)


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the tempo subcommand."""
    parser = subparsers.add_parser("tempo", help="Estimate the BPM of a MIDI track from inter-onset intervals.", description=__doc__)
    parser.add_argument("track", metavar="TRACK", help="Workspace-relative path to a .mid file.")
    parser.add_argument("--commit", "-c", metavar="REF", default=None, dest="ref", help="Analyse a historical snapshot instead of the working tree.")
    parser.add_argument("--json", action="store_true", dest="as_json", help="Emit results as JSON.")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> None:
    """Estimate the BPM of a MIDI track from inter-onset intervals.

    ``muse tempo`` uses IOI voting to estimate the underlying beat duration
    and converts it to BPM.  Confidence is rated high/medium/low based on
    how consistently notes cluster around a common beat subdivision.

    For agents: use this to verify that time-stretch transformations
    produced the expected tempo, or to detect BPM drift between branches.
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

    est = estimate_tempo(notes)

    if as_json:
        print(json.dumps({"track": track, "commit": commit_label, **est}, indent=2))
        return

    print(f"\nTempo analysis: {track} — {commit_label}")
    print(f"Estimated BPM:    {est['estimated_bpm']}")
    print(f"Ticks per beat:   {est['ticks_per_beat']}")
    print(f"Confidence:       {est['confidence']}  ({est['method']} method)")
    print("")
    print("Note: BPM is estimated from inter-onset intervals.")
    print("For authoritative BPM, embed a MIDI tempo event at tick 0.")

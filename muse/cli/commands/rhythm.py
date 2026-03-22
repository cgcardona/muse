"""muse rhythm — rhythmic analysis of a MIDI track.

Quantifies syncopation, quantisation accuracy, swing ratio, and dominant note
length.  In a world of agent swarms, rhythm is the temporal contract between
parts — this command makes it inspectable and diffable across commits.

Usage::

    muse rhythm tracks/drums.mid
    muse rhythm tracks/melody.mid --commit HEAD~3
    muse rhythm tracks/bass.mid --json

Output::

    Rhythmic analysis: tracks/drums.mid — working tree
    Notes: 64  ·  Bars: 8  ·  Notes/bar avg: 8.0
    Dominant subdivision: sixteenth
    Quantisation score:   0.94  (very tight)
    Syncopation score:    0.31  (moderate)
    Swing ratio:          1.42  (moderate swing)
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
from muse.plugins.midi._analysis import RhythmAnalysis, analyze_rhythm
from muse.plugins.midi._query import load_track, load_track_from_workdir

logger = logging.getLogger(__name__)


def _read_repo_id(root: pathlib.Path) -> str:
    import json as _json

    return str(_json.loads((root / ".muse" / "repo.json").read_text())["repo_id"])


def _read_branch(root: pathlib.Path) -> str:
    return read_current_branch(root)


def _quant_label(score: float) -> str:
    if score >= 0.95:
        return "very tight"
    if score >= 0.80:
        return "tight"
    if score >= 0.60:
        return "moderate"
    return "loose / human"


def _synco_label(score: float) -> str:
    if score < 0.10:
        return "straight"
    if score < 0.30:
        return "mild"
    if score < 0.55:
        return "moderate"
    return "highly syncopated"


def _swing_label(ratio: float) -> str:
    if ratio < 1.10:
        return "straight"
    if ratio < 1.30:
        return "light swing"
    if ratio < 1.60:
        return "moderate swing"
    return "heavy swing"


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the rhythm subcommand."""
    parser = subparsers.add_parser("rhythm", help="Quantify syncopation, swing, and quantisation accuracy in a MIDI track.", description=__doc__)
    parser.add_argument("track", metavar="TRACK", help="Workspace-relative path to a .mid file.")
    parser.add_argument("--commit", "-c", metavar="REF", default=None, dest="ref", help="Analyse a historical snapshot instead of the working tree.")
    parser.add_argument("--json", action="store_true", dest="as_json", help="Emit results as JSON.")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> None:
    """Quantify syncopation, swing, and quantisation accuracy in a MIDI track.

    ``muse rhythm`` gives agents and composers a numerical fingerprint of a
    track's rhythmic character — how quantised is it, how much does it swing,
    how syncopated?  These metrics are invisible in Git; Muse computes them
    from structured note data at any point in history.

    Use ``--json`` for agent-readable output to drive automated rhythmic
    quality gates or style-matching pipelines.
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

    analysis: RhythmAnalysis = analyze_rhythm(notes)

    if as_json:
        print(json.dumps({"track": track, "commit": commit_label, **analysis}, indent=2))
        return

    print(f"\nRhythmic analysis: {track} — {commit_label}")
    print(
        f"Notes: {analysis['total_notes']}  ·  "
        f"Bars: {analysis['bars']}  ·  "
        f"Notes/bar avg: {analysis['notes_per_bar_avg']}"
    )
    print(f"Dominant subdivision: {analysis['dominant_subdivision']}")
    qs = analysis["quantization_score"]
    ss = analysis["syncopation_score"]
    sw = analysis["swing_ratio"]
    print(f"Quantisation score:   {qs:.3f}  ({_quant_label(qs)})")
    print(f"Syncopation score:    {ss:.3f}  ({_synco_label(ss)})")
    print(f"Swing ratio:          {sw:.3f}  ({_swing_label(sw)})")

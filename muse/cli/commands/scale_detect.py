"""muse scale — detect the scale or mode of a MIDI track.

Goes far beyond key signature: detects pentatonic, blues, modal, whole-tone,
diminished, and chromatic scales by pitch-class frequency analysis.  Essential
for agent pipelines that need to harmonically reason about or transform a track.

Usage::

    muse scale tracks/melody.mid
    muse scale tracks/lead.mid --commit HEAD~2 --top 3
    muse scale tracks/bass.mid --json

Output::

    Scale analysis: tracks/melody.mid — working tree

    Rank  Root   Scale             Confidence  Out-of-scale
    ───────────────────────────────────────────────────────
       1  D      dorian            0.964            0
       2  A      natural minor     0.946            1
       3  G      major             0.929            2
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
from muse.plugins.midi._analysis import detect_scale
from muse.plugins.midi._query import load_track, load_track_from_workdir

logger = logging.getLogger(__name__)


def _read_repo_id(root: pathlib.Path) -> str:
    import json as _json

    return str(_json.loads((root / ".muse" / "repo.json").read_text())["repo_id"])


def _read_branch(root: pathlib.Path) -> str:
    return read_current_branch(root)


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the scale subcommand."""
    parser = subparsers.add_parser("scale", help="Detect the scale or mode of a MIDI track by pitch-class analysis.", description=__doc__)
    parser.add_argument("track", metavar="TRACK", help="Workspace-relative path to a .mid file.")
    parser.add_argument("--commit", "-c", metavar="REF", default=None, dest="ref", help="Analyse a historical snapshot instead of the working tree.")
    parser.add_argument("--top", "-n", metavar="N", type=int, default=3, help="Number of top scale matches to show.")
    parser.add_argument("--json", action="store_true", dest="as_json", help="Emit results as JSON.")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> None:
    """Detect the scale or mode of a MIDI track by pitch-class analysis.

    ``muse scale`` tests every root × scale combination and ranks them by the
    fraction of note weight covered by that scale's pitch classes.  Supports
    major, minor, all church modes, pentatonic, blues, whole-tone, diminished,
    and chromatic scales.

    For agents: combine with ``muse harmony`` to get both the implied chord
    progression and the underlying scale in one pipeline.
    """
    track: str = args.track
    ref: str | None = args.ref
    top: int = args.top
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

    matches = detect_scale(notes)[: max(1, top)]

    if as_json:
        print(json.dumps(
            {"track": track, "commit": commit_label, "matches": list(matches)},
            indent=2,
        ))
        return

    print(f"\nScale analysis: {track} — {commit_label}\n")
    print(f"  {'Rank':>4}  {'Root':<5}  {'Scale':<20}  {'Confidence':>10}  {'Out-of-scale':>12}")
    print("  " + "─" * 57)
    for i, m in enumerate(matches, 1):
        print(
            f"  {i:>4}  {m['root']:<5}  {m['name']:<20}  {m['confidence']:>10.3f}  {m['out_of_scale_notes']:>12}"
        )

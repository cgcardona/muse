"""muse motif — recurring melodic pattern detection for a MIDI track.

Finds repeated interval sequences (motifs) in a melodic line.  In a swarm
of agents each writing a section, motif detection ensures that a unifying
melodic idea recurs coherently — or surfaces when it has been accidentally
dropped.

Usage::

    muse motif tracks/melody.mid
    muse motif tracks/lead.mid --min-length 4 --min-occurrences 3
    muse motif tracks/violin.mid --commit HEAD~2
    muse motif tracks/piano.mid --json

Output::

    Motif analysis: tracks/melody.mid — working tree
    Found 3 motifs

    Motif 0  [+2 +2 -3]          3×   first: D4   bars: 1, 5, 13
    Motif 1  [+4 -2 -2 +1]       2×   first: G3   bars: 3, 11
    Motif 2  [-1 -1 +3]          2×   first: A4   bars: 7, 15
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
from muse.plugins.midi._analysis import find_motifs
from muse.plugins.midi._query import load_track, load_track_from_workdir

logger = logging.getLogger(__name__)


def _read_repo_id(root: pathlib.Path) -> str:
    import json as _json

    return str(_json.loads((root / ".muse" / "repo.json").read_text())["repo_id"])


def _read_branch(root: pathlib.Path) -> str:
    return read_current_branch(root)


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the motif subcommand."""
    parser = subparsers.add_parser("motif", help="Find recurring melodic patterns (motifs) in a MIDI track.", description=__doc__)
    parser.add_argument("track", metavar="TRACK", help="Workspace-relative path to a .mid file.")
    parser.add_argument("--commit", "-c", metavar="REF", default=None, dest="ref", help="Analyse a historical snapshot instead of the working tree.")
    parser.add_argument("--min-length", "-l", metavar="N", type=int, default=3, help="Minimum motif length in notes.")
    parser.add_argument("--min-occurrences", "-o", metavar="N", type=int, default=2, dest="min_occ", help="Minimum number of recurrences.")
    parser.add_argument("--json", action="store_true", dest="as_json", help="Emit results as JSON.")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> None:
    """Find recurring melodic patterns (motifs) in a MIDI track.

    ``muse motif`` scans the interval sequence between consecutive notes and
    finds the most frequently recurring sub-sequences.  It ignores transposition
    — only the interval pattern (the shape) matters, not the starting pitch.

    For agents:
    - Use ``--min-length 4`` for tighter, more distinctive motifs.
    - Use ``--commit`` to check whether a motif introduced in a previous commit
      is still present after a merge.
    - Combine with ``muse note-log`` to track where a motif first appeared.
    """
    track: str = args.track
    ref: str | None = args.ref
    min_length: int = args.min_length
    min_occ: int = args.min_occ
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

    motifs = find_motifs(notes, min_length=min_length, min_occurrences=min_occ)

    if as_json:
        print(json.dumps(
            {"track": track, "commit": commit_label, "motifs": list(motifs)},
            indent=2,
        ))
        return

    print(f"\nMotif analysis: {track} — {commit_label}")
    if not motifs:
        print(
            f"  (no motifs found with length ≥ {min_length} and occurrences ≥ {min_occ})"
        )
        return

    print(f"Found {len(motifs)} motif{'s' if len(motifs) != 1 else ''}\n")
    for m in motifs:
        intervals_str = " ".join(f"{iv:+d}" for iv in m["interval_pattern"])
        bars_str = ", ".join(str(b) for b in m["bars"])
        print(
            f"  Motif {m['id']}  [{intervals_str}]"
            f"  {m['occurrences']}×"
            f"   first: {m['first_pitch']}"
            f"   bars: {bars_str}"
        )

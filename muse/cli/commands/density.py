"""muse density — note density analysis per bar for a MIDI track.

Shows how many notes per beat fall in each bar, revealing texture changes:
sparse verses, dense choruses, quiet codas.  A swarm of agents editing
different sections can use this to reason about arrangement density without
audio playback.

Usage::

    muse density tracks/piano.mid
    muse density tracks/melody.mid --commit HEAD~4
    muse density tracks/rhythm.mid --json

Output::

    Note density: tracks/piano.mid — working tree
    Bars: 16  ·  Peak: bar 9 (4.25 notes/beat)  ·  Avg: 2.1

    bar   1  ████████          2.00 notes/beat   ( 8 notes)
    bar   2  ██████████████    3.50 notes/beat   (14 notes)
    bar   3  ████              1.00 notes/beat   ( 4 notes)
    ...
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
from muse.plugins.midi._analysis import analyze_density
from muse.plugins.midi._query import load_track, load_track_from_workdir

logger = logging.getLogger(__name__)

_BAR_WIDTH = 32


def _read_repo_id(root: pathlib.Path) -> str:
    import json as _json

    return str(_json.loads((root / ".muse" / "repo.json").read_text())["repo_id"])


def _read_branch(root: pathlib.Path) -> str:
    return read_current_branch(root)


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the density subcommand."""
    parser = subparsers.add_parser("density", help="Show note density (notes per beat) per bar of a MIDI track.", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("track", metavar="TRACK", help="Workspace-relative path to a .mid file.")
    parser.add_argument("--commit", "-c", metavar="REF", default=None, dest="ref", help="Analyse a historical snapshot instead of the working tree.")
    parser.add_argument("--json", action="store_true", dest="as_json", help="Emit results as JSON.")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> None:
    """Show note density (notes per beat) per bar of a MIDI track.

    ``muse density`` reveals the textural arc of a composition: which bars are
    dense, which are sparse.  Agents orchestrating multi-part arrangements use
    this to avoid over-crowding any single section, and to verify that section
    transitions (verse → chorus) are properly contrast-shaped.

    Git cannot do this.  Muse stores notes as structured data, so density is
    computable at any historical snapshot with no manual inspection.
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

    bars = analyze_density(notes)

    if as_json:
        print(json.dumps(
            {"track": track, "commit": commit_label, "bars": list(bars)},
            indent=2,
        ))
        return

    if not bars:
        print("  (no bars detected)")
        return

    peak_bar = max(bars, key=lambda b: b["notes_per_beat"])
    avg_npb = sum(b["notes_per_beat"] for b in bars) / len(bars)

    print(f"\nNote density: {track} — {commit_label}")
    print(
        f"Bars: {len(bars)}  ·  "
        f"Peak: bar {peak_bar['bar']} ({peak_bar['notes_per_beat']} notes/beat)  ·  "
        f"Avg: {avg_npb:.1f}"
    )
    print("")

    max_npb = max(b["notes_per_beat"] for b in bars) or 1.0
    for b in bars:
        fill = int(b["notes_per_beat"] / max_npb * _BAR_WIDTH)
        print(
            f"  bar {b['bar']:>3}  {'█' * fill:<{_BAR_WIDTH}}"
            f"  {b['notes_per_beat']:>5.2f} notes/beat"
            f"  ({b['note_count']:>3} notes)"
        )

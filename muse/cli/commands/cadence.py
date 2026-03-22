"""muse cadence — cadence detection for a MIDI track.

Identifies phrase endings (authentic, deceptive, half, plagal cadences) by
examining chord motions at bar boundaries.  Agents composing or reviewing
multi-section music need automated cadence detection to enforce correct
phrase structure without listening to audio.

Usage::

    muse cadence tracks/chords.mid
    muse cadence tracks/piano.mid --commit HEAD~1
    muse cadence tracks/strings.mid --json

Output::

    Cadence analysis: tracks/chords.mid — working tree
    Found 3 cadences

    Bar   Type         From       To
    ──────────────────────────────────────
      5   authentic    Gdom7      Cmaj
      9   half         Cmaj       Gdom7
     13   authentic    Ddom7      Gmaj
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
from muse.plugins.midi._analysis import detect_cadences
from muse.plugins.midi._query import load_track, load_track_from_workdir

logger = logging.getLogger(__name__)


def _read_repo_id(root: pathlib.Path) -> str:
    import json as _json

    return str(_json.loads((root / ".muse" / "repo.json").read_text())["repo_id"])


def _read_branch(root: pathlib.Path) -> str:
    return read_current_branch(root)


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the cadence subcommand."""
    parser = subparsers.add_parser("cadence", help="Detect phrase-ending cadences in a MIDI track.", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("track", metavar="TRACK", help="Workspace-relative path to a .mid file.")
    parser.add_argument("--commit", "-c", metavar="REF", default=None, dest="ref", help="Analyse a historical snapshot instead of the working tree.")
    parser.add_argument("--json", action="store_true", dest="as_json", help="Emit results as JSON.")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> None:
    """Detect phrase-ending cadences in a MIDI track.

    ``muse cadence`` identifies authentic, deceptive, half, and plagal
    cadences by examining chord motions at phrase boundaries (every 4 bars).

    Agents can use this to:
    - Verify that phrase structure matches an intended form.
    - Flag compositions where phrase endings lack proper resolution.
    - Compare cadence patterns across branches to detect structural drift.

    Git cannot do this — it has no concept of musical phrase structure.
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

    cadences = detect_cadences(notes)

    if as_json:
        print(json.dumps(
            {"track": track, "commit": commit_label, "cadences": list(cadences)},
            indent=2,
        ))
        return

    print(f"\nCadence analysis: {track} — {commit_label}")
    if not cadences:
        print("  (no cadences detected — track may be too short or lack chords)")
        return

    print(f"Found {len(cadences)} cadence{'s' if len(cadences) != 1 else ''}\n")
    print(f"  {'Bar':>4}  {'Type':<14}  {'From':<12}  {'To':<12}")
    print("  " + "─" * 46)
    for c in cadences:
        print(f"  {c['bar']:>4}  {c['cadence_type']:<14}  {c['from_chord']:<12}  {c['to_chord']:<12}")

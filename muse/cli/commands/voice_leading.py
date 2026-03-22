"""muse voice-leading — check for voice-leading violations in a MIDI track.

Detects parallel fifths, parallel octaves, and large leaps in the top voice —
the classic rules of contrapuntal writing.  Agents that auto-harmonise or
fill in inner voices can use this as an automated lint step before committing.

Usage::

    muse voice-leading tracks/chords.mid
    muse voice-leading tracks/strings.mid --commit HEAD~1
    muse voice-leading tracks/piano.mid --json

Output::

    Voice-leading check: tracks/chords.mid — working tree
    ⚠️  3 issues found

    Bar   Type               Description
    ──────────────────────────────────────────────────────
      5   parallel_fifths    voices 0–1: parallel perfect fifths
      9   large_leap         top voice: leap of 10 semitones
     13   parallel_octaves   voices 1–2: parallel octaves
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
from muse.plugins.midi._analysis import check_voice_leading
from muse.plugins.midi._query import load_track, load_track_from_workdir

logger = logging.getLogger(__name__)


def _read_repo_id(root: pathlib.Path) -> str:
    import json as _json

    return str(_json.loads((root / ".muse" / "repo.json").read_text())["repo_id"])


def _read_branch(root: pathlib.Path) -> str:
    return read_current_branch(root)


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the voice-leading subcommand."""
    parser = subparsers.add_parser("voice-leading", help="Detect parallel fifths, octaves, and large leaps in a MIDI track.", description=__doc__)
    parser.add_argument("track", metavar="TRACK", help="Workspace-relative path to a .mid file.")
    parser.add_argument("--commit", "-c", metavar="REF", default=None, dest="ref", help="Analyse a historical snapshot instead of the working tree.")
    parser.add_argument("--strict", action="store_true", help="Exit with error code if any issues are found (for CI use).")
    parser.add_argument("--json", action="store_true", dest="as_json", help="Emit results as JSON.")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> None:
    """Detect parallel fifths, octaves, and large leaps in a MIDI track.

    ``muse voice-leading`` applies classical counterpoint rules to the
    bar-by-bar note set.  It flags parallel fifths/octaves between any pair
    of voices and large melodic leaps (> a sixth) in the highest voice.

    For CI integration, use ``--strict`` to fail the pipeline when issues
    are present — preventing agents from committing harmonically problematic
    voice leading without review.
    """
    track: str = args.track
    ref: str | None = args.ref
    strict: bool = args.strict
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

    issues = check_voice_leading(notes)

    if as_json:
        print(json.dumps(
            {"track": track, "commit": commit_label, "issues": list(issues)},
            indent=2,
        ))
        if strict and issues:
            raise SystemExit(ExitCode.USER_ERROR)
        return

    print(f"\nVoice-leading check: {track} — {commit_label}")
    if not issues:
        print("✅ No voice-leading issues found.")
        return

    print(f"⚠️  {len(issues)} issue{'s' if len(issues) != 1 else ''} found\n")
    print(f"  {'Bar':>4}  {'Type':<22}  Description")
    print("  " + "─" * 58)
    for issue in issues:
        print(
            f"  {issue['bar']:>4}  {issue['issue_type']:<22}  {issue['description']}"
        )

    if strict:
        raise SystemExit(ExitCode.USER_ERROR)

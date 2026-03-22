"""muse note-hotspots — bar-level churn leaderboard across MIDI tracks.

Walks the commit history and counts how many times each bar in each track
was touched (had notes added or removed).  High churn at the bar level
reveals the musical sections under active evolution — the bridge that's
always changing, the verse that won't settle.

Usage::

    muse note-hotspots
    muse note-hotspots --top 20
    muse note-hotspots --track tracks/melody.mid
    muse note-hotspots --from HEAD~30

Output::

    Note churn — top 10 most-changed bars
    Commits analysed: 47

      1   tracks/melody.mid   bar  8    12 changes
      2   tracks/melody.mid   bar  4     9 changes
      3   tracks/bass.mid     bar  8     7 changes
      4   tracks/piano.mid    bar 12     5 changes

    High churn = compositional instability. Consider locking this section.
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
from muse.plugins.midi._query import NoteInfo, walk_commits_for_track

logger = logging.getLogger(__name__)


def _read_repo_id(root: pathlib.Path) -> str:
    return str(json.loads((root / ".muse" / "repo.json").read_text())["repo_id"])


def _read_branch(root: pathlib.Path) -> str:
    return read_current_branch(root)


def _bar_of_beat_summary(note_summary: str, tpb: int) -> int | None:
    """Extract bar number from a note summary string like 'C4 vel=80 @beat=3.00 dur=1.00'.

    Returns ``None`` when the beat position cannot be parsed.
    """
    for part in note_summary.split():
        if part.startswith("@beat="):
            try:
                beat = float(part.removeprefix("@beat="))
                bar = int(beat // 4) + 1
                return bar
            except ValueError:
                return None
    return None


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the note-hotspots subcommand."""
    parser = subparsers.add_parser("note-hotspots", help="Show the musical sections (bars) that change most often.", description=__doc__)
    parser.add_argument("--top", "-n", metavar="N", type=int, default=20, help="Number of bars to show (default: 20).")
    parser.add_argument("--track", "-t", metavar="TRACK", default=None, dest="track_filter", help="Restrict to a specific track file.")
    parser.add_argument("--from", metavar="REF", default=None, dest="from_ref", help="Exclusive start of the commit range (default: initial commit).")
    parser.add_argument("--to", metavar="REF", default=None, dest="to_ref", help="Inclusive end of the commit range (default: HEAD).")
    parser.add_argument("--json", action="store_true", dest="as_json", help="Emit results as JSON.")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> None:
    """Show the musical sections (bars) that change most often.

    ``muse note-hotspots`` walks the commit history and counts note-level
    changes per bar per track.  High churn at the bar level reveals the
    musical sections under most active revision — the bridge that keeps
    changing, the chorus that won't settle.

    This is the musical equivalent of ``muse hotspots`` for code: instead
    of "which function changes most?", it answers "which bar changes most?"

    Use ``--track`` to focus on a specific MIDI file.  Use ``--from`` /
    ``--to`` to scope to a sprint or release window.
    """
    top: int = args.top
    track_filter: str | None = args.track_filter
    from_ref: str | None = args.from_ref
    to_ref: str | None = args.to_ref
    as_json: bool = args.as_json

    root = require_repo()
    repo_id = _read_repo_id(root)
    branch = _read_branch(root)

    to_commit = resolve_commit_ref(root, repo_id, branch, to_ref)
    if to_commit is None:
        print(f"❌ Commit '{to_ref or 'HEAD'}' not found.", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)

    from_commit_id: str | None = None
    if from_ref is not None:
        from_c = resolve_commit_ref(root, repo_id, branch, from_ref)
        if from_c is None:
            print(f"❌ Commit '{from_ref}' not found.", file=sys.stderr)
            raise SystemExit(ExitCode.USER_ERROR)
        from_commit_id = from_c.commit_id

    # Discover all MIDI tracks touched in history.
    seen_commit_ids: set[str] = set()
    commits_count = 0

    # Walk commits from to_commit back.
    from muse.core.store import read_commit
    bar_counts: dict[tuple[str, int], int] = {}
    current_id: str | None = to_commit.commit_id

    while current_id and current_id != from_commit_id:
        if current_id in seen_commit_ids:
            break
        seen_commit_ids.add(current_id)
        commit = read_commit(root, current_id)
        if commit is None:
            break
        commits_count += 1
        current_id = commit.parent_commit_id

        if commit.structured_delta is None:
            continue

        for op in commit.structured_delta["ops"]:
            track_addr = op["address"]
            if track_filter and track_addr != track_filter:
                continue
            if not track_addr.lower().endswith(".mid"):
                continue
            child_ops = op["child_ops"] if op["op"] == "patch" else []
            for child in child_ops:
                if child["op"] == "insert":
                    summary: str = child["content_summary"]
                elif child["op"] == "delete":
                    summary = child["content_summary"]
                else:
                    continue
                bar_num = _bar_of_beat_summary(summary, 480)  # 480 = standard tpb
                if bar_num is None:
                    continue
                key = (track_addr, bar_num)
                bar_counts[key] = bar_counts.get(key, 0) + 1

    ranked = sorted(bar_counts.items(), key=lambda kv: kv[1], reverse=True)[:top]

    if as_json:
        print(json.dumps(
            {
                "commits_analysed": commits_count,
                "hotspots": [
                    {"track": t, "bar": b, "changes": c} for (t, b), c in ranked
                ],
            },
            indent=2,
        ))
        return

    track_label = f"  track={track_filter}" if track_filter else ""
    print(f"\nNote churn — top {len(ranked)} most-changed bars{track_label}")
    print(f"Commits analysed: {commits_count}")
    print("")

    if not ranked:
        print("  (no note-level bar changes found)")
        return

    width = len(str(len(ranked)))
    for rank, ((track_addr, bar_num), count) in enumerate(ranked, 1):
        label = "change" if count == 1 else "changes"
        print(
            f"  {rank:>{width}}   {track_addr:<40}  bar {bar_num:>4}    {count:>3} {label}"
        )

    print("")
    print("High churn = compositional instability. Consider locking this section.")

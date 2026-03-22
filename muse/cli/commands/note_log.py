"""muse note-log — note-level commit history for a MIDI track.

Walks the commit history and shows exactly which notes were added and
removed in each commit that touched a specific MIDI track.  Every change
is expressed in musical notation, not as a binary blob diff.

Usage::

    muse note-log tracks/melody.mid
    muse note-log tracks/melody.mid --from HEAD~10
    muse note-log tracks/melody.mid --json

Output::

    Note history: tracks/melody.mid
    Commits analysed: 12

    cb4afaed  2026-03-16  "Perf: vectorise melody"  (3 changes)
      +  C4  vel=80  @beat=1.00  dur=1.00  ch 0
      +  E4  vel=75  @beat=2.00  dur=0.50  ch 0
      -  D4  vel=72  @beat=2.00  dur=0.50  ch 0  (removed)

    1d2e3faa  2026-03-15  "Add bridge section"  (4 changes)
      +  A4  vel=78  @beat=9.00  dur=1.00  ch 0
      +  B4  vel=75  @beat=10.00 dur=1.00  ch 0
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
from muse.domain import DomainOp
from muse.plugins.midi._query import (
    NoteInfo,
    load_track,
    walk_commits_for_track,
)
from muse.plugins.midi.midi_diff import NoteKey, _note_summary, extract_notes
from muse.core.object_store import read_object

logger = logging.getLogger(__name__)


def _read_repo_id(root: pathlib.Path) -> str:
    return str(json.loads((root / ".muse" / "repo.json").read_text())["repo_id"])


def _read_branch(root: pathlib.Path) -> str:
    return read_current_branch(root)


def _flat_ops(ops: list[DomainOp]) -> list[DomainOp]:
    """Flatten PatchOp child_ops for the given track."""
    result: list[DomainOp] = []
    for op in ops:
        if op["op"] == "patch":
            result.extend(op["child_ops"])
        else:
            result.append(op)
    return result


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the note-log subcommand."""
    parser = subparsers.add_parser("note-log", help="Show the note-level commit history for a MIDI track.", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("track", metavar="TRACK", help="Workspace-relative path to a .mid file.")
    parser.add_argument("--from", metavar="REF", default=None, dest="from_ref", help="Start walking from this commit (default: HEAD).")
    parser.add_argument("--max", "-n", metavar="N", type=int, default=50, dest="max_commits", help="Maximum number of commits to walk (default: 50).")
    parser.add_argument("--json", action="store_true", dest="as_json", help="Emit results as JSON.")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> None:
    """Show the note-level commit history for a MIDI track.

    ``muse note-log`` walks the commit history and, for each commit that
    touched *TRACK*, shows exactly which notes were added and removed —
    expressed in musical notation (pitch name, beat position, velocity,
    duration), not as a binary diff.

    This is the music-domain equivalent of ``muse symbol-log``: a
    semantic history of a single artefact, at the level of individual notes.

    Use ``--from`` to start at a different point in history.  Use ``--json``
    to pipe the output to an agent for further processing.
    """
    track: str = args.track
    from_ref: str | None = args.from_ref
    max_commits: int = args.max_commits
    as_json: bool = args.as_json

    root = require_repo()
    repo_id = _read_repo_id(root)
    branch = _read_branch(root)

    start_commit = resolve_commit_ref(root, repo_id, branch, from_ref)
    if start_commit is None:
        print(f"❌ Commit '{from_ref or 'HEAD'}' not found.", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)

    commits_with_manifest = walk_commits_for_track(
        root, start_commit.commit_id, track, max_commits=max_commits
    )

    # Collect events: (commit, note_summary, op_kind) per commit that touched the track.
    EventEntry = tuple[str, str, str, str, str, list[tuple[str, str]]]
    events: list[EventEntry] = []

    for commit, manifest in commits_with_manifest:
        if commit.structured_delta is None:
            continue
        # Find the PatchOp for this track.
        track_ops: list[DomainOp] = []
        for op in commit.structured_delta["ops"]:
            if op["address"] == track:
                if op["op"] == "patch":
                    track_ops.extend(op["child_ops"])
                else:
                    # File-level insert/delete/replace — not note-level.
                    track_ops.append(op)

        if not track_ops:
            continue

        note_changes: list[tuple[str, str]] = []
        for op in track_ops:
            if op["op"] == "insert":
                note_changes.append(("+", op.get("content_summary", op["address"])))
            elif op["op"] == "delete":
                note_changes.append(("-", op.get("content_summary", op["address"])))

        if note_changes:
            date_str = commit.committed_at.strftime("%Y-%m-%d")
            events.append((
                commit.commit_id[:8],
                date_str,
                commit.message,
                commit.author or "unknown",
                commit.commit_id,
                note_changes,
            ))

    if as_json:
        out: list[dict[str, str | list[dict[str, str]]]] = []
        for short_id, date, msg, author, full_id, changes in events:
            out.append({
                "commit_id": full_id,
                "date": date,
                "message": msg,
                "author": author,
                "changes": [{"op": op, "note": note} for op, note in changes],
            })
        print(json.dumps({"track": track, "events": out}, indent=2))
        return

    print(f"\nNote history: {track}")
    print(f"Commits analysed: {len(commits_with_manifest)}")

    if not events:
        print("\n  (no note-level changes found for this track)")
        return

    for short_id, date, msg, author, _full_id, changes in events:
        print(f"\n{short_id}  {date}  \"{msg}\"  ({len(changes)} change(s))")
        for op_kind, note_summary in changes:
            prefix = "  +" if op_kind == "+" else "  -"
            suffix = "  (removed)" if op_kind == "-" else ""
            print(f"{prefix}  {note_summary}{suffix}")

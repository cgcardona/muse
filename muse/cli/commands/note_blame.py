"""muse note-blame — per-bar attribution for a MIDI track.

Shows which commit introduced the notes currently in a specific bar.
The music-domain equivalent of ``muse blame`` — instead of "which commit
last touched this function?", it answers "which commit wrote the notes
in bar 8 of the melody?"

Usage::

    muse note-blame tracks/melody.mid --bar 4
    muse note-blame tracks/melody.mid --bar 12 --json

Output::

    Note attribution: tracks/melody.mid  bar 4

      C4   vel=80  @beat=1.00  dur=1.00  ch 0
      E4   vel=75  @beat=2.00  dur=0.50  ch 0
      G4   vel=72  @beat=2.50  dur=0.50  ch 0
      E4   vel=75  @beat=3.00  dur=1.00  ch 0

      2 notes in bar 4 introduced by:
      cb4afaed  2026-03-16  alice  "Add chord arpeggiation in bar 4"
"""
from __future__ import annotations

import json
import logging
import pathlib

import typer

from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.store import resolve_commit_ref
from muse.domain import DomainOp
from muse.plugins.music._query import (
    NoteInfo,
    load_track,
    walk_commits_for_track,
)
from muse.plugins.music.midi_diff import NoteKey, _note_content_id

logger = logging.getLogger(__name__)

app = typer.Typer()


def _read_repo_id(root: pathlib.Path) -> str:
    return str(json.loads((root / ".muse" / "repo.json").read_text())["repo_id"])


def _read_branch(root: pathlib.Path) -> str:
    head_ref = (root / ".muse" / "HEAD").read_text().strip()
    return head_ref.removeprefix("refs/heads/").strip()


def _cid_for_note(note: NoteInfo) -> str:
    """Return the content ID for a ``NoteInfo`` (delegates to midi_diff logic)."""
    key = NoteKey(
        pitch=note.pitch,
        velocity=note.velocity,
        start_tick=note.start_tick,
        duration_ticks=note.duration_ticks,
        channel=note.channel,
    )
    return _note_content_id(key)


@app.callback(invoke_without_command=True)
def note_blame(
    ctx: typer.Context,
    track: str = typer.Argument(..., metavar="TRACK", help="Workspace-relative path to a .mid file."),
    bar: int = typer.Option(..., "--bar", "-b", metavar="N", help="Bar number (1-indexed, assumes 4/4 time)."),
    from_ref: str | None = typer.Option(
        None, "--from", metavar="REF",
        help="Start search from this commit (default: HEAD).",
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit results as JSON."),
) -> None:
    """Show which commit introduced the notes in a specific bar.

    ``muse note-blame`` walks the commit history and finds the commit that
    first inserted each note currently in bar N.  This gives per-note
    attribution at the musical level — not the line level.

    This is strictly impossible in Git: Git cannot tell you "these notes in
    bar 4 were added in commit X" because Git has no concept of notes or bars.

    Use ``--bar`` to specify the bar number (1-indexed, 4/4 time assumed).
    Use ``--from`` to start the search at a different point in history.
    """
    root = require_repo()
    repo_id = _read_repo_id(root)
    branch = _read_branch(root)

    start_commit = resolve_commit_ref(root, repo_id, branch, from_ref)
    if start_commit is None:
        typer.echo(f"❌ Commit '{from_ref or 'HEAD'}' not found.", err=True)
        raise typer.Exit(code=ExitCode.USER_ERROR)

    track_result = load_track(root, start_commit.commit_id, track)
    if track_result is None:
        typer.echo(f"❌ Track '{track}' not found in this commit.", err=True)
        raise typer.Exit(code=ExitCode.USER_ERROR)

    current_notes, _tpb = track_result
    bar_notes = [n for n in current_notes if n.bar == bar]

    if not bar_notes:
        typer.echo(f"  (no notes found in bar {bar} of '{track}')")
        return

    # Build a set of content IDs for the bar's notes.
    target_ids: set[str] = {_cid_for_note(n) for n in bar_notes}

    # Walk commits to find when each note was first inserted.
    note_origins: dict[str, tuple[str, str, str, str]] = {}
    commits_data = walk_commits_for_track(root, start_commit.commit_id, track)

    for commit, _manifest in commits_data:
        if commit.structured_delta is None:
            continue
        for op in commit.structured_delta["ops"]:
            if op["address"] != track:
                continue
            child_ops: list[DomainOp] = op["child_ops"] if op["op"] == "patch" else []
            for child in child_ops:
                if child["op"] == "insert":
                    cid = child["content_id"]
                    if cid in target_ids and cid not in note_origins:
                        date_str = commit.committed_at.strftime("%Y-%m-%d")
                        note_origins[cid] = (
                            commit.commit_id[:8],
                            date_str,
                            commit.author or "unknown",
                            commit.message,
                        )

    if as_json:
        typer.echo(json.dumps(
            {
                "track": track,
                "bar": bar,
                "notes": [
                    {
                        "pitch_name": n.pitch_name,
                        "velocity": n.velocity,
                        "beat_in_bar": round(n.beat_in_bar, 2),
                        "beat_duration": round(n.beat_duration, 2),
                        "channel": n.channel,
                        "introduced_by": note_origins.get(_cid_for_note(n), ("unknown", "", "", ""))[0],
                    }
                    for n in bar_notes
                ],
            },
            indent=2,
        ))
        return

    typer.echo(f"\nNote attribution: {track}  bar {bar}")
    typer.echo("")
    for n in bar_notes:
        typer.echo(
            f"  {n.pitch_name:<5}  vel={n.velocity:<3}  "
            f"@beat={n.beat_in_bar:.2f}  dur={n.beat_duration:.2f}  ch {n.channel}"
        )

    typer.echo("")

    commit_counts: dict[tuple[str, str, str, str], int] = {}
    for n in bar_notes:
        origin = note_origins.get(_cid_for_note(n))
        if origin:
            commit_counts[origin] = commit_counts.get(origin, 0) + 1

    if not commit_counts:
        typer.echo("  (could not trace origin — notes may predate the tracked history)")
        return

    for (short_id, date, author, message), count in sorted(
        commit_counts.items(), key=lambda kv: kv[1], reverse=True
    ):
        label = "note" if count == 1 else "notes"
        typer.echo(f"  {count} {label} in bar {bar} introduced by:")
        typer.echo(f"  {short_id}  {date}  {author}  \"{message}\"")
        typer.echo("")

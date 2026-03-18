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
from muse.plugins.music.midi_diff import NoteKey, _note_summary, extract_notes
from muse.core.object_store import read_object

logger = logging.getLogger(__name__)

app = typer.Typer()


def _read_repo_id(root: pathlib.Path) -> str:
    return str(json.loads((root / ".muse" / "repo.json").read_text())["repo_id"])


def _read_branch(root: pathlib.Path) -> str:
    head_ref = (root / ".muse" / "HEAD").read_text().strip()
    return head_ref.removeprefix("refs/heads/").strip()


def _flat_ops(ops: list[DomainOp]) -> list[DomainOp]:
    """Flatten PatchOp child_ops for the given track."""
    result: list[DomainOp] = []
    for op in ops:
        if op["op"] == "patch":
            result.extend(op["child_ops"])
        else:
            result.append(op)
    return result


@app.callback(invoke_without_command=True)
def note_log(
    ctx: typer.Context,
    track: str = typer.Argument(..., metavar="TRACK", help="Workspace-relative path to a .mid file."),
    from_ref: str | None = typer.Option(
        None, "--from", metavar="REF",
        help="Start walking from this commit (default: HEAD).",
    ),
    max_commits: int = typer.Option(
        50, "--max", "-n", metavar="N",
        help="Maximum number of commits to walk (default: 50).",
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit results as JSON."),
) -> None:
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
    root = require_repo()
    repo_id = _read_repo_id(root)
    branch = _read_branch(root)

    start_commit = resolve_commit_ref(root, repo_id, branch, from_ref)
    if start_commit is None:
        typer.echo(f"❌ Commit '{from_ref or 'HEAD'}' not found.", err=True)
        raise typer.Exit(code=ExitCode.USER_ERROR)

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
        typer.echo(json.dumps({"track": track, "events": out}, indent=2))
        return

    typer.echo(f"\nNote history: {track}")
    typer.echo(f"Commits analysed: {len(commits_with_manifest)}")

    if not events:
        typer.echo("\n  (no note-level changes found for this track)")
        return

    for short_id, date, msg, author, _full_id, changes in events:
        typer.echo(f"\n{short_id}  {date}  \"{msg}\"  ({len(changes)} change(s))")
        for op_kind, note_summary in changes:
            prefix = "  +" if op_kind == "+" else "  -"
            suffix = "  (removed)" if op_kind == "-" else ""
            typer.echo(f"{prefix}  {note_summary}{suffix}")

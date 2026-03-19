"""muse notes — musical notation view of a MIDI track.

Shows every note in a MIDI file as structured musical data: pitch name,
beat position, bar number, duration, velocity, and MIDI channel.

Unlike ``git show`` which gives you a binary blob diff, ``muse notes``
gives you the actual musical content — readable, sorted, historical.

Usage::

    muse notes tracks/melody.mid
    muse notes tracks/bass.mid --commit HEAD~3
    muse notes tracks/drums.mid --bar 4         # only notes in bar 4
    muse notes tracks/melody.mid --channel 0   # only channel 0
    muse notes tracks/melody.mid --json

Output::

    tracks/melody.mid — 23 notes — commit cb4afaed
    Key signature (estimated): G major

    Bar  Beat  Pitch  Vel  Dur(beats)  Channel
    ─────────────────────────────────────────────────
      1   1.00  G4     80   1.00        ch 0
      1   2.00  B4     75   0.50        ch 0
      1   2.50  D5     72   0.50        ch 0
      1   3.00  G4     80   1.00        ch 0
      2   1.00  A4     78   1.00        ch 0
    ...

    23 note(s) across 8 bar(s)
"""

from __future__ import annotations

import json
import logging
import pathlib

import typer

from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.store import get_head_commit_id, resolve_commit_ref
from muse.plugins.midi._query import (
    NoteInfo,
    key_signature_guess,
    load_track,
    load_track_from_workdir,
)

logger = logging.getLogger(__name__)

app = typer.Typer()


def _read_repo_id(root: pathlib.Path) -> str:
    return str(json.loads((root / ".muse" / "repo.json").read_text())["repo_id"])


def _read_branch(root: pathlib.Path) -> str:
    head_ref = (root / ".muse" / "HEAD").read_text().strip()
    return head_ref.removeprefix("refs/heads/").strip()


@app.callback(invoke_without_command=True)
def notes(
    ctx: typer.Context,
    track: str = typer.Argument(..., metavar="TRACK", help="Workspace-relative path to a .mid file."),
    ref: str | None = typer.Option(
        None, "--commit", "-c", metavar="REF",
        help="Read from a historical commit instead of the working tree.",
    ),
    bar_filter: int | None = typer.Option(
        None, "--bar", "-b", metavar="N",
        help="Only show notes in bar N (1-indexed, assumes 4/4 time).",
    ),
    channel_filter: int | None = typer.Option(
        None, "--channel", "-C", metavar="N",
        help="Only show notes on MIDI channel N (0-based).",
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit results as JSON."),
) -> None:
    """Show every note in a MIDI track as structured musical data.

    ``muse notes`` parses the MIDI file and displays all notes with pitch
    name, beat position, bar number, duration, velocity, and channel.

    Use ``--commit`` to inspect a historical snapshot.  Use ``--bar`` to
    focus on a single bar.  Use ``--json`` for pipeline integration.

    Unlike ``git show`` which gives you a raw binary diff, ``muse notes``
    gives you the actual musical content at any point in history — sorted
    by time, readable as music notation.
    """
    root = require_repo()

    result: tuple[list[NoteInfo], int] | None
    commit_label = "working tree"

    if ref is not None:
        repo_id = _read_repo_id(root)
        branch = _read_branch(root)
        commit = resolve_commit_ref(root, repo_id, branch, ref)
        if commit is None:
            typer.echo(f"❌ Commit '{ref}' not found.", err=True)
            raise typer.Exit(code=ExitCode.USER_ERROR)
        result = load_track(root, commit.commit_id, track)
        commit_label = commit.commit_id[:8]
    else:
        result = load_track_from_workdir(root, track)

    if result is None:
        typer.echo(f"❌ Track '{track}' not found or not a valid MIDI file.", err=True)
        raise typer.Exit(code=ExitCode.USER_ERROR)

    note_list, tpb = result

    # Apply filters.
    if bar_filter is not None:
        note_list = [n for n in note_list if n.bar == bar_filter]
    if channel_filter is not None:
        note_list = [n for n in note_list if n.channel == channel_filter]

    if as_json:
        out: list[dict[str, str | int | float]] = [
            {
                "pitch": n.pitch,
                "pitch_name": n.pitch_name,
                "velocity": n.velocity,
                "start_tick": n.start_tick,
                "duration_ticks": n.duration_ticks,
                "beat": round(n.beat, 4),
                "beat_duration": round(n.beat_duration, 4),
                "bar": n.bar,
                "beat_in_bar": round(n.beat_in_bar, 2),
                "channel": n.channel,
            }
            for n in note_list
        ]
        typer.echo(json.dumps({"track": track, "commit": commit_label, "notes": out}, indent=2))
        return

    bars_seen: set[int] = {n.bar for n in note_list}

    key = key_signature_guess(note_list) if not bar_filter and not channel_filter else ""
    key_line = f"\nKey signature (estimated): {key}" if key else ""

    typer.echo(f"\n{track} — {len(note_list)} notes — {commit_label}{key_line}")
    typer.echo("")
    typer.echo(f"  {'Bar':>4}  {'Beat':>5}  {'Pitch':<6}  {'Vel':>3}  {'Dur':>10}  Channel")
    typer.echo("  " + "─" * 50)

    for note in note_list:
        typer.echo(
            f"  {note.bar:>4}  {note.beat_in_bar:>5.2f}  {note.pitch_name:<6}  "
            f"{note.velocity:>3}  {note.beat_duration:>10.2f}  ch {note.channel}"
        )

    typer.echo(f"\n{len(note_list)} note(s) across {len(bars_seen)} bar(s)")

"""muse contour — melodic contour analysis for a MIDI track.

Classifies the overall melodic shape (arch, ascending, wave, …), computes
the pitch range, counts direction changes, and shows the full interval
sequence.  Agents use contour to compare melodic variation across branches
without listening to audio.

Usage::

    muse contour tracks/melody.mid
    muse contour tracks/lead.mid --commit HEAD~1
    muse contour tracks/violin.mid --json

Output::

    Melodic contour: tracks/melody.mid — working tree
    Shape:             arch
    Pitch range:       E3 – C6  (32 semitones)
    Direction changes: 7
    Avg interval size: 2.14 semitones

    Interval sequence (semitones):
    +2 +2 +3 +2 -1 -2 -2 +4 -3 -2 -1 +1 ...
"""

from __future__ import annotations

import json
import logging
import pathlib

import typer

from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.store import read_current_branch, resolve_commit_ref
from muse.plugins.midi._analysis import analyze_contour
from muse.plugins.midi._query import load_track, load_track_from_workdir

logger = logging.getLogger(__name__)
app = typer.Typer()


def _read_repo_id(root: pathlib.Path) -> str:
    import json as _json

    return str(_json.loads((root / ".muse" / "repo.json").read_text())["repo_id"])


def _read_branch(root: pathlib.Path) -> str:
    return read_current_branch(root)


@app.callback(invoke_without_command=True)
def contour(
    ctx: typer.Context,
    track: str = typer.Argument(..., metavar="TRACK", help="Workspace-relative path to a .mid file."),
    ref: str | None = typer.Option(
        None, "--commit", "-c", metavar="REF",
        help="Analyse a historical snapshot instead of the working tree.",
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit results as JSON."),
) -> None:
    """Analyse the melodic contour (shape) of a MIDI track.

    ``muse contour`` classifies the overall pitch trajectory — ascending,
    descending, arch, valley, wave, or flat — and reports pitch range,
    interval sequence, and directional complexity.

    For agents: contour is a fast structural fingerprint.  Use it to detect
    when a branch has inadvertently flattened or inverted a melody, or to
    verify that a transposition preserved the intended shape.
    """
    root = require_repo()
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

    notes, _tpb = result
    if not notes:
        typer.echo(f"  (no notes found in '{track}')")
        return

    analysis = analyze_contour(notes)

    if as_json:
        typer.echo(json.dumps({"track": track, "commit": commit_label, **analysis}, indent=2))
        return

    typer.echo(f"\nMelodic contour: {track} — {commit_label}")
    typer.echo(f"Shape:             {analysis['shape']}")
    typer.echo(
        f"Pitch range:       {analysis['lowest_pitch']} – {analysis['highest_pitch']}"
        f"  ({analysis['range_semitones']} semitones)"
    )
    typer.echo(f"Direction changes: {analysis['direction_changes']}")
    typer.echo(f"Avg interval size: {analysis['avg_interval_size']} semitones")

    intervals = analysis["intervals"]
    if intervals:
        typer.echo("\nInterval sequence (semitones):")
        parts = [f"{iv:+d}" for iv in intervals]
        typer.echo("  " + " ".join(parts[:32]) + (" …" if len(intervals) > 32 else ""))

"""muse tempo — estimate and report the tempo of a MIDI track.

Estimates BPM from inter-onset intervals and reports the ticks-per-beat
metadata.  For agent workflows that need to match tempo across branches or
verify that time-stretching operations preserved the rhythmic grid.

Usage::

    muse tempo tracks/drums.mid
    muse tempo tracks/bass.mid --commit HEAD~2
    muse tempo tracks/melody.mid --json

Output::

    Tempo analysis: tracks/drums.mid — working tree
    Estimated BPM:    120.0
    Ticks per beat:   480
    Confidence:       high  (ioi_voting method)

    Note: BPM is estimated from inter-onset intervals.
    For authoritative BPM, embed a MIDI tempo event at tick 0.
"""

from __future__ import annotations

import json
import logging
import pathlib

import typer

from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.store import read_current_branch, resolve_commit_ref
from muse.plugins.midi._analysis import estimate_tempo
from muse.plugins.midi._query import load_track, load_track_from_workdir

logger = logging.getLogger(__name__)
app = typer.Typer()


def _read_repo_id(root: pathlib.Path) -> str:
    import json as _json

    return str(_json.loads((root / ".muse" / "repo.json").read_text())["repo_id"])


def _read_branch(root: pathlib.Path) -> str:
    return read_current_branch(root)


@app.callback(invoke_without_command=True)
def tempo(
    ctx: typer.Context,
    track: str = typer.Argument(..., metavar="TRACK", help="Workspace-relative path to a .mid file."),
    ref: str | None = typer.Option(
        None, "--commit", "-c", metavar="REF",
        help="Analyse a historical snapshot instead of the working tree.",
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit results as JSON."),
) -> None:
    """Estimate the BPM of a MIDI track from inter-onset intervals.

    ``muse tempo`` uses IOI voting to estimate the underlying beat duration
    and converts it to BPM.  Confidence is rated high/medium/low based on
    how consistently notes cluster around a common beat subdivision.

    For agents: use this to verify that time-stretch transformations
    produced the expected tempo, or to detect BPM drift between branches.
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

    est = estimate_tempo(notes)

    if as_json:
        typer.echo(json.dumps({"track": track, "commit": commit_label, **est}, indent=2))
        return

    typer.echo(f"\nTempo analysis: {track} — {commit_label}")
    typer.echo(f"Estimated BPM:    {est['estimated_bpm']}")
    typer.echo(f"Ticks per beat:   {est['ticks_per_beat']}")
    typer.echo(f"Confidence:       {est['confidence']}  ({est['method']} method)")
    typer.echo("")
    typer.echo("Note: BPM is estimated from inter-onset intervals.")
    typer.echo("For authoritative BPM, embed a MIDI tempo event at tick 0.")

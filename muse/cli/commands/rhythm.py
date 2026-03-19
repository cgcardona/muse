"""muse rhythm — rhythmic analysis of a MIDI track.

Quantifies syncopation, quantisation accuracy, swing ratio, and dominant note
length.  In a world of agent swarms, rhythm is the temporal contract between
parts — this command makes it inspectable and diffable across commits.

Usage::

    muse rhythm tracks/drums.mid
    muse rhythm tracks/melody.mid --commit HEAD~3
    muse rhythm tracks/bass.mid --json

Output::

    Rhythmic analysis: tracks/drums.mid — working tree
    Notes: 64  ·  Bars: 8  ·  Notes/bar avg: 8.0
    Dominant subdivision: sixteenth
    Quantisation score:   0.94  (very tight)
    Syncopation score:    0.31  (moderate)
    Swing ratio:          1.42  (moderate swing)
"""

from __future__ import annotations

import json
import logging
import pathlib

import typer

from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.store import resolve_commit_ref
from muse.plugins.midi._analysis import RhythmAnalysis, analyze_rhythm
from muse.plugins.midi._query import load_track, load_track_from_workdir

logger = logging.getLogger(__name__)
app = typer.Typer()


def _read_repo_id(root: pathlib.Path) -> str:
    import json as _json

    return str(_json.loads((root / ".muse" / "repo.json").read_text())["repo_id"])


def _read_branch(root: pathlib.Path) -> str:
    return (root / ".muse" / "HEAD").read_text().strip().removeprefix("refs/heads/").strip()


def _quant_label(score: float) -> str:
    if score >= 0.95:
        return "very tight"
    if score >= 0.80:
        return "tight"
    if score >= 0.60:
        return "moderate"
    return "loose / human"


def _synco_label(score: float) -> str:
    if score < 0.10:
        return "straight"
    if score < 0.30:
        return "mild"
    if score < 0.55:
        return "moderate"
    return "highly syncopated"


def _swing_label(ratio: float) -> str:
    if ratio < 1.10:
        return "straight"
    if ratio < 1.30:
        return "light swing"
    if ratio < 1.60:
        return "moderate swing"
    return "heavy swing"


@app.callback(invoke_without_command=True)
def rhythm(
    ctx: typer.Context,
    track: str = typer.Argument(..., metavar="TRACK", help="Workspace-relative path to a .mid file."),
    ref: str | None = typer.Option(
        None, "--commit", "-c", metavar="REF",
        help="Analyse a historical snapshot instead of the working tree.",
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit results as JSON."),
) -> None:
    """Quantify syncopation, swing, and quantisation accuracy in a MIDI track.

    ``muse rhythm`` gives agents and composers a numerical fingerprint of a
    track's rhythmic character — how quantised is it, how much does it swing,
    how syncopated?  These metrics are invisible in Git; Muse computes them
    from structured note data at any point in history.

    Use ``--json`` for agent-readable output to drive automated rhythmic
    quality gates or style-matching pipelines.
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

    analysis: RhythmAnalysis = analyze_rhythm(notes)

    if as_json:
        typer.echo(json.dumps({"track": track, "commit": commit_label, **analysis}, indent=2))
        return

    typer.echo(f"\nRhythmic analysis: {track} — {commit_label}")
    typer.echo(
        f"Notes: {analysis['total_notes']}  ·  "
        f"Bars: {analysis['bars']}  ·  "
        f"Notes/bar avg: {analysis['notes_per_bar_avg']}"
    )
    typer.echo(f"Dominant subdivision: {analysis['dominant_subdivision']}")
    qs = analysis["quantization_score"]
    ss = analysis["syncopation_score"]
    sw = analysis["swing_ratio"]
    typer.echo(f"Quantisation score:   {qs:.3f}  ({_quant_label(qs)})")
    typer.echo(f"Syncopation score:    {ss:.3f}  ({_synco_label(ss)})")
    typer.echo(f"Swing ratio:          {sw:.3f}  ({_swing_label(sw)})")

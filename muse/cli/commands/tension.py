"""muse tension — harmonic tension curve for a MIDI track.

Scores each bar's dissonance level from 0 (perfectly consonant) to 1
(maximally tense).  Agents composing multi-part music or reviewing agent-
generated harmony use this to verify that tension builds toward climaxes and
resolves at cadences — an impossible analysis in Git's binary-blob world.

Usage::

    muse tension tracks/chords.mid
    muse tension tracks/piano.mid --commit HEAD~2
    muse tension tracks/strings.mid --json

Output::

    Harmonic tension: tracks/chords.mid — working tree

    bar  1  ▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁   0.05  consonant
    bar  2  ████████             0.41  mild
    bar  3  ████████████████     0.72  tense
    bar  4  ████                 0.21  mild
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
from muse.plugins.midi._analysis import compute_tension
from muse.plugins.midi._query import load_track, load_track_from_workdir

logger = logging.getLogger(__name__)
app = typer.Typer()

_BAR_WIDTH = 20


def _read_repo_id(root: pathlib.Path) -> str:
    import json as _json

    return str(_json.loads((root / ".muse" / "repo.json").read_text())["repo_id"])


def _read_branch(root: pathlib.Path) -> str:
    return (root / ".muse" / "HEAD").read_text().strip().removeprefix("refs/heads/").strip()


def _tension_bar(tension: float) -> str:
    blocks = ["▁", "▂", "▃", "▄", "▅", "▆", "▇", "█"]
    level = int(tension * (_BAR_WIDTH - 1))
    block = blocks[min(int(tension * 7), 7)]
    return block * level


@app.callback(invoke_without_command=True)
def tension(
    ctx: typer.Context,
    track: str = typer.Argument(..., metavar="TRACK", help="Workspace-relative path to a .mid file."),
    ref: str | None = typer.Option(
        None, "--commit", "-c", metavar="REF",
        help="Analyse a historical snapshot instead of the working tree.",
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit results as JSON."),
) -> None:
    """Show the harmonic tension arc of a MIDI track bar by bar.

    ``muse tension`` uses interval dissonance weights to score each bar's
    harmonic complexity.  A well-structured composition typically builds
    tension toward phrase climaxes and resolves it at cadence points.

    Agents can use this as an automated quality gate: if tension is flat or
    unresolved at expected cadence points, the composition needs revision.
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

    bars = compute_tension(notes)

    if as_json:
        typer.echo(json.dumps(
            {"track": track, "commit": commit_label, "bars": list(bars)},
            indent=2,
        ))
        return

    typer.echo(f"\nHarmonic tension: {track} — {commit_label}\n")
    for b in bars:
        bar_str = _tension_bar(b["tension"])
        typer.echo(
            f"  bar {b['bar']:>3}  {bar_str:<{_BAR_WIDTH}}"
            f"  {b['tension']:.3f}  {b['label']}"
        )

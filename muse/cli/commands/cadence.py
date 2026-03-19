"""muse cadence — cadence detection for a MIDI track.

Identifies phrase endings (authentic, deceptive, half, plagal cadences) by
examining chord motions at bar boundaries.  Agents composing or reviewing
multi-section music need automated cadence detection to enforce correct
phrase structure without listening to audio.

Usage::

    muse cadence tracks/chords.mid
    muse cadence tracks/piano.mid --commit HEAD~1
    muse cadence tracks/strings.mid --json

Output::

    Cadence analysis: tracks/chords.mid — working tree
    Found 3 cadences

    Bar   Type         From       To
    ──────────────────────────────────────
      5   authentic    Gdom7      Cmaj
      9   half         Cmaj       Gdom7
     13   authentic    Ddom7      Gmaj
"""

from __future__ import annotations

import json
import logging
import pathlib

import typer

from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.store import resolve_commit_ref
from muse.plugins.midi._analysis import detect_cadences
from muse.plugins.midi._query import load_track, load_track_from_workdir

logger = logging.getLogger(__name__)
app = typer.Typer()


def _read_repo_id(root: pathlib.Path) -> str:
    import json as _json

    return str(_json.loads((root / ".muse" / "repo.json").read_text())["repo_id"])


def _read_branch(root: pathlib.Path) -> str:
    return (root / ".muse" / "HEAD").read_text().strip().removeprefix("refs/heads/").strip()


@app.callback(invoke_without_command=True)
def cadence(
    ctx: typer.Context,
    track: str = typer.Argument(..., metavar="TRACK", help="Workspace-relative path to a .mid file."),
    ref: str | None = typer.Option(
        None, "--commit", "-c", metavar="REF",
        help="Analyse a historical snapshot instead of the working tree.",
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit results as JSON."),
) -> None:
    """Detect phrase-ending cadences in a MIDI track.

    ``muse cadence`` identifies authentic, deceptive, half, and plagal
    cadences by examining chord motions at phrase boundaries (every 4 bars).

    Agents can use this to:
    - Verify that phrase structure matches an intended form.
    - Flag compositions where phrase endings lack proper resolution.
    - Compare cadence patterns across branches to detect structural drift.

    Git cannot do this — it has no concept of musical phrase structure.
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

    cadences = detect_cadences(notes)

    if as_json:
        typer.echo(json.dumps(
            {"track": track, "commit": commit_label, "cadences": list(cadences)},
            indent=2,
        ))
        return

    typer.echo(f"\nCadence analysis: {track} — {commit_label}")
    if not cadences:
        typer.echo("  (no cadences detected — track may be too short or lack chords)")
        return

    typer.echo(f"Found {len(cadences)} cadence{'s' if len(cadences) != 1 else ''}\n")
    typer.echo(f"  {'Bar':>4}  {'Type':<14}  {'From':<12}  {'To':<12}")
    typer.echo("  " + "─" * 46)
    for c in cadences:
        typer.echo(f"  {c['bar']:>4}  {c['cadence_type']:<14}  {c['from_chord']:<12}  {c['to_chord']:<12}")

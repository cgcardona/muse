"""muse voice-leading — check for voice-leading violations in a MIDI track.

Detects parallel fifths, parallel octaves, and large leaps in the top voice —
the classic rules of contrapuntal writing.  Agents that auto-harmonise or
fill in inner voices can use this as an automated lint step before committing.

Usage::

    muse voice-leading tracks/chords.mid
    muse voice-leading tracks/strings.mid --commit HEAD~1
    muse voice-leading tracks/piano.mid --json

Output::

    Voice-leading check: tracks/chords.mid — working tree
    ⚠️  3 issues found

    Bar   Type               Description
    ──────────────────────────────────────────────────────
      5   parallel_fifths    voices 0–1: parallel perfect fifths
      9   large_leap         top voice: leap of 10 semitones
     13   parallel_octaves   voices 1–2: parallel octaves
"""

from __future__ import annotations

import json
import logging
import pathlib

import typer

from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.store import read_current_branch, resolve_commit_ref
from muse.plugins.midi._analysis import check_voice_leading
from muse.plugins.midi._query import load_track, load_track_from_workdir

logger = logging.getLogger(__name__)
app = typer.Typer()


def _read_repo_id(root: pathlib.Path) -> str:
    import json as _json

    return str(_json.loads((root / ".muse" / "repo.json").read_text())["repo_id"])


def _read_branch(root: pathlib.Path) -> str:
    return read_current_branch(root)


@app.callback(invoke_without_command=True)
def voice_leading(
    ctx: typer.Context,
    track: str = typer.Argument(..., metavar="TRACK", help="Workspace-relative path to a .mid file."),
    ref: str | None = typer.Option(
        None, "--commit", "-c", metavar="REF",
        help="Analyse a historical snapshot instead of the working tree.",
    ),
    strict: bool = typer.Option(
        False, "--strict",
        help="Exit with error code if any issues are found (for CI use).",
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit results as JSON."),
) -> None:
    """Detect parallel fifths, octaves, and large leaps in a MIDI track.

    ``muse voice-leading`` applies classical counterpoint rules to the
    bar-by-bar note set.  It flags parallel fifths/octaves between any pair
    of voices and large melodic leaps (> a sixth) in the highest voice.

    For CI integration, use ``--strict`` to fail the pipeline when issues
    are present — preventing agents from committing harmonically problematic
    voice leading without review.
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

    issues = check_voice_leading(notes)

    if as_json:
        typer.echo(json.dumps(
            {"track": track, "commit": commit_label, "issues": list(issues)},
            indent=2,
        ))
        if strict and issues:
            raise typer.Exit(code=ExitCode.USER_ERROR)
        return

    typer.echo(f"\nVoice-leading check: {track} — {commit_label}")
    if not issues:
        typer.echo("✅ No voice-leading issues found.")
        return

    typer.echo(f"⚠️  {len(issues)} issue{'s' if len(issues) != 1 else ''} found\n")
    typer.echo(f"  {'Bar':>4}  {'Type':<22}  Description")
    typer.echo("  " + "─" * 58)
    for issue in issues:
        typer.echo(
            f"  {issue['bar']:>4}  {issue['issue_type']:<22}  {issue['description']}"
        )

    if strict:
        raise typer.Exit(code=ExitCode.USER_ERROR)

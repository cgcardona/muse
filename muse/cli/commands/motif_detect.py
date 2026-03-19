"""muse motif — recurring melodic pattern detection for a MIDI track.

Finds repeated interval sequences (motifs) in a melodic line.  In a swarm
of agents each writing a section, motif detection ensures that a unifying
melodic idea recurs coherently — or surfaces when it has been accidentally
dropped.

Usage::

    muse motif tracks/melody.mid
    muse motif tracks/lead.mid --min-length 4 --min-occurrences 3
    muse motif tracks/violin.mid --commit HEAD~2
    muse motif tracks/piano.mid --json

Output::

    Motif analysis: tracks/melody.mid — working tree
    Found 3 motifs

    Motif 0  [+2 +2 -3]          3×   first: D4   bars: 1, 5, 13
    Motif 1  [+4 -2 -2 +1]       2×   first: G3   bars: 3, 11
    Motif 2  [-1 -1 +3]          2×   first: A4   bars: 7, 15
"""

from __future__ import annotations

import json
import logging
import pathlib

import typer

from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.store import resolve_commit_ref
from muse.plugins.midi._analysis import find_motifs
from muse.plugins.midi._query import load_track, load_track_from_workdir

logger = logging.getLogger(__name__)
app = typer.Typer()


def _read_repo_id(root: pathlib.Path) -> str:
    import json as _json

    return str(_json.loads((root / ".muse" / "repo.json").read_text())["repo_id"])


def _read_branch(root: pathlib.Path) -> str:
    return (root / ".muse" / "HEAD").read_text().strip().removeprefix("refs/heads/").strip()


@app.callback(invoke_without_command=True)
def motif(
    ctx: typer.Context,
    track: str = typer.Argument(..., metavar="TRACK", help="Workspace-relative path to a .mid file."),
    ref: str | None = typer.Option(
        None, "--commit", "-c", metavar="REF",
        help="Analyse a historical snapshot instead of the working tree.",
    ),
    min_length: int = typer.Option(3, "--min-length", "-l", metavar="N", help="Minimum motif length in notes."),
    min_occ: int = typer.Option(2, "--min-occurrences", "-o", metavar="N", help="Minimum number of recurrences."),
    as_json: bool = typer.Option(False, "--json", help="Emit results as JSON."),
) -> None:
    """Find recurring melodic patterns (motifs) in a MIDI track.

    ``muse motif`` scans the interval sequence between consecutive notes and
    finds the most frequently recurring sub-sequences.  It ignores transposition
    — only the interval pattern (the shape) matters, not the starting pitch.

    For agents:
    - Use ``--min-length 4`` for tighter, more distinctive motifs.
    - Use ``--commit`` to check whether a motif introduced in a previous commit
      is still present after a merge.
    - Combine with ``muse note-log`` to track where a motif first appeared.
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

    motifs = find_motifs(notes, min_length=min_length, min_occurrences=min_occ)

    if as_json:
        typer.echo(json.dumps(
            {"track": track, "commit": commit_label, "motifs": list(motifs)},
            indent=2,
        ))
        return

    typer.echo(f"\nMotif analysis: {track} — {commit_label}")
    if not motifs:
        typer.echo(
            f"  (no motifs found with length ≥ {min_length} and occurrences ≥ {min_occ})"
        )
        return

    typer.echo(f"Found {len(motifs)} motif{'s' if len(motifs) != 1 else ''}\n")
    for m in motifs:
        intervals_str = " ".join(f"{iv:+d}" for iv in m["interval_pattern"])
        bars_str = ", ".join(str(b) for b in m["bars"])
        typer.echo(
            f"  Motif {m['id']}  [{intervals_str}]"
            f"  {m['occurrences']}×"
            f"   first: {m['first_pitch']}"
            f"   bars: {bars_str}"
        )

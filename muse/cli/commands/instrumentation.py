"""muse instrumentation — MIDI channel and note-range map for a track.

Shows which MIDI channels carry notes, the pitch range each channel spans,
velocity statistics per channel, and the approximate register (bass/mid/treble).
Agents handling multi-channel orchestration use this to verify that instrument
assignments are coherent before committing.

Usage::

    muse instrumentation tracks/full_score.mid
    muse instrumentation tracks/orchestra.mid --commit HEAD~3
    muse instrumentation tracks/ensemble.mid --json

Output::

    Instrumentation map: tracks/full_score.mid — working tree
    Channels: 4  ·  Total notes: 128

    Ch   Notes  Range        Register   Mean vel
    ───────────────────────────────────────────────
     0      32  C2–G2        bass         78.4
     1      40  C3–C5        mid          72.1
     2      28  G4–E6        treble       65.3
     3      28  F#3–D5       mid          80.0
"""

from __future__ import annotations

import json
import logging
import pathlib
from collections import defaultdict
from typing import TypedDict

import typer

from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.store import read_current_branch, resolve_commit_ref
from muse.plugins.midi._query import NoteInfo, load_track, load_track_from_workdir
from muse.plugins.midi.midi_diff import _pitch_name

logger = logging.getLogger(__name__)
app = typer.Typer()


class ChannelInfo(TypedDict):
    """Statistics for one MIDI channel."""

    channel: int
    note_count: int
    pitch_min: int
    pitch_max: int
    pitch_min_name: str
    pitch_max_name: str
    register: str
    mean_velocity: float


def _register(pitch_min: int, pitch_max: int) -> str:
    mid = (pitch_min + pitch_max) / 2
    if mid < 48:
        return "bass"
    if mid < 72:
        return "mid"
    return "treble"


def _channel_info(channel: int, notes: list[NoteInfo]) -> ChannelInfo:
    pitches = [n.pitch for n in notes]
    vels = [n.velocity for n in notes]
    lo, hi = min(pitches), max(pitches)
    return ChannelInfo(
        channel=channel,
        note_count=len(notes),
        pitch_min=lo,
        pitch_max=hi,
        pitch_min_name=_pitch_name(lo),
        pitch_max_name=_pitch_name(hi),
        register=_register(lo, hi),
        mean_velocity=round(sum(vels) / len(vels), 1),
    )


def _read_repo_id(root: pathlib.Path) -> str:
    import json as _json

    return str(_json.loads((root / ".muse" / "repo.json").read_text())["repo_id"])


def _read_branch(root: pathlib.Path) -> str:
    return read_current_branch(root)


@app.callback(invoke_without_command=True)
def instrumentation(
    ctx: typer.Context,
    track: str = typer.Argument(..., metavar="TRACK", help="Workspace-relative path to a .mid file."),
    ref: str | None = typer.Option(
        None, "--commit", "-c", metavar="REF",
        help="Analyse a historical snapshot instead of the working tree.",
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit results as JSON."),
) -> None:
    """Show per-channel note distribution, pitch range, and register.

    ``muse instrumentation`` groups notes by MIDI channel and reports:
    note count, lowest/highest pitch, register classification, and mean
    velocity.  Use it to verify that instrument roles are coherent — that
    the bass channel stays low, that the melody channel occupies the right
    register, and that no channel is accidentally silent.

    For agents coordinating multi-channel scores, this is the fast sanity
    check before every commit: ``muse instrumentation tracks/score.mid``.
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

    by_channel: dict[int, list[NoteInfo]] = defaultdict(list)
    for n in notes:
        by_channel[n.channel].append(n)

    channels = [_channel_info(ch, ch_notes) for ch, ch_notes in sorted(by_channel.items())]

    if as_json:
        typer.echo(json.dumps(
            {"track": track, "commit": commit_label, "channels": list(channels)},
            indent=2,
        ))
        return

    typer.echo(f"\nInstrumentation map: {track} — {commit_label}")
    typer.echo(f"Channels: {len(channels)}  ·  Total notes: {len(notes)}\n")
    typer.echo(f"  {'Ch':>3}  {'Notes':>6}  {'Range':<14}  {'Register':<10}  {'Mean vel':>8}")
    typer.echo("  " + "─" * 50)
    for ch in channels:
        rng = f"{ch['pitch_min_name']}–{ch['pitch_max_name']}"
        typer.echo(
            f"  {ch['channel']:>3}  {ch['note_count']:>6}  {rng:<14}  "
            f"{ch['register']:<10}  {ch['mean_velocity']:>8.1f}"
        )

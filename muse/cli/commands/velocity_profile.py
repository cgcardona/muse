"""muse velocity-profile — dynamic range analysis for a MIDI track.

Shows the velocity distribution of a MIDI track — peak, average, RMS,
and a per-velocity-bucket histogram.  Reveals the dynamic character of
a composition: is it always forte?  Does it have a wide dynamic range?
Are some bars particularly loud or soft?

Usage::

    muse velocity-profile tracks/melody.mid
    muse velocity-profile tracks/piano.mid --commit HEAD~5
    muse velocity-profile tracks/drums.mid --by-bar
    muse velocity-profile tracks/melody.mid --json

Output::

    Velocity profile: tracks/melody.mid — cb4afaed
    Notes: 23  ·  Range: 48–96  ·  Mean: 78.3  ·  RMS: 79.1

    ppp ( 1–15)  │                                │    0
    pp  (16–31)  │                                │    0
    p   (32–47)  │                                │    0
    mp  (48–63)  │████                            │    2  ( 8.7%)
    mf  (64–79)  │████████████████████████        │   12  (52.2%)
    f   (80–95)  │████████████                    │    8  (34.8%)
    ff  (96–111) │██                              │    1  ( 4.3%)
    fff (112–127)│                                │    0

    Dynamic character: mf–f  (moderate-loud)
"""
from __future__ import annotations

import json
import logging
import math
import pathlib

import typer

from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.store import resolve_commit_ref
from muse.plugins.music._query import (
    NoteInfo,
    load_track,
    load_track_from_workdir,
    notes_by_bar,
)

logger = logging.getLogger(__name__)

app = typer.Typer()

_DYNAMIC_LEVELS: list[tuple[str, int, int]] = [
    ("ppp", 1,   15),
    ("pp",  16,  31),
    ("p",   32,  47),
    ("mp",  48,  63),
    ("mf",  64,  79),
    ("f",   80,  95),
    ("ff",  96,  111),
    ("fff", 112, 127),
]
_BAR_WIDTH = 32  # histogram bar chars


def _velocity_level(velocity: int) -> str:
    for name, lo, hi in _DYNAMIC_LEVELS:
        if lo <= velocity <= hi:
            return name
    return "fff"


def _rms(values: list[int]) -> float:
    if not values:
        return 0.0
    return math.sqrt(sum(v * v for v in values) / len(values))


def _read_repo_id(root: pathlib.Path) -> str:
    return str(json.loads((root / ".muse" / "repo.json").read_text())["repo_id"])


def _read_branch(root: pathlib.Path) -> str:
    head_ref = (root / ".muse" / "HEAD").read_text().strip()
    return head_ref.removeprefix("refs/heads/").strip()


@app.callback(invoke_without_command=True)
def velocity_profile(
    ctx: typer.Context,
    track: str = typer.Argument(..., metavar="TRACK", help="Workspace-relative path to a .mid file."),
    ref: str | None = typer.Option(
        None, "--commit", "-c", metavar="REF",
        help="Analyse a historical snapshot instead of the working tree.",
    ),
    by_bar: bool = typer.Option(
        False, "--by-bar", "-b",
        help="Show per-bar average velocity instead of the overall histogram.",
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit results as JSON."),
) -> None:
    """Analyse the dynamic range and velocity distribution of a MIDI track.

    ``muse velocity-profile`` shows peak, average, and RMS velocity, plus
    a histogram of notes by dynamic level (ppp through fff).

    Use ``--by-bar`` to see per-bar average velocity — useful for spotting
    which sections of a composition are louder or softer.

    Use ``--commit`` to analyse a historical snapshot.  Use ``--json`` for
    agent-readable output.

    This is fundamentally impossible in Git: Git has no model of what the
    MIDI velocity values in a binary file mean.  Muse stores notes as
    structured semantic data, enabling musical dynamics analysis at any
    point in history.
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

    note_list, _tpb = result

    if not note_list:
        typer.echo(f"  (no notes found in '{track}')")
        return

    velocities = [n.velocity for n in note_list]
    v_min = min(velocities)
    v_max = max(velocities)
    v_mean = sum(velocities) / len(velocities)
    v_rms = _rms(velocities)

    # Dynamic level counts.
    level_counts: dict[str, int] = {name: 0 for name, _, _ in _DYNAMIC_LEVELS}
    for v in velocities:
        level_counts[_velocity_level(v)] += 1

    if as_json:
        if by_bar:
            bars = notes_by_bar(note_list)
            bar_data: list[dict[str, int | float]] = [
                {
                    "bar": bar_num,
                    "mean_velocity": round(sum(n.velocity for n in bar_notes) / len(bar_notes), 1),
                    "note_count": len(bar_notes),
                }
                for bar_num, bar_notes in sorted(bars.items())
            ]
            typer.echo(json.dumps(
                {"track": track, "commit": commit_label, "by_bar": bar_data}, indent=2
            ))
        else:
            typer.echo(json.dumps(
                {
                    "track": track,
                    "commit": commit_label,
                    "notes": len(note_list),
                    "min": v_min, "max": v_max,
                    "mean": round(v_mean, 1), "rms": round(v_rms, 1),
                    "histogram": {k: v for k, v in level_counts.items()},
                },
                indent=2,
            ))
        return

    typer.echo(f"\nVelocity profile: {track} — {commit_label}")
    typer.echo(
        f"Notes: {len(note_list)}  ·  Range: {v_min}–{v_max}"
        f"  ·  Mean: {v_mean:.1f}  ·  RMS: {v_rms:.1f}"
    )
    typer.echo("")

    if by_bar:
        bars = notes_by_bar(note_list)
        for bar_num, bar_notes in sorted(bars.items()):
            bar_vels = [n.velocity for n in bar_notes]
            bar_mean = sum(bar_vels) / len(bar_vels)
            bar_len = min(int(bar_mean / 127 * _BAR_WIDTH), _BAR_WIDTH)
            typer.echo(
                f"  bar {bar_num:>4}  {'█' * bar_len:<{_BAR_WIDTH}}  "
                f"avg={bar_mean:>5.1f}  ({len(bar_notes)} notes)"
            )
        return

    total = max(len(velocities), 1)
    for name, lo, hi in _DYNAMIC_LEVELS:
        count = level_counts[name]
        bar_len = min(int(count / total * _BAR_WIDTH), _BAR_WIDTH)
        pct = count / total * 100
        typer.echo(
            f"  {name:<4}({lo:>3}–{hi:>3})  │{'█' * bar_len:<{_BAR_WIDTH}}│"
            f"  {count:>4}  ({pct:>5.1f}%)"
        )

    # Dominant dynamic level.
    dominant = max(level_counts, key=lambda k: level_counts[k])
    typer.echo(f"\nDynamic character: {dominant}")

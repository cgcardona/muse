"""muse piano-roll — ASCII piano roll visualization of a MIDI track.

Renders the note grid as a terminal-friendly ASCII art piano roll:
time runs left-to-right (columns = half-beats), pitches run bottom-to-top.
Consecutive occupied cells for the same note show as "═══" (sustained),
the onset cell shows the pitch name truncated to fit.

Usage::

    muse piano-roll tracks/melody.mid
    muse piano-roll tracks/melody.mid --commit HEAD~3
    muse piano-roll tracks/melody.mid --bars 1-8
    muse piano-roll tracks/melody.mid --resolution 4   # 4 cells per beat

Output::

    Piano roll: tracks/melody.mid — cb4afaed  (bars 1–4,  res=2 cells/beat)

    B5 │                        │                        │
    A5 │                        │                        │
    G5 │  G5══════  G5══════    │  G5══════              │
    F5 │                        │                        │
    E5 │      E5════        E5══│════                    │
    D5 │                        │  D5══════              │
    C5 │  C5══                  │  C5══                  │
    B4 │                        │                        │
       └────────────────────────┴────────────────────────┘
         1       2       3       4       1       2       3
"""

import json
import logging
import pathlib

import typer

from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.store import resolve_commit_ref
from muse.plugins.midi._query import (
    NoteInfo,
    load_track,
    load_track_from_workdir,
)
from muse.plugins.midi.midi_diff import _pitch_name

logger = logging.getLogger(__name__)

app = typer.Typer()

_CELL_WIDTH = 3  # characters per cell in the grid


def _read_repo_id(root: pathlib.Path) -> str:
    return str(json.loads((root / ".muse" / "repo.json").read_text())["repo_id"])


def _read_branch(root: pathlib.Path) -> str:
    head_ref = (root / ".muse" / "HEAD").read_text().strip()
    return head_ref.removeprefix("refs/heads/").strip()


def _render_piano_roll(
    notes: list[NoteInfo],
    tpb: int,
    bar_start: int,
    bar_end: int,
    resolution: int,
) -> list[str]:
    """Render an ASCII piano roll as a list of strings.

    Args:
        notes:      All notes in the track.
        tpb:        Ticks per beat.
        bar_start:  First bar to show (1-indexed).
        bar_end:    Last bar to show (inclusive).
        resolution: Grid cells per beat (1=quarter, 2=eighth, 4=sixteenth).

    Returns:
        Lines of the piano roll grid.
    """
    if not notes:
        return ["  (no notes to display)"]

    # Tick range for the selected bars.
    ticks_per_bar = 4 * max(tpb, 1)
    tick_start = (bar_start - 1) * ticks_per_bar
    tick_end = bar_end * ticks_per_bar
    ticks_per_cell = max(tpb // max(resolution, 1), 1)
    n_cells = (tick_end - tick_start) // ticks_per_cell

    if n_cells > 120:
        n_cells = 120  # terminal width guard

    # Pitch range.
    visible = [n for n in notes if tick_start <= n.start_tick < tick_end]
    if not visible:
        return [f"  (no notes in bars {bar_start}–{bar_end})"]

    pitch_lo = max(min(n.pitch for n in visible) - 1, 0)
    pitch_hi = min(max(n.pitch for n in visible) + 2, 127)

    # Build the cell grid: pitch_row × time_col → label string.
    n_rows = pitch_hi - pitch_lo + 1
    grid: list[list[str]] = [["   "] * n_cells for _ in range(n_rows)]

    for note in visible:
        pitch_row = pitch_hi - note.pitch  # top = high pitch
        col_start = (note.start_tick - tick_start) // ticks_per_cell
        col_end = min(
            (note.start_tick + note.duration_ticks - tick_start) // ticks_per_cell,
            n_cells - 1,
        )
        if col_start >= n_cells:
            continue
        # Onset cell: pitch name.
        pname = _pitch_name(note.pitch)
        onset_str = f"{pname:<3}"[:3]
        grid[pitch_row][col_start] = onset_str
        # Sustain cells.
        for col in range(col_start + 1, col_end + 1):
            grid[pitch_row][col] = "═══"

    # Build bar separator columns.
    bar_sep_cols: set[int] = set()
    for b in range(bar_start, bar_end + 1):
        col = ((b - 1) * ticks_per_bar - tick_start) // ticks_per_cell
        if 0 <= col < n_cells:
            bar_sep_cols.add(col)

    # Render rows.
    lines: list[str] = []
    pitch_label_width = 4  # e.g. "G#5 "
    for row_idx, row in enumerate(grid):
        pitch = pitch_hi - row_idx
        label = f"{_pitch_name(pitch):<4}"
        cells = ""
        for col, cell in enumerate(row):
            if col in bar_sep_cols:
                cells += "│"
            cells += cell
        lines.append(f"  {label} {cells}")

    # Bottom rule.
    bottom = "  " + " " * pitch_label_width
    for col in range(n_cells):
        bottom += "│" if col in bar_sep_cols else "─"
    lines.append(bottom)

    # Beat labels.
    beat_line = "  " + " " * pitch_label_width
    for col in range(n_cells):
        tick = tick_start + col * ticks_per_cell
        beat_in_bar = ((tick % ticks_per_bar) // max(tpb, 1)) + 1
        is_downbeat = tick % ticks_per_bar == 0
        if col in bar_sep_cols:
            beat_line += " "
        beat_line += f"{beat_in_bar:<3}" if is_downbeat else "   "
    lines.append(beat_line)

    return lines


@app.callback(invoke_without_command=True)
def piano_roll(
    ctx: typer.Context,
    track: str = typer.Argument(..., metavar="TRACK", help="Workspace-relative path to a .mid file."),
    ref: str | None = typer.Option(
        None, "--commit", "-c", metavar="REF",
        help="Render from a historical snapshot instead of the working tree.",
    ),
    bars_range: str | None = typer.Option(
        None, "--bars", "-b", metavar="START-END",
        help='Bar range to render, e.g. "1-8". Default: first 8 bars.',
    ),
    resolution: int = typer.Option(
        2, "--resolution", "-r", metavar="N",
        help="Grid cells per beat (1=quarter, 2=eighth, 4=sixteenth). Default: 2.",
    ),
) -> None:
    """Render an ASCII piano roll of a MIDI track.

    ``muse piano-roll`` produces a terminal-friendly piano roll view:
    time runs left-to-right, pitch runs bottom-to-top.  Bar lines are
    shown as vertical separators.  Each note onset shows the pitch name;
    sustained portions show "═══".

    Use ``--bars`` to show a specific bar range.  Use ``--resolution``
    to control grid density (2 = eighth-note resolution, the default).

    This command works on any historical snapshot via ``--commit``, letting
    you visually compare compositions across commits.
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

    # Parse bar range.
    bar_start = 1
    bar_end = 8
    if bars_range is not None:
        parts = bars_range.split("-", 1)
        try:
            bar_start = int(parts[0])
            bar_end = int(parts[1]) if len(parts) > 1 else bar_start + 7
        except ValueError:
            typer.echo(f"❌ Invalid bar range '{bars_range}'. Use 'START-END' e.g. '1-8'.", err=True)
            raise typer.Exit(code=ExitCode.USER_ERROR)

    typer.echo(
        f"\nPiano roll: {track} — {commit_label}  "
        f"(bars {bar_start}–{bar_end},  res={resolution} cells/beat)"
    )
    typer.echo("")

    lines = _render_piano_roll(note_list, tpb, bar_start, bar_end, resolution)
    for line in lines:
        typer.echo(line)

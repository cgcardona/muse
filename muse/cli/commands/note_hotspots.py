"""muse note-hotspots — bar-level churn leaderboard across MIDI tracks.

Walks the commit history and counts how many times each bar in each track
was touched (had notes added or removed).  High churn at the bar level
reveals the musical sections under active evolution — the bridge that's
always changing, the verse that won't settle.

Usage::

    muse note-hotspots
    muse note-hotspots --top 20
    muse note-hotspots --track tracks/melody.mid
    muse note-hotspots --from HEAD~30

Output::

    Note churn — top 10 most-changed bars
    Commits analysed: 47

      1   tracks/melody.mid   bar  8    12 changes
      2   tracks/melody.mid   bar  4     9 changes
      3   tracks/bass.mid     bar  8     7 changes
      4   tracks/piano.mid    bar 12     5 changes

    High churn = compositional instability. Consider locking this section.
"""

from __future__ import annotations

import json
import logging
import pathlib

import typer

from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.store import read_current_branch, resolve_commit_ref
from muse.plugins.midi._query import NoteInfo, walk_commits_for_track

logger = logging.getLogger(__name__)

app = typer.Typer()


def _read_repo_id(root: pathlib.Path) -> str:
    return str(json.loads((root / ".muse" / "repo.json").read_text())["repo_id"])


def _read_branch(root: pathlib.Path) -> str:
    return read_current_branch(root)


def _bar_of_beat_summary(note_summary: str, tpb: int) -> int | None:
    """Extract bar number from a note summary string like 'C4 vel=80 @beat=3.00 dur=1.00'.

    Returns ``None`` when the beat position cannot be parsed.
    """
    for part in note_summary.split():
        if part.startswith("@beat="):
            try:
                beat = float(part.removeprefix("@beat="))
                bar = int(beat // 4) + 1
                return bar
            except ValueError:
                return None
    return None


@app.callback(invoke_without_command=True)
def note_hotspots(
    ctx: typer.Context,
    top: int = typer.Option(20, "--top", "-n", metavar="N", help="Number of bars to show (default: 20)."),
    track_filter: str | None = typer.Option(
        None, "--track", "-t", metavar="TRACK",
        help="Restrict to a specific track file.",
    ),
    from_ref: str | None = typer.Option(
        None, "--from", metavar="REF",
        help="Exclusive start of the commit range (default: initial commit).",
    ),
    to_ref: str | None = typer.Option(
        None, "--to", metavar="REF",
        help="Inclusive end of the commit range (default: HEAD).",
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit results as JSON."),
) -> None:
    """Show the musical sections (bars) that change most often.

    ``muse note-hotspots`` walks the commit history and counts note-level
    changes per bar per track.  High churn at the bar level reveals the
    musical sections under most active revision — the bridge that keeps
    changing, the chorus that won't settle.

    This is the musical equivalent of ``muse hotspots`` for code: instead
    of "which function changes most?", it answers "which bar changes most?"

    Use ``--track`` to focus on a specific MIDI file.  Use ``--from`` /
    ``--to`` to scope to a sprint or release window.
    """
    root = require_repo()
    repo_id = _read_repo_id(root)
    branch = _read_branch(root)

    to_commit = resolve_commit_ref(root, repo_id, branch, to_ref)
    if to_commit is None:
        typer.echo(f"❌ Commit '{to_ref or 'HEAD'}' not found.", err=True)
        raise typer.Exit(code=ExitCode.USER_ERROR)

    from_commit_id: str | None = None
    if from_ref is not None:
        from_c = resolve_commit_ref(root, repo_id, branch, from_ref)
        if from_c is None:
            typer.echo(f"❌ Commit '{from_ref}' not found.", err=True)
            raise typer.Exit(code=ExitCode.USER_ERROR)
        from_commit_id = from_c.commit_id

    # Discover all MIDI tracks touched in history.
    seen_commit_ids: set[str] = set()
    commits_count = 0

    # Walk commits from to_commit back.
    from muse.core.store import read_commit
    bar_counts: dict[tuple[str, int], int] = {}
    current_id: str | None = to_commit.commit_id

    while current_id and current_id != from_commit_id:
        if current_id in seen_commit_ids:
            break
        seen_commit_ids.add(current_id)
        commit = read_commit(root, current_id)
        if commit is None:
            break
        commits_count += 1
        current_id = commit.parent_commit_id

        if commit.structured_delta is None:
            continue

        for op in commit.structured_delta["ops"]:
            track_addr = op["address"]
            if track_filter and track_addr != track_filter:
                continue
            if not track_addr.lower().endswith(".mid"):
                continue
            child_ops = op["child_ops"] if op["op"] == "patch" else []
            for child in child_ops:
                if child["op"] == "insert":
                    summary: str = child["content_summary"]
                elif child["op"] == "delete":
                    summary = child["content_summary"]
                else:
                    continue
                bar_num = _bar_of_beat_summary(summary, 480)  # 480 = standard tpb
                if bar_num is None:
                    continue
                key = (track_addr, bar_num)
                bar_counts[key] = bar_counts.get(key, 0) + 1

    ranked = sorted(bar_counts.items(), key=lambda kv: kv[1], reverse=True)[:top]

    if as_json:
        typer.echo(json.dumps(
            {
                "commits_analysed": commits_count,
                "hotspots": [
                    {"track": t, "bar": b, "changes": c} for (t, b), c in ranked
                ],
            },
            indent=2,
        ))
        return

    track_label = f"  track={track_filter}" if track_filter else ""
    typer.echo(f"\nNote churn — top {len(ranked)} most-changed bars{track_label}")
    typer.echo(f"Commits analysed: {commits_count}")
    typer.echo("")

    if not ranked:
        typer.echo("  (no note-level bar changes found)")
        return

    width = len(str(len(ranked)))
    for rank, ((track_addr, bar_num), count) in enumerate(ranked, 1):
        label = "change" if count == 1 else "changes"
        typer.echo(
            f"  {rank:>{width}}   {track_addr:<40}  bar {bar_num:>4}    {count:>3} {label}"
        )

    typer.echo("")
    typer.echo("High churn = compositional instability. Consider locking this section.")

"""``muse music-query`` — music DSL query over commit history.

Evaluates a predicate expression against the note content of all MIDI tracks
across the commit history and returns matching bars with chord annotations,
agent provenance, and note tables.

Usage::

    muse music-query "note.pitch_class == 'Eb' and bar == 12"
    muse music-query "note.velocity > 100" --track piano.mid
    muse music-query "agent_id == 'counterpoint-bot'" --from HEAD~10
    muse music-query "harmony.quality == 'dim'" --json

Grammar::

    query     = or_expr
    or_expr   = and_expr ( 'or' and_expr )*
    and_expr  = not_expr ( 'and' not_expr )*
    not_expr  = 'not' not_expr | atom
    atom      = '(' query ')' | FIELD OP VALUE
    FIELD     = note.pitch | note.pitch_class | note.velocity |
                note.channel | note.duration | bar | track |
                harmony.chord | harmony.quality |
                author | agent_id | model_id | toolchain_id
    OP        = == | != | > | < | >= | <=

See ``muse/plugins/music/_music_query.py`` for the full grammar reference.
"""
from __future__ import annotations

import json
import logging
import pathlib
import sys

import typer

from muse.core.repo import require_repo
from muse.core.store import get_head_commit_id, read_commit
from muse.plugins.music._music_query import run_query

logger = logging.getLogger(__name__)

app = typer.Typer(no_args_is_help=True)


def _read_branch(root: pathlib.Path) -> str:
    head_ref = (root / ".muse" / "HEAD").read_text().strip()
    return head_ref.removeprefix("refs/heads/").strip()


def _resolve_head(root: pathlib.Path, alias: str | None = None) -> str | None:
    """Resolve ``None``, ``HEAD``, or ``HEAD~N`` to a concrete commit ID."""
    branch = _read_branch(root)
    commit_id = get_head_commit_id(root, branch)
    if commit_id is None:
        return None
    if alias is None or alias == "HEAD":
        return commit_id

    # Handle HEAD~N.
    parts = alias.split("~")
    if len(parts) != 2:
        return alias
    try:
        steps = int(parts[1])
    except ValueError:
        return alias

    current: str | None = commit_id
    for _ in range(steps):
        if current is None:
            break
        commit = read_commit(root, current)
        if commit is None:
            break
        current = commit.parent_commit_id

    return current or alias


@app.command(name="music-query")
def music_query_cmd(
    query_expr: str = typer.Argument(
        ...,
        metavar="QUERY",
        help=(
            "Music query DSL expression.  Examples: "
            "\"note.pitch_class == 'Eb'\", "
            "\"harmony.quality == 'dim' and bar == 8\", "
            "\"agent_id == 'my-bot' and note.velocity > 80\""
        ),
    ),
    track: str | None = typer.Option(
        None,
        "--track",
        "-t",
        metavar="PATH",
        help="Restrict search to a single MIDI file path.",
    ),
    start: str | None = typer.Option(
        None,
        "--from",
        "-f",
        metavar="COMMIT",
        help="Start commit (default: HEAD).",
    ),
    stop: str | None = typer.Option(
        None,
        "--to",
        metavar="COMMIT",
        help="Stop before this commit (exclusive).",
    ),
    max_results: int = typer.Option(
        100,
        "--max-results",
        "-n",
        metavar="N",
        help="Maximum number of matches to return.",
    ),
    as_json: bool = typer.Option(
        False,
        "--json",
        help="Output machine-readable JSON instead of formatted text.",
    ),
) -> None:
    """Query the MIDI note history using a music DSL predicate."""
    root = require_repo()

    start_id = _resolve_head(root, start)
    if start_id is None:
        typer.echo("❌ No commits in this repository.", err=True)
        raise typer.Exit(1)

    try:
        matches = run_query(
            query_expr,
            root,
            start_id,
            track_filter=track,
            from_commit_id=stop,
            max_results=max_results,
        )
    except ValueError as exc:
        typer.echo(f"❌ Query parse error: {exc}", err=True)
        raise typer.Exit(1)

    if not matches:
        typer.echo("No matches found.")
        return

    if as_json:
        sys.stdout.write(json.dumps(matches, indent=2) + "\n")
        return

    for m in matches:
        typer.echo(
            f"commit {m['commit_short']}  {m['committed_at'][:19]}  "
            f"author={m['author']}  agent={m['agent_id'] or '—'}"
        )
        typer.echo(f"  track={m['track']}  bar={m['bar']}  chord={m['chord'] or '—'}")
        for n in m["notes"]:
            typer.echo(
                f"    {n['pitch_class']:3} (MIDI {n['pitch']:3})  "
                f"vel={n['velocity']:3}  ch={n['channel']}  "
                f"beat={n['beat']:.2f}  dur={n['duration_beats']:.2f}"
            )
        typer.echo("")

    typer.echo(f"— {len(matches)} match{'es' if len(matches) != 1 else ''} —")

"""``muse midi-query`` — MIDI DSL query over commit history.

Evaluates a predicate expression against the note content of all MIDI tracks
across the commit history and returns matching bars with chord annotations,
agent provenance, and note tables.

Usage::

    muse midi-query "note.pitch_class == 'Eb' and bar == 12"
    muse midi-query "note.velocity > 100" --track piano.mid
    muse midi-query "agent_id == 'counterpoint-bot'" --from HEAD~10
    muse midi-query "harmony.quality == 'dim'" --json

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

See ``muse/plugins/midi/_midi_query.py`` for the full grammar reference.
"""

from __future__ import annotations

import argparse
import json
import logging
import pathlib
import sys

from muse.core.repo import require_repo
from muse.core.store import get_head_commit_id, read_commit, read_current_branch
from muse.plugins.midi._midi_query import run_query

logger = logging.getLogger(__name__)


def _read_branch(root: pathlib.Path) -> str:
    return read_current_branch(root)


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


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the midi-query subcommand."""
    parser = subparsers.add_parser("query", help="Query the MIDI note history using a MIDI DSL predicate.", description=__doc__)
    parser.add_argument("query_expr", metavar="QUERY", help=(
        "Music query DSL expression.  Examples: "
        "\"note.pitch_class == 'Eb'\", "
        "\"harmony.quality == 'dim' and bar == 8\", "
        "\"agent_id == 'my-bot' and note.velocity > 80\""
    ))
    parser.add_argument("--track", "-t", metavar="PATH", default=None, help="Restrict search to a single MIDI file path.")
    parser.add_argument("--from", "-f", metavar="COMMIT", default=None, dest="start", help="Start commit (default: HEAD).")
    parser.add_argument("--to", metavar="COMMIT", default=None, dest="stop", help="Stop before this commit (exclusive).")
    parser.add_argument("--max-results", "-n", metavar="N", type=int, default=100, help="Maximum number of matches to return.")
    parser.add_argument("--json", action="store_true", dest="as_json", help="Output machine-readable JSON instead of formatted text.")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> None:
    """Query the MIDI note history using a MIDI DSL predicate."""
    query_expr: str = args.query_expr
    track: str | None = args.track
    start: str | None = args.start
    stop: str | None = args.stop
    max_results: int = args.max_results
    as_json: bool = args.as_json

    root = require_repo()

    start_id = _resolve_head(root, start)
    if start_id is None:
        print("❌ No commits in this repository.", file=sys.stderr)
        raise SystemExit(1)

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
        print(f"❌ Query parse error: {exc}", file=sys.stderr)
        raise SystemExit(1)

    if not matches:
        print("No matches found.")
        return

    if as_json:
        sys.stdout.write(json.dumps(matches, indent=2) + "\n")
        return

    for m in matches:
        print(
            f"commit {m['commit_short']}  {m['committed_at'][:19]}  "
            f"author={m['author']}  agent={m['agent_id'] or '—'}"
        )
        print(f"  track={m['track']}  bar={m['bar']}  chord={m['chord'] or '—'}")
        for n in m["notes"]:
            print(
                f"    {n['pitch_class']:3} (MIDI {n['pitch']:3})  "
                f"vel={n['velocity']:3}  ch={n['channel']}  "
                f"beat={n['beat']:.2f}  dur={n['duration_beats']:.2f}"
            )
        print("")

    print(f"— {len(matches)} match{'es' if len(matches) != 1 else ''} —")

"""muse agent-map — show which agents have edited which bars of a MIDI track.

Walks the commit graph and annotates each bar of the composition with the
agent (commit author) that last touched it.  The musical equivalent of
``git blame`` at the bar level — essential in a multi-agent swarm to
understand who owns what section.

Usage::

    muse agent-map tracks/melody.mid
    muse agent-map tracks/bass.mid --depth 20
    muse agent-map tracks/piano.mid --json

Output::

    Agent map: tracks/melody.mid

    Bar   Last author              Commit    Message
    ──────────────────────────────────────────────────────────────
      1   agent-melody-composer    cb4afaed  feat: add intro melody
      2   agent-melody-composer    cb4afaed  feat: add intro melody
      3   agent-harmoniser         9f3a12e7  feat: harmonise verse
      4   agent-harmoniser         9f3a12e7  feat: harmonise verse
      5   agent-arranger           1b2c3d4e  refactor: restructure bridge
    ...
"""

from __future__ import annotations

import json
import logging
import pathlib
from typing import TypedDict

import typer

from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.store import read_current_branch, resolve_commit_ref
from muse.core.validation import sanitize_display
from muse.plugins.midi._query import (
    NoteInfo,
    load_track,
    notes_by_bar,
    walk_commits_for_track,
)

logger = logging.getLogger(__name__)
app = typer.Typer()


class BarAttribution(TypedDict):
    """Attribution record for one bar."""

    bar: int
    author: str
    commit_id: str
    message: str


def _read_repo_id(root: pathlib.Path) -> str:
    import json as _json

    return str(_json.loads((root / ".muse" / "repo.json").read_text())["repo_id"])


def _read_branch(root: pathlib.Path) -> str:
    return read_current_branch(root)


def _bar_set(notes: list[NoteInfo]) -> frozenset[int]:
    return frozenset(notes_by_bar(notes).keys())


@app.callback(invoke_without_command=True)
def agent_map(
    ctx: typer.Context,
    track: str = typer.Argument(..., metavar="TRACK", help="Workspace-relative path to a .mid file."),
    ref: str | None = typer.Option(
        None, "--commit", "-c", metavar="REF",
        help="Start walking from this commit (default: HEAD).",
    ),
    depth: int = typer.Option(
        50, "--depth", "-d", metavar="N",
        help="Maximum commits to walk back (default 50).",
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit results as JSON."),
) -> None:
    """Show which agent last edited each bar of a MIDI track.

    ``muse agent-map`` walks the commit graph from HEAD (or ``--commit``)
    backward and annotates each bar with the commit that introduced or last
    modified it.  When multiple agents work on different sections of a
    composition, this shows the ownership map at a glance.

    Git cannot do this: it has no model of bars or note-level changes.
    Muse tracks note-level diffs at every commit, enabling per-bar blame.
    """
    if depth < 1 or depth > 10_000:
        typer.echo(f"❌ --depth must be between 1 and 10,000 (got {depth}).", err=True)
        raise typer.Exit(code=ExitCode.USER_ERROR)
    root = require_repo()
    repo_id = _read_repo_id(root)
    branch = _read_branch(root)

    start_ref = ref or "HEAD"
    start_commit = resolve_commit_ref(root, repo_id, branch, start_ref)
    if start_commit is None:
        typer.echo(f"❌ Commit '{start_ref}' not found.", err=True)
        raise typer.Exit(code=ExitCode.USER_ERROR)

    history = walk_commits_for_track(root, start_commit.commit_id, track, max_commits=depth)

    # For each bar, find the most recent commit that contains it
    bar_attr: dict[int, BarAttribution] = {}
    prev_bars: frozenset[int] = frozenset()

    for commit, manifest in history:
        if manifest is None or track not in manifest:
            continue
        result = load_track(root, commit.commit_id, track)
        if result is None:
            continue
        notes, _tpb = result
        cur_bars = _bar_set(notes)

        # Bars that appear now but not in the previous (newer) snapshot
        new_bars = cur_bars - prev_bars if prev_bars else cur_bars

        for bar in new_bars:
            if bar not in bar_attr:
                bar_attr[bar] = BarAttribution(
                    bar=bar,
                    author=sanitize_display(commit.author or "unknown"),
                    commit_id=commit.commit_id[:8],
                    message=sanitize_display((commit.message or "").splitlines()[0][:60]),
                )
        prev_bars = cur_bars

    if not bar_attr:
        typer.echo(f"  (no bar attribution data found for '{track}')")
        return

    attributions = sorted(bar_attr.values(), key=lambda a: a["bar"])

    if as_json:
        typer.echo(json.dumps(
            {"track": track, "start_ref": start_ref, "attributions": list(attributions)},
            indent=2,
        ))
        return

    typer.echo(f"\nAgent map: {track}\n")
    typer.echo(f"  {'Bar':>4}  {'Last author':<28}  {'Commit':<10}  Message")
    typer.echo("  " + "─" * 76)
    for attr in attributions:
        typer.echo(
            f"  {attr['bar']:>4}  {attr['author']:<28}  {attr['commit_id']:<10}  {attr['message']}"
        )

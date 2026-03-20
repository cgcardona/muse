"""muse find-phrase — search for a melodic phrase across commit history.

Scans every commit that contains a MIDI track and computes a similarity score
between the query phrase (a short .mid file or a bar range of the track) and
each historical snapshot.  Returns the commits where the phrase appears most
strongly.

Usage::

    muse find-phrase tracks/melody.mid --query query/motif.mid
    muse find-phrase tracks/melody.mid --query query/motif.mid --min-score 0.7
    muse find-phrase tracks/melody.mid --query query/motif.mid --depth 100 --json

Output::

    Phrase search: tracks/melody.mid  (query: query/motif.mid)
    Scanning 24 commits…

    Score   Commit    Author                  Message
    ──────────────────────────────────────────────────────────────────
    0.934   cb4afaed  agent-melody-composer   feat: add intro melody
    0.871   9f3a12e7  agent-harmoniser        feat: harmonise verse
    0.612   1b2c3d4e  agent-arranger          refactor: restructure bridge
"""

from __future__ import annotations

import json
import logging
import pathlib
from typing import TypedDict

import typer

from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.store import resolve_commit_ref
from muse.core.validation import sanitize_display
from muse.plugins.midi._analysis import phrase_similarity
from muse.plugins.midi._query import (
    NoteInfo,
    load_track,
    load_track_from_workdir,
    walk_commits_for_track,
)

logger = logging.getLogger(__name__)
app = typer.Typer()


class PhraseMatch(TypedDict):
    """A commit that contains the searched phrase."""

    score: float
    commit_id: str
    author: str
    message: str


def _read_repo_id(root: pathlib.Path) -> str:
    import json as _json

    return str(_json.loads((root / ".muse" / "repo.json").read_text())["repo_id"])


def _read_branch(root: pathlib.Path) -> str:
    return (root / ".muse" / "HEAD").read_text().strip().removeprefix("refs/heads/").strip()


@app.callback(invoke_without_command=True)
def find_phrase(
    ctx: typer.Context,
    track: str = typer.Argument(..., metavar="TRACK", help="Workspace-relative path to the .mid file to search in."),
    query: str = typer.Option(
        ..., "--query", "-q", metavar="QUERY_MIDI",
        help="Path to a short .mid file containing the phrase to search for.",
    ),
    ref: str | None = typer.Option(
        None, "--commit", "-c", metavar="REF",
        help="Start the history walk from this commit (default: HEAD).",
    ),
    depth: int = typer.Option(
        50, "--depth", "-d", metavar="N",
        help="Maximum commits to scan (default 50).",
    ),
    min_score: float = typer.Option(
        0.5, "--min-score", "-s", metavar="S",
        help="Minimum similarity score to report (default 0.5).",
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit results as JSON."),
) -> None:
    """Search for a melodic phrase across MIDI commit history.

    ``muse find-phrase`` computes pitch-class histogram and interval-fingerprint
    similarity between a query MIDI file and every historical snapshot of a
    track.  Use it to answer: "At which commit did this motif first appear?"
    or "Which branches contain this theme?"

    For agents: pipe the output (``--json``) into a decision loop to select the
    commit with the highest match score as the merge base for a cherry-pick.
    """
    if depth < 1 or depth > 10_000:
        typer.echo(f"❌ --depth must be between 1 and 10,000 (got {depth}).", err=True)
        raise typer.Exit(code=ExitCode.USER_ERROR)
    if not 0.0 <= min_score <= 1.0:
        typer.echo(f"❌ --min-score must be between 0.0 and 1.0 (got {min_score}).", err=True)
        raise typer.Exit(code=ExitCode.USER_ERROR)
    root = require_repo()

    # Load query phrase
    query_result = load_track_from_workdir(root, query)
    if query_result is None:
        typer.echo(f"❌ Query file '{query}' not found or not a valid MIDI file.", err=True)
        raise typer.Exit(code=ExitCode.USER_ERROR)
    query_notes, _qtpb = query_result

    if not query_notes:
        typer.echo(f"  (query file '{query}' contains no notes — cannot search)")
        return

    repo_id = _read_repo_id(root)
    branch = _read_branch(root)
    start_ref = ref or "HEAD"
    start_commit = resolve_commit_ref(root, repo_id, branch, start_ref)
    if start_commit is None:
        typer.echo(f"❌ Commit '{start_ref}' not found.", err=True)
        raise typer.Exit(code=ExitCode.USER_ERROR)

    history = walk_commits_for_track(root, start_commit.commit_id, track, max_commits=depth)

    if not as_json:
        typer.echo(f"\nPhrase search: {track}  (query: {query})")
        typer.echo(f"Scanning {len(history)} commits…\n")

    matches: list[PhraseMatch] = []
    for commit, manifest in history:
        if manifest is None or track not in manifest:
            continue
        result = load_track(root, commit.commit_id, track)
        if result is None:
            continue
        candidate_notes: list[NoteInfo] = result[0]
        if not candidate_notes:
            continue
        score = phrase_similarity(query_notes, candidate_notes)
        if score >= min_score:
            matches.append(PhraseMatch(
                score=score,
                commit_id=commit.commit_id[:8],
                author=sanitize_display(commit.author or "unknown"),
                message=sanitize_display((commit.message or "").splitlines()[0][:60]),
            ))

    matches.sort(key=lambda m: -m["score"])

    if as_json:
        typer.echo(json.dumps(
            {"track": track, "query": query, "matches": list(matches)},
            indent=2,
        ))
        return

    if not matches:
        typer.echo(f"  (no commits with score ≥ {min_score} found)")
        return

    typer.echo(f"  {'Score':>7}  {'Commit':<10}  {'Author':<28}  Message")
    typer.echo("  " + "─" * 74)
    for m in matches:
        typer.echo(
            f"  {m['score']:>7.3f}  {m['commit_id']:<10}  {m['author']:<28}  {m['message']}"
        )

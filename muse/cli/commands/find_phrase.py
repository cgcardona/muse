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

import argparse
import json
import logging
import pathlib
import sys
from typing import TypedDict

from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.store import read_current_branch, resolve_commit_ref
from muse.core.validation import sanitize_display
from muse.plugins.midi._analysis import phrase_similarity
from muse.plugins.midi._query import (
    NoteInfo,
    load_track,
    load_track_from_workdir,
    walk_commits_for_track,
)

logger = logging.getLogger(__name__)


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
    return read_current_branch(root)


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the find-phrase subcommand."""
    parser = subparsers.add_parser("find-phrase", help="Search for a melodic phrase across MIDI commit history.", description=__doc__)
    parser.add_argument("track", metavar="TRACK", help="Workspace-relative path to the .mid file to search in.")
    parser.add_argument("--query", "-q", metavar="QUERY_MIDI", required=True, help="Path to a short .mid file containing the phrase to search for.")
    parser.add_argument("--commit", "-c", metavar="REF", default=None, dest="ref", help="Start the history walk from this commit (default: HEAD).")
    parser.add_argument("--depth", "-d", metavar="N", type=int, default=50, help="Maximum commits to scan (default 50).")
    parser.add_argument("--min-score", "-s", metavar="S", type=float, default=0.5, dest="min_score", help="Minimum similarity score to report (default 0.5).")
    parser.add_argument("--json", action="store_true", dest="as_json", help="Emit results as JSON.")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> None:
    """Search for a melodic phrase across MIDI commit history.

    ``muse find-phrase`` computes pitch-class histogram and interval-fingerprint
    similarity between a query MIDI file and every historical snapshot of a
    track.  Use it to answer: "At which commit did this motif first appear?"
    or "Which branches contain this theme?"

    For agents: pipe the output (``--json``) into a decision loop to select the
    commit with the highest match score as the merge base for a cherry-pick.
    """
    track: str = args.track
    query: str = args.query
    ref: str | None = args.ref
    depth: int = args.depth
    min_score: float = args.min_score
    as_json: bool = args.as_json

    if depth < 1 or depth > 10_000:
        print(f"❌ --depth must be between 1 and 10,000 (got {depth}).", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)
    if not 0.0 <= min_score <= 1.0:
        print(f"❌ --min-score must be between 0.0 and 1.0 (got {min_score}).", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)
    root = require_repo()

    # Load query phrase
    query_result = load_track_from_workdir(root, query)
    if query_result is None:
        print(f"❌ Query file '{query}' not found or not a valid MIDI file.", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)
    query_notes, _qtpb = query_result

    if not query_notes:
        print(f"  (query file '{query}' contains no notes — cannot search)")
        return

    repo_id = _read_repo_id(root)
    branch = _read_branch(root)
    start_ref = ref or "HEAD"
    start_commit = resolve_commit_ref(root, repo_id, branch, start_ref)
    if start_commit is None:
        print(f"❌ Commit '{start_ref}' not found.", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)

    history = walk_commits_for_track(root, start_commit.commit_id, track, max_commits=depth)

    if not as_json:
        print(f"\nPhrase search: {track}  (query: {query})")
        print(f"Scanning {len(history)} commits…\n")

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
        print(json.dumps(
            {"track": track, "query": query, "matches": list(matches)},
            indent=2,
        ))
        return

    if not matches:
        print(f"  (no commits with score ≥ {min_score} found)")
        return

    print(f"  {'Score':>7}  {'Commit':<10}  {'Author':<28}  Message")
    print("  " + "─" * 74)
    for m in matches:
        print(
            f"  {m['score']:>7.3f}  {m['commit_id']:<10}  {m['author']:<28}  {m['message']}"
        )

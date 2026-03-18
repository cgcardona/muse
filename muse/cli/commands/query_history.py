"""muse query-history — temporal symbol search across commit history.

Searches the commit history for symbols matching a predicate expression,
bounded by a commit range.  Unlike ``muse query --all-commits``, this command
is focused on *change events* — it shows when each symbol first appeared, when
it was last seen, how many commits it survived, and what changes occurred.

It answers questions that are impossible in Git:

* "Find all public Python functions introduced after tag v1.0"
* "Show me every class whose signature changed in the last 50 commits"
* "Which functions were present in tag v1.0 but are gone in tag v2.0?"
* "Find methods renamed between two refs"

Usage::

    muse query-history "kind=function" "language=Python"
    muse query-history "name~=validate" --from v1.0 --to HEAD
    muse query-history "kind=class" --from abc12345
    muse query-history "file~=billing" "kind=function" --json

Output::

    Symbol history — kind=function language=Python (42 commits)
    ──────────────────────────────────────────────────────────────

    src/billing.py::compute_total    function  [12 commits]  2026-01-01..2026-03-10
    src/billing.py::compute_tax      function  [ 8 commits]  2026-01-15..2026-03-10
      └─ introduced:  a1b2c3d4  2026-01-15
      └─ last seen:   f7a8b9c0  2026-03-10

Flags:

``--from REF``
    Start of the commit range (exclusive; default: initial commit).

``--to REF``
    End of the commit range (inclusive; default: HEAD).

``--json``
    Emit results as JSON.
"""
from __future__ import annotations

import json
import logging
import pathlib

import typer

from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.store import (
    get_commit_snapshot_manifest,
    resolve_commit_ref,
    walk_commits_between,
)
from muse.plugins.code._predicate import PredicateError, parse_query
from muse.plugins.code._query import language_of, symbols_for_snapshot
from muse.plugins.code.ast_parser import SymbolRecord

logger = logging.getLogger(__name__)

app = typer.Typer()


def _read_repo_id(root: pathlib.Path) -> str:
    return str(json.loads((root / ".muse" / "repo.json").read_text())["repo_id"])


def _read_branch(root: pathlib.Path) -> str:
    head_ref = (root / ".muse" / "HEAD").read_text().strip()
    return head_ref.removeprefix("refs/heads/").strip()


class _SymbolHistory:
    """Accumulated history of one symbol across a commit range."""

    def __init__(self, address: str, kind: str, language: str) -> None:
        self.address = address
        self.kind = kind
        self.language = language
        self.first_commit_id: str = ""
        self.first_committed_at: str = ""
        self.last_commit_id: str = ""
        self.last_committed_at: str = ""
        self.commit_count: int = 0
        self.content_ids: set[str] = set()

    @property
    def change_count(self) -> int:
        """Number of distinct content_ids seen — 1 means unchanged."""
        return len(self.content_ids)

    def record(self, commit_id: str, committed_at: str, content_id: str) -> None:
        if not self.first_commit_id:
            self.first_commit_id = commit_id
            self.first_committed_at = committed_at
        self.last_commit_id = commit_id
        self.last_committed_at = committed_at
        self.commit_count += 1
        self.content_ids.add(content_id)

    def to_dict(self) -> dict[str, str | int]:
        return {
            "address": self.address,
            "kind": self.kind,
            "language": self.language,
            "commit_count": self.commit_count,
            "change_count": self.change_count,
            "first_commit_id": self.first_commit_id[:8],
            "first_committed_at": self.first_committed_at[:10],
            "last_commit_id": self.last_commit_id[:8],
            "last_committed_at": self.last_committed_at[:10],
        }


@app.callback(invoke_without_command=True)
def query_history(
    ctx: typer.Context,
    predicates: list[str] = typer.Argument(
        ..., metavar="PREDICATE...",
        help='One or more predicates, e.g. "kind=function" "language=Python".',
    ),
    from_ref: str | None = typer.Option(
        None, "--from", metavar="REF",
        help="Start of range (exclusive; default: initial commit).",
    ),
    to_ref: str | None = typer.Option(
        None, "--to", metavar="REF",
        help="End of range (inclusive; default: HEAD).",
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit results as JSON."),
) -> None:
    """Search commit history for symbols matching a predicate expression.

    Walks the commit range from ``--from`` to ``--to`` (oldest-first),
    collecting every snapshot where each matching symbol is present.

    Summarises: first appearance, last appearance, commit count, and number
    of distinct implementations (content_id changes).

    The predicate grammar is the same as ``muse query`` — supports OR, NOT,
    and parentheses.

    Examples::

        muse query-history "kind=function" "language=Python"
        muse query-history "name~=validate" --from v1.0 --to HEAD
        muse query-history "kind=class" --json
    """
    root = require_repo()
    repo_id = _read_repo_id(root)
    branch = _read_branch(root)

    if not predicates:
        typer.echo("❌ At least one predicate is required.", err=True)
        raise typer.Exit(code=ExitCode.USER_ERROR)

    try:
        predicate = parse_query(predicates)
    except PredicateError as exc:
        typer.echo(f"❌ {exc}", err=True)
        raise typer.Exit(code=ExitCode.USER_ERROR)

    # Resolve range endpoints.
    to_commit = resolve_commit_ref(root, repo_id, branch, to_ref)
    if to_commit is None:
        typer.echo(f"❌ --to ref '{to_ref or 'HEAD'}' not found.", err=True)
        raise typer.Exit(code=ExitCode.USER_ERROR)

    from_commit_id: str | None = None
    if from_ref is not None:
        from_c = resolve_commit_ref(root, repo_id, branch, from_ref)
        if from_c is None:
            typer.echo(f"❌ --from ref '{from_ref}' not found.", err=True)
            raise typer.Exit(code=ExitCode.USER_ERROR)
        from_commit_id = from_c.commit_id

    # Walk commits oldest-first within the range.
    commits = sorted(
        walk_commits_between(root, to_commit.commit_id, from_commit_id),
        key=lambda c: c.committed_at,
    )

    # Accumulate per-symbol history.
    history: dict[str, _SymbolHistory] = {}
    for commit in commits:
        manifest = get_commit_snapshot_manifest(root, commit.commit_id) or {}
        sym_map = symbols_for_snapshot(root, manifest)
        for file_path, tree in sym_map.items():
            for addr, rec in tree.items():
                if not predicate(file_path, rec):
                    continue
                if addr not in history:
                    history[addr] = _SymbolHistory(
                        address=addr,
                        kind=rec["kind"],
                        language=language_of(file_path),
                    )
                history[addr].record(
                    commit.commit_id,
                    commit.committed_at.isoformat(),
                    rec["content_id"],
                )

    results = sorted(history.values(), key=lambda h: h.address)

    if as_json:
        typer.echo(json.dumps(
            {
                "schema_version": 2,
                "to_commit": to_commit.commit_id[:8],
                "from_commit": from_commit_id[:8] if from_commit_id else None,
                "commits_scanned": len(commits),
                "symbols_found": len(results),
                "results": [r.to_dict() for r in results],
            },
            indent=2,
        ))
        return

    pred_display = " AND ".join(predicates)
    typer.echo(f"\nSymbol history — {pred_display} ({len(commits)} commit(s) scanned)")
    typer.echo("─" * 62)

    if not results:
        typer.echo("  (no matching symbols found in range)")
        return

    max_addr = max(len(r.address) for r in results)
    for r in results:
        change_label = f"{r.change_count} version(s)" if r.change_count > 1 else "stable"
        span = f"{r.first_committed_at[:10]}..{r.last_committed_at[:10]}"
        typer.echo(
            f"  {r.address:<{max_addr}}  {r.kind:<14}  "
            f"[{r.commit_count:>3} commit(s)]  {span}  {change_label}"
        )
        if r.first_commit_id:
            typer.echo(f"    └─ introduced:  {r.first_commit_id[:8]}")
        if r.first_commit_id != r.last_commit_id:
            typer.echo(f"    └─ last seen:   {r.last_commit_id[:8]}")

    typer.echo(f"\n  {len(results)} symbol(s) found")

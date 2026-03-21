"""muse hotspots — symbol churn leaderboard.

Walks the commit history and counts how many commits touched each symbol.
High churn = instability signal.  The functions that change most are the
ones that need the most attention — refactoring targets, test coverage gaps,
or domain logic under active evolution.

Usage::

    muse hotspots
    muse hotspots --top 20
    muse hotspots --kind function --language Python
    muse hotspots --from HEAD~30 --to HEAD

Output::

    Symbol churn — top 10 most-changed symbols
    Commits analysed: 47

      1   src/billing.py::compute_invoice_total    12 changes
      2   src/api.py::handle_request                9 changes
      3   src/auth.py::validate_token               7 changes
      4   src/models.py::User.save                  5 changes

    High churn = instability signal.
"""

from __future__ import annotations

import json
import logging
import pathlib

import typer

from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.store import read_current_branch, resolve_commit_ref
from muse.plugins.code._query import flat_symbol_ops, language_of, walk_commits_range

logger = logging.getLogger(__name__)

app = typer.Typer()


def _read_repo_id(root: pathlib.Path) -> str:
    return str(json.loads((root / ".muse" / "repo.json").read_text())["repo_id"])


def _read_branch(root: pathlib.Path) -> str:
    return read_current_branch(root)


def _collect_churn(
    root: pathlib.Path,
    to_commit_id: str,
    from_commit_id: str | None,
    kind_filter: str | None,
    language_filter: str | None,
) -> tuple[dict[str, int], int]:
    """Return ``(churn_counts, commits_analysed)``."""
    commits = walk_commits_range(root, to_commit_id, from_commit_id)
    counts: dict[str, int] = {}
    for commit in commits:
        if commit.structured_delta is None:
            continue
        for op in flat_symbol_ops(commit.structured_delta["ops"]):
            addr = op["address"]
            if "::" not in addr:
                continue
            file_path = addr.split("::")[0]
            if language_filter and language_of(file_path) != language_filter:
                continue
            counts[addr] = counts.get(addr, 0) + 1
    return counts, len(commits)


@app.callback(invoke_without_command=True)
def hotspots(
    ctx: typer.Context,
    top: int = typer.Option(20, "--top", "-n", metavar="N", help="Number of symbols to show (default: 20)."),
    kind_filter: str | None = typer.Option(
        None, "--kind", "-k", metavar="KIND",
        help="Restrict to symbols of this kind (function, class, method, …).",
    ),
    language_filter: str | None = typer.Option(
        None, "--language", "-l", metavar="LANG",
        help="Restrict to symbols from files of this language.",
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
    """Show the symbols that change most often — the churn leaderboard.

    Walks the commit history and counts how many commits touched each symbol.
    High churn at the function level reveals instability that file-level
    metrics miss: a single file may be stable while one specific function
    inside it burns.

    Use ``--from`` / ``--to`` to scope the analysis to a sprint, a release,
    or any custom range.  Use ``--kind function`` to focus on functions only.
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
        from_commit = resolve_commit_ref(root, repo_id, branch, from_ref)
        if from_commit is None:
            typer.echo(f"❌ Commit '{from_ref}' not found.", err=True)
            raise typer.Exit(code=ExitCode.USER_ERROR)
        from_commit_id = from_commit.commit_id

    counts, total_commits = _collect_churn(
        root, to_commit.commit_id, from_commit_id, kind_filter, language_filter
    )

    if not counts:
        typer.echo("  (no symbol-level changes found in this range)")
        return

    ranked = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:top]

    if as_json:
        typer.echo(json.dumps(
            {"commits_analysed": total_commits, "hotspots": [{"address": a, "changes": c} for a, c in ranked]},
            indent=2,
        ))
        return

    filters = ""
    if kind_filter:
        filters += f"  kind={kind_filter}"
    if language_filter:
        filters += f"  language={language_filter}"
    typer.echo(f"\nSymbol churn — top {len(ranked)} most-changed symbols{filters}")
    typer.echo(f"Commits analysed: {total_commits}")
    typer.echo("")

    width = len(str(len(ranked)))
    for rank, (addr, count) in enumerate(ranked, 1):
        label = "change" if count == 1 else "changes"
        typer.echo(f"  {rank:>{width}}   {addr:<60}  {count:>4} {label}")

    typer.echo("")
    typer.echo("High churn = instability signal. Consider refactoring or adding tests.")

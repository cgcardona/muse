"""muse query — symbol graph predicate query (v2).

SQL for your codebase.  A full predicate DSL over the typed, content-addressed
symbol graph — with OR, NOT, grouping, and an expanded field set.

v2 grammar::

    expr    = or_expr
    or_expr = and_expr ( OR and_expr )*
    and_expr = not_expr ( [AND] not_expr )*   # implicit AND
    not_expr = NOT primary | primary
    primary  = "(" expr ")" | atom
    atom     = KEY OP VALUE

Supported operators::

    =       exact match
    ~=      contains (case-insensitive)
    ^=      starts with (case-insensitive)
    $=      ends with (case-insensitive)
    !=      not equal

Supported keys::

    kind           function | class | method | variable | import | …
    language       Python | Go | Rust | TypeScript | …
    name           bare symbol name
    qualified_name dotted name (User.save)
    file           file path
    hash           content_id prefix (exact-body match)
    body_hash      body_hash prefix
    signature_id   signature_id prefix
    lineno_gt      symbol starts after line N
    lineno_lt      symbol starts before line N

Usage::

    muse query "kind=function" "language=Python" "name~=validate"
    muse query "(kind=function OR kind=method) name^=_"
    muse query "NOT kind=import" "file~=billing"
    muse query "hash=a3f2c9"
    muse query "kind=function" "name$=_test" --commit HEAD~10
    muse query "kind=function" "name~=validate" --all-commits
"""

from __future__ import annotations

import json
import logging
import pathlib

import typer

from muse._version import __version__
from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.store import CommitRecord, get_all_commits, get_commit_snapshot_manifest, read_current_branch, resolve_commit_ref
from muse.plugins.code._predicate import Predicate, PredicateError, parse_query
from muse.plugins.code._query import language_of, symbols_for_snapshot
from muse.plugins.code.ast_parser import SymbolRecord  # used in _query_all_commits signature

logger = logging.getLogger(__name__)

app = typer.Typer()

_KIND_ICON: dict[str, str] = {
    "function": "fn",
    "async_function": "fn~",
    "class": "class",
    "method": "method",
    "async_method": "method~",
    "variable": "var",
    "import": "import",
}


def _read_repo_id(root: pathlib.Path) -> str:
    return str(json.loads((root / ".muse" / "repo.json").read_text())["repo_id"])


def _read_branch(root: pathlib.Path) -> str:
    return read_current_branch(root)


# Predicate parsing is handled by muse.plugins.code._predicate (v2 grammar).


class _HistoricalMatch:
    """A symbol match found in a historical commit (--all-commits mode)."""

    def __init__(
        self,
        address: str,
        rec: SymbolRecord,
        commit: CommitRecord,
        first_seen: bool,
    ) -> None:
        self.address = address
        self.rec = rec
        self.commit = commit
        self.first_seen = first_seen  # True when this is the oldest appearance

    def to_dict(self) -> dict[str, str | int | bool]:
        return {
            "address": self.address,
            "kind": self.rec["kind"],
            "name": self.rec["name"],
            "content_id": self.rec["content_id"],
            "first_seen": self.first_seen,
            "commit_id": self.commit.commit_id,
            "commit_message": self.commit.message,
            "committed_at": self.commit.committed_at.isoformat(),
            "branch": self.commit.branch,
        }


def _query_all_commits(
    root: pathlib.Path,
    filters: list[Predicate],
) -> list[_HistoricalMatch]:
    """Walk every commit oldest-first, apply predicates against each snapshot.

    Returns one entry per (address, commit) pair that matches.  The
    ``first_seen`` flag is True on the oldest commit where each
    (content_id, address) pair appears.
    """
    all_commits = get_all_commits(root)
    if not all_commits:
        return []
    sorted_commits = sorted(all_commits, key=lambda c: c.committed_at)

    results: list[_HistoricalMatch] = []
    # Track content_id → first commit_id for first_seen annotation.
    first_seen_map: dict[str, str] = {}

    for commit in sorted_commits:
        manifest = _manifest_for_commit(root, commit)
        if not manifest:
            continue
        symbol_map = symbols_for_snapshot(root, manifest)
        for file_path, tree in sorted(symbol_map.items()):
            for addr, rec in sorted(tree.items(), key=lambda kv: kv[1]["lineno"]):
                if not all(f(file_path, rec) for f in filters):
                    continue
                cid = rec["content_id"]
                is_first = cid not in first_seen_map
                if is_first:
                    first_seen_map[cid] = commit.commit_id
                results.append(_HistoricalMatch(addr, rec, commit, is_first))

    return results


def _manifest_for_commit(
    root: pathlib.Path,
    commit: CommitRecord,
) -> dict[str, str]:
    """Load the snapshot manifest for *commit*, returning empty dict on failure."""
    snap_path = root / ".muse" / "snapshots" / f"{commit.snapshot_id}.json"
    if not snap_path.exists():
        return {}
    try:
        return dict(json.loads(snap_path.read_text()).get("manifest", {}))
    except (json.JSONDecodeError, KeyError):
        return {}


@app.callback(invoke_without_command=True)
def query(
    ctx: typer.Context,
    predicates: list[str] = typer.Argument(
        ..., metavar="PREDICATE...",
        help="One or more predicates, e.g. \"kind=function\" \"name~=validate\".",
    ),
    ref: str | None = typer.Option(
        None, "--commit", "-c", metavar="REF",
        help="Query a historical snapshot instead of HEAD.",
    ),
    all_commits: bool = typer.Option(
        False, "--all-commits",
        help=(
            "Search across ALL commits (every branch). "
            "Enables temporal hash= queries: find when a function body first appeared. "
            "Mutually exclusive with --commit."
        ),
    ),
    show_hashes: bool = typer.Option(
        False, "--hashes", help="Include content hashes in output.",
    ),
    as_json: bool = typer.Option(
        False, "--json", help="Emit results as JSON.",
    ),
) -> None:
    """Query the symbol graph with a predicate DSL.

    ``muse query`` is SQL for your codebase.  Every predicate is evaluated
    against the typed, content-addressed symbol graph — not raw text.

    Predicate syntax: ``key=value`` (exact), ``key~=value`` (contains),
    ``key^=value`` (starts with), ``key$=value`` (ends with).

    The ``hash`` predicate finds every symbol whose normalized AST matches
    that content hash — duplicate function detection, clone tracking, and
    cross-module copy detection in one query.

    With ``--all-commits``, the query searches every commit ever recorded
    (across all branches), ordered oldest-first.  The first time each unique
    ``content_id`` appears is marked.  This enables temporal queries:
    "when did this function body first enter the repository?"

    \\b
    Examples::

        muse query "kind=function" "language=Python"
        muse query "hash=a3f2c9"
        muse query "hash=a3f2c9" --all-commits   # when did it first appear?
        muse query "name~=validate" --all-commits --json
    """
    root = require_repo()
    repo_id = _read_repo_id(root)
    branch = _read_branch(root)

    if not predicates:
        typer.echo("❌ At least one predicate is required.", err=True)
        raise typer.Exit(code=ExitCode.USER_ERROR)

    if all_commits and ref is not None:
        typer.echo("❌ --all-commits and --commit are mutually exclusive.", err=True)
        raise typer.Exit(code=ExitCode.USER_ERROR)

    # Parse predicates using the predicate grammar (OR / NOT / grouping supported).
    # Each CLI argument is joined with implicit AND; a single argument may
    # contain OR/NOT/parentheses.
    try:
        combined_predicate: Predicate = parse_query(predicates)
    except PredicateError as exc:
        typer.echo(f"❌ {exc}", err=True)
        raise typer.Exit(code=ExitCode.USER_ERROR)
    filters: list[Predicate] = [combined_predicate]

    # ----------------------------------------------------------------
    # --all-commits mode: temporal search across every recorded commit
    # ----------------------------------------------------------------
    if all_commits:
        historical = _query_all_commits(root, filters)
        if as_json:
            typer.echo(json.dumps(
                {
                    "schema_version": __version__,
                    "mode": "all-commits",
                    "results": [h.to_dict() for h in historical],
                },
                indent=2,
            ))
            return
        if not historical:
            pred_display = "  AND  ".join(predicates)
            typer.echo(f"  (no symbols matching: {pred_display}  [searched all commits])")
            return
        # Deduplicate for display: show unique addresses with their first-seen commit.
        seen_addrs: set[str] = set()
        unique: list[_HistoricalMatch] = []
        for h in historical:
            if h.first_seen and h.address not in seen_addrs:
                seen_addrs.add(h.address)
                unique.append(h)
        pred_display = "  AND  ".join(predicates)
        typer.echo(f"\n{len(unique)} unique symbol(s) matching [{pred_display}] across all commits\n")
        for h in unique:
            date_str = h.commit.committed_at.strftime("%Y-%m-%d")
            short_id = h.commit.commit_id[:8]
            icon = _KIND_ICON.get(h.rec["kind"], h.rec["kind"])
            hash_part = f"  {h.rec['content_id'][:8]}.." if show_hashes else ""
            branch_label = f"  [{h.commit.branch}]" if h.commit.branch else ""
            typer.echo(
                f"  {h.address:<60}  {icon:<8}"
                f"  first seen {short_id} {date_str}{branch_label}{hash_part}"
            )
        return

    # ----------------------------------------------------------------
    # Single-snapshot mode (default)
    # ----------------------------------------------------------------
    commit = resolve_commit_ref(root, repo_id, branch, ref)
    if commit is None:
        typer.echo(f"❌ Commit '{ref or 'HEAD'}' not found.", err=True)
        raise typer.Exit(code=ExitCode.USER_ERROR)

    manifest = get_commit_snapshot_manifest(root, commit.commit_id) or {}
    symbol_map = symbols_for_snapshot(root, manifest)

    # Apply all predicates.
    matches: list[tuple[str, str, SymbolRecord]] = []
    for file_path, tree in sorted(symbol_map.items()):
        for addr, rec in sorted(tree.items(), key=lambda kv: kv[1]["lineno"]):
            if all(f(file_path, rec) for f in filters):
                matches.append((file_path, addr, rec))

    if as_json:
        out: list[dict[str, str | int]] = []
        for fp, addr, rec in matches:
            out.append({
                "address": addr,
                "kind": rec["kind"],
                "name": rec["name"],
                "qualified_name": rec["qualified_name"],
                "file": fp,
                "lineno": rec["lineno"],
                "end_lineno": rec["end_lineno"],
                "language": language_of(fp),
                "content_id": rec["content_id"],
                "body_hash": rec["body_hash"],
                "signature_id": rec["signature_id"],
            })
        typer.echo(json.dumps(
            {"schema_version": __version__, "commit": commit.commit_id[:8], "results": out},
            indent=2,
        ))
        return

    if not matches:
        pred_str = "  AND  ".join(predicates)
        typer.echo(f"  (no symbols matching: {pred_str})")
        return

    files_seen: set[str] = set()
    for fp, addr, rec in matches:
        files_seen.add(fp)
        icon = _KIND_ICON.get(rec["kind"], rec["kind"])
        name = rec["qualified_name"]
        line = rec["lineno"]
        hash_part = f"  {rec['content_id'][:8]}.." if show_hashes else ""
        typer.echo(f"  {addr:<60}  {icon:<10}  line {line:>4}{hash_part}")

    pred_display = "  AND  ".join(predicates)
    typer.echo(f"\n{len(matches)} match(es) across {len(files_seen)} file(s)  [{pred_display}]")

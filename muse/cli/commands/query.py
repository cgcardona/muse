"""muse query — symbol graph predicate query.

SQL for your codebase.  A simple predicate DSL over the typed, content-
addressed symbol graph.  Filter by kind, language, name pattern, file path,
or content hash prefix.  Combine any number of predicates — all are AND'd.

The ``hash`` predicate is uniquely powerful: ``hash=a3f2c9`` finds every
symbol whose AST is byte-for-byte identical to the one with that hash prefix
— duplicate function detection, clone tracking, cross-module copy detection.
This has no analogue anywhere in Git's model.

Predicate syntax::

    key=value     exact match
    key~=value    contains (case-insensitive substring)
    key^=value    starts with (case-insensitive)
    key$=value    ends with (case-insensitive)

Predicate keys::

    kind        function | async_function | class | method | async_method | variable | import
    language    Python | TypeScript | JavaScript | Go | Rust | Java | C | C++ | C# | Ruby | Kotlin
    name        matches rec["name"]
    file        matches the file path
    hash        matches content_id prefix (find identical implementations)

Usage::

    muse query "kind=function" "language=Python" "name~=validate"
    muse query "kind=method" "name^=__"
    muse query "hash=a3f2c9"    # find all copies of a specific function body
    muse query "file~=billing" "kind=class"
    muse query "kind=function" "name$=_test" --commit HEAD~10

Output::

    src/billing.py::validate_amount    fn  line  8
    src/auth.py::validate_token        fn  line 14

    2 match(es)
"""
from __future__ import annotations

import json
import logging
import pathlib
import re
from typing import Callable

import typer

from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.store import CommitRecord, get_all_commits, get_commit_snapshot_manifest, resolve_commit_ref
from muse.plugins.code._query import language_of, symbols_for_snapshot
from muse.plugins.code.ast_parser import SymbolRecord

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
    head_ref = (root / ".muse" / "HEAD").read_text().strip()
    return head_ref.removeprefix("refs/heads/").strip()


# ---------------------------------------------------------------------------
# Predicate parsing
# ---------------------------------------------------------------------------


_OP_SUFFIXES = [("~=", "contains"), ("^=", "startswith"), ("$=", "endswith"), ("=", "exact")]


class _PredicateError(ValueError):
    """Raised when a predicate string cannot be parsed."""


def _parse_predicate(
    pred_str: str,
) -> Callable[[str, SymbolRecord], bool]:
    """Parse a single predicate string and return a filter function.

    The filter takes ``(file_path, record)`` and returns ``True`` when the
    record satisfies the predicate.

    Args:
        pred_str: A string like ``"kind=function"``, ``"name~=validate"``, etc.

    Returns:
        A callable predicate.

    Raises:
        _PredicateError: When the string cannot be parsed.
    """
    for suffix, op_name in _OP_SUFFIXES:
        if suffix in pred_str:
            key, _, value = pred_str.partition(suffix)
            key = key.strip()
            value = value.strip()
            break
    else:
        raise _PredicateError(
            f"Cannot parse predicate '{pred_str}'. "
            "Use key=value, key~=value, key^=value, or key$=value."
        )

    value_lower = value.lower()

    def _match(field: str) -> bool:
        f = field.lower()
        if op_name == "exact":
            return f == value_lower
        if op_name == "contains":
            return value_lower in f
        if op_name == "startswith":
            return f.startswith(value_lower)
        # endswith
        return f.endswith(value_lower)

    def predicate(file_path: str, rec: SymbolRecord) -> bool:
        if key == "kind":
            return _match(rec["kind"])
        if key == "language":
            return _match(language_of(file_path))
        if key == "name":
            return _match(rec["name"])
        if key == "file":
            return _match(file_path)
        if key == "hash":
            return rec["content_id"].startswith(value.lower())
        raise _PredicateError(
            f"Unknown predicate key '{key}'. "
            "Valid keys: kind, language, name, file, hash."
        )

    return predicate


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
    filters: list[Callable[[str, SymbolRecord], bool]],
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

    # Parse predicates.
    filters: list[Callable[[str, SymbolRecord], bool]] = []
    for pred_str in predicates:
        try:
            filters.append(_parse_predicate(pred_str))
        except _PredicateError as exc:
            typer.echo(f"❌ {exc}", err=True)
            raise typer.Exit(code=ExitCode.USER_ERROR)

    # ----------------------------------------------------------------
    # --all-commits mode: temporal search across every recorded commit
    # ----------------------------------------------------------------
    if all_commits:
        historical = _query_all_commits(root, filters)
        if as_json:
            typer.echo(json.dumps(
                {"mode": "all-commits", "results": [h.to_dict() for h in historical]},
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
                "language": language_of(fp),
                "content_id": rec["content_id"],
                "body_hash": rec["body_hash"],
                "signature_id": rec["signature_id"],
            })
        typer.echo(json.dumps(out, indent=2))
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

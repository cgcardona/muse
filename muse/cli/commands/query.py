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
from muse.core.store import get_commit_snapshot_manifest, resolve_commit_ref
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

    The ``hash`` predicate is uniquely powerful: ``hash=a3f2c9`` finds every
    symbol whose normalized AST matches that content hash — duplicate function
    detection, clone tracking, and cross-module copy detection in one query.

    All predicates are AND'd.  Use ``--commit`` to query any historical snapshot.
    Use ``--json`` for pipeline integration.

    \\b
    Examples::

        muse query "kind=function" "language=Python"
        muse query "kind=method" "name^=__"
        muse query "hash=a3f2c9"
        muse query "file~=billing" "kind=class"
    """
    root = require_repo()
    repo_id = _read_repo_id(root)
    branch = _read_branch(root)

    if not predicates:
        typer.echo("❌ At least one predicate is required.", err=True)
        raise typer.Exit(code=ExitCode.USER_ERROR)

    # Parse predicates.
    filters: list[Callable[[str, SymbolRecord], bool]] = []
    for pred_str in predicates:
        try:
            filters.append(_parse_predicate(pred_str))
        except _PredicateError as exc:
            typer.echo(f"❌ {exc}", err=True)
            raise typer.Exit(code=ExitCode.USER_ERROR)

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

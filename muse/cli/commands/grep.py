"""muse grep — semantic symbol search across the symbol graph.

Unlike ``git grep`` which searches raw text lines, ``muse grep`` searches
the *typed symbol graph* — only returning actual symbol declarations with
their kind, file, line number, and stable content hash.

No false positives from comments, string literals, or call sites.  Every
result is a real symbol that exists in the repository.

Usage::

    muse grep "validate"                 # all symbols whose name contains "validate"
    muse grep "^handle" --regex          # names matching regex "^handle"
    muse grep "Invoice" --kind class     # only class symbols
    muse grep "compute" --language Go    # only Go symbols
    muse grep "total" --commit HEAD~5    # search a historical snapshot

Output::

    src/billing.py::validate_amount      function   line  8   a3f2c9..
    src/auth.py::validate_token          function   line 14   cb4afa..
    src/auth.py::Validator               class      line 22   1d2e3f..
    src/auth.py::Validator.validate      method     line 28   4a5b6c..

    4 match(es) across 2 files
"""
from __future__ import annotations

import json
import logging
import pathlib
import re

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


@app.callback(invoke_without_command=True)
def grep(
    ctx: typer.Context,
    pattern: str = typer.Argument(..., metavar="PATTERN", help="Name pattern to search for."),
    use_regex: bool = typer.Option(
        False, "--regex", "-e",
        help="Treat PATTERN as a regular expression (default: substring match).",
    ),
    kind_filter: str | None = typer.Option(
        None, "--kind", "-k", metavar="KIND",
        help="Restrict to symbols of this kind (function, class, method, …).",
    ),
    language_filter: str | None = typer.Option(
        None, "--language", "-l", metavar="LANG",
        help="Restrict to symbols from files of this language (Python, Go, …).",
    ),
    ref: str | None = typer.Option(
        None, "--commit", "-c", metavar="REF",
        help="Search a historical commit instead of HEAD.",
    ),
    show_hashes: bool = typer.Option(
        False, "--hashes", help="Include content hashes in output.",
    ),
    as_json: bool = typer.Option(
        False, "--json", help="Emit results as JSON.",
    ),
) -> None:
    """Search the symbol graph by name — not file text.

    ``muse grep`` searches the typed, content-addressed symbol graph.
    Every result is a real symbol declaration — no false positives from
    comments, string literals, or call sites.

    The ``--regex`` flag enables full Python regex syntax.  Without it,
    PATTERN is matched as a case-insensitive substring of the symbol name.

    The ``--hashes`` flag adds the 8-character content-ID prefix to each
    result, enabling downstream filtering by identity (e.g. find clones
    with ``muse query hash=<prefix>``).
    """
    root = require_repo()
    repo_id = _read_repo_id(root)
    branch = _read_branch(root)

    commit = resolve_commit_ref(root, repo_id, branch, ref)
    if commit is None:
        typer.echo(f"❌ Commit '{ref or 'HEAD'}' not found.", err=True)
        raise typer.Exit(code=ExitCode.USER_ERROR)

    manifest = get_commit_snapshot_manifest(root, commit.commit_id) or {}

    try:
        regex = re.compile(pattern, re.IGNORECASE) if use_regex else re.compile(
            re.escape(pattern), re.IGNORECASE
        )
    except re.error as exc:
        typer.echo(f"❌ Invalid regex pattern: {exc}", err=True)
        raise typer.Exit(code=ExitCode.USER_ERROR)

    symbol_map = symbols_for_snapshot(
        root, manifest,
        kind_filter=kind_filter,
        language_filter=language_filter,
    )

    # Filter by name pattern.
    matches: list[tuple[str, str, SymbolRecord]] = []
    for file_path, tree in sorted(symbol_map.items()):
        for addr, rec in sorted(tree.items(), key=lambda kv: kv[1]["lineno"]):
            if regex.search(rec["name"]):
                matches.append((file_path, addr, rec))

    if as_json:
        out: list[dict[str, str | int]] = []
        for _fp, addr, rec in matches:
            out.append({
                "address": addr,
                "kind": rec["kind"],
                "name": rec["name"],
                "qualified_name": rec["qualified_name"],
                "file": addr.split("::")[0],
                "lineno": rec["lineno"],
                "language": language_of(addr.split("::")[0]),
                "content_id": rec["content_id"],
            })
        typer.echo(json.dumps(out, indent=2))
        return

    if not matches:
        typer.echo(f"  (no symbols matching '{pattern}')")
        return

    files_seen: set[str] = set()
    for file_path, addr, rec in matches:
        files_seen.add(file_path)
        icon = _KIND_ICON.get(rec["kind"], rec["kind"])
        name = rec["qualified_name"]
        line = rec["lineno"]
        hash_part = f"  {rec['content_id'][:8]}.." if show_hashes else ""
        typer.echo(f"  {addr:<60}  {icon:<10}  line {line:>4}{hash_part}")

    typer.echo(f"\n{len(matches)} match(es) across {len(files_seen)} file(s)")

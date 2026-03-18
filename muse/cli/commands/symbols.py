"""muse symbols — list every semantic symbol in a snapshot.

This command is unique to Muse: Git stores files as blobs of text and has no
concept of the functions, classes, or methods inside them.  ``muse symbols``
exposes the *semantic interior* of every source file in a commit — the full
symbol graph that the code plugin builds at commit time.

Output (default — human-readable table)::

    src/utils.py
      function  calculate_total     line 12  a3f2c9..
      function  _validate_amount    line 28  cb4afa..
      class     Invoice             line 45  1d2e3f..
      method    Invoice.to_dict     line 52  4a5b6c..
      method    Invoice.from_dict   line 61  7d8e9f..

    src/models.py
      class     User                line  8  b1c2d3..
      method    User.__init__       line 10  e4f5a6..
      method    User.save           line 19  b7c8d9..

    12 symbols across 2 files  (Python: 12)

Flags:

``--commit <ref>``
    Inspect a specific commit instead of HEAD.

``--kind <kind>``
    Filter to symbols of a specific kind (``function``, ``class``,
    ``method``, ``async_method``, ``variable``, ``import``).

``--file <path>``
    Show symbols from a single file only.

``--count``
    Print only the total symbol count and per-language breakdown.

``--json``
    Emit the full symbol table as JSON for tooling integration.
"""
from __future__ import annotations

import json
import logging
import pathlib
from typing import Literal

import typer

from muse.core.errors import ExitCode
from muse.core.object_store import read_object
from muse.core.repo import require_repo
from muse.core.store import (
    get_commit_snapshot_manifest,
    read_commit,
    resolve_commit_ref,
)
from muse.plugins.code.ast_parser import (
    SEMANTIC_EXTENSIONS,
    SymbolRecord,
    SymbolTree,
    parse_symbols,
)

logger = logging.getLogger(__name__)

app = typer.Typer()

_KindFilter = Literal[
    "function", "async_function", "class", "method", "async_method",
    "variable", "import",
]

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


def _is_semantic(file_path: str) -> bool:
    suffix = pathlib.PurePosixPath(file_path).suffix.lower()
    return suffix in SEMANTIC_EXTENSIONS


def _symbols_for_snapshot(
    root: pathlib.Path,
    manifest: dict[str, str],
    kind_filter: str | None,
    file_filter: str | None,
) -> dict[str, SymbolTree]:
    """Extract symbol trees for all semantic files in *manifest*.

    Returns a dict mapping file_path → SymbolTree, with empty trees omitted.
    """
    result: dict[str, SymbolTree] = {}
    for file_path, object_id in sorted(manifest.items()):
        if not _is_semantic(file_path):
            continue
        if file_filter and file_path != file_filter:
            continue
        raw = read_object(root, object_id)
        if raw is None:
            logger.debug("Object %s missing from store — skipping %s", object_id[:8], file_path)
            continue
        tree = parse_symbols(raw, file_path)
        if kind_filter:
            tree = {addr: rec for addr, rec in tree.items() if rec["kind"] == kind_filter}
        if tree:
            result[file_path] = tree
    return result


def _language_of(file_path: str) -> str:
    suffix = pathlib.PurePosixPath(file_path).suffix.lower()
    _SUFFIX_LANG: dict[str, str] = {
        ".py": "Python", ".pyi": "Python",
        ".ts": "TypeScript", ".tsx": "TypeScript",
        ".js": "JavaScript", ".jsx": "JavaScript",
        ".mjs": "JavaScript", ".cjs": "JavaScript",
        ".go": "Go",
        ".rs": "Rust",
        ".java": "Java",
        ".cs": "C#",
        ".c": "C", ".h": "C",
        ".cpp": "C++", ".cc": "C++", ".cxx": "C++", ".hpp": "C++", ".hxx": "C++",
        ".rb": "Ruby",
        ".kt": "Kotlin", ".kts": "Kotlin",
    }
    return _SUFFIX_LANG.get(suffix, suffix)


def _print_human(
    symbol_map: dict[str, SymbolTree],
    show_hashes: bool,
) -> None:
    total = 0
    lang_counts: dict[str, int] = {}

    for file_path, tree in symbol_map.items():
        lang = _language_of(file_path)
        lang_counts[lang] = lang_counts.get(lang, 0) + len(tree)
        total += len(tree)

        typer.echo(f"\n{file_path}")
        for addr, rec in sorted(tree.items(), key=lambda kv: kv[1]["lineno"]):
            icon = _KIND_ICON.get(rec["kind"], rec["kind"])
            name = rec["qualified_name"]
            line = rec["lineno"]
            hash_suffix = f"  {rec['content_id'][:8]}.." if show_hashes else ""
            typer.echo(f"  {icon:<10}  {name:<40}  line {line:>4}{hash_suffix}")

    if not symbol_map:
        typer.echo("  (no semantic symbols found)")
        return

    lang_str = "  ".join(f"{lang}: {count}" for lang, count in sorted(lang_counts.items()))
    typer.echo(f"\n{total} symbol(s) across {len(symbol_map)} file(s)  ({lang_str})")


def _emit_json(symbol_map: dict[str, SymbolTree]) -> None:
    out: dict[str, list[dict[str, str | int]]] = {}
    for file_path, tree in symbol_map.items():
        entries: list[dict[str, str | int]] = []
        for addr, rec in sorted(tree.items(), key=lambda kv: kv[1]["lineno"]):
            entries.append({
                "address": addr,
                "kind": rec["kind"],
                "name": rec["name"],
                "qualified_name": rec["qualified_name"],
                "lineno": rec["lineno"],
                "end_lineno": rec["end_lineno"],
                "content_id": rec["content_id"],
                "body_hash": rec["body_hash"],
                "signature_id": rec["signature_id"],
            })
        out[file_path] = entries
    typer.echo(json.dumps(out, indent=2))


@app.callback(invoke_without_command=True)
def symbols(
    ctx: typer.Context,
    ref: str | None = typer.Option(
        None, "--commit", "-c", metavar="REF",
        help="Commit ID or branch to inspect (default: HEAD).",
    ),
    kind_filter: str | None = typer.Option(
        None, "--kind", "-k", metavar="KIND",
        help="Filter to symbols of a specific kind "
             "(function, class, method, async_method, variable, import).",
    ),
    file_filter: str | None = typer.Option(
        None, "--file", "-f", metavar="PATH",
        help="Show symbols from a single file only.",
    ),
    count_only: bool = typer.Option(
        False, "--count", help="Print only the total count and language breakdown.",
    ),
    show_hashes: bool = typer.Option(
        False, "--hashes", help="Include content hashes in the output.",
    ),
    as_json: bool = typer.Option(
        False, "--json", help="Emit the full symbol table as JSON.",
    ),
) -> None:
    """List every semantic symbol (function, class, method…) in a snapshot.

    Unlike ``git grep`` or ``ctags``, ``muse symbols`` reads the semantic
    symbol graph produced by the domain plugin's AST analysis — stable,
    content-addressed identities for every symbol, independent of line numbers
    or formatting.

    Use ``--commit <ref>`` to inspect a historical snapshot.  Use ``--kind``
    and ``--file`` to narrow the output.  Use ``--json`` for tooling
    integration.
    """
    root = require_repo()
    repo_id = _read_repo_id(root)
    branch = _read_branch(root)

    commit = resolve_commit_ref(root, repo_id, branch, ref)
    if commit is None:
        label = ref or "HEAD"
        typer.echo(f"❌ Commit '{label}' not found.", err=True)
        raise typer.Exit(code=ExitCode.USER_ERROR)

    manifest = get_commit_snapshot_manifest(root, commit.commit_id) or {}
    if not manifest:
        typer.echo(f"❌ Snapshot for commit {commit.commit_id[:8]} has no files.", err=True)
        raise typer.Exit(code=ExitCode.USER_ERROR)

    symbol_map = _symbols_for_snapshot(root, manifest, kind_filter, file_filter)

    if count_only:
        total = sum(len(t) for t in symbol_map.values())
        lang_counts: dict[str, int] = {}
        for file_path, tree in symbol_map.items():
            lang = _language_of(file_path)
            lang_counts[lang] = lang_counts.get(lang, 0) + len(tree)
        lang_str = "  ".join(f"{lang}: {count}" for lang, count in sorted(lang_counts.items()))
        typer.echo(f"{total} symbol(s)  ({lang_str})")
        return

    if as_json:
        _emit_json(symbol_map)
        return

    typer.echo(f'commit {commit.commit_id[:8]}  "{commit.message}"')
    _print_human(symbol_map, show_hashes)

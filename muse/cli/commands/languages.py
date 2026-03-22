"""muse languages — language breakdown of the current snapshot.

Shows the composition of the repository by programming language —
how many files, symbols, and which symbol kinds are present for
each language.

Usage::

    muse languages
    muse languages --commit HEAD~5
    muse languages --json

Output::

    Language breakdown — commit cb4afaed

      Python       8 files   43 symbols  (fn: 18  class: 5  method: 20)
      TypeScript   3 files   12 symbols  (fn:  4  class: 3  method:  5)
      Go           2 files    8 symbols  (fn:  6  method: 2)
      Rust         1 file     4 symbols  (fn:  2  method: 2)
      ─────────────────────────────────────────────────────────────────
      Total       14 files   67 symbols  (4 languages)
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
from muse.core.store import get_commit_snapshot_manifest, read_current_branch, resolve_commit_ref
from muse.plugins.code._query import language_of, symbols_for_snapshot

logger = logging.getLogger(__name__)


class _LangEntry(TypedDict):
    language: str
    files: int
    symbols: int
    kinds: dict[str, int]


def _read_repo_id(root: pathlib.Path) -> str:
    return str(json.loads((root / ".muse" / "repo.json").read_text())["repo_id"])


def _read_branch(root: pathlib.Path) -> str:
    return read_current_branch(root)


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the languages subcommand."""
    parser = subparsers.add_parser(
        "languages",
        help="Show the language composition of the repository.",
        description=__doc__,
    )
    parser.add_argument(
        "--commit", "-c",
        dest="ref",
        default=None,
        metavar="REF",
        help="Commit to inspect (default: HEAD).",
    )
    parser.add_argument("--json", dest="as_json", action="store_true", help="Emit results as JSON.")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> None:
    """Show the language composition of the repository.

    Counts files and semantic symbols (functions, classes, methods) by
    programming language.  Only languages with AST-level support are shown
    in the symbol breakdown — other file types are counted as files only.

    Use ``--commit`` to inspect any historical snapshot.
    """
    ref: str | None = args.ref
    as_json: bool = args.as_json

    root = require_repo()
    repo_id = _read_repo_id(root)
    branch = _read_branch(root)

    commit = resolve_commit_ref(root, repo_id, branch, ref)
    if commit is None:
        print(f"❌ Commit '{ref or 'HEAD'}' not found.", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)

    # Flat dict[str, str] of file_path → sha256.
    manifest: dict[str, str] = get_commit_snapshot_manifest(root, commit.commit_id) or {}
    symbol_map = symbols_for_snapshot(root, manifest)

    # Accumulate per-language stats.
    lang_files: dict[str, int] = {}
    lang_symbols: dict[str, int] = {}
    lang_kinds: dict[str, dict[str, int]] = {}

    for file_path in manifest:
        lang = language_of(file_path)
        lang_files[lang] = lang_files.get(lang, 0) + 1

    for file_path, tree in symbol_map.items():
        lang = language_of(file_path)
        lang_symbols[lang] = lang_symbols.get(lang, 0) + len(tree)
        kinds = lang_kinds.setdefault(lang, {})
        for rec in tree.values():
            kinds[rec["kind"]] = kinds.get(rec["kind"], 0) + 1

    all_langs = sorted(lang_files)

    if as_json:
        out: list[_LangEntry] = [
            _LangEntry(
                language=lang,
                files=lang_files[lang],
                symbols=lang_symbols.get(lang, 0),
                kinds=lang_kinds.get(lang, {}),
            )
            for lang in all_langs
        ]
        print(json.dumps({"commit": commit.commit_id[:8], "languages": out}, indent=2))
        return

    print(f"\nLanguage breakdown — commit {commit.commit_id[:8]}")
    print("")

    max_lang = max((len(lang) for lang in all_langs), default=8)
    total_files = 0
    total_syms = 0

    for lang in all_langs:
        files = lang_files[lang]
        syms = lang_symbols.get(lang, 0)
        total_files += files
        total_syms += syms
        kinds = lang_kinds.get(lang, {})

        kind_parts: list[str] = []
        for k, label in [
            ("function", "fn"), ("async_function", "fn~"),
            ("class", "class"), ("method", "method"), ("async_method", "method~"),
            ("variable", "var"),
        ]:
            if k in kinds:
                kind_parts.append(f"{label}: {kinds[k]}")
        kind_str = f"  ({',  '.join(kind_parts)})" if kind_parts else ""

        file_label = "file " if files == 1 else "files"
        print(
            f"  {lang:<{max_lang}}   {files:>4} {file_label}   {syms:>5} symbols{kind_str}"
        )

    print("  " + "─" * 60)
    print(
        f"  {'Total':<{max_lang}}   {total_files:>4} files    {total_syms:>5} symbols"
        f"  ({len(all_langs)} languages)"
    )

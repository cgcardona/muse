"""muse code symbols \033[1m—\033[0m list every semantic symbol in a snapshot.

Muse tracks the semantic interior of every source file — the full symbol graph
the code plugin builds at commit time — giving each function, class, method,
and variable a stable, content-addressed identity independent of line numbers
or formatting.

Output (default — human-readable table)::

    \033[1msrc/utils.py\033[0m
      \033[34mfn        \033[0m  calculate_total                           \033[2mline  12\033[0m
      \033[34mfn        \033[0m  _validate_amount                          \033[2mline  28\033[0m
      \033[1m\033[33mclass     \033[0m  Invoice                                   \033[2mline  45\033[0m
      \033[36mmethod    \033[0m  Invoice.to_dict                           \033[2mline  52\033[0m
      \033[36mmethod    \033[0m  Invoice.from_dict                         \033[2mline  61\033[0m

    \033[1msrc/models.py\033[0m
      \033[1m\033[33mclass     \033[0m  User                                      \033[2mline   8\033[0m
      \033[36mmethod    \033[0m  User.__init__                             \033[2mline  10\033[0m
      \033[36mmethod    \033[0m  User.save                                 \033[2mline  19\033[0m

    \033[1m12\033[0m symbols across 2 files  (Python: 12)

Flags:

\033[1m--commit <ref>\033[0m
    Inspect a specific commit instead of HEAD.

\033[1m--kind <kind>\033[0m
    Filter to a specific symbol kind:
    function, async_function, class, method, async_method,
    variable, import, section, rule.

\033[1m--file <path>\033[0m
    Show symbols from a single file only.

\033[1m--language <lang>\033[0m
    Show symbols from files of this language only (e.g. Python, Go, Rust).

\033[1m--count\033[0m
    Print only the total symbol count and per-language breakdown.

\033[1m--hashes\033[0m
    Include content hashes alongside each symbol.

\033[1m--json\033[0m
    Emit the full symbol table as JSON for tooling integration.
"""

from __future__ import annotations

import argparse
import json
import logging
import pathlib
import sys

from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.store import (
    get_commit_snapshot_manifest,
    read_current_branch,
    resolve_commit_ref,
)
from muse.plugins.code._query import language_of, symbols_for_snapshot
from muse.plugins.code.ast_parser import SymbolTree

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# ANSI colour helpers — only emitted when stdout is a TTY.
# ---------------------------------------------------------------------------

_RESET  = "\033[0m"
_BOLD   = "\033[1m"
_DIM    = "\033[2m"
_CYAN   = "\033[36m"
_YELLOW = "\033[33m"
_BLUE   = "\033[34m"
_GREEN  = "\033[32m"
_MAGENTA = "\033[35m"
_WHITE  = "\033[37m"


def _c(text: str, *codes: str, tty: bool) -> str:
    """Wrap *text* in ANSI *codes* when *tty* is True."""
    if not tty:
        return text
    return "".join(codes) + text + _RESET


# Maps symbol kind → (short icon, ANSI colour codes).
_KIND_DISPLAY: dict[str, tuple[str, list[str]]] = {
    "function":      ("fn",       [_BLUE]),
    "async_function": ("fn~",     [_BLUE, _DIM]),
    "class":         ("class",    [_YELLOW, _BOLD]),
    "method":        ("method",   [_CYAN]),
    "async_method":  ("method~",  [_CYAN, _DIM]),
    "variable":      ("var",      [_DIM]),
    "import":        ("import",   [_DIM]),
    "section":       ("section",  [_GREEN]),
    "rule":          ("rule",     [_MAGENTA]),
}

_VALID_KINDS: frozenset[str] = frozenset(_KIND_DISPLAY)

# ---------------------------------------------------------------------------
# Repository helpers
# ---------------------------------------------------------------------------


def _read_repo_id(root: pathlib.Path) -> str:
    return str(json.loads((root / ".muse" / "repo.json").read_text())["repo_id"])


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------


def _lang_counts(symbol_map: dict[str, SymbolTree]) -> dict[str, int]:
    """Return a language-name → symbol-count mapping for *symbol_map*."""
    counts: dict[str, int] = {}
    for file_path, tree in symbol_map.items():
        lang = language_of(file_path)
        counts[lang] = counts.get(lang, 0) + len(tree)
    return counts


def _print_human(
    symbol_map: dict[str, SymbolTree],
    show_hashes: bool,
    tty: bool,
) -> None:
    """Render symbol_map as a human-readable, optionally coloured table."""
    if not symbol_map:
        print("  (no semantic symbols found)")
        return

    total = 0
    for file_path, tree in symbol_map.items():
        total += len(tree)
        print(f"\n{_c(file_path, _BOLD, tty=tty)}")
        for _addr, rec in sorted(tree.items(), key=lambda kv: kv[1]["lineno"]):
            kind = rec["kind"]
            icon, colour_codes = _KIND_DISPLAY.get(kind, (kind, []))
            name = rec["qualified_name"]
            lineno = rec["lineno"]
            icon_str = _c(f"{icon:<10}", *colour_codes, tty=tty)
            name_str = f"{name:<40}"
            line_str = _c(f"line {lineno:>4}", _DIM, tty=tty)
            hash_suffix = (
                _c(f"  {rec['content_id'][:8]}..", _DIM, tty=tty)
                if show_hashes
                else ""
            )
            print(f"  {icon_str}  {name_str}  {line_str}{hash_suffix}")

    counts = _lang_counts(symbol_map)
    lang_str = ", ".join(f"{lang}: {count:,}" for lang, count in sorted(counts.items()))
    sym_word = "symbol" if total == 1 else "symbols"
    file_word = "file" if len(symbol_map) == 1 else "files"
    print(
        f"\n{_c(f'{total:,}', _BOLD, tty=tty)} {sym_word} across "
        f"{len(symbol_map):,} {file_word}  ({lang_str})"
    )


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
    print(json.dumps(out, indent=2))


# ---------------------------------------------------------------------------
# Argument parser registration
# ---------------------------------------------------------------------------


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the symbols subcommand."""
    parser = subparsers.add_parser(
        "symbols",
        help="List every semantic symbol (function, class, method…) in a snapshot.",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--commit", "-c",
        dest="ref",
        default=None,
        metavar="REF",
        help="Commit ID or branch to inspect (default: HEAD).",
    )
    parser.add_argument(
        "--kind", "-k",
        dest="kind_filter",
        default=None,
        metavar="KIND",
        help=(
            "Filter to symbols of a specific kind "
            "(function, async_function, class, method, async_method, "
            "variable, import, section, rule)."
        ),
    )
    parser.add_argument(
        "--file", "-f",
        dest="file_filter",
        default=None,
        metavar="PATH",
        help="Show symbols from a single file only.",
    )
    parser.add_argument(
        "--language", "-l",
        dest="language_filter",
        default=None,
        metavar="LANG",
        help="Show symbols from files of this language only (e.g. Python, Go, Rust).",
    )
    parser.add_argument(
        "--hashes",
        dest="show_hashes",
        action="store_true",
        help="Include content hashes in the output.",
    )

    output_group = parser.add_mutually_exclusive_group()
    output_group.add_argument(
        "--count",
        dest="count_only",
        action="store_true",
        help="Print only the total symbol count and language breakdown.",
    )
    output_group.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        help="Emit the full symbol table as JSON.",
    )

    parser.set_defaults(func=run)


# ---------------------------------------------------------------------------
# Command entry point
# ---------------------------------------------------------------------------


def run(args: argparse.Namespace) -> None:
    """List every semantic symbol (function, class, method…) in a snapshot.

    Unlike ``git grep`` or ``ctags``, ``muse symbols`` reads the semantic
    symbol graph produced by the domain plugin's AST analysis — stable,
    content-addressed identities for every symbol, independent of line numbers
    or formatting.

    Use ``--commit <ref>`` to inspect a historical snapshot.  Use ``--kind``
    and ``--file`` to narrow the output.  Use ``--json`` for tooling
    integration.
    """
    ref: str | None = args.ref
    kind_filter: str | None = args.kind_filter
    file_filter: str | None = args.file_filter
    language_filter: str | None = args.language_filter
    count_only: bool = args.count_only
    show_hashes: bool = args.show_hashes
    as_json: bool = args.as_json
    tty: bool = sys.stdout.isatty()

    if kind_filter is not None and kind_filter not in _VALID_KINDS:
        valid = ", ".join(sorted(_VALID_KINDS))
        print(
            f"❌ Unknown kind '{kind_filter}'. Valid kinds: {valid}",
            file=sys.stderr,
        )
        raise SystemExit(ExitCode.USER_ERROR)

    root = require_repo()
    repo_id = _read_repo_id(root)
    branch = read_current_branch(root)

    commit = resolve_commit_ref(root, repo_id, branch, ref)
    if commit is None:
        label = ref or "HEAD"
        print(f"❌ Commit '{label}' not found.", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)

    manifest = get_commit_snapshot_manifest(root, commit.commit_id) or {}
    if not manifest:
        print(f"❌ Snapshot for commit {commit.commit_id[:8]} has no files.", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)

    symbol_map = symbols_for_snapshot(
        root,
        manifest,
        kind_filter=kind_filter,
        file_filter=file_filter,
        language_filter=language_filter,
    )

    if count_only:
        total = sum(len(t) for t in symbol_map.values())
        counts = _lang_counts(symbol_map)
        lang_str = ", ".join(f"{lang}: {count:,}" for lang, count in sorted(counts.items()))
        sym_word = "symbol" if total == 1 else "symbols"
        print(f"{total:,} {sym_word}  ({lang_str})")
        return

    if as_json:
        _emit_json(symbol_map)
        return

    header = f'commit {commit.commit_id[:8]}  "{commit.message}"'
    print(_c(header, _DIM, tty=tty))
    _print_human(symbol_map, show_hashes, tty)

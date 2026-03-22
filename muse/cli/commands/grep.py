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
    muse grep "validate" --json          # machine-readable output for agents

Output::

    src/billing.py::validate_amount      function   line  8   a3f2c9..
    src/auth.py::validate_token          function   line 14   cb4afa..
    src/auth.py::Validator               class      line 22   1d2e3f..
    src/auth.py::Validator.validate      method     line 28   4a5b6c..

    4 match(es) across 2 files

Security note: patterns are capped at 512 characters to prevent ReDoS.
Invalid regex syntax is caught and reported as exit 1 rather than crashing.
"""

from __future__ import annotations

import argparse
import json
import logging
import pathlib
import re
import sys

from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.store import get_commit_snapshot_manifest, read_current_branch, resolve_commit_ref
from muse.plugins.code._query import language_of, symbols_for_snapshot
from muse.plugins.code.ast_parser import SymbolRecord

logger = logging.getLogger(__name__)

# Guard against ReDoS: reject patterns longer than this before compiling.
_MAX_PATTERN_LEN: int = 512

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


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the grep subcommand."""
    parser = subparsers.add_parser(
        "grep",
        help="Search the symbol graph by name — not file text.",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "pattern", metavar="PATTERN",
        help="Name pattern to search for.",
    )
    parser.add_argument(
        "--regex", "-e", action="store_true", dest="use_regex",
        help="Treat PATTERN as a regular expression (default: substring match).",
    )
    parser.add_argument(
        "--kind", "-k", default=None, metavar="KIND", dest="kind_filter",
        help="Restrict to symbols of this kind (function, class, method, …).",
    )
    parser.add_argument(
        "--language", "-l", default=None, metavar="LANG", dest="language_filter",
        help="Restrict to symbols from files of this language (Python, Go, …).",
    )
    parser.add_argument(
        "--commit", "-c", default=None, metavar="REF", dest="ref",
        help="Search a historical commit instead of HEAD.",
    )
    parser.add_argument(
        "--hashes", action="store_true", dest="show_hashes",
        help="Include content hashes in output.",
    )
    parser.add_argument(
        "--json", action="store_true", dest="as_json",
        help="Emit results as JSON.",
    )
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> None:
    """Search the symbol graph by name — not file text.

    ``muse grep`` searches the typed, content-addressed symbol graph.
    Every result is a real symbol declaration — no false positives from
    comments, string literals, or call sites.

    The ``--regex`` flag enables full Python regex syntax.  Without it,
    PATTERN is matched as a case-insensitive substring of the symbol name.

    The ``--hashes`` flag adds the 8-character content-ID prefix to each
    result, enabling downstream filtering by identity (e.g. find clones
    with ``muse query hash=<prefix>``).

    Patterns are capped at 512 characters to guard against ReDoS.
    """
    pattern: str = args.pattern
    use_regex: bool = args.use_regex
    kind_filter: str | None = args.kind_filter
    language_filter: str | None = args.language_filter
    ref: str | None = args.ref
    show_hashes: bool = args.show_hashes
    as_json: bool = args.as_json

    root = require_repo()
    repo_id = _read_repo_id(root)
    branch = _read_branch(root)

    if len(pattern) > _MAX_PATTERN_LEN:
        print(
            f"❌ Pattern too long ({len(pattern)} chars) — maximum is {_MAX_PATTERN_LEN}.",
            file=sys.stderr,
        )
        raise SystemExit(ExitCode.USER_ERROR)

    commit = resolve_commit_ref(root, repo_id, branch, ref)
    if commit is None:
        print(f"❌ Commit '{ref or 'HEAD'}' not found.", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)

    manifest = get_commit_snapshot_manifest(root, commit.commit_id) or {}

    try:
        regex = re.compile(pattern, re.IGNORECASE) if use_regex else re.compile(
            re.escape(pattern), re.IGNORECASE
        )
    except re.error as exc:
        print(f"❌ Invalid regex pattern: {exc}", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)

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
        print(json.dumps(out, indent=2))
        return

    if not matches:
        print(f"  (no symbols matching '{pattern}')")
        return

    files_seen: set[str] = set()
    for file_path, addr, rec in matches:
        files_seen.add(file_path)
        icon = _KIND_ICON.get(rec["kind"], rec["kind"])
        name = rec["qualified_name"]
        line = rec["lineno"]
        hash_part = f"  {rec['content_id'][:8]}.." if show_hashes else ""
        print(f"  {addr:<60}  {icon:<10}  line {line:>4}{hash_part}")

    print(f"\n{len(matches)} match(es) across {len(files_seen)} file(s)")

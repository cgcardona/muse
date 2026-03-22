"""muse deps — import graph and call-graph analysis.

Answers two questions that Git cannot:

**File mode** (``muse deps src/billing.py``):
  What does this file import, and what files in the repo import it?

**Symbol mode** (``muse deps "src/billing.py::compute_invoice_total"``):
  What does this function call?  (Python only; uses stdlib ``ast``.)
  With ``--reverse``: what symbols in the repo call this function?

These relationships are *structural impossibilities* in Git: Git stores files
as blobs of text with no concept of imports or call-sites.  Muse reads the
typed symbol graph produced at commit time and the AST of the working tree
to answer these questions in milliseconds.

Usage::

    muse deps src/billing.py                        # import graph (file)
    muse deps src/billing.py --reverse              # who imports this file?
    muse deps "src/billing.py::compute_invoice_total"          # call graph (Python)
    muse deps "src/billing.py::compute_invoice_total" --reverse  # callers

Flags:

``--commit, -c REF``
    Inspect a historical snapshot instead of HEAD (import graph mode only).

``--reverse``
    Invert the query: show callers instead of callees, or importers instead
    of imports.

``--json``
    Emit results as JSON.
"""

from __future__ import annotations

import argparse
import json
import logging
import pathlib
import sys

from muse.core.errors import ExitCode
from muse.core.object_store import read_object
from muse.core.repo import require_repo
from muse.core.store import get_commit_snapshot_manifest, read_current_branch, resolve_commit_ref
from muse.plugins.code._callgraph import build_reverse_graph, callees_for_symbol
from muse.plugins.code._query import language_of, symbols_for_snapshot
from muse.plugins.code.ast_parser import SymbolTree, parse_symbols

logger = logging.getLogger(__name__)


def _read_repo_id(root: pathlib.Path) -> str:
    return str(json.loads((root / ".muse" / "repo.json").read_text())["repo_id"])


def _read_branch(root: pathlib.Path) -> str:
    return read_current_branch(root)


# ---------------------------------------------------------------------------
# Import graph helpers
# ---------------------------------------------------------------------------


def _imports_in_tree(tree: SymbolTree) -> list[str]:
    """Return the list of module/symbol names imported by symbols in *tree*."""
    return sorted(
        rec["qualified_name"]
        for rec in tree.values()
        if rec["kind"] == "import"
    )


def _file_imports(
    root: pathlib.Path,
    manifest: dict[str, str],
    target_file: str,
) -> list[str]:
    """Return import names declared in *target_file* within *manifest*."""
    obj_id = manifest.get(target_file)
    if obj_id is None:
        return []
    raw = read_object(root, obj_id)
    if raw is None:
        return []
    tree = parse_symbols(raw, target_file)
    return _imports_in_tree(tree)


def _reverse_imports(
    root: pathlib.Path,
    manifest: dict[str, str],
    target_file: str,
) -> list[str]:
    """Return files in *manifest* that import a name matching *target_file*.

    The heuristic: the target file's stem (e.g. ``billing`` for
    ``src/billing.py``) is matched against each other file's import names.
    This catches ``import billing``, ``from billing import X``, and fully-
    qualified paths like ``src.billing``.
    """
    from muse.plugins.code.ast_parser import SEMANTIC_EXTENSIONS
    target_stem = pathlib.PurePosixPath(target_file).stem
    target_module = pathlib.PurePosixPath(target_file).with_suffix("").as_posix().replace("/", ".")
    importers: list[str] = []
    for file_path, obj_id in manifest.items():
        if file_path == target_file:
            continue
        suffix = pathlib.PurePosixPath(file_path).suffix.lower()
        if suffix not in SEMANTIC_EXTENSIONS:
            continue
        raw = read_object(root, obj_id)
        if raw is None:
            continue
        tree = parse_symbols(raw, file_path)
        for imp_name in _imports_in_tree(tree):
            # Match stem or any suffix of the dotted module path.
            if (
                imp_name == target_stem
                or imp_name == target_module
                or imp_name.endswith(f".{target_stem}")
                or imp_name.endswith(f".{target_module}")
                or target_stem in imp_name.split(".")
            ):
                importers.append(file_path)
                break
    return sorted(importers)


# ---------------------------------------------------------------------------
# Call-graph helpers (Python only) — thin wrappers over _callgraph
# ---------------------------------------------------------------------------


def _python_callers(
    root: pathlib.Path,
    manifest: dict[str, str],
    target_name: str,
) -> list[str]:
    """Return addresses of Python symbols that call *target_name*."""
    reverse = build_reverse_graph(root, manifest)
    return reverse.get(target_name, [])


# ---------------------------------------------------------------------------
# Command
# ---------------------------------------------------------------------------


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the deps subcommand."""
    parser = subparsers.add_parser(
        "deps",
        help="Show the import graph or call graph for a file or symbol.",
        description=__doc__,
    )
    parser.add_argument(
        "target", metavar="TARGET",
        help=(
            'File path (e.g. "src/billing.py") for import graph, or '
            'symbol address (e.g. "src/billing.py::compute_invoice_total") for call graph.'
        ),
    )
    parser.add_argument(
        "--reverse", "-r", action="store_true",
        help="Show importers (file mode) or callers (symbol mode) instead.",
    )
    parser.add_argument(
        "--commit", "-c", default=None, metavar="REF", dest="ref",
        help="Inspect a historical commit instead of HEAD (import graph mode only).",
    )
    parser.add_argument(
        "--json", action="store_true", dest="as_json",
        help="Emit results as JSON.",
    )
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> None:
    """Show the import graph or call graph for a file or symbol.

    **File mode** — pass a file path::

        muse deps src/billing.py           # what does billing.py import?
        muse deps src/billing.py --reverse # what files import billing.py?

    **Symbol mode** — pass a symbol address (Python only for call graph)::

        muse deps "src/billing.py::compute_invoice_total"
        muse deps "src/billing.py::compute_invoice_total" --reverse

    Call-graph analysis uses the live working tree for symbol mode.
    Import-graph analysis uses the committed snapshot (``--commit`` to pin).
    """
    target: str = args.target
    reverse: bool = args.reverse
    ref: str | None = args.ref
    as_json: bool = args.as_json

    root = require_repo()
    repo_id = _read_repo_id(root)
    branch = _read_branch(root)

    is_symbol_mode = "::" in target

    # ----------------------------------------------------------------
    # Symbol mode: call-graph (Python only)
    # ----------------------------------------------------------------
    if is_symbol_mode:
        file_rel, sym_qualified = target.split("::", 1)
        lang = language_of(file_rel)
        if lang != "Python":
            print(
                f"⚠️  Call-graph analysis is currently Python-only.  "
                f"'{file_rel}' is {lang}.",
                file=sys.stderr,
            )
            raise SystemExit(ExitCode.USER_ERROR)

        # Read from working tree.
        candidates = [root / file_rel]
        src_path: pathlib.Path | None = None
        for c in candidates:
            if c.exists():
                src_path = c
                break
        if src_path is None:
            print(f"❌ File '{file_rel}' not found in working tree.", file=sys.stderr)
            raise SystemExit(ExitCode.USER_ERROR)

        source = src_path.read_bytes()

        if not reverse:
            callees = callees_for_symbol(source, target)
            if as_json:
                print(json.dumps({"address": target, "calls": callees}, indent=2))
                return
            print(f"\nCallees of {target}")
            print("─" * 62)
            if not callees:
                print("  (no function calls detected)")
            else:
                for name in callees:
                    print(f"  {name}")
            print(f"\n{len(callees)} callee(s)")
        else:
            target_name = sym_qualified.split(".")[-1]
            commit = resolve_commit_ref(root, repo_id, branch, None)
            if commit is None:
                print("❌ No commits found.", file=sys.stderr)
                raise SystemExit(ExitCode.USER_ERROR)
            manifest = get_commit_snapshot_manifest(root, commit.commit_id) or {}
            callers = _python_callers(root, manifest, target_name)
            if as_json:
                print(json.dumps(
                    {"address": target, "target_name": target_name, "called_by": callers},
                    indent=2,
                ))
                return
            print(f"\nCallers of {target}")
            print(f"  (matching bare name: {target_name!r})")
            print("─" * 62)
            if not callers:
                print("  (no callers found in committed snapshot)")
            else:
                for addr in callers:
                    print(f"  {addr}")
            print(f"\n{len(callers)} caller(s) found")
        return

    # ----------------------------------------------------------------
    # File mode: import graph
    # ----------------------------------------------------------------
    commit = resolve_commit_ref(root, repo_id, branch, ref)
    if commit is None:
        print(f"❌ Commit '{ref or 'HEAD'}' not found.", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)

    manifest = get_commit_snapshot_manifest(root, commit.commit_id) or {}

    if not reverse:
        imports = _file_imports(root, manifest, target)
        if as_json:
            print(json.dumps({"file": target, "imports": imports}, indent=2))
            return
        print(f"\nImports declared in {target}")
        print("─" * 62)
        if not imports:
            print("  (no imports found)")
        else:
            for name in imports:
                print(f"  {name}")
        print(f"\n{len(imports)} import(s)")
    else:
        importers = _reverse_imports(root, manifest, target)
        if as_json:
            print(json.dumps({"file": target, "imported_by": importers}, indent=2))
            return
        print(f"\nFiles that import {target}")
        print("─" * 62)
        if not importers:
            print("  (no files import this module in the committed snapshot)")
        else:
            for fp in importers:
                print(f"  {fp}")
        print(f"\n{len(importers)} importer(s) found")

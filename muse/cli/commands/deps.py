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

import ast
import json
import logging
import pathlib

import typer

from muse.core.errors import ExitCode
from muse.core.object_store import read_object
from muse.core.repo import require_repo
from muse.core.store import get_commit_snapshot_manifest, resolve_commit_ref
from muse.plugins.code._query import language_of, symbols_for_snapshot
from muse.plugins.code.ast_parser import SEMANTIC_EXTENSIONS, SymbolTree, parse_symbols

logger = logging.getLogger(__name__)

app = typer.Typer()


def _read_repo_id(root: pathlib.Path) -> str:
    return str(json.loads((root / ".muse" / "repo.json").read_text())["repo_id"])


def _read_branch(root: pathlib.Path) -> str:
    head_ref = (root / ".muse" / "HEAD").read_text().strip()
    return head_ref.removeprefix("refs/heads/").strip()


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
# Call-graph helpers (Python only)
# ---------------------------------------------------------------------------


def _call_name(func_node: ast.expr) -> str | None:
    """Extract a readable callee name from an ``ast.Call`` func node."""
    if isinstance(func_node, ast.Name):
        return func_node.id
    if isinstance(func_node, ast.Attribute):
        # e.g.  obj.method()  → "method"
        # e.g.  module.func() → "func"
        return func_node.attr
    return None


def _find_func_node(
    stmts: list[ast.stmt],
    name_parts: list[str],
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    """Recursively locate a function node by its dotted qualified name."""
    target = name_parts[0]
    for stmt in stmts:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)) and stmt.name == target:
            if len(name_parts) == 1:
                return stmt
        elif isinstance(stmt, ast.ClassDef) and stmt.name == target and len(name_parts) > 1:
            return _find_func_node(stmt.body, name_parts[1:])
    return None


def _python_callees(source: bytes, address: str) -> list[str]:
    """Return sorted unique names of callees inside the Python symbol at *address*."""
    sym_qualified = address.split("::", 1)[1] if "::" in address else address
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    func_node = _find_func_node(tree.body, sym_qualified.split("."))
    if func_node is None:
        return []
    names: set[str] = set()
    for node in ast.walk(func_node):
        if isinstance(node, ast.Call):
            name = _call_name(node.func)
            if name:
                names.add(name)
    return sorted(names)


def _python_callers(
    root: pathlib.Path,
    manifest: dict[str, str],
    target_name: str,
) -> list[str]:
    """Return addresses of Python symbols that call *target_name*.

    *target_name* is the bare function/method name extracted from the address.
    Scans all Python files in *manifest*.
    """
    callers: list[str] = []
    for file_path, obj_id in sorted(manifest.items()):
        if pathlib.PurePosixPath(file_path).suffix.lower() not in {".py", ".pyi"}:
            continue
        raw = read_object(root, obj_id)
        if raw is None:
            continue
        try:
            tree = ast.parse(raw)
        except SyntaxError:
            continue
        sym_tree = parse_symbols(raw, file_path)
        # Check each symbol body for calls to target_name.
        for addr, rec in sym_tree.items():
            if rec["kind"] not in {"function", "async_function", "method", "async_method"}:
                continue
            qualified = rec["qualified_name"]
            func_node = _find_func_node(tree.body, qualified.split("."))
            if func_node is None:
                continue
            for node in ast.walk(func_node):
                if isinstance(node, ast.Call):
                    name = _call_name(node.func)
                    if name == target_name:
                        callers.append(addr)
                        break
    return callers


# ---------------------------------------------------------------------------
# Command
# ---------------------------------------------------------------------------


@app.callback(invoke_without_command=True)
def deps(
    ctx: typer.Context,
    target: str = typer.Argument(
        ..., metavar="TARGET",
        help=(
            'File path (e.g. "src/billing.py") for import graph, or '
            'symbol address (e.g. "src/billing.py::compute_invoice_total") for call graph.'
        ),
    ),
    reverse: bool = typer.Option(
        False, "--reverse", "-r",
        help="Show importers (file mode) or callers (symbol mode) instead.",
    ),
    ref: str | None = typer.Option(
        None, "--commit", "-c", metavar="REF",
        help="Inspect a historical commit instead of HEAD (import graph mode only).",
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit results as JSON."),
) -> None:
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
            typer.echo(
                f"⚠️  Call-graph analysis is currently Python-only.  "
                f"'{file_rel}' is {lang}.",
                err=True,
            )
            raise typer.Exit(code=ExitCode.USER_ERROR)

        # Read from working tree.
        candidates = [root / "muse-work" / file_rel, root / file_rel]
        src_path: pathlib.Path | None = None
        for c in candidates:
            if c.exists():
                src_path = c
                break
        if src_path is None:
            typer.echo(f"❌ File '{file_rel}' not found in working tree.", err=True)
            raise typer.Exit(code=ExitCode.USER_ERROR)

        source = src_path.read_bytes()

        if not reverse:
            callees = _python_callees(source, target)
            if as_json:
                typer.echo(json.dumps({"address": target, "calls": callees}, indent=2))
                return
            typer.echo(f"\nCallees of {target}")
            typer.echo("─" * 62)
            if not callees:
                typer.echo("  (no function calls detected)")
            else:
                for name in callees:
                    typer.echo(f"  {name}")
            typer.echo(f"\n{len(callees)} callee(s)")
        else:
            target_name = sym_qualified.split(".")[-1]
            commit = resolve_commit_ref(root, repo_id, branch, None)
            if commit is None:
                typer.echo("❌ No commits found.", err=True)
                raise typer.Exit(code=ExitCode.USER_ERROR)
            manifest = get_commit_snapshot_manifest(root, commit.commit_id) or {}
            callers = _python_callers(root, manifest, target_name)
            if as_json:
                typer.echo(json.dumps(
                    {"address": target, "target_name": target_name, "called_by": callers},
                    indent=2,
                ))
                return
            typer.echo(f"\nCallers of {target}")
            typer.echo(f"  (matching bare name: {target_name!r})")
            typer.echo("─" * 62)
            if not callers:
                typer.echo("  (no callers found in committed snapshot)")
            else:
                for addr in callers:
                    typer.echo(f"  {addr}")
            typer.echo(f"\n{len(callers)} caller(s) found")
        return

    # ----------------------------------------------------------------
    # File mode: import graph
    # ----------------------------------------------------------------
    commit = resolve_commit_ref(root, repo_id, branch, ref)
    if commit is None:
        typer.echo(f"❌ Commit '{ref or 'HEAD'}' not found.", err=True)
        raise typer.Exit(code=ExitCode.USER_ERROR)

    manifest = get_commit_snapshot_manifest(root, commit.commit_id) or {}

    if not reverse:
        imports = _file_imports(root, manifest, target)
        if as_json:
            typer.echo(json.dumps({"file": target, "imports": imports}, indent=2))
            return
        typer.echo(f"\nImports declared in {target}")
        typer.echo("─" * 62)
        if not imports:
            typer.echo("  (no imports found)")
        else:
            for name in imports:
                typer.echo(f"  {name}")
        typer.echo(f"\n{len(imports)} import(s)")
    else:
        importers = _reverse_imports(root, manifest, target)
        if as_json:
            typer.echo(json.dumps({"file": target, "imported_by": importers}, indent=2))
            return
        typer.echo(f"\nFiles that import {target}")
        typer.echo("─" * 62)
        if not importers:
            typer.echo("  (no files import this module in the committed snapshot)")
        else:
            for fp in importers:
                typer.echo(f"  {fp}")
        typer.echo(f"\n{len(importers)} importer(s) found")

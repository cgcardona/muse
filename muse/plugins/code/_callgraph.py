"""Call-graph construction for the code-domain CLI commands.

Provides two data structures built from the symbol graph and AST walk:

``ForwardGraph``
    ``caller_address → frozenset[callee_bare_name]``
    "What does this function call?"

``ReverseGraph``
    ``callee_bare_name → list[caller_address]``
    "What calls this function name?"

Both structures cover **Python only** (stdlib ``ast``).  tree-sitter languages
receive import-level analysis through the existing import symbol extraction;
call-site extraction requires a tree-sitter query extension that is deferred.

The reverse graph is the foundation for three new commands:

* ``muse impact``   — transitive blast-radius of a change
* ``muse dead``     — symbols with no callers and no importers
* ``muse coverage`` — method call-coverage for a class interface

Design note
-----------
All graph-building functions perform a single linear pass over the manifest
(read blob → parse AST → walk).  They are intentionally not cached, so that
callers always see the committed state for a given manifest.  CLI commands
that need the graph multiple times should call once and pass the result.
"""

from __future__ import annotations

import ast
import logging
import pathlib

from muse.core.object_store import read_object
from muse.plugins.code.ast_parser import SymbolTree, parse_symbols

logger = logging.getLogger(__name__)

#: Mapping from caller symbol address to the set of bare callee names.
ForwardGraph = dict[str, frozenset[str]]

#: Mapping from bare callee name to the list of caller addresses.
ReverseGraph = dict[str, list[str]]

_PY_SUFFIXES: frozenset[str] = frozenset({".py", ".pyi"})


# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------


def call_name(func_node: ast.expr) -> str | None:
    """Return the bare callee name from an ``ast.Call`` func node, or None.

    Handles simple names (``foo()``) and attribute access (``obj.method()``).
    Ignores subscript calls and other exotic forms.
    """
    if isinstance(func_node, ast.Name):
        return func_node.id
    if isinstance(func_node, ast.Attribute):
        return func_node.attr
    return None


def find_func_node(
    stmts: list[ast.stmt],
    name_parts: list[str],
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    """Recursively locate a function node by its dotted qualified-name parts.

    Args:
        stmts:      Statement list from a module or class body.
        name_parts: Dotted path components, e.g. ``["User", "save"]``.

    Returns:
        The matching ``FunctionDef``/``AsyncFunctionDef`` node, or ``None``.
    """
    if not name_parts:
        return None
    target = name_parts[0]
    for stmt in stmts:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)) and stmt.name == target:
            if len(name_parts) == 1:
                return stmt
        elif isinstance(stmt, ast.ClassDef) and stmt.name == target and len(name_parts) > 1:
            return find_func_node(stmt.body, name_parts[1:])
    return None


def callees_for_symbol(source: bytes, address: str) -> list[str]:
    """Return sorted unique bare callee names called by the Python symbol at *address*.

    Args:
        source:  Raw bytes of the Python source file.
        address: Full symbol address, e.g. ``"src/billing.py::compute_invoice_total"``.

    Returns:
        Sorted list of bare callee names.  Empty if the file has a syntax
        error, the symbol is not found, or it is not a function/method.
    """
    if "::" not in address:
        return []
    sym_qualified = address.split("::", 1)[1]
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    func_node = find_func_node(tree.body, sym_qualified.split("."))
    if func_node is None:
        return []
    names: set[str] = set()
    for node in ast.walk(func_node):
        if isinstance(node, ast.Call):
            name = call_name(node.func)
            if name:
                names.add(name)
    return sorted(names)


# ---------------------------------------------------------------------------
# Graph construction
# ---------------------------------------------------------------------------


def build_forward_graph(
    root: pathlib.Path,
    manifest: dict[str, str],
) -> ForwardGraph:
    """Build the forward call graph from *manifest*.

    Scans every Python file in the manifest, walking each symbol's body for
    ``ast.Call`` nodes.

    Args:
        root:     Repository root (used to read blobs from the object store).
        manifest: Snapshot manifest mapping file path → SHA-256 object ID.

    Returns:
        ``{caller_address: frozenset[callee_bare_name]}``.
    """
    graph: ForwardGraph = {}
    for file_path, obj_id in manifest.items():
        if pathlib.PurePosixPath(file_path).suffix.lower() not in _PY_SUFFIXES:
            continue
        raw = read_object(root, obj_id)
        if raw is None:
            continue
        try:
            tree = ast.parse(raw)
        except SyntaxError:
            continue
        sym_tree: SymbolTree = parse_symbols(raw, file_path)
        for addr, rec in sym_tree.items():
            if rec["kind"] not in {"function", "async_function", "method", "async_method"}:
                continue
            func_node = find_func_node(tree.body, rec["qualified_name"].split("."))
            if func_node is None:
                continue
            names: set[str] = set()
            for node in ast.walk(func_node):
                if isinstance(node, ast.Call):
                    name = call_name(node.func)
                    if name:
                        names.add(name)
            graph[addr] = frozenset(names)
    return graph


def build_reverse_graph(
    root: pathlib.Path,
    manifest: dict[str, str],
) -> ReverseGraph:
    """Build the reverse call graph from *manifest*.

    Inverts the forward graph: maps each callee bare name to every caller
    address that calls it.

    Args:
        root:     Repository root.
        manifest: Snapshot manifest mapping file path → SHA-256 object ID.

    Returns:
        ``{callee_bare_name: [caller_address, ...]}``.
    """
    forward = build_forward_graph(root, manifest)
    reverse: ReverseGraph = {}
    for caller_addr, callee_names in forward.items():
        for name in callee_names:
            reverse.setdefault(name, []).append(caller_addr)
    # Sort each caller list for deterministic output.
    for name in reverse:
        reverse[name].sort()
    return reverse


def transitive_callers(
    start_name: str,
    reverse: ReverseGraph,
    max_depth: int = 0,
) -> dict[int, list[str]]:
    """BFS through *reverse* to find all transitive callers of *start_name*.

    Args:
        start_name: Bare function/method name to start from.
        reverse:    Reverse call graph produced by :func:`build_reverse_graph`.
        max_depth:  Maximum BFS depth.  ``0`` means unlimited.

    Returns:
        ``{depth: [caller_address, ...]}``, depth 1 = direct callers.
        Addresses that appear at multiple depths are recorded only at the
        shallowest depth (first encounter wins).
    """
    result: dict[int, list[str]] = {}
    visited_names: set[str] = {start_name}
    visited_addrs: set[str] = set()
    # Queue items: (callee_bare_name, depth)
    queue: list[tuple[str, int]] = [(start_name, 0)]

    while queue:
        name, depth = queue.pop(0)
        if max_depth > 0 and depth >= max_depth:
            continue
        next_depth = depth + 1
        for caller_addr in reverse.get(name, []):
            if caller_addr in visited_addrs:
                continue
            visited_addrs.add(caller_addr)
            result.setdefault(next_depth, []).append(caller_addr)
            # Extract the bare name of the caller to continue BFS.
            caller_name = caller_addr.split("::")[-1].split(".")[-1]
            if caller_name not in visited_names:
                visited_names.add(caller_name)
                queue.append((caller_name, next_depth))

    return result

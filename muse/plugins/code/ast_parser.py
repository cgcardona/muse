"""AST parsing and symbol extraction for the code domain plugin.

This module provides the :class:`LanguageAdapter` protocol and concrete
adapters for parsing source files into :type:`SymbolTree` structures.

Language support matrix
-----------------------
- **Python** (``*.py``, ``*.pyi``): Full AST-based extraction using the
  stdlib :mod:`ast` module.  Content IDs are hashes of normalized (unparsed)
  AST text — insensitive to whitespace, comments, and formatting.
- **All others**: file-level tracking only (raw-bytes SHA-256).

Symbol addresses
----------------
Every extracted symbol is stored in the :type:`SymbolTree` dict under a
stable *address* key of the form::

    "<workspace-relative-posix-path>::<qualified-symbol-name>"

Nested symbols (class methods) use dotted qualified names::

    "src/models.py::User.save"
    "src/models.py::User.__init__"

Top-level symbols::

    "src/utils.py::calculate_total"
    "src/utils.py::import::pathlib"

Content IDs and rename / move detection
----------------------------------------
Each :class:`SymbolRecord` carries three hashes:

``content_id``
    SHA-256 of the full normalized AST of the symbol (includes name,
    signature, and body).  Two symbols are "the same thing" when their
    ``content_id`` matches — regardless of where in the repo they live.

``body_hash``
    SHA-256 of the normalized body statements only (excludes the ``def``
    line).  Used to detect *renames*: same body, different name.

``signature_id``
    SHA-256 of ``"name(args) -> return"``.  Used to detect *implementation-
    only changes*: signature unchanged, body changed.

Extending
---------
Implement :class:`LanguageAdapter` and append an instance to
:data:`ADAPTERS`.  The adapter is selected by the file's suffix, with the
first matching adapter taking priority.
"""
from __future__ import annotations

import ast
import hashlib
import pathlib
from typing import Literal, Protocol, TypedDict, runtime_checkable

# ---------------------------------------------------------------------------
# Symbol record types
# ---------------------------------------------------------------------------

SymbolKind = Literal[
    "function",
    "async_function",
    "class",
    "method",
    "async_method",
    "variable",
    "import",
]


class SymbolRecord(TypedDict):
    """Content-addressed record for a single named symbol in source code."""

    kind: SymbolKind
    name: str
    qualified_name: str  # "ClassName.method" for nested; flat name for top-level
    content_id: str      # SHA-256 of full normalized AST (name + signature + body)
    body_hash: str       # SHA-256 of body stmts only — for rename detection
    signature_id: str    # SHA-256 of "name(args)->return" — for impl-only changes
    lineno: int
    end_lineno: int


#: Flat map from symbol address to :class:`SymbolRecord`.
#: Nested symbols (methods) appear at their qualified address alongside the
#: parent class.
SymbolTree = dict[str, SymbolRecord]


# ---------------------------------------------------------------------------
# Language adapter protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class LanguageAdapter(Protocol):
    """Protocol every language adapter must implement.

    Adapters are stateless.  The same instance may be called concurrently
    for different files without synchronization.
    """

    def supported_extensions(self) -> frozenset[str]:
        """Return the set of lowercase file suffixes this adapter handles."""
        ...

    def parse_symbols(self, source: bytes, file_path: str) -> SymbolTree:
        """Extract the symbol tree from raw source bytes.

        Args:
            source:    Raw bytes of the source file.
            file_path: Workspace-relative POSIX path — used to build the
                       symbol address prefix.

        Returns:
            A :type:`SymbolTree` mapping symbol addresses to
            :class:`SymbolRecord` dicts.  Returns an empty dict on parse
            errors so that the caller can fall through to file-level ops.
        """
        ...

    def file_content_id(self, source: bytes) -> str:
        """Return a stable content identifier for the whole file.

        For AST-capable adapters: hash of the normalized (unparsed) module
        AST — insensitive to formatting and comments.
        For non-AST adapters: SHA-256 of raw bytes.

        Args:
            source: Raw bytes of the file.

        Returns:
            Hex-encoded SHA-256 digest.
        """
        ...


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# Python adapter
# ---------------------------------------------------------------------------


class PythonAdapter:
    """Python language adapter — AST-based, zero external dependencies.

    Uses :func:`ast.parse` for parsing and :func:`ast.unparse` for
    normalization.  The result is a deterministic, whitespace-insensitive
    representation that strips comments and normalizes indentation.

    ``ast.unparse`` is available since Python 3.9; Muse requires 3.12.
    """

    def supported_extensions(self) -> frozenset[str]:
        return frozenset({".py", ".pyi"})

    def parse_symbols(self, source: bytes, file_path: str) -> SymbolTree:
        try:
            tree = ast.parse(source, filename=file_path)
        except SyntaxError:
            return {}
        symbols: SymbolTree = {}
        _extract_stmts(tree.body, file_path, "", symbols)
        return symbols

    def file_content_id(self, source: bytes) -> str:
        try:
            tree = ast.parse(source)
            return _sha256(ast.unparse(tree))
        except SyntaxError:
            return _sha256_bytes(source)


# ---------------------------------------------------------------------------
# AST extraction helpers (module-level so they can be tested independently)
# ---------------------------------------------------------------------------


def _extract_stmts(
    stmts: list[ast.stmt],
    file_path: str,
    class_prefix: str,
    out: SymbolTree,
) -> None:
    """Recursively walk *stmts* and populate *out* with symbol records.

    Args:
        stmts:        Statement list from an :class:`ast.Module` or
                      :class:`ast.ClassDef` body.
        file_path:    Workspace-relative POSIX path — used as address prefix.
        class_prefix: Dotted class path for methods (e.g. ``"MyClass."``).
                      Empty string at top-level.
        out:          Accumulator — modified in place.
    """
    for node in stmts:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            is_async = isinstance(node, ast.AsyncFunctionDef)
            if class_prefix:
                kind: SymbolKind = "async_method" if is_async else "method"
            else:
                kind = "async_function" if is_async else "function"
            qualified = f"{class_prefix}{node.name}"
            addr = f"{file_path}::{qualified}"
            out[addr] = _make_func_record(node, node.name, qualified, kind)

        elif isinstance(node, ast.ClassDef):
            qualified = f"{class_prefix}{node.name}"
            addr = f"{file_path}::{qualified}"
            out[addr] = _make_class_record(node, qualified)
            _extract_stmts(node.body, file_path, f"{qualified}.", out)

        elif isinstance(node, (ast.Assign, ast.AnnAssign)) and not class_prefix:
            # Only top-level assignments — class-level attributes are captured
            # as part of the parent class's content_id.
            for name in _assignment_names(node):
                addr = f"{file_path}::{name}"
                out[addr] = _make_var_record(node, name)

        elif isinstance(node, (ast.Import, ast.ImportFrom)) and not class_prefix:
            for name in _import_names(node):
                addr = f"{file_path}::import::{name}"
                out[addr] = _make_import_record(node, name)


def _make_func_record(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    name: str,
    qualified_name: str,
    kind: SymbolKind,
) -> SymbolRecord:
    full_src = ast.unparse(node)
    body_src = "\n".join(ast.unparse(s) for s in node.body)
    args_src = ast.unparse(node.args)
    ret_src = ast.unparse(node.returns) if node.returns else ""
    return SymbolRecord(
        kind=kind,
        name=name,
        qualified_name=qualified_name,
        content_id=_sha256(full_src),
        body_hash=_sha256(body_src),
        signature_id=_sha256(f"{name}({args_src})->{ret_src}"),
        lineno=node.lineno,
        end_lineno=node.end_lineno or node.lineno,
    )


def _make_class_record(node: ast.ClassDef, qualified_name: str) -> SymbolRecord:
    full_src = ast.unparse(node)
    base_src = ", ".join(ast.unparse(b) for b in node.bases) if node.bases else ""
    # Body hash captures class structure (bases + method names) but NOT method
    # bodies — those change independently and have their own records.
    method_names = sorted(
        n.name
        for n in node.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    )
    structure = f"class {node.name}({base_src}):{method_names}"
    header = f"class {node.name}({base_src})" if node.bases else f"class {node.name}"
    return SymbolRecord(
        kind="class",
        name=node.name,
        qualified_name=qualified_name,
        content_id=_sha256(full_src),
        body_hash=_sha256(structure),
        signature_id=_sha256(header),
        lineno=node.lineno,
        end_lineno=node.end_lineno or node.lineno,
    )


def _make_var_record(node: ast.Assign | ast.AnnAssign, name: str) -> SymbolRecord:
    normalized = ast.unparse(node)
    return SymbolRecord(
        kind="variable",
        name=name,
        qualified_name=name,
        content_id=_sha256(normalized),
        body_hash=_sha256(normalized),
        signature_id=_sha256(name),
        lineno=node.lineno,
        end_lineno=node.end_lineno or node.lineno,
    )


def _make_import_record(
    node: ast.Import | ast.ImportFrom, name: str
) -> SymbolRecord:
    normalized = ast.unparse(node)
    return SymbolRecord(
        kind="import",
        name=name,
        qualified_name=f"import::{name}",
        content_id=_sha256(normalized),
        body_hash=_sha256(normalized),
        signature_id=_sha256(name),
        lineno=node.lineno,
        end_lineno=node.lineno,
    )


def _assignment_names(node: ast.Assign | ast.AnnAssign) -> list[str]:
    if isinstance(node, ast.Assign):
        return [t.id for t in node.targets if isinstance(t, ast.Name)]
    if isinstance(node.target, ast.Name):
        return [node.target.id]
    return []


def _import_names(node: ast.Import | ast.ImportFrom) -> list[str]:
    if isinstance(node, ast.Import):
        return [a.asname or a.name for a in node.names]
    # ImportFrom
    if node.names and node.names[0].name == "*":
        return [f"*:{node.module or '?'}"]
    return [a.asname or a.name for a in node.names]


# ---------------------------------------------------------------------------
# Fallback adapter — file-level identity only, no symbol extraction
# ---------------------------------------------------------------------------


class FallbackAdapter:
    """Fallback adapter for languages without a dedicated AST parser.

    Returns an empty :type:`SymbolTree` (file-level tracking only) and uses
    raw-bytes SHA-256 as the file content ID.
    """

    def __init__(self, extensions: frozenset[str]) -> None:
        self._extensions = extensions

    def supported_extensions(self) -> frozenset[str]:
        return self._extensions

    def parse_symbols(self, source: bytes, file_path: str) -> SymbolTree:  # noqa: ARG002
        return {}

    def file_content_id(self, source: bytes) -> str:
        return _sha256_bytes(source)


# ---------------------------------------------------------------------------
# Adapter registry and public helpers
# ---------------------------------------------------------------------------

_PYTHON = PythonAdapter()
_FALLBACK = FallbackAdapter(frozenset())

#: Adapters checked in order; first match wins.
ADAPTERS: list[LanguageAdapter] = [_PYTHON]

#: File extensions that receive semantic (AST-based) symbol extraction.
SEMANTIC_EXTENSIONS: frozenset[str] = _PYTHON.supported_extensions()

#: Source extensions tracked as first-class files (raw-bytes identity for
#: languages without an AST adapter, AST identity for Python).
SOURCE_EXTENSIONS: frozenset[str] = frozenset({
    ".py", ".pyi",
    ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
    ".swift",
    ".go",
    ".rs",
    ".java",
    ".c", ".cpp", ".cc", ".cxx", ".h", ".hpp",
    ".rb",
    ".kt",
    ".cs",
    ".sh", ".bash", ".zsh",
    ".toml", ".yaml", ".yml", ".json", ".jsonc",
    ".md", ".rst", ".txt",
    ".css", ".scss", ".html",
    ".sql",
    ".proto",
    ".tf",
})


def adapter_for_path(file_path: str) -> LanguageAdapter:
    """Return the best :class:`LanguageAdapter` for *file_path*.

    Checks registered adapters in order; falls back to
    :class:`FallbackAdapter` when no adapter claims the suffix.

    Args:
        file_path: Workspace-relative POSIX path (e.g. ``"src/utils.py"``).

    Returns:
        The first adapter whose :meth:`~LanguageAdapter.supported_extensions`
        set contains the file's lowercase suffix.
    """
    suffix = pathlib.PurePosixPath(file_path).suffix.lower()
    for adapter in ADAPTERS:
        if suffix in adapter.supported_extensions():
            return adapter
    return _FALLBACK


def parse_symbols(source: bytes, file_path: str) -> SymbolTree:
    """Parse *source* with the best available adapter for *file_path*.

    Args:
        source:    Raw bytes of the source file.
        file_path: Workspace-relative POSIX path.

    Returns:
        A :type:`SymbolTree` (may be empty for unsupported file types).
    """
    return adapter_for_path(file_path).parse_symbols(source, file_path)


def file_content_id(source: bytes, file_path: str) -> str:
    """Return the semantic content ID for *file_path* given its raw *source*.

    Args:
        source:    Raw bytes of the file.
        file_path: Workspace-relative POSIX path.

    Returns:
        Hex-encoded SHA-256 digest — AST-based for Python, raw-bytes for others.
    """
    return adapter_for_path(file_path).file_content_id(source)

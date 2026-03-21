"""AST parsing and symbol extraction for the code domain plugin.

This module provides the :class:`LanguageAdapter` protocol and concrete
adapters for parsing source files into :type:`SymbolTree` structures.

Language support matrix
-----------------------
- **Python** (``*.py``, ``*.pyi``): Full AST-based extraction using the
  stdlib :mod:`ast` module.  Content IDs are hashes of normalized (unparsed)
  AST text — insensitive to whitespace, comments, and formatting.
- **JavaScript / TypeScript** (``*.js``, ``*.jsx``, ``*.mjs``, ``*.cjs``,
  ``*.ts``, ``*.tsx``): tree-sitter based.  Async functions, arrow functions
  bound to ``const``/``let``, and module-level variables are all extracted.
- **Go** (``*.go``): tree-sitter based.  Method qualified names carry the
  receiver type (e.g. ``Dog.Bark``).  Package-level ``const``/``var`` included.
- **Rust** (``*.rs``): tree-sitter based.  Functions inside ``impl`` blocks
  are qualified with the implementing type (e.g. ``Dog.bark``).  ``static``,
  ``const``, type aliases, and ``mod`` declarations are extracted.
- **Java** (``*.java``), **C#** (``*.cs``): tree-sitter based.
- **C** (``*.c``, ``*.h``), **C++** (``*.cpp``, ``*.cc``, ``*.cxx``,
  ``*.hpp``, ``*.hxx``): tree-sitter based.  Structs and enums extracted.
- **Ruby** (``*.rb``), **Kotlin** (``*.kt``, ``*.kts``): tree-sitter based.
- **Swift** (``*.swift``): tree-sitter based; requires ``py-tree-sitter-swift``
  (degrades to file-level tracking if the package is unavailable).
- **Markdown** (``*.md``, ``*.rst``, ``*.txt``): ATX headings extracted as
  ``section`` symbols; requires ``tree-sitter-markdown``.
- **CSS / SCSS** (``*.css``, ``*.scss``): rule-sets, keyframes, and media
  queries extracted; requires ``tree-sitter-css``.
- **HTML** (``*.html``, ``*.htm``): semantic elements and id-bearing elements
  extracted; requires ``tree-sitter-html``.

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
import importlib
import logging
import pathlib
import re
import sys
import types as _types
from typing import TYPE_CHECKING, Literal, Protocol, TypedDict, runtime_checkable

if TYPE_CHECKING:
    from tree_sitter import Language, Node, Parser, Query, QueryCursor

logger = logging.getLogger(__name__)

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
    "section",   # Markdown ATX/setext heading; HTML semantic element
    "rule",      # CSS/SCSS rule-set (keyed by selector string)
]


class SymbolRecord(TypedDict):
    """Content-addressed record for a single named symbol in source code.

    Hash dimensions (v2)
    --------------------
    ``content_id``
        SHA-256 of the full normalized symbol (name + signature + body).
    ``body_hash``
        SHA-256 of body statements only.  Same body, different name → rename.
    ``signature_id``
        SHA-256 of ``"name(args) -> return"``.  Stable across body changes.
    ``metadata_id``
        SHA-256 of metadata that wraps the symbol but is not part of its body:
        decorators, ``async`` flag, class bases, visibility modifiers (where
        extractable by the language adapter).  Empty string for legacy records
        or adapters that do not support metadata extraction.
    ``canonical_key``
        Stable machine handle: ``{file}#{scope_path}#{kind}#{name}#{lineno}``.
        Disambiguates overloads and nested scopes.  Unique within a snapshot.
    """

    kind: SymbolKind
    name: str
    qualified_name: str  # "ClassName.method" for nested; flat name for top-level
    content_id: str      # SHA-256 of full normalized AST (name + signature + body)
    body_hash: str       # SHA-256 of body stmts only — for rename detection
    signature_id: str    # SHA-256 of "name(args)->return" — for impl-only changes
    metadata_id: str     # SHA-256 of decorator/async/bases metadata (v2; "" = pre-v2)
    canonical_key: str   # {file}#{scope}#{kind}#{name}#{lineno} — stable handle
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
            out[addr] = _make_func_record(
                node, node.name, qualified, kind,
                file_path=file_path, class_prefix=class_prefix,
            )

        elif isinstance(node, ast.ClassDef):
            qualified = f"{class_prefix}{node.name}"
            addr = f"{file_path}::{qualified}"
            out[addr] = _make_class_record(node, qualified, file_path=file_path)
            _extract_stmts(node.body, file_path, f"{qualified}.", out)

        elif isinstance(node, (ast.Assign, ast.AnnAssign)) and not class_prefix:
            # Only top-level assignments — class-level attributes are captured
            # as part of the parent class's content_id.
            for name in _assignment_names(node):
                addr = f"{file_path}::{name}"
                out[addr] = _make_var_record(node, name, file_path=file_path)

        elif isinstance(node, (ast.Import, ast.ImportFrom)) and not class_prefix:
            for name in _import_names(node):
                addr = f"{file_path}::import::{name}"
                out[addr] = _make_import_record(node, name, file_path=file_path)


def _compute_metadata_id_func(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """SHA-256 of Python function metadata: decorators + async flag."""
    dec_src = " ".join(ast.unparse(d) for d in node.decorator_list)
    async_flag = "async" if isinstance(node, ast.AsyncFunctionDef) else "sync"
    return _sha256(f"{async_flag}:{dec_src}")


def _compute_metadata_id_class(node: ast.ClassDef) -> str:
    """SHA-256 of Python class metadata: decorators + bases."""
    dec_src = " ".join(ast.unparse(d) for d in node.decorator_list)
    base_src = ", ".join(ast.unparse(b) for b in node.bases)
    return _sha256(f"{dec_src}:{base_src}")


def _canonical_key(
    file_path: str, scope: str, kind: str, name: str, lineno: int
) -> str:
    """Return the canonical machine handle for a symbol.

    Format: ``{file}#{scope}#{kind}#{name}#{lineno}``

    ``scope`` is the dotted class prefix (e.g. ``User.``) or empty for
    top-level symbols.  This key is unique within a snapshot and stable
    across renames (by lineno), though lineno drift after edits is expected.
    """
    return f"{file_path}#{scope}#{kind}#{name}#{lineno}"


def _make_func_record(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    name: str,
    qualified_name: str,
    kind: SymbolKind,
    file_path: str = "",
    class_prefix: str = "",
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
        metadata_id=_compute_metadata_id_func(node),
        canonical_key=_canonical_key(file_path, class_prefix, kind, name, node.lineno),
        lineno=node.lineno,
        end_lineno=node.end_lineno or node.lineno,
    )


def _make_class_record(
    node: ast.ClassDef,
    qualified_name: str,
    file_path: str = "",
) -> SymbolRecord:
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
        metadata_id=_compute_metadata_id_class(node),
        canonical_key=_canonical_key(file_path, "", "class", node.name, node.lineno),
        lineno=node.lineno,
        end_lineno=node.end_lineno or node.lineno,
    )


def _make_var_record(
    node: ast.Assign | ast.AnnAssign,
    name: str,
    file_path: str = "",
) -> SymbolRecord:
    normalized = ast.unparse(node)
    return SymbolRecord(
        kind="variable",
        name=name,
        qualified_name=name,
        content_id=_sha256(normalized),
        body_hash=_sha256(normalized),
        signature_id=_sha256(name),
        metadata_id="",
        canonical_key=_canonical_key(file_path, "", "variable", name, node.lineno),
        lineno=node.lineno,
        end_lineno=node.end_lineno or node.lineno,
    )


def _make_import_record(
    node: ast.Import | ast.ImportFrom,
    name: str,
    file_path: str = "",
) -> SymbolRecord:
    normalized = ast.unparse(node)
    return SymbolRecord(
        kind="import",
        name=name,
        qualified_name=f"import::{name}",
        content_id=_sha256(normalized),
        body_hash=_sha256(normalized),
        signature_id=_sha256(name),
        metadata_id="",
        canonical_key=_canonical_key(file_path, "", "import", name, node.lineno),
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
# Markdown adapter — ATX heading extraction via tree-sitter-markdown
# ---------------------------------------------------------------------------

_MD_HEADING_MARKERS: dict[str, int] = {
    "atx_h1_marker": 1,
    "atx_h2_marker": 2,
    "atx_h3_marker": 3,
    "atx_h4_marker": 4,
    "atx_h5_marker": 5,
    "atx_h6_marker": 6,
}


class MarkdownAdapter:
    """Extract ATX headings as ``section`` symbols from Markdown/text files.

    Each heading becomes an addressable symbol so that diffs report
    "Section X changed" rather than "file changed".  Requires the
    ``tree-sitter-markdown`` package; degrades to empty symbol tree without it.
    """

    _EXTENSIONS: frozenset[str] = frozenset({".md", ".rst", ".txt"})

    def __init__(self) -> None:
        self._parser: Parser | None = None
        try:
            from tree_sitter import Language, Parser
            import tree_sitter_markdown as _md
            lang = Language(_md.language())
            self._parser = Parser(lang)
        except Exception as exc:  # noqa: BLE001
            logger.debug("tree-sitter-markdown unavailable — Markdown file-level only: %s", exc)

    def supported_extensions(self) -> frozenset[str]:
        return self._EXTENSIONS

    def parse_symbols(self, source: bytes, file_path: str) -> SymbolTree:
        if self._parser is None:
            return {}
        try:
            tree = self._parser.parse(source)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Markdown parse error in %s: %s", file_path, exc)
            return {}
        symbols: SymbolTree = {}
        self._walk(tree.root_node, source, file_path, symbols)
        return symbols

    def _walk(self, node: Node, src: bytes, file_path: str, out: SymbolTree) -> None:
        """Recursively find atx_heading nodes and emit section records."""
        if node.type == "atx_heading":
            self._emit_heading(node, src, file_path, out)
        for child in node.children:
            self._walk(child, src, file_path, out)

    def _emit_heading(
        self, node: Node, src: bytes, file_path: str, out: SymbolTree
    ) -> None:
        level = 0
        inline_text = ""
        for child in node.children:
            if child.type in _MD_HEADING_MARKERS:
                level = _MD_HEADING_MARKERS[child.type]
            elif child.type == "inline":
                inline_text = _node_text(src, child).decode("utf-8", errors="replace").strip()
        if not inline_text or not level:
            return
        name = f"h{level}: {inline_text}"
        lineno = node.start_point[0] + 1
        addr = f"{file_path}::{name}"
        node_bytes = _node_text(src, node)
        out[addr] = SymbolRecord(
            kind="section",
            name=name,
            qualified_name=name,
            content_id=_sha256_bytes(_norm_ws(node_bytes)),
            body_hash=_sha256(inline_text),
            signature_id=_sha256(name),
            metadata_id="",
            canonical_key=_canonical_key(file_path, "", "section", name, lineno),
            lineno=lineno,
            end_lineno=node.end_point[0] + 1,
        )

    def file_content_id(self, source: bytes) -> str:
        return _sha256_bytes(source)


# ---------------------------------------------------------------------------
# HTML adapter — semantic element and id-bearing element extraction
# ---------------------------------------------------------------------------

_HTML_SEMANTIC_TAGS: frozenset[str] = frozenset({
    "main", "header", "footer", "nav", "article", "section", "aside",
    "h1", "h2", "h3", "h4", "h5", "h6", "form", "dialog", "figure",
})


class HtmlAdapter:
    """Extract named HTML elements as symbols.

    Emits a symbol for:
    - Heading elements (h1–h6): ``section`` kind, name = ``h1: heading text``
    - Elements with an ``id`` attribute: ``section`` kind, name = ``tag#id``
    - Semantic structural elements (section, article, main, nav, …) without
      an id: ``section`` kind, name = ``tag`` + line number to disambiguate.

    Requires ``tree-sitter-html``; degrades to empty symbol tree without it.
    """

    _EXTENSIONS: frozenset[str] = frozenset({".html", ".htm"})

    def __init__(self) -> None:
        self._parser: Parser | None = None
        try:
            from tree_sitter import Language, Parser
            import tree_sitter_html as _html
            lang = Language(_html.language())
            self._parser = Parser(lang)
        except Exception as exc:  # noqa: BLE001
            logger.debug("tree-sitter-html unavailable — HTML file-level only: %s", exc)

    def supported_extensions(self) -> frozenset[str]:
        return self._EXTENSIONS

    def parse_symbols(self, source: bytes, file_path: str) -> SymbolTree:
        if self._parser is None:
            return {}
        try:
            tree = self._parser.parse(source)
        except Exception as exc:  # noqa: BLE001
            logger.debug("HTML parse error in %s: %s", file_path, exc)
            return {}
        symbols: SymbolTree = {}
        self._walk(tree.root_node, source, file_path, symbols)
        return symbols

    def _walk(self, node: Node, src: bytes, file_path: str, out: SymbolTree) -> None:
        if node.type == "element":
            self._try_emit(node, src, file_path, out)
        for child in node.children:
            self._walk(child, src, file_path, out)

    def _try_emit(
        self, node: Node, src: bytes, file_path: str, out: SymbolTree
    ) -> None:
        start_tag = next((c for c in node.children if c.type == "start_tag"), None)
        if start_tag is None:
            return
        tag_node = start_tag.child_by_field_name("name")
        if tag_node is None:
            # Fallback: first named child of start_tag with type tag_name
            tag_node = next((c for c in start_tag.named_children if c.type == "tag_name"), None)
        if tag_node is None:
            return
        tag = _node_text(src, tag_node).decode("utf-8", errors="replace").lower()
        element_id = self._find_id_attr(start_tag, src)
        lineno = node.start_point[0] + 1

        is_heading = tag in {"h1", "h2", "h3", "h4", "h5", "h6"}
        is_semantic = tag in _HTML_SEMANTIC_TAGS

        if not (is_heading or is_semantic or element_id):
            return

        if element_id:
            name = f"{tag}#{element_id}"
        elif is_heading:
            # Extract visible text from heading
            text_parts = [
                _node_text(src, c).decode("utf-8", errors="replace").strip()
                for c in node.named_children
                if c.type == "text"
            ]
            heading_text = " ".join(text_parts).strip() or tag
            name = f"{tag}: {heading_text}"
        else:
            name = f"{tag}@{lineno}"

        addr = f"{file_path}::{name}"
        node_bytes = _node_text(src, node)
        out[addr] = SymbolRecord(
            kind="section",
            name=name,
            qualified_name=name,
            content_id=_sha256_bytes(_norm_ws(node_bytes)),
            body_hash=_sha256_bytes(_norm_ws(node_bytes)),
            signature_id=_sha256(name),
            metadata_id="",
            canonical_key=_canonical_key(file_path, "", "section", name, lineno),
            lineno=lineno,
            end_lineno=node.end_point[0] + 1,
        )

    def _find_id_attr(self, start_tag: Node, src: bytes) -> str:
        """Return the value of the ``id`` attribute on *start_tag*, or ``""``."""
        for attr in start_tag.named_children:
            if attr.type != "attribute":
                continue
            children = attr.named_children
            if not children:
                continue
            attr_name = _node_text(src, children[0]).decode("utf-8", errors="replace").strip()
            if attr_name == "id" and len(children) >= 2:
                val = _node_text(src, children[-1]).decode("utf-8", errors="replace")
                return val.strip('"\'').strip()
        return ""

    def file_content_id(self, source: bytes) -> str:
        return _sha256_bytes(source)


# ---------------------------------------------------------------------------
# tree-sitter adapter — shared infrastructure for all non-Python languages
# ---------------------------------------------------------------------------

_WS_RE: re.Pattern[bytes] = re.compile(rb"\s+")


def _norm_ws(src: bytes) -> bytes:
    """Collapse all whitespace runs to a single space and strip the result."""
    return _WS_RE.sub(b" ", src).strip()


def _node_text(src: bytes, node: Node) -> bytes:
    """Extract the raw source bytes covered by a tree-sitter node."""
    return src[node.start_byte : node.end_byte]


def _class_name_from(src: bytes, node: Node, field: str) -> str | None:
    """Extract a class/struct name from a parent CST node.

    Tries ``child_by_field_name(field)`` first (covers Java, C#, C++, Rust).
    Falls back to the first ``identifier``-typed named child to handle
    languages like Kotlin where the class name is not a named field.
    """
    child = node.child_by_field_name(field)
    if child is None:
        for c in node.named_children:
            if c.type == "identifier":
                child = c
                break
    if child is None:
        return None
    return _node_text(src, child).decode("utf-8", errors="replace")


def _qualified_name_ts(
    src: bytes,
    sym_node: Node,
    name: str,
    class_node_types: frozenset[str],
    class_name_field: str,
) -> str:
    """Walk the CST parent chain to build a dotted qualified name.

    For a method ``bark`` inside ``class Dog``, returns ``"Dog.bark"``.
    For a top-level function, returns just ``"standalone"``.
    """
    parts = [name]
    parent = sym_node.parent
    while parent is not None:
        if parent.type in class_node_types:
            class_name = _class_name_from(src, parent, class_name_field)
            if class_name:
                parts.insert(0, class_name)
        parent = parent.parent
    return ".".join(parts)


class LangSpec(TypedDict):
    """Per-language tree-sitter configuration consumed by :class:`TreeSitterAdapter`."""

    extensions: frozenset[str]
    module_name: str       # Python import name, e.g. ``"tree_sitter_javascript"``
    lang_func: str         # Attribute on the module returning the raw capsule
    query_str: str         # tree-sitter S-expr query — must capture ``@sym`` and ``@name``
    kind_map: dict[str, SymbolKind]       # CST node type → SymbolKind
    class_node_types: frozenset[str]      # Ancestor types that scope methods
    class_name_field: str  # Field name for the class name (e.g. ``"name"`` or ``"type"``)
    receiver_capture: str  # Capture name for Go-style method receivers; ``""`` to skip
    async_node_child: str  # Direct child type marking async (``"async"`` for JS/TS; ``""`` to skip)


class TreeSitterAdapter:
    """Implements :class:`LanguageAdapter` using tree-sitter for real CST parsing.

    tree-sitter is the same parsing technology used by GitHub Copilot, VS Code,
    Neovim, and Zed.  It produces a concrete syntax tree from every source file,
    even if the file has syntax errors — making it suitable for real-world repos
    that may contain partially-written code.

    Parsing is error-tolerant: individual file failures are logged at DEBUG
    level and return an empty :type:`SymbolTree` so the caller falls back to
    file-level diffing rather than crashing.
    """

    def __init__(
        self,
        spec: LangSpec,
        parser: Parser,
        query: Query,
    ) -> None:
        self._spec = spec
        self._parser = parser
        self._query = query

    def supported_extensions(self) -> frozenset[str]:
        return self._spec["extensions"]

    def parse_symbols(self, source: bytes, file_path: str) -> SymbolTree:
        from tree_sitter import QueryCursor
        try:
            tree = self._parser.parse(source)
            cursor = QueryCursor(self._query)
            symbols: SymbolTree = {}
            recv_cap = self._spec["receiver_capture"]

            for _pat, caps in cursor.matches(tree.root_node):
                sym_list = caps.get("sym", [])
                name_list = caps.get("name", [])
                if not sym_list or not name_list:
                    continue
                sym_node = sym_list[0]
                name_node = name_list[0]

                name_txt = _node_text(source, name_node).decode(
                    "utf-8", errors="replace"
                )
                kind = self._spec["kind_map"].get(sym_node.type, "function")

                # Promote function/method to async variant when the node has
                # an "async" keyword as a direct child (JS, TS, Swift, etc.).
                async_child = self._spec["async_node_child"]
                if async_child and kind in ("function", "method"):
                    if any(c.type == async_child for c in sym_node.children[:3]):
                        kind = "async_function" if kind == "function" else "async_method"

                # Build qualified name — walking ancestor chain for methods.
                qualified = _qualified_name_ts(
                    source,
                    sym_node,
                    name_txt,
                    self._spec["class_node_types"],
                    self._spec["class_name_field"],
                )

                # Go-style receiver prefix: (d *Dog) → "Dog.Bark"
                if recv_cap:
                    recv_list = caps.get(recv_cap, [])
                    if recv_list:
                        recv_txt = (
                            _node_text(source, recv_list[0])
                            .decode("utf-8", errors="replace")
                            .lstrip("*")
                            .strip()
                        )
                        if recv_txt:
                            qualified = f"{recv_txt}.{qualified}"

                addr = f"{file_path}::{qualified}"
                node_bytes = _node_text(source, sym_node)
                name_bytes = _node_text(source, name_node)
                # Substitute the name with a placeholder to isolate the body
                # from the identifier — two symbols with the same body but
                # different names share the same body_hash, signalling a rename.
                body_bytes = node_bytes.replace(name_bytes, b"\xfe", 1)

                params_node = (
                    sym_node.child_by_field_name("parameters")
                    or sym_node.child_by_field_name("formal_parameters")
                    or sym_node.child_by_field_name("function_value_parameters")
                )
                params_bytes = (
                    _node_text(source, params_node)
                    if params_node is not None
                    else b""
                )

                sym_lineno = sym_node.start_point[0] + 1
                # Determine class prefix for canonical_key (dotted scope path).
                scope_prefix = ".".join(qualified.split(".")[:-1]) + "." if "." in qualified else ""
                symbols[addr] = SymbolRecord(
                    kind=kind,
                    name=name_txt,
                    qualified_name=qualified,
                    content_id=_sha256_bytes(_norm_ws(node_bytes)),
                    body_hash=_sha256_bytes(_norm_ws(body_bytes)),
                    signature_id=_sha256_bytes(_norm_ws(name_bytes + params_bytes)),
                    # metadata_id: tree-sitter adapters extract annotations/visibility
                    # where available.  Currently stubbed as "" — future adapters can
                    # enrich this by reading modifier nodes.
                    metadata_id="",
                    canonical_key=_canonical_key(file_path, scope_prefix, kind, name_txt, sym_lineno),
                    lineno=sym_lineno,
                    end_lineno=sym_node.end_point[0] + 1,
                )
            return symbols
        except Exception as exc:  # noqa: BLE001
            logger.debug("tree-sitter parse error in %s: %s", file_path, exc)
            return {}

    def file_content_id(self, source: bytes) -> str:
        """Whitespace-normalised SHA-256 of the source — insensitive to reformatting."""
        return _sha256_bytes(_norm_ws(source))

    def validate_source(self, source: bytes) -> str | None:
        """Return an error description if *source* has syntax errors, else None.

        tree-sitter always produces a parse tree even for broken code.
        Errors appear as nodes with ``type == "ERROR"`` or ``is_missing == True``.
        ``root_node.has_error`` is the fast top-level check.
        """
        try:
            tree = self._parser.parse(source)
        except Exception as exc:  # noqa: BLE001
            return f"parser error: {exc}"

        if not tree.root_node.has_error:
            return None

        # Walk the tree to find the first concrete error site.
        error_node = _first_error_node(tree.root_node)
        if error_node is not None:
            line = error_node.start_point[0] + 1
            fragment = source[
                error_node.start_byte : min(error_node.end_byte, error_node.start_byte + 60)
            ].decode("utf-8", errors="replace").strip()
            msg = f"syntax error on line {line}"
            if fragment:
                msg += f": {fragment!r}"
            return msg
        return "syntax error (unknown location)"


def _make_ts_adapter(spec: LangSpec) -> LanguageAdapter:
    """Build a :class:`TreeSitterAdapter`; fall back to :class:`FallbackAdapter` on error.

    Importing the grammar capsule is deferred to this factory so that a
    missing or incompatible grammar package degrades gracefully rather than
    preventing the entire plugin from loading.  tree_sitter itself is also
    imported here — not at module level — so that importing ast_parser does
    not pay the C-extension load cost unless semantic analysis is actually
    requested.
    """
    try:
        from tree_sitter import Language, Parser, Query
        mod = importlib.import_module(spec["module_name"])
        raw_lang = getattr(mod, spec["lang_func"])()
        lang = Language(raw_lang)
        parser = Parser(lang)
        query = Query(lang, spec["query_str"])
        return TreeSitterAdapter(spec, parser, query)
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "tree-sitter grammar %s.%s unavailable — using file-level fallback: %s",
            spec["module_name"],
            spec["lang_func"],
            exc,
        )
        return FallbackAdapter(spec["extensions"])


# ---------------------------------------------------------------------------
# Per-language tree-sitter specs
# ---------------------------------------------------------------------------

_JS_SPEC: LangSpec = {
    "extensions": frozenset({".js", ".jsx", ".mjs", ".cjs"}),
    "module_name": "tree_sitter_javascript",
    "lang_func": "language",
    # tree-sitter-javascript uses "class" for named class expressions.
    # Arrow functions and function expressions assigned to variables are
    # captured via variable_declarator so that `const greet = () => {}` is
    # a first-class symbol.
    "query_str": (
        "(function_declaration name: (identifier) @name) @sym\n"
        "(function_expression name: (identifier) @name) @sym\n"
        "(generator_function_declaration name: (identifier) @name) @sym\n"
        "(class_declaration name: (identifier) @name) @sym\n"
        "(class name: (identifier) @name) @sym\n"
        "(method_definition name: (property_identifier) @name) @sym\n"
        "(variable_declarator name: (identifier) @name"
        " value: (arrow_function)) @sym\n"
        "(variable_declarator name: (identifier) @name"
        " value: (function_expression)) @sym\n"
        "(variable_declarator name: (identifier) @name"
        " value: (generator_function)) @sym"
    ),
    "kind_map": {
        "function_declaration": "function",
        "function_expression": "function",
        "generator_function_declaration": "function",
        "class_declaration": "class",
        "class": "class",
        "method_definition": "method",
        "variable_declarator": "function",
    },
    "class_node_types": frozenset({"class_declaration", "class"}),
    "class_name_field": "name",
    "receiver_capture": "",
    # async keyword appears as a direct child token on function/method nodes.
    "async_node_child": "async",
}

_TS_QUERY = (
    # TypeScript uses type_identifier (not identifier) for class names.
    "(function_declaration name: (identifier) @name) @sym\n"
    "(function_expression name: (identifier) @name) @sym\n"
    "(generator_function_declaration name: (identifier) @name) @sym\n"
    "(class_declaration name: (type_identifier) @name) @sym\n"
    "(class name: (type_identifier) @name) @sym\n"
    "(abstract_class_declaration name: (type_identifier) @name) @sym\n"
    "(method_definition name: (property_identifier) @name) @sym\n"
    "(interface_declaration name: (type_identifier) @name) @sym\n"
    "(type_alias_declaration name: (type_identifier) @name) @sym\n"
    "(enum_declaration name: (identifier) @name) @sym\n"
    "(variable_declarator name: (identifier) @name"
    " value: (arrow_function)) @sym\n"
    "(variable_declarator name: (identifier) @name"
    " value: (function_expression)) @sym\n"
    "(variable_declarator name: (identifier) @name"
    " value: (generator_function)) @sym"
)

_TS_KIND_MAP: dict[str, SymbolKind] = {
    "function_declaration": "function",
    "function_expression": "function",
    "generator_function_declaration": "function",
    "class_declaration": "class",
    "class": "class",
    "abstract_class_declaration": "class",
    "method_definition": "method",
    "interface_declaration": "class",
    "type_alias_declaration": "variable",
    "enum_declaration": "class",
    "variable_declarator": "function",
}

_TS_CLASS_NODES: frozenset[str] = frozenset(
    {"class_declaration", "class", "abstract_class_declaration"}
)

_TS_SPEC: LangSpec = {
    "extensions": frozenset({".ts"}),
    "module_name": "tree_sitter_typescript",
    "lang_func": "language_typescript",
    "query_str": _TS_QUERY,
    "kind_map": _TS_KIND_MAP,
    "class_node_types": _TS_CLASS_NODES,
    "class_name_field": "name",
    "receiver_capture": "",
    "async_node_child": "async",
}

_TSX_SPEC: LangSpec = {
    "extensions": frozenset({".tsx"}),
    "module_name": "tree_sitter_typescript",
    "lang_func": "language_tsx",
    "query_str": _TS_QUERY,
    "kind_map": _TS_KIND_MAP,
    "class_node_types": _TS_CLASS_NODES,
    "class_name_field": "name",
    "receiver_capture": "",
    "async_node_child": "async",
}

_GO_SPEC: LangSpec = {
    "extensions": frozenset({".go"}),
    "module_name": "tree_sitter_go",
    "lang_func": "language",
    "query_str": (
        "(function_declaration name: (identifier) @name) @sym\n"
        "(method_declaration\n"
        "  receiver: (parameter_list\n"
        "    (parameter_declaration type: _ @recv))\n"
        "  name: (field_identifier) @name) @sym\n"
        "(type_spec name: (type_identifier) @name) @sym\n"
        # Package-level const and var groups — each spec/value_spec carries names.
        "(const_spec name: (identifier) @name) @sym\n"
        "(var_spec name: (identifier) @name) @sym"
    ),
    "kind_map": {
        "function_declaration": "function",
        "method_declaration": "method",
        "type_spec": "class",
        "const_spec": "variable",
        "var_spec": "variable",
    },
    "class_node_types": frozenset(),
    "class_name_field": "name",
    "receiver_capture": "recv",
    "async_node_child": "",
}

_RUST_SPEC: LangSpec = {
    "extensions": frozenset({".rs"}),
    "module_name": "tree_sitter_rust",
    "lang_func": "language",
    "query_str": (
        "(function_item name: (identifier) @name) @sym\n"
        "(struct_item name: (type_identifier) @name) @sym\n"
        "(enum_item name: (type_identifier) @name) @sym\n"
        "(trait_item name: (type_identifier) @name) @sym\n"
        "(type_item name: (type_identifier) @name) @sym\n"
        "(mod_item name: (identifier) @name) @sym\n"
        "(static_item name: (identifier) @name) @sym\n"
        "(const_item name: (identifier) @name) @sym"
    ),
    "kind_map": {
        "function_item": "function",
        "struct_item": "class",
        "enum_item": "class",
        "trait_item": "class",
        "type_item": "variable",
        "mod_item": "class",
        "static_item": "variable",
        "const_item": "variable",
    },
    # impl_item scopes methods; its implementing type is in the "type" field.
    "class_node_types": frozenset({"impl_item"}),
    "class_name_field": "type",
    "receiver_capture": "",
    "async_node_child": "",
}

_JAVA_SPEC: LangSpec = {
    "extensions": frozenset({".java"}),
    "module_name": "tree_sitter_java",
    "lang_func": "language",
    "query_str": (
        "(method_declaration name: (identifier) @name) @sym\n"
        "(constructor_declaration name: (identifier) @name) @sym\n"
        "(class_declaration name: (identifier) @name) @sym\n"
        "(interface_declaration name: (identifier) @name) @sym\n"
        "(enum_declaration name: (identifier) @name) @sym\n"
        "(annotation_type_declaration name: (identifier) @name) @sym\n"
        "(record_declaration name: (identifier) @name) @sym"
    ),
    "kind_map": {
        "method_declaration": "method",
        "constructor_declaration": "function",
        "class_declaration": "class",
        "interface_declaration": "class",
        "enum_declaration": "class",
        "annotation_type_declaration": "class",
        "record_declaration": "class",
    },
    "class_node_types": frozenset(
        {"class_declaration", "interface_declaration", "enum_declaration", "record_declaration"}
    ),
    "class_name_field": "name",
    "receiver_capture": "",
    "async_node_child": "",
}

_C_SPEC: LangSpec = {
    "extensions": frozenset({".c", ".h"}),
    "module_name": "tree_sitter_c",
    "lang_func": "language",
    "query_str": (
        "(function_definition\n"
        "  declarator: (function_declarator\n"
        "    declarator: (identifier) @name)) @sym\n"
        # Structs and enums defined via typedef or direct declaration.
        "(struct_specifier name: (type_identifier) @name) @sym\n"
        "(enum_specifier name: (type_identifier) @name) @sym"
    ),
    "kind_map": {
        "function_definition": "function",
        "struct_specifier": "class",
        "enum_specifier": "class",
    },
    "class_node_types": frozenset(),
    "class_name_field": "name",
    "receiver_capture": "",
    "async_node_child": "",
}

_CPP_SPEC: LangSpec = {
    "extensions": frozenset({".cpp", ".cc", ".cxx", ".hpp", ".hxx"}),
    "module_name": "tree_sitter_cpp",
    "lang_func": "language",
    "query_str": (
        # Plain function definitions (top-level or namespaced).
        "(function_definition\n"
        "  declarator: (function_declarator\n"
        "    declarator: (identifier) @name)) @sym\n"
        # Out-of-class method definitions: void Dog::bark() {}
        "(function_definition\n"
        "  declarator: (function_declarator\n"
        "    declarator: (qualified_identifier\n"
        "      name: (identifier) @name))) @sym\n"
        "(class_specifier name: (type_identifier) @name) @sym\n"
        "(struct_specifier name: (type_identifier) @name) @sym\n"
        "(enum_specifier name: (type_identifier) @name) @sym\n"
        "(namespace_definition (namespace_identifier) @name) @sym"
    ),
    "kind_map": {
        "function_definition": "function",
        "class_specifier": "class",
        "struct_specifier": "class",
        "enum_specifier": "class",
        "namespace_definition": "class",
    },
    "class_node_types": frozenset({"class_specifier", "struct_specifier"}),
    "class_name_field": "name",
    "receiver_capture": "",
    "async_node_child": "",
}

_CS_SPEC: LangSpec = {
    "extensions": frozenset({".cs"}),
    "module_name": "tree_sitter_c_sharp",
    "lang_func": "language",
    "query_str": (
        "(method_declaration name: (identifier) @name) @sym\n"
        "(constructor_declaration name: (identifier) @name) @sym\n"
        "(class_declaration name: (identifier) @name) @sym\n"
        "(interface_declaration name: (identifier) @name) @sym\n"
        "(enum_declaration name: (identifier) @name) @sym\n"
        "(struct_declaration name: (identifier) @name) @sym\n"
        "(record_declaration name: (identifier) @name) @sym\n"
        "(property_declaration name: (identifier) @name) @sym"
    ),
    "kind_map": {
        "method_declaration": "method",
        "constructor_declaration": "function",
        "class_declaration": "class",
        "interface_declaration": "class",
        "enum_declaration": "class",
        "struct_declaration": "class",
        "record_declaration": "class",
        "property_declaration": "variable",
    },
    "class_node_types": frozenset(
        {"class_declaration", "interface_declaration", "struct_declaration", "record_declaration"}
    ),
    "class_name_field": "name",
    "receiver_capture": "",
    "async_node_child": "",
}

_RUBY_SPEC: LangSpec = {
    "extensions": frozenset({".rb"}),
    "module_name": "tree_sitter_ruby",
    "lang_func": "language",
    "query_str": (
        "(method name: (identifier) @name) @sym\n"
        "(singleton_method name: (identifier) @name) @sym\n"
        "(class name: (constant) @name) @sym\n"
        "(module name: (constant) @name) @sym\n"
        "(singleton_class value: (self) @name) @sym"
    ),
    "kind_map": {
        "method": "method",
        "singleton_method": "method",
        "class": "class",
        "module": "class",
        "singleton_class": "class",
    },
    "class_node_types": frozenset({"class", "module"}),
    "class_name_field": "name",
    "receiver_capture": "",
    "async_node_child": "",
}

_KT_SPEC: LangSpec = {
    "extensions": frozenset({".kt", ".kts"}),
    "module_name": "tree_sitter_kotlin",
    "lang_func": "language",
    # Kotlin uses plain `identifier` for all names (no type_identifier or
    # simple_identifier variants at this grammar version).
    "query_str": (
        "(function_declaration (identifier) @name) @sym\n"
        "(class_declaration (identifier) @name) @sym\n"
        "(object_declaration (identifier) @name) @sym\n"
        "(property_declaration (variable_declaration"
        " (identifier) @name)) @sym"
    ),
    "kind_map": {
        "function_declaration": "function",
        "class_declaration": "class",
        "object_declaration": "class",
        "property_declaration": "variable",
    },
    # Kotlin methods are function_declaration nodes inside class_body.
    # child_by_field_name("name") is None for Kotlin classes; _class_name_from
    # falls back to the first identifier-typed named child automatically.
    "class_node_types": frozenset({"class_declaration", "object_declaration"}),
    "class_name_field": "name",
    "receiver_capture": "",
    "async_node_child": "",
}

# Swift: requires py-tree-sitter-swift (builds from source).  _make_ts_adapter
# degrades to FallbackAdapter if the package is not available.
_SWIFT_SPEC: LangSpec = {
    "extensions": frozenset({".swift"}),
    "module_name": "py_tree_sitter_swift",
    "lang_func": "language",
    "query_str": (
        "(function_declaration name: (simple_identifier) @name) @sym\n"
        "(class_declaration name: (type_identifier) @name) @sym\n"
        "(struct_declaration name: (type_identifier) @name) @sym\n"
        "(enum_declaration name: (type_identifier) @name) @sym\n"
        "(protocol_declaration name: (type_identifier) @name) @sym\n"
        "(typealias_declaration name: (type_identifier) @name) @sym\n"
        "(computed_property (simple_identifier) @name) @sym\n"
        "(init_declaration) @sym"
    ),
    "kind_map": {
        "function_declaration": "function",
        "class_declaration": "class",
        "struct_declaration": "class",
        "enum_declaration": "class",
        "protocol_declaration": "class",
        "typealias_declaration": "variable",
        "computed_property": "variable",
        "init_declaration": "function",
    },
    "class_node_types": frozenset(
        {"class_declaration", "struct_declaration", "enum_declaration"}
    ),
    "class_name_field": "name",
    "receiver_capture": "",
    "async_node_child": "async",
}

# CSS/SCSS: selectors of rule-sets, @keyframes names, and @media conditions
# become addressable symbols so that diffs report "selector changed" vs
# "file changed".
_CSS_SPEC: LangSpec = {
    "extensions": frozenset({".css", ".scss"}),
    "module_name": "tree_sitter_css",
    "lang_func": "language",
    "query_str": (
        "(rule_set (selectors) @name) @sym\n"
        "(keyframes_statement (keyframes_name) @name) @sym\n"
        "(media_statement (keyword_query) @name) @sym"
    ),
    "kind_map": {
        "rule_set": "rule",
        "keyframes_statement": "rule",
        "media_statement": "rule",
    },
    "class_node_types": frozenset(),
    "class_name_field": "name",
    "receiver_capture": "",
    "async_node_child": "",
}

#: All tree-sitter language specs, loaded in registration order.
_TS_LANG_SPECS: list[LangSpec] = [
    _JS_SPEC,
    _TS_SPEC,
    _TSX_SPEC,
    _GO_SPEC,
    _RUST_SPEC,
    _JAVA_SPEC,
    _C_SPEC,
    _CPP_SPEC,
    _CS_SPEC,
    _RUBY_SPEC,
    _KT_SPEC,
    _SWIFT_SPEC,
    _CSS_SPEC,
]


# ---------------------------------------------------------------------------
# Adapter registry and public helpers
# ---------------------------------------------------------------------------

#: Fallback adapter for file types without a registered adapter — always cheap.
_FALLBACK: FallbackAdapter = FallbackAdapter(frozenset())

#: Internal caches — populated on first call to :func:`_adapters`.
_ADAPTERS_CACHE: list[LanguageAdapter] | None = None
_SEM_EXT_CACHE: frozenset[str] | None = None


def _adapters() -> list[LanguageAdapter]:
    """Return the global adapter list, building it on first call.

    Tree-sitter grammar packages are imported here — not at module level —
    so that importing :mod:`ast_parser` costs nothing for commands that do
    not perform semantic analysis (e.g. ``muse init``, ``muse log``).
    """
    global _ADAPTERS_CACHE, _SEM_EXT_CACHE
    if _ADAPTERS_CACHE is None:
        result: list[LanguageAdapter] = [
            PythonAdapter(),
            MarkdownAdapter(),
            HtmlAdapter(),
        ]
        for spec in _TS_LANG_SPECS:
            result.append(_make_ts_adapter(spec))
        _ADAPTERS_CACHE = result
        _SEM_EXT_CACHE = frozenset().union(
            *(a.supported_extensions() for a in result if not isinstance(a, FallbackAdapter))
        )
        # Promote computed values to real module attributes so subsequent
        # attribute lookups bypass __getattr__ and become O(1) dict access.
        _mod: _types.ModuleType = sys.modules[__name__]
        setattr(_mod, "ADAPTERS", result)
        setattr(_mod, "SEMANTIC_EXTENSIONS", _SEM_EXT_CACHE)
    return _ADAPTERS_CACHE


def _semantic_extensions() -> frozenset[str]:
    """Return the set of extensions with AST-level support, building it on first call."""
    _adapters()
    if _SEM_EXT_CACHE is None:
        return frozenset()
    return _SEM_EXT_CACHE


def __getattr__(name: str) -> list[LanguageAdapter] | frozenset[str]:
    """Lazy module attributes — ADAPTERS and SEMANTIC_EXTENSIONS.

    Both are computed on first access (which triggers adapter building) and
    then cached as real module attributes so subsequent lookups are O(1).
    """
    if name == "ADAPTERS":
        return _adapters()
    if name == "SEMANTIC_EXTENSIONS":
        return _semantic_extensions()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


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
    ".css", ".scss", ".html", ".htm",
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
    for adapter in _adapters():
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


def _first_error_node(node: Node) -> Node | None:
    """Return the first ERROR or MISSING node in *node*'s subtree, depth-first."""
    if node.type == "ERROR" or node.is_missing:
        return node
    for child in node.children:
        found = _first_error_node(child)
        if found is not None:
            return found
    return None


def validate_syntax(source: bytes, file_path: str) -> str | None:
    """Return a human-readable error description if *source* has syntax errors.

    Covers Python (via :mod:`ast`) and all tree-sitter languages.  Returns
    ``None`` for valid files and for file types without a parser.

    This is used by ``muse patch`` to verify that a surgical replacement
    does not introduce a syntax error before writing the result to disk.

    Args:
        source:    UTF-8 encoded source bytes to validate.
        file_path: Workspace-relative path — used to select the parser.

    Returns:
        A human-readable error string, or ``None`` if the file is valid.
    """
    suffix = pathlib.PurePosixPath(file_path).suffix.lower()

    if suffix in {".py", ".pyi"}:
        try:
            ast.parse(source)
            return None
        except SyntaxError as exc:
            return f"syntax error on line {exc.lineno}: {exc.msg}"

    adapter = adapter_for_path(file_path)
    if isinstance(adapter, TreeSitterAdapter):
        return adapter.validate_source(source)

    # MarkdownAdapter and HtmlAdapter use tree-sitter internally when available
    # but do not expose validation — no syntax errors to report for prose files.
    return None

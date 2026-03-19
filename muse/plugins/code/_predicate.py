"""Predicate DSL parser for ``muse query`` and ``muse query-history``.

Grammar (v2)
============

.. code-block:: text

    expr    = or_expr
    or_expr = and_expr ( "OR" and_expr )*
    and_expr = not_expr ( and_expr )*        # implicit AND / explicit AND
    not_expr = "NOT" primary | primary
    primary  = "(" expr ")" | atom
    atom     = KEY OP VALUE

    KEY  = [a-zA-Z_][a-zA-Z_0-9]*
    OP   = "~=" | "^=" | "$=" | ">=" | "<=" | "!=" | "="
    VALUE = double-quoted-string | bare-word

Supported keys
--------------

Snapshot-local (available with or without ``--all-commits``):

  kind          function | class | method | variable | import | …
  language      Python | Go | Rust | …
  name          bare symbol name
  qualified_name  dotted name (e.g. User.save)
  file          file path
  hash          content_id prefix (exact-body match)
  body_hash     body_hash prefix
  signature_id  signature_id prefix
  lineno_gt     symbol starts after line N  (integer)
  lineno_lt     symbol starts before line N (integer)

Example queries
---------------

    kind=function language=Python name~=validate
    (kind=function OR kind=method) name^=_
    NOT kind=import file~=billing
    kind=class name~=Service language=Python
    lineno_gt=100 lineno_lt=200 file=src/billing.py

The parser is a hand-written recursive descent parser — no external
dependencies, no regex-based hacks.  All parsing errors raise
``PredicateError`` with a human-readable message including the position
in the input string.
"""

from __future__ import annotations

import re
import logging
from collections.abc import Callable

from muse.plugins.code._query import language_of
from muse.plugins.code.ast_parser import SymbolRecord

logger = logging.getLogger(__name__)

# Signature: (file_path: str, rec: SymbolRecord) -> bool
Predicate = Callable[[str, SymbolRecord], bool]


class PredicateError(ValueError):
    """Raised when a predicate string cannot be parsed or evaluated."""

# ---------------------------------------------------------------------------
# Tokeniser
# ---------------------------------------------------------------------------

_TOKEN_SPEC = [
    ("LPAREN",  r"\("),
    ("RPAREN",  r"\)"),
    ("OR",      r"\bOR\b"),
    ("NOT",     r"\bNOT\b"),
    ("AND",     r"\bAND\b"),
    ("ATOM",    r'[a-zA-Z_][a-zA-Z_0-9]*(?:~=|\^=|\$=|>=|<=|!=|=)"[^"]*"'),
    ("ATOM",    r'[a-zA-Z_][a-zA-Z_0-9]*(?:~=|\^=|\$=|>=|<=|!=|=)[^\s()]+'),
    ("WS",      r"\s+"),
]

_TOKEN_RE = re.compile("|".join(f"(?P<{name}_{i}>{pat})" for i, (name, pat) in enumerate(_TOKEN_SPEC)))


class _Token:
    __slots__ = ("kind", "value", "pos")

    def __init__(self, kind: str, value: str, pos: int) -> None:
        self.kind = kind
        self.value = value
        self.pos = pos

    def __repr__(self) -> str:
        return f"Token({self.kind!r}, {self.value!r})"


def _tokenise(text: str) -> list[_Token]:
    tokens: list[_Token] = []
    pos = 0
    while pos < len(text):
        m = _TOKEN_RE.match(text, pos)
        if m is None:
            raise PredicateError(
                f"Unexpected character at position {pos}: {text[pos]!r}"
            )
        kind_raw = m.lastgroup or ""
        # Strip the numeric suffix we added.
        kind = kind_raw.rsplit("_", 1)[0]
        if kind != "WS":
            tokens.append(_Token(kind, m.group(), pos))
        pos = m.end()
    return tokens


# ---------------------------------------------------------------------------
# Atom parser
# ---------------------------------------------------------------------------

_OP_RE = re.compile(r"(~=|\^=|\$=|>=|<=|!=|=)")
_VALID_KEYS = frozenset({
    "kind", "language", "name", "qualified_name", "file",
    "hash", "body_hash", "signature_id",
    "lineno_gt", "lineno_lt",
})


def _parse_atom(atom_str: str) -> Predicate:
    """Parse a single ``key OP value`` atom into a predicate callable."""
    m = _OP_RE.search(atom_str)
    if m is None:
        raise PredicateError(
            f"Cannot parse predicate '{atom_str}'. "
            "Expected: key=value, key~=value, key^=value, key$=value, key!=value, "
            "key>=value, key<=value."
        )
    op = m.group(1)
    key = atom_str[:m.start()].strip()
    value = atom_str[m.end():]
    # Strip surrounding double quotes.
    if value.startswith('"') and value.endswith('"'):
        value = value[1:-1]
    if key not in _VALID_KEYS:
        raise PredicateError(
            f"Unknown predicate key '{key}'. "
            f"Valid keys: {', '.join(sorted(_VALID_KEYS))}."
        )

    value_lower = value.lower()

    def _str_match(field: str) -> bool:
        f = field.lower()
        if op == "=":
            return f == value_lower
        if op == "~=":
            return value_lower in f
        if op == "^=":
            return f.startswith(value_lower)
        if op == "$=":
            return f.endswith(value_lower)
        if op == "!=":
            return f != value_lower
        return False

    def _int_match(field_val: int) -> bool:
        try:
            threshold = int(value)
        except ValueError:
            raise PredicateError(
                f"Predicate '{key}' requires an integer value (got '{value}')."
            )
        if op == ">=":
            return field_val >= threshold
        if op == "<=":
            return field_val <= threshold
        if op == ">":
            return field_val > threshold
        if op == "<":
            return field_val < threshold
        if op == "=":
            return field_val == threshold
        return False

    def predicate(file_path: str, rec: SymbolRecord) -> bool:
        if key == "kind":
            return _str_match(rec["kind"])
        if key == "language":
            return _str_match(language_of(file_path))
        if key == "name":
            return _str_match(rec["name"])
        if key == "qualified_name":
            return _str_match(rec["qualified_name"])
        if key == "file":
            return _str_match(file_path)
        if key == "hash":
            return rec["content_id"].startswith(value.lower())
        if key == "body_hash":
            return rec["body_hash"].startswith(value.lower())
        if key == "signature_id":
            return rec["signature_id"].startswith(value.lower())
        if key == "lineno_gt":
            return _int_match(rec["lineno"])  # value must be <
        if key == "lineno_lt":
            return _int_match(rec["lineno"])  # value must be >
        return False

    # Swap semantics for lineno_gt / lineno_lt.
    if key == "lineno_gt":
        try:
            threshold = int(value)
        except ValueError:
            raise PredicateError(f"lineno_gt requires integer (got '{value}').")
        def _lineno_gt(file_path: str, rec: SymbolRecord) -> bool:
            return rec["lineno"] > threshold
        return _lineno_gt

    if key == "lineno_lt":
        try:
            threshold = int(value)
        except ValueError:
            raise PredicateError(f"lineno_lt requires integer (got '{value}').")
        def _lineno_lt(file_path: str, rec: SymbolRecord) -> bool:
            return rec["lineno"] < threshold
        return _lineno_lt

    return predicate


# ---------------------------------------------------------------------------
# Recursive descent parser
# ---------------------------------------------------------------------------


class _Parser:
    """Recursive descent parser for the predicate grammar."""

    def __init__(self, tokens: list[_Token]) -> None:
        self._tokens = tokens
        self._pos = 0

    def _peek(self) -> _Token | None:
        if self._pos < len(self._tokens):
            return self._tokens[self._pos]
        return None

    def _consume(self, kind: str | None = None) -> _Token:
        tok = self._peek()
        if tok is None:
            raise PredicateError("Unexpected end of predicate expression.")
        if kind is not None and tok.kind != kind:
            raise PredicateError(
                f"Expected {kind!r} at position {tok.pos}, got {tok.kind!r} ({tok.value!r})."
            )
        self._pos += 1
        return tok

    def parse(self) -> Predicate:
        pred = self._parse_or()
        if self._peek() is not None:
            tok = self._peek()
            assert tok is not None
            raise PredicateError(
                f"Unexpected token at position {tok.pos}: {tok.value!r}"
            )
        return pred

    def _parse_or(self) -> Predicate:
        left = self._parse_and()
        peek = self._peek()
        while peek is not None and peek.kind == "OR":
            self._consume("OR")
            right = self._parse_and()
            left_cap = left
            right_cap = right
            def _or(fp: str, rec: SymbolRecord, _l: Predicate = left_cap, _r: Predicate = right_cap) -> bool:
                return _l(fp, rec) or _r(fp, rec)
            left = _or
            peek = self._peek()
        return left

    def _parse_and(self) -> Predicate:
        left = self._parse_not()
        while True:
            tok = self._peek()
            # Continue AND-chaining: next token is ATOM, LPAREN, or explicit AND.
            if tok is None:
                break
            if tok.kind in ("RPAREN", "OR"):
                break
            if tok.kind == "AND":
                self._consume("AND")
            right = self._parse_not()
            left_cap = left
            right_cap = right
            def _and(fp: str, rec: SymbolRecord, _l: Predicate = left_cap, _r: Predicate = right_cap) -> bool:
                return _l(fp, rec) and _r(fp, rec)
            left = _and
        return left

    def _parse_not(self) -> Predicate:
        peek = self._peek()
        if peek is not None and peek.kind == "NOT":
            self._consume("NOT")
            inner = self._parse_primary()
            inner_cap = inner
            def _not(fp: str, rec: SymbolRecord, _i: Predicate = inner_cap) -> bool:
                return not _i(fp, rec)
            return _not
        return self._parse_primary()

    def _parse_primary(self) -> Predicate:
        tok = self._peek()
        if tok is None:
            raise PredicateError("Unexpected end of expression — expected predicate or '('.")
        if tok.kind == "LPAREN":
            self._consume("LPAREN")
            inner = self._parse_or()
            self._consume("RPAREN")
            return inner
        if tok.kind == "ATOM":
            self._consume("ATOM")
            return _parse_atom(tok.value)
        raise PredicateError(
            f"Unexpected token at position {tok.pos}: {tok.value!r}. "
            "Expected a predicate (key=value) or '('."
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse_query(tokens_or_str: str | list[str]) -> Predicate:
    """Parse a predicate query expression into a single callable.

    Args:
        tokens_or_str: Either a single query string (which may contain OR/NOT/
                       parentheses) or a list of strings that are AND'd together
                       (legacy multi-argument style).

    Returns:
        A ``Predicate`` callable ``(file_path, SymbolRecord) -> bool``.

    Raises:
        PredicateError: If the query cannot be parsed.
    """
    if isinstance(tokens_or_str, list):
        # Legacy: list of atoms → implicit AND of all.
        if not tokens_or_str:
            # Match everything.
            return lambda _fp, _rec: True
        combined = " ".join(tokens_or_str)
    else:
        combined = tokens_or_str

    combined = combined.strip()
    if not combined:
        return lambda _fp, _rec: True

    tokens = _tokenise(combined)
    if not tokens:
        return lambda _fp, _rec: True

    return _Parser(tokens).parse()

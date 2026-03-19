"""Code-domain query evaluator for the Muse generic query engine.

Implements :data:`~muse.core.query_engine.CommitEvaluator` for the code domain.
Allows agents and humans to search the commit history for code changes::

    muse code-query "symbol == 'my_function' and change == 'added'"
    muse code-query "language == 'Python' and author == 'agent-x'"
    muse code-query "agent_id == 'claude' and sem_ver_bump == 'major'"
    muse code-query "file == 'src/core.py'"
    muse code-query "change == 'added' and kind == 'class'"

Query language
--------------

    query      = and_expr ( 'or' and_expr )*
    and_expr   = atom ( 'and' atom )*
    atom       = FIELD OP VALUE
    FIELD      = 'symbol' | 'file' | 'language' | 'kind' | 'change'
               | 'author' | 'agent_id' | 'model_id' | 'toolchain_id'
               | 'sem_ver_bump' | 'branch'
    OP         = '==' | '!=' | 'contains' | 'startswith'
    VALUE      = QUOTED_STRING | UNQUOTED_WORD

Supported fields
----------------

``symbol``       Qualified symbol name (e.g. ``"MyClass.method"``).
``file``         Workspace-relative file path.
``language``     Language name (``"Python"``, ``"TypeScript"``…).
``kind``         Symbol kind (``"function"``, ``"class"``, ``"method"``…).
``change``       ``"added"``, ``"removed"``, or ``"modified"``.
``author``       Commit author string.
``agent_id``     Agent identity from commit provenance.
``model_id``     Model ID from commit provenance.
``toolchain_id`` Toolchain string from commit provenance.
``sem_ver_bump`` Semantic version bump: ``"none"``, ``"patch"``,
                 ``"minor"``, ``"major"``.
``branch``       Branch name.
"""

from __future__ import annotations

import logging
import pathlib
import re
from dataclasses import dataclass
from typing import Literal, TypeIs, get_args

from muse.core.query_engine import CommitEvaluator, QueryMatch
from muse.core.store import CommitRecord
from muse.domain import DomainOp
from muse.plugins.code._query import language_of, symbols_for_snapshot

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Query AST types
# ---------------------------------------------------------------------------

CodeField = Literal[
    "symbol", "file", "language", "kind", "change",
    "author", "agent_id", "model_id", "toolchain_id",
    "sem_ver_bump", "branch",
]

CodeOp = Literal["==", "!=", "contains", "startswith"]


@dataclass(frozen=True)
class Comparison:
    """A single field OP value predicate."""

    field: CodeField
    op: CodeOp
    value: str


@dataclass(frozen=True)
class AndExpr:
    """Conjunction of predicates (all must match)."""

    clauses: list[Comparison]


@dataclass(frozen=True)
class OrExpr:
    """Disjunction of AND-expressions (any must match)."""

    clauses: list[AndExpr]


# ---------------------------------------------------------------------------
# Tokeniser & parser
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(
    r"""
    (?P<keyword>(?:or|and|contains|startswith)(?![A-Za-z0-9_.]))
    |(?P<op>==|!=)
    |(?P<quoted>"[^"]*"|'[^']*')
    |(?P<word>[A-Za-z_][A-Za-z0-9_.]*)
    """,
    re.VERBOSE,
)

_VALID_FIELDS: frozenset[str] = frozenset(get_args(CodeField))
_VALID_OPS: frozenset[str] = frozenset(get_args(CodeOp))


def _is_code_field(tok: str) -> TypeIs[CodeField]:
    return tok in _VALID_FIELDS


def _is_code_op(tok: str) -> TypeIs[CodeOp]:
    return tok in _VALID_OPS


def _as_code_field(tok: str) -> CodeField:
    """Validate and narrow *tok* to :data:`CodeField`; raises :exc:`ValueError` if invalid."""
    if not _is_code_field(tok):
        raise ValueError(f"Unknown field: {tok!r}. Valid: {sorted(_VALID_FIELDS)}")
    return tok


def _as_code_op(tok: str) -> CodeOp:
    """Validate and narrow *tok* to :data:`CodeOp`; raises :exc:`ValueError` if invalid."""
    if not _is_code_op(tok):
        raise ValueError(f"Unknown operator: {tok!r}. Valid: {sorted(_VALID_OPS)}")
    return tok


def _tokenize(query: str) -> list[str]:
    return [m.group() for m in _TOKEN_RE.finditer(query)]


def _parse_query(query: str) -> OrExpr:
    """Parse a query string into an :class:`OrExpr` AST."""
    tokens = _tokenize(query.strip())
    pos = 0

    def peek() -> str | None:
        return tokens[pos] if pos < len(tokens) else None

    def consume() -> str:
        nonlocal pos
        tok = tokens[pos]
        pos += 1
        return tok

    def parse_atom() -> Comparison:
        field_tok = consume()
        validated_field = _as_code_field(field_tok)
        op_tok = consume()
        validated_op = _as_code_op(op_tok)
        val_tok = consume()
        if val_tok.startswith(("'", '"')):
            val_tok = val_tok[1:-1]
        return Comparison(
            field=validated_field,
            op=validated_op,
            value=val_tok,
        )

    def parse_and() -> AndExpr:
        clauses: list[Comparison] = [parse_atom()]
        while peek() == "and":
            consume()
            clauses.append(parse_atom())
        return AndExpr(clauses=clauses)

    def parse_or() -> OrExpr:
        clauses: list[AndExpr] = [parse_and()]
        while peek() == "or":
            consume()
            clauses.append(parse_and())
        return OrExpr(clauses=clauses)

    return parse_or()


# ---------------------------------------------------------------------------
# Evaluator
# ---------------------------------------------------------------------------


def _match_op(actual: str, op: CodeOp, expected: str) -> bool:
    """Apply *op* to *actual* and *expected* strings."""
    if op == "==":
        return actual == expected
    if op == "!=":
        return actual != expected
    if op == "contains":
        return expected.lower() in actual.lower()
    # op == "startswith"
    return actual.lower().startswith(expected.lower())


def _commit_matches_comparison(
    comparison: Comparison,
    commit: CommitRecord,
    manifest: dict[str, str],
    root: pathlib.Path,
    symbol_matches: list[dict[str, str]],
) -> bool:
    """Return True if *commit* + its symbols satisfy *comparison*.

    For symbol/file/language/kind/change fields, each (symbol, file) pair
    that matches is appended to *symbol_matches* for result detail.
    """
    f = comparison.field
    op = comparison.op
    v = comparison.value

    # Commit-level fields — no symbol iteration needed.
    if f == "author":
        return _match_op(commit.author, op, v)
    if f == "agent_id":
        return _match_op(commit.agent_id, op, v)
    if f == "model_id":
        return _match_op(commit.model_id, op, v)
    if f == "toolchain_id":
        return _match_op(commit.toolchain_id, op, v)
    if f == "sem_ver_bump":
        return _match_op(commit.sem_ver_bump, op, v)
    if f == "branch":
        return _match_op(commit.branch, op, v)

    # Symbol/file-level fields — iterate the delta ops.
    delta = commit.structured_delta
    if delta is None:
        return False

    hit = False
    for op_rec in delta.get("ops", []):
        op_type = op_rec.get("op", "")
        address: str = op_rec.get("address", "")

        # Resolve file vs symbol from address.
        if "::" in address:
            file_path, symbol_name = address.split("::", 1)
        else:
            file_path = address
            symbol_name = ""

        lang = language_of(file_path)
        change_type = (
            "added" if op_type == "insert"
            else "removed" if op_type == "delete"
            else "modified"
        )

        # For PatchOps also iterate child_ops.
        all_ops: list[DomainOp] = [op_rec]
        if op_rec.get("op") == "patch" and op_rec["op"] == "patch":
            all_ops = [op_rec] + op_rec["child_ops"]

        for rec in all_ops:
            rec_address: str = str(rec.get("address", address))
            if "::" in rec_address:
                rec_file, rec_symbol = rec_address.split("::", 1)
            else:
                rec_file = rec_address
                rec_symbol = ""

            rec_kind = str(rec.get("kind", ""))
            rec_op_type = str(rec.get("op", ""))
            rec_change = (
                "added" if rec_op_type == "insert"
                else "removed" if rec_op_type == "delete"
                else "modified"
            )

            field_val = {
                "symbol": rec_symbol or symbol_name,
                "file": rec_file or file_path,
                "language": lang,
                "kind": rec_kind,
                "change": rec_change or change_type,
            }.get(f, "")

            if field_val is not None and _match_op(field_val, op, v):
                hit = True
                sym = rec_symbol or symbol_name
                symbol_matches.append({
                    "file": rec_file or file_path,
                    "symbol": sym,
                    "kind": rec_kind,
                    "change": rec_change or change_type,
                    "language": lang,
                })

    return hit


def build_evaluator(query: str) -> CommitEvaluator:
    """Parse *query* and return a :data:`CommitEvaluator` for :func:`~muse.core.query_engine.walk_history`.

    Args:
        query: A query string in the code query DSL.

    Returns:
        A callable that can be passed to :func:`~muse.core.query_engine.walk_history`.

    Raises:
        ValueError: If the query cannot be parsed.
    """
    ast = _parse_query(query)

    def evaluator(
        commit: CommitRecord,
        manifest: dict[str, str],
        root: pathlib.Path,
    ) -> list[QueryMatch]:
        matches: list[QueryMatch] = []
        symbol_matches: list[dict[str, str]] = []

        # An OrExpr matches when any AndExpr matches.
        for and_expr in ast.clauses:
            clause_symbols: list[dict[str, str]] = []
            # An AndExpr matches when ALL comparisons match.
            all_match = all(
                _commit_matches_comparison(cmp, commit, manifest, root, clause_symbols)
                for cmp in and_expr.clauses
            )
            if all_match:
                symbol_matches.extend(clause_symbols)
                break  # or-short-circuit

        if not symbol_matches:
            # Check if commit-level only match.
            only_commit_fields = all(
                cmp.field in {"author", "agent_id", "model_id", "toolchain_id", "sem_ver_bump", "branch"}
                for and_expr in ast.clauses
                for cmp in and_expr.clauses
            )
            commit_match = any(
                all(
                    _commit_matches_comparison(cmp, commit, manifest, root, [])
                    for cmp in and_expr.clauses
                )
                for and_expr in ast.clauses
            )
            if only_commit_fields and commit_match:
                m = QueryMatch(
                    commit_id=commit.commit_id,
                    author=commit.author,
                    committed_at=commit.committed_at.isoformat(),
                    branch=commit.branch,
                    detail=commit.message[:80],
                    extra={},
                )
                if commit.agent_id:
                    m["agent_id"] = commit.agent_id
                if commit.model_id:
                    m["model_id"] = commit.model_id
                matches.append(m)
        else:
            for sym in symbol_matches[:20]:  # cap per-commit matches
                detail = sym.get("symbol") or sym.get("file", "?")
                change = sym.get("change", "")
                if change:
                    detail = f"{detail} ({change})"
                m = QueryMatch(
                    commit_id=commit.commit_id,
                    author=commit.author,
                    committed_at=commit.committed_at.isoformat(),
                    branch=commit.branch,
                    detail=detail,
                    extra={k: v for k, v in sym.items()},
                )
                if commit.agent_id:
                    m["agent_id"] = commit.agent_id
                matches.append(m)

        return matches

    return evaluator

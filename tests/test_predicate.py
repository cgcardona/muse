"""Tests for the predicate DSL parser (muse/plugins/code/_predicate.py).

Coverage
--------
Tokenisation
    - Valid atoms, operators, keywords, parentheses, whitespace skipping.
    - Unexpected character raises PredicateError.

Atom parsing
    - All seven operators: = ~= ^= $= != >= <=
    - All ten predicate keys: kind, language, name, qualified_name, file,
      hash, body_hash, signature_id, lineno_gt, lineno_lt.
    - Double-quoted values.
    - Unknown key raises PredicateError.
    - Non-integer value for lineno_gt / lineno_lt raises PredicateError.

Compound expressions
    - Implicit AND (adjacent atoms).
    - Explicit OR.
    - Explicit NOT.
    - Parenthesised sub-expressions.
    - Mixed OR / NOT / AND / parentheses.
    - Trailing garbage token raises PredicateError.

parse_query
    - Empty string → match-all predicate.
    - Empty list → match-all predicate.
    - List of atoms → implicit AND.
    - Single string → parsed normally.

Predicate evaluation
    - Each key field reads the correct SymbolRecord / file_path field.
    - lineno_gt / lineno_lt boundary conditions (strict inequality).
    - hash / body_hash / signature_id prefix matching.
    - Case-insensitive string matching for =, ~=, ^=, $=, !=.
"""

import pytest

from muse.plugins.code._predicate import PredicateError, parse_query
from muse.plugins.code.ast_parser import SymbolRecord


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _rec(
    *,
    kind: str = "function",
    name: str = "my_func",
    qualified_name: str = "my_func",
    lineno: int = 10,
    end_lineno: int = 20,
    content_id: str = "abcdef1234567890" * 2,
    body_hash: str = "deadbeef1234" * 4,
    signature_id: str = "cafebabe5678" * 4,
    metadata_id: str = "",
    canonical_key: str = "",
) -> SymbolRecord:
    return SymbolRecord(
        kind=kind,
        name=name,
        qualified_name=qualified_name,
        lineno=lineno,
        end_lineno=end_lineno,
        content_id=content_id,
        body_hash=body_hash,
        signature_id=signature_id,
        metadata_id=metadata_id,
        canonical_key=canonical_key,
    )


def _match(
    query: str | list[str],
    file_path: str = "src/billing.py",
    kind: str = "function",
    name: str = "my_func",
    qualified_name: str = "my_func",
    lineno: int = 10,
) -> bool:
    rec = _rec(kind=kind, name=name, qualified_name=qualified_name, lineno=lineno)
    pred = parse_query(query)
    return pred(file_path, rec)


# ---------------------------------------------------------------------------
# Empty / match-all
# ---------------------------------------------------------------------------


class TestMatchAll:
    def test_empty_string_matches_everything(self) -> None:
        pred = parse_query("")
        assert pred("src/foo.py", _rec())

    def test_empty_list_matches_everything(self) -> None:
        pred = parse_query([])
        assert pred("src/foo.py", _rec())

    def test_whitespace_only_matches_everything(self) -> None:
        pred = parse_query("   ")
        assert pred("src/foo.py", _rec())


# ---------------------------------------------------------------------------
# Single atom — kind key
# ---------------------------------------------------------------------------


class TestKindPredicate:
    def test_exact_match(self) -> None:
        assert _match("kind=function", kind="function")

    def test_exact_match_no_hit(self) -> None:
        assert not _match("kind=class", kind="function")

    def test_case_insensitive(self) -> None:
        assert _match("kind=Function", kind="function")

    def test_not_equal(self) -> None:
        assert _match("kind!=class", kind="function")
        assert not _match("kind!=function", kind="function")

    def test_contains(self) -> None:
        assert _match("kind~=unc", kind="function")
        assert not _match("kind~=xyz", kind="function")

    def test_starts_with(self) -> None:
        assert _match("kind^=func", kind="function")
        assert not _match("kind^=class", kind="function")

    def test_ends_with(self) -> None:
        assert _match("kind$=tion", kind="function")
        assert not _match("kind$=ass", kind="function")


# ---------------------------------------------------------------------------
# name key
# ---------------------------------------------------------------------------


class TestNamePredicate:
    def test_exact(self) -> None:
        assert _match("name=compute_total", name="compute_total")
        assert not _match("name=compute_total", name="compute_invoice")

    def test_contains(self) -> None:
        assert _match("name~=total", name="compute_total")
        assert not _match("name~=invoice", name="compute_total")

    def test_starts_with(self) -> None:
        assert _match("name^=compute", name="compute_total")

    def test_ends_with(self) -> None:
        assert _match("name$=total", name="compute_total")


# ---------------------------------------------------------------------------
# qualified_name key
# ---------------------------------------------------------------------------


class TestQualifiedNamePredicate:
    def test_dotted_name(self) -> None:
        assert _match("qualified_name=Invoice.compute", qualified_name="Invoice.compute")
        assert not _match("qualified_name=Invoice.pay", qualified_name="Invoice.compute")

    def test_contains(self) -> None:
        assert _match("qualified_name~=Invoice", qualified_name="Invoice.compute")


# ---------------------------------------------------------------------------
# file key
# ---------------------------------------------------------------------------


class TestFilePredicate:
    def test_exact(self) -> None:
        assert _match("file=src/billing.py", file_path="src/billing.py")
        assert not _match("file=src/utils.py", file_path="src/billing.py")

    def test_contains(self) -> None:
        assert _match("file~=billing", file_path="src/billing.py")

    def test_starts_with(self) -> None:
        assert _match("file^=src/", file_path="src/billing.py")

    def test_ends_with(self) -> None:
        assert _match("file$=.py", file_path="src/billing.py")


# ---------------------------------------------------------------------------
# hash / body_hash / signature_id keys (prefix matching)
# ---------------------------------------------------------------------------


class TestHashPredicates:
    def test_content_id_prefix(self) -> None:
        rec = _rec(content_id="abcdef" + "0" * 58)
        pred = parse_query("hash=abcde")
        assert pred("f.py", rec)

    def test_content_id_prefix_no_match(self) -> None:
        rec = _rec(content_id="abcdef" + "0" * 58)
        pred = parse_query("hash=xyz")
        assert not pred("f.py", rec)

    def test_body_hash_prefix(self) -> None:
        rec = _rec(body_hash="deadbeef" + "0" * 56)
        pred = parse_query("body_hash=deadbe")
        assert pred("f.py", rec)

    def test_signature_id_prefix(self) -> None:
        rec = _rec(signature_id="cafebabe" + "0" * 56)
        pred = parse_query("signature_id=cafeba")
        assert pred("f.py", rec)

    def test_hash_prefix_case_sensitive_match(self) -> None:
        # Hash matching uses prefix-startswith; stored value case must match query case.
        rec = _rec(content_id="abcdef" + "0" * 58)
        pred = parse_query("hash=abcdef")
        assert pred("f.py", rec)
        # Upper-case stored hash won't match lower-case query prefix
        # (hash= uses startswith without normalization — this is by design).
        rec_upper = _rec(content_id="ABCDEF" + "0" * 58)
        pred_lower = parse_query("hash=abcdef")
        # The stored hash starts with "ABCDEF", query is "abcdef" → no match.
        assert not pred_lower("f.py", rec_upper)


# ---------------------------------------------------------------------------
# lineno_gt / lineno_lt
# ---------------------------------------------------------------------------


class TestLinenoPredicates:
    def test_lineno_gt_pass(self) -> None:
        assert _match("lineno_gt=5", lineno=10)

    def test_lineno_gt_boundary(self) -> None:
        # lineno_gt=10 means lineno > 10, so lineno=10 should NOT match
        assert not _match("lineno_gt=10", lineno=10)
        assert _match("lineno_gt=9", lineno=10)

    def test_lineno_lt_pass(self) -> None:
        assert _match("lineno_lt=20", lineno=10)

    def test_lineno_lt_boundary(self) -> None:
        assert not _match("lineno_lt=10", lineno=10)
        assert _match("lineno_lt=11", lineno=10)

    def test_lineno_gt_bad_value(self) -> None:
        with pytest.raises(PredicateError, match="integer"):
            parse_query("lineno_gt=abc")

    def test_lineno_lt_bad_value(self) -> None:
        with pytest.raises(PredicateError, match="integer"):
            parse_query("lineno_lt=abc")


# ---------------------------------------------------------------------------
# language key
# ---------------------------------------------------------------------------


class TestLanguagePredicate:
    def test_python_by_extension(self) -> None:
        pred = parse_query("language=Python")
        assert pred("src/billing.py", _rec())
        assert not pred("src/billing.go", _rec())

    def test_go_by_extension(self) -> None:
        pred = parse_query("language=Go")
        assert pred("cmd/main.go", _rec())
        assert not pred("cmd/main.py", _rec())

    def test_typescript(self) -> None:
        pred = parse_query("language=TypeScript")
        assert pred("src/index.ts", _rec())

    def test_rust(self) -> None:
        pred = parse_query("language=Rust")
        assert pred("src/main.rs", _rec())


# ---------------------------------------------------------------------------
# Compound: AND (implicit)
# ---------------------------------------------------------------------------


class TestImplicitAnd:
    def test_two_atoms_both_match(self) -> None:
        assert _match("kind=function name=compute_total", kind="function", name="compute_total")

    def test_two_atoms_first_no_match(self) -> None:
        assert not _match("kind=class name=compute_total", kind="function", name="compute_total")

    def test_two_atoms_second_no_match(self) -> None:
        assert not _match("kind=function name=invoice", kind="function", name="compute_total")

    def test_three_atoms(self) -> None:
        assert _match(
            "kind=function name~=compute file~=billing",
            kind="function",
            name="compute_total",
            file_path="src/billing.py",
        )

    def test_explicit_and_keyword(self) -> None:
        assert _match("kind=function AND name=compute_total", kind="function", name="compute_total")


# ---------------------------------------------------------------------------
# Compound: OR
# ---------------------------------------------------------------------------


class TestOr:
    def test_or_first_matches(self) -> None:
        assert _match("kind=function OR kind=class", kind="function")

    def test_or_second_matches(self) -> None:
        assert _match("kind=function OR kind=class", kind="class")

    def test_or_neither_matches(self) -> None:
        assert not _match("kind=function OR kind=class", kind="method")

    def test_or_with_three_alternatives(self) -> None:
        pred = parse_query("kind=function OR kind=class OR kind=method")
        assert pred("f.py", _rec(kind="function"))
        assert pred("f.py", _rec(kind="class"))
        assert pred("f.py", _rec(kind="method"))
        assert not pred("f.py", _rec(kind="variable"))

    def test_or_in_list_mode(self) -> None:
        # List mode joins with spaces, so OR in middle still works.
        pred = parse_query(["kind=function OR kind=class"])
        assert pred("f.py", _rec(kind="class"))


# ---------------------------------------------------------------------------
# Compound: NOT
# ---------------------------------------------------------------------------


class TestNot:
    def test_not_inverts_match(self) -> None:
        assert not _match("NOT kind=function", kind="function")
        assert _match("NOT kind=function", kind="class")

    def test_not_with_and(self) -> None:
        pred = parse_query("NOT kind=import name~=billing")
        # kind=function, name=billing_util → matches (not import AND name contains billing)
        assert pred("f.py", _rec(kind="function", name="billing_util"))
        # kind=import → fails NOT
        assert not pred("f.py", _rec(kind="import", name="billing_util"))
        # name doesn't contain billing → fails AND
        assert not pred("f.py", _rec(kind="function", name="compute"))

    def test_not_with_parenthesised_group(self) -> None:
        # NOT applied to a grouped predicate.
        pred = parse_query("NOT (kind=import)")
        assert pred("f.py", _rec(kind="function"))
        assert not pred("f.py", _rec(kind="import"))


# ---------------------------------------------------------------------------
# Parentheses / grouping
# ---------------------------------------------------------------------------


class TestParentheses:
    def test_parenthesised_or(self) -> None:
        pred = parse_query("(kind=function OR kind=method) name^=_")
        # function starting with _ → matches
        assert pred("f.py", _rec(kind="function", name="_private"))
        # method starting with _ → matches
        assert pred("f.py", _rec(kind="method", name="_helper"))
        # class starting with _ → does NOT match (kind check fails)
        assert not pred("f.py", _rec(kind="class", name="_Base"))
        # function NOT starting with _ → does NOT match (name check fails)
        assert not pred("f.py", _rec(kind="function", name="public_func"))

    def test_nested_parens(self) -> None:
        pred = parse_query("((kind=function OR kind=class) AND file~=billing)")
        assert pred("src/billing.py", _rec(kind="function"))
        assert pred("src/billing.py", _rec(kind="class"))
        assert not pred("src/utils.py", _rec(kind="function"))

    def test_not_parenthesised_group(self) -> None:
        pred = parse_query("NOT (kind=function OR kind=class)")
        assert pred("f.py", _rec(kind="method"))
        assert not pred("f.py", _rec(kind="function"))


# ---------------------------------------------------------------------------
# parse_query list mode
# ---------------------------------------------------------------------------


class TestParseQueryListMode:
    def test_single_atom_list(self) -> None:
        pred = parse_query(["kind=function"])
        assert pred("f.py", _rec(kind="function"))
        assert not pred("f.py", _rec(kind="class"))

    def test_multi_atom_list_implicit_and(self) -> None:
        pred = parse_query(["kind=function", "name~=compute"])
        assert pred("f.py", _rec(kind="function", name="compute_total"))
        assert not pred("f.py", _rec(kind="class", name="compute_total"))

    def test_atom_with_or_in_list(self) -> None:
        pred = parse_query(["kind=function OR kind=method"])
        assert pred("f.py", _rec(kind="method"))


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------


class TestErrors:
    def test_unknown_key(self) -> None:
        with pytest.raises(PredicateError, match="Unknown predicate key"):
            parse_query("colour=red")

    def test_missing_operator(self) -> None:
        with pytest.raises(PredicateError):
            parse_query("kind function")  # no operator

    def test_unclosed_paren(self) -> None:
        with pytest.raises(PredicateError):
            parse_query("(kind=function")

    def test_unexpected_close_paren(self) -> None:
        with pytest.raises(PredicateError):
            parse_query("kind=function)")

    def test_trailing_garbage(self) -> None:
        # "kind=function" is valid, but then extra garbage
        with pytest.raises(PredicateError):
            parse_query("kind=function )")

    def test_empty_not(self) -> None:
        with pytest.raises(PredicateError):
            parse_query("NOT")

    def test_double_quoted_value(self) -> None:
        # Double-quoted values are stripped correctly.
        pred = parse_query('name="compute total"')
        assert pred("f.py", _rec(name="compute total"))

    def test_or_without_rhs(self) -> None:
        with pytest.raises(PredicateError):
            parse_query("kind=function OR")

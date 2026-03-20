"""Tests for the code domain query evaluator."""

import pathlib

import pytest

from muse.domain import SemVerBump
from muse.plugins.code._code_query import (
    AndExpr,
    Comparison,
    OrExpr,
    build_evaluator,
    _parse_query,
)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


class TestParseQuery:
    def test_simple_equality(self) -> None:
        q = _parse_query("author == 'gabriel'")
        assert isinstance(q, OrExpr)
        assert len(q.clauses) == 1
        and_expr = q.clauses[0]
        assert isinstance(and_expr, AndExpr)
        cmp = and_expr.clauses[0]
        assert cmp.field == "author"
        assert cmp.op == "=="
        assert cmp.value == "gabriel"

    def test_and_expression(self) -> None:
        q = _parse_query("author == 'x' and language == 'Python'")
        assert len(q.clauses[0].clauses) == 2

    def test_or_expression(self) -> None:
        q = _parse_query("author == 'x' or author == 'y'")
        assert len(q.clauses) == 2

    def test_contains_operator(self) -> None:
        q = _parse_query("agent_id contains claude")
        cmp = q.clauses[0].clauses[0]
        assert cmp.op == "contains"
        assert cmp.value == "claude"

    def test_startswith_operator(self) -> None:
        q = _parse_query("symbol startswith my_")
        cmp = q.clauses[0].clauses[0]
        assert cmp.op == "startswith"

    def test_unknown_field_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown field"):
            _parse_query("nonexistent == 'x'")

    def test_double_quoted_string(self) -> None:
        q = _parse_query('author == "gabriel"')
        cmp = q.clauses[0].clauses[0]
        assert cmp.value == "gabriel"


# ---------------------------------------------------------------------------
# build_evaluator + evaluator logic
# ---------------------------------------------------------------------------


import datetime
from muse.core.query_engine import QueryMatch
from muse.core.store import CommitRecord
from muse.domain import StructuredDelta, InsertOp


def _make_commit(
    author: str = "alice",
    agent_id: str = "",
    model_id: str = "",
    branch: str = "main",
    sem_ver_bump: SemVerBump = "none",
    delta: StructuredDelta | None = None,
) -> CommitRecord:
    return CommitRecord(
        commit_id="abc1234",
        repo_id="repo",
        branch=branch,
        snapshot_id="s" * 64,
        message="test commit",
        committed_at=datetime.datetime.now(datetime.timezone.utc),
        author=author,
        agent_id=agent_id,
        model_id=model_id,
        sem_ver_bump=sem_ver_bump,
        structured_delta=delta,
    )


class TestBuildEvaluator:
    def test_author_match(self) -> None:
        evaluator = build_evaluator("author == 'alice'")
        commit = _make_commit(author="alice")
        results = evaluator(commit, {}, pathlib.Path("."))
        assert len(results) == 1

    def test_author_no_match(self) -> None:
        evaluator = build_evaluator("author == 'bob'")
        commit = _make_commit(author="alice")
        results = evaluator(commit, {}, pathlib.Path("."))
        assert results == []

    def test_agent_id_contains(self) -> None:
        evaluator = build_evaluator("agent_id contains claude")
        commit = _make_commit(agent_id="claude-v4")
        results = evaluator(commit, {}, pathlib.Path("."))
        assert len(results) == 1

    def test_sem_ver_bump_match(self) -> None:
        evaluator = build_evaluator("sem_ver_bump == 'major'")
        commit = _make_commit(sem_ver_bump="major")
        results = evaluator(commit, {}, pathlib.Path("."))
        assert len(results) == 1

    def test_and_both_must_match(self) -> None:
        evaluator = build_evaluator("author == 'alice' and agent_id == 'bot'")
        # Only author matches, not agent_id.
        commit = _make_commit(author="alice", agent_id="human")
        results = evaluator(commit, {}, pathlib.Path("."))
        assert results == []

    def test_or_one_match_sufficient(self) -> None:
        evaluator = build_evaluator("author == 'alice' or author == 'bob'")
        commit_alice = _make_commit(author="alice")
        commit_bob = _make_commit(author="bob")
        assert len(evaluator(commit_alice, {}, pathlib.Path("."))) >= 1
        assert len(evaluator(commit_bob, {}, pathlib.Path("."))) >= 1

    def test_branch_match(self) -> None:
        evaluator = build_evaluator("branch == 'dev'")
        commit = _make_commit(branch="dev")
        assert len(evaluator(commit, {}, pathlib.Path("."))) >= 1

    def test_symbol_match_from_delta(self) -> None:
        op = InsertOp(
            op="insert",
            address="src/utils.py::my_func",
            position=None,
            content_id="hash1",
            content_summary="added my_func",
        )
        delta = StructuredDelta(domain="code", ops=[op], summary="1 symbol added")
        commit = _make_commit(delta=delta)
        evaluator = build_evaluator("symbol == 'my_func'")
        results = evaluator(commit, {}, pathlib.Path("."))
        assert len(results) >= 1
        assert any("my_func" in r.get("detail", "") for r in results)

    def test_change_added_match(self) -> None:
        op = InsertOp(
            op="insert",
            address="src/foo.py::bar",
            position=None,
            content_id="h1",
            content_summary="bar added",
        )
        delta = StructuredDelta(domain="code", ops=[op], summary="added bar")
        commit = _make_commit(delta=delta)
        evaluator = build_evaluator("change == 'added'")
        results = evaluator(commit, {}, pathlib.Path("."))
        assert len(results) >= 1

    def test_invalid_query_raises(self) -> None:
        with pytest.raises(ValueError):
            build_evaluator("badfield == 'x'")

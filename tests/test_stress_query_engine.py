"""Stress tests for the generic query engine and code query DSL.

Covers:
- walk_history on linear chains of 100+ commits.
- CommitEvaluator with correct 3-arg signature.
- format_matches output format.
- Code query DSL: all field types, all operators, AND/OR composition.
- Code query DSL: unknown field raises ValueError.
- Query against large history (200 commits).
- Branch-scoped queries.
"""

import datetime
import pathlib

import pytest

from muse.core.query_engine import CommitEvaluator, QueryMatch, format_matches, walk_history
from muse.core.store import CommitRecord, write_commit
from muse.domain import SemVerBump
from muse.plugins.code._code_query import build_evaluator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _write(
    root: pathlib.Path,
    cid: str,
    branch: str = "main",
    parent: str | None = None,
    author: str = "alice",
    agent_id: str = "",
    model_id: str = "",
    sem_ver_bump: SemVerBump = "none",
    message: str = "",
) -> CommitRecord:
    c = CommitRecord(
        commit_id=cid,
        repo_id="repo",
        branch=branch,
        snapshot_id=f"snap-{cid}",
        message=message or f"commit {cid}",
        committed_at=_now(),
        parent_commit_id=parent,
        author=author,
        agent_id=agent_id,
        model_id=model_id,
        sem_ver_bump=sem_ver_bump,
    )
    write_commit(root, c)
    ref = root / ".muse" / "refs" / "heads" / branch
    ref.write_text(cid)
    return c


def _make_match(commit: CommitRecord) -> QueryMatch:
    return QueryMatch(
        commit_id=commit.commit_id,
        author=commit.author,
        committed_at=commit.committed_at.isoformat(),
        branch=commit.branch,
        detail=f"matched commit {commit.commit_id}",
    )


@pytest.fixture
def repo(tmp_path: pathlib.Path) -> pathlib.Path:
    muse = tmp_path / ".muse"
    (muse / "commits").mkdir(parents=True)
    (muse / "refs" / "heads").mkdir(parents=True)
    return tmp_path


# ===========================================================================
# walk_history — basic
# ===========================================================================


class TestWalkHistoryBasic:
    def test_empty_history_no_matches(self, repo: pathlib.Path) -> None:
        def ev(commit: CommitRecord, manifest: dict[str, str], root: pathlib.Path) -> list[QueryMatch]:
            return [_make_match(commit)]
        result = walk_history(repo, "nonexistent-branch", ev)
        assert result == []

    def test_single_commit_matches(self, repo: pathlib.Path) -> None:
        _write(repo, "only", branch="main")
        def ev(commit: CommitRecord, manifest: dict[str, str], root: pathlib.Path) -> list[QueryMatch]:
            return [_make_match(commit)]
        result = walk_history(repo, "main", ev)
        assert len(result) == 1
        assert result[0]["commit_id"] == "only"

    def test_single_commit_no_match(self, repo: pathlib.Path) -> None:
        _write(repo, "only", branch="main")
        def ev(commit: CommitRecord, manifest: dict[str, str], root: pathlib.Path) -> list[QueryMatch]:
            return []
        result = walk_history(repo, "main", ev)
        assert result == []

    def test_linear_chain_all_match(self, repo: pathlib.Path) -> None:
        prev = None
        for i in range(10):
            cid = f"c{i:03d}"
            _write(repo, cid, parent=prev)
            prev = cid
        def ev(commit: CommitRecord, manifest: dict[str, str], root: pathlib.Path) -> list[QueryMatch]:
            return [_make_match(commit)]
        result = walk_history(repo, "main", ev)
        assert len(result) == 10

    def test_linear_chain_filtered(self, repo: pathlib.Path) -> None:
        prev = None
        for i in range(10):
            cid = f"c{i:03d}"
            author = "alice" if i % 2 == 0 else "bob"
            _write(repo, cid, parent=prev, author=author)
            prev = cid

        def ev(commit: CommitRecord, manifest: dict[str, str], root: pathlib.Path) -> list[QueryMatch]:
            if commit.author == "alice":
                return [_make_match(commit)]
            return []

        result = walk_history(repo, "main", ev)
        assert len(result) == 5

    def test_max_commits_limits_walk(self, repo: pathlib.Path) -> None:
        prev = None
        for i in range(50):
            cid = f"c{i:03d}"
            _write(repo, cid, parent=prev)
            prev = cid
        def ev(commit: CommitRecord, manifest: dict[str, str], root: pathlib.Path) -> list[QueryMatch]:
            return [_make_match(commit)]
        result = walk_history(repo, "main", ev, max_commits=10)
        assert len(result) == 10

    def test_matches_include_commit_id_and_branch(self, repo: pathlib.Path) -> None:
        _write(repo, "abc123", branch="main", author="alice")
        def ev(commit: CommitRecord, manifest: dict[str, str], root: pathlib.Path) -> list[QueryMatch]:
            return [_make_match(commit)]
        result = walk_history(repo, "main", ev)
        assert result[0]["commit_id"] == "abc123"
        assert result[0]["branch"] == "main"
        assert result[0]["author"] == "alice"


# ===========================================================================
# walk_history — large history
# ===========================================================================


class TestWalkHistoryLarge:
    def test_200_commit_chain_full_scan(self, repo: pathlib.Path) -> None:
        prev = None
        for i in range(200):
            cid = f"large-{i:04d}"
            _write(repo, cid, parent=prev, agent_id="bot" if i % 3 == 0 else "")
            prev = cid

        def bot_only(commit: CommitRecord, manifest: dict[str, str], root: pathlib.Path) -> list[QueryMatch]:
            if commit.agent_id == "bot":
                return [_make_match(commit)]
            return []

        result = walk_history(repo, "main", bot_only)
        # 200 commits, every 3rd is bot: indices 0, 3, 6, ..., 198 → 67 commits.
        assert len(result) == 67

    def test_query_by_agent_across_100_commits(self, repo: pathlib.Path) -> None:
        prev = None
        for i in range(100):
            cid = f"agent-test-{i:04d}"
            agent = f"agent-{i % 5}"
            _write(repo, cid, parent=prev, agent_id=agent)
            prev = cid

        def agent_0_only(commit: CommitRecord, manifest: dict[str, str], root: pathlib.Path) -> list[QueryMatch]:
            if commit.agent_id == "agent-0":
                return [_make_match(commit)]
            return []

        result = walk_history(repo, "main", agent_0_only)
        assert len(result) == 20  # 100 / 5 = 20


# ===========================================================================
# format_matches
# ===========================================================================


class TestFormatMatches:
    def test_empty_matches_produces_output(self) -> None:
        out = format_matches([])
        assert isinstance(out, str)

    def test_single_match_includes_commit_id(self) -> None:
        match = QueryMatch(
            commit_id="a" * 64,
            branch="main",
            author="alice",
            committed_at=_now().isoformat(),
            detail="test match",
        )
        out = format_matches([match])
        assert "aaaaaaaa" in out

    def test_multiple_matches_all_present(self) -> None:
        matches = [
            QueryMatch(
                commit_id=f"id{i:04d}",
                branch="main",
                author="alice",
                committed_at=_now().isoformat(),
                detail="matched",
            )
            for i in range(5)
        ]
        out = format_matches(matches)
        for i in range(5):
            assert f"id{i:04d}" in out


# ===========================================================================
# Code query DSL — build_evaluator
# ===========================================================================


class TestCodeQueryDSL:
    # --- author field ---

    def test_author_equals(self, repo: pathlib.Path) -> None:
        _write(repo, "a1", author="alice")
        _write(repo, "a2", author="bob", parent="a1")
        evaluator = build_evaluator("author == 'alice'")
        result = walk_history(repo, "main", evaluator)
        assert any(m["commit_id"] == "a1" for m in result)
        assert not any(m["commit_id"] == "a2" for m in result)

    def test_author_not_equals(self, repo: pathlib.Path) -> None:
        _write(repo, "b1", author="alice")
        _write(repo, "b2", author="bob", parent="b1")
        evaluator = build_evaluator("author != 'alice'")
        result = walk_history(repo, "main", evaluator)
        assert all(m["author"] != "alice" for m in result)

    def test_author_contains(self, repo: pathlib.Path) -> None:
        _write(repo, "c1", author="alice-smith")
        _write(repo, "c2", author="bob-jones", parent="c1")
        evaluator = build_evaluator("author contains 'alice'")
        result = walk_history(repo, "main", evaluator)
        assert len(result) == 1
        assert "alice" in result[0]["author"]

    def test_author_startswith(self, repo: pathlib.Path) -> None:
        _write(repo, "d1", author="agent-claude")
        _write(repo, "d2", author="human-alice", parent="d1")
        evaluator = build_evaluator("author startswith 'agent'")
        result = walk_history(repo, "main", evaluator)
        assert len(result) == 1
        assert result[0]["author"].startswith("agent")

    # --- agent_id field ---

    def test_agent_id_equals(self, repo: pathlib.Path) -> None:
        _write(repo, "e1", agent_id="claude-v4")
        _write(repo, "e2", agent_id="gpt-4o", parent="e1")
        evaluator = build_evaluator("agent_id == 'claude-v4'")
        result = walk_history(repo, "main", evaluator)
        assert len(result) == 1
        assert result[0]["commit_id"] == "e1"

    # --- sem_ver_bump field ---

    def test_sem_ver_bump_major(self, repo: pathlib.Path) -> None:
        _write(repo, "f1", sem_ver_bump="major")
        _write(repo, "f2", sem_ver_bump="minor", parent="f1")
        _write(repo, "f3", sem_ver_bump="patch", parent="f2")
        evaluator = build_evaluator("sem_ver_bump == 'major'")
        result = walk_history(repo, "main", evaluator)
        assert len(result) == 1

    # --- model_id field ---

    def test_model_id_contains(self, repo: pathlib.Path) -> None:
        _write(repo, "g1", model_id="claude-3-5-sonnet-20241022")
        _write(repo, "g2", model_id="gpt-4o-2024-08-06", parent="g1")
        evaluator = build_evaluator("model_id contains 'claude'")
        result = walk_history(repo, "main", evaluator)
        assert len(result) == 1

    # --- AND composition ---

    def test_and_composition(self, repo: pathlib.Path) -> None:
        _write(repo, "h1", author="alice", agent_id="bot-1")
        _write(repo, "h2", author="alice", agent_id="bot-2", parent="h1")
        _write(repo, "h3", author="bob", agent_id="bot-1", parent="h2")
        evaluator = build_evaluator("author == 'alice' and agent_id == 'bot-1'")
        result = walk_history(repo, "main", evaluator)
        assert len(result) == 1
        assert result[0]["commit_id"] == "h1"

    # --- OR composition ---

    def test_or_composition(self, repo: pathlib.Path) -> None:
        _write(repo, "i1", author="alice")
        _write(repo, "i2", author="bob", parent="i1")
        _write(repo, "i3", author="charlie", parent="i2")
        evaluator = build_evaluator("author == 'alice' or author == 'bob'")
        result = walk_history(repo, "main", evaluator)
        assert len(result) == 2

    # --- complex nested AND OR ---

    def test_complex_and_or(self, repo: pathlib.Path) -> None:
        _write(repo, "j1", author="alice", sem_ver_bump="major")
        _write(repo, "j2", author="bob", sem_ver_bump="minor", parent="j1")
        _write(repo, "j3", author="alice", sem_ver_bump="patch", parent="j2")
        evaluator = build_evaluator(
            "sem_ver_bump == 'major' or sem_ver_bump == 'minor'"
        )
        result = walk_history(repo, "main", evaluator)
        assert len(result) == 2

    # --- error cases ---

    def test_unknown_field_raises_value_error(self) -> None:
        with pytest.raises(ValueError):
            build_evaluator("unknown_field == 'something'")

    def test_unknown_operator_raises_value_error(self) -> None:
        with pytest.raises(ValueError):
            build_evaluator("author REGEX 'alice'")

    def test_empty_query_raises(self) -> None:
        with pytest.raises((ValueError, IndexError)):
            build_evaluator("")

    # --- branch field ---

    def test_branch_field_matches_correctly(self, repo: pathlib.Path) -> None:
        _write(repo, "k1", branch="main", author="alice")
        evaluator = build_evaluator("branch == 'main'")
        result = walk_history(repo, "main", evaluator)
        assert all(m["branch"] == "main" for m in result)

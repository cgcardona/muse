"""Tests for the generic query engine in muse/core/query_engine.py."""

import datetime
import pathlib
import tempfile

import pytest

from muse.core.query_engine import QueryMatch, format_matches, walk_history
from muse.core.store import CommitRecord, write_commit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_repo(tmp_path: pathlib.Path) -> pathlib.Path:
    """Set up a minimal .muse/ structure for query_engine tests."""
    muse = tmp_path / ".muse"
    muse.mkdir()
    (muse / "repo.json").write_text('{"repo_id":"test-repo"}')
    (muse / "HEAD").write_text("refs/heads/main")
    (muse / "commits").mkdir()
    (muse / "snapshots").mkdir()
    (muse / "refs" / "heads").mkdir(parents=True)
    return tmp_path


def _write_commit(root: pathlib.Path, commit_id: str, parent_id: str | None = None) -> CommitRecord:
    record = CommitRecord(
        commit_id=commit_id,
        repo_id="test-repo",
        branch="main",
        snapshot_id="snap-" + commit_id,
        message=f"commit {commit_id}",
        committed_at=datetime.datetime.now(datetime.timezone.utc),
        parent_commit_id=parent_id,
        author="test-author",
    )
    write_commit(root, record)
    return record


# ---------------------------------------------------------------------------
# walk_history
# ---------------------------------------------------------------------------


class TestWalkHistory:
    def test_empty_branch_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_repo(pathlib.Path(tmp))
            results = walk_history(root, "main", lambda c, m, r: [])
            assert results == []

    def test_single_commit_visited(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_repo(pathlib.Path(tmp))
            _write_commit(root, "aaa111")
            (root / ".muse" / "refs" / "heads" / "main").write_text("aaa111")

            visited: list[str] = []

            def evaluator(commit: CommitRecord, manifest: dict[str, str], r: pathlib.Path) -> list[QueryMatch]:
                visited.append(commit.commit_id)
                return []

            walk_history(root, "main", evaluator)
            assert visited == ["aaa111"]

    def test_chain_walked_newest_first(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_repo(pathlib.Path(tmp))
            _write_commit(root, "aaa111")
            _write_commit(root, "bbb222", parent_id="aaa111")
            (root / ".muse" / "refs" / "heads" / "main").write_text("bbb222")

            visited: list[str] = []

            def evaluator(commit: CommitRecord, manifest: dict[str, str], r: pathlib.Path) -> list[QueryMatch]:
                visited.append(commit.commit_id)
                return []

            walk_history(root, "main", evaluator)
            assert visited == ["bbb222", "aaa111"]

    def test_matches_collected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_repo(pathlib.Path(tmp))
            _write_commit(root, "ccc333")
            (root / ".muse" / "refs" / "heads" / "main").write_text("ccc333")

            def evaluator(commit: CommitRecord, manifest: dict[str, str], r: pathlib.Path) -> list[QueryMatch]:
                return [QueryMatch(
                    commit_id=commit.commit_id,
                    author=commit.author,
                    committed_at=commit.committed_at.isoformat(),
                    branch=commit.branch,
                    detail="test match",
                    extra={},
                )]

            results = walk_history(root, "main", evaluator)
            assert len(results) == 1
            assert results[0]["detail"] == "test match"

    def test_max_commits_limits_walk(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_repo(pathlib.Path(tmp))
            ids = [f"commit{i:03d}" for i in range(10)]
            for i, cid in enumerate(ids):
                parent = ids[i - 1] if i > 0 else None
                _write_commit(root, cid, parent_id=parent)
            (root / ".muse" / "refs" / "heads" / "main").write_text(ids[-1])

            visited: list[str] = []

            def evaluator(commit: CommitRecord, manifest: dict[str, str], r: pathlib.Path) -> list[QueryMatch]:
                visited.append(commit.commit_id)
                return []

            walk_history(root, "main", evaluator, max_commits=3)
            assert len(visited) == 3

    def test_head_commit_id_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_repo(pathlib.Path(tmp))
            _write_commit(root, "aaa111")
            _write_commit(root, "bbb222", parent_id="aaa111")
            # HEAD points to bbb222 but we override to aaa111.
            (root / ".muse" / "refs" / "heads" / "main").write_text("bbb222")

            visited: list[str] = []

            def evaluator(commit: CommitRecord, manifest: dict[str, str], r: pathlib.Path) -> list[QueryMatch]:
                visited.append(commit.commit_id)
                return []

            walk_history(root, "main", evaluator, head_commit_id="aaa111")
            assert visited == ["aaa111"]


# ---------------------------------------------------------------------------
# format_matches
# ---------------------------------------------------------------------------


class TestFormatMatches:
    def test_empty_returns_no_matches(self) -> None:
        assert "No matches" in format_matches([])

    def test_single_match_formatted(self) -> None:
        m = QueryMatch(
            commit_id="abc12345",
            author="gabriel",
            committed_at="2026-03-18T12:00:00+00:00",
            branch="main",
            detail="my_function (added)",
            extra={},
        )
        out = format_matches([m])
        assert "abc12345"[:8] in out
        assert "gabriel" in out
        assert "my_function (added)" in out

    def test_agent_id_shown_when_present(self) -> None:
        m = QueryMatch(
            commit_id="abc12345",
            author="bot",
            committed_at="2026-03-18T12:00:00+00:00",
            branch="main",
            detail="something",
            extra={},
            agent_id="claude-v4",
        )
        out = format_matches([m])
        assert "claude-v4" in out

    def test_max_results_capped(self) -> None:
        matches = [
            QueryMatch(
                commit_id=f"commit{i:04d}",
                author="x",
                committed_at="2026-01-01T00:00:00+00:00",
                branch="main",
                detail=f"match {i}",
                extra={},
            )
            for i in range(100)
        ]
        out = format_matches(matches, max_results=5)
        assert "95 more" in out

"""Tests for muse annotate — CRDT-backed commit annotations."""

import datetime
import pathlib

import pytest
from typer.testing import CliRunner

from muse.cli.app import cli
from muse.core.store import CommitRecord, write_commit

runner = CliRunner()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def repo(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    """Set up a minimal Muse repo and chdir into it."""
    monkeypatch.chdir(tmp_path)
    muse = tmp_path / ".muse"
    muse.mkdir()
    (muse / "repo.json").write_text('{"repo_id":"test-repo"}')
    (muse / "HEAD").write_text("refs/heads/main")
    (muse / "commits").mkdir()
    (muse / "snapshots").mkdir()
    (muse / "refs" / "heads").mkdir(parents=True)
    return tmp_path


def _write_commit(root: pathlib.Path, commit_id: str = "a" * 64) -> CommitRecord:
    record = CommitRecord(
        commit_id=commit_id,
        repo_id="test-repo",
        branch="main",
        snapshot_id="s" * 64,
        message="test commit",
        committed_at=datetime.datetime.now(datetime.timezone.utc),
        author="test-author",
    )
    write_commit(root, record)
    (root / ".muse" / "refs" / "heads" / "main").write_text(commit_id)
    return record


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestAnnotateCommand:
    def test_show_annotations_when_no_flags(self, repo: pathlib.Path) -> None:
        _write_commit(repo, "a" * 64)
        result = runner.invoke(cli, ["annotate", "a" * 64], catch_exceptions=False)
        assert result.exit_code == 0
        assert "reviewed-by" in result.output

    def test_add_reviewer(self, repo: pathlib.Path) -> None:
        _write_commit(repo, "a" * 64)
        result = runner.invoke(cli, ["annotate", "--reviewed-by", "agent-x", "a" * 64], catch_exceptions=False)
        assert result.exit_code == 0, result.output
        assert "agent-x" in result.output

        # Verify it's persisted.
        from muse.core.store import read_commit
        record = read_commit(repo, "a" * 64)
        assert record is not None
        assert "agent-x" in record.reviewed_by

    def test_add_multiple_reviewers(self, repo: pathlib.Path) -> None:
        _write_commit(repo, "a" * 64)
        # Pass comma-separated reviewers in one call.
        r = runner.invoke(cli, ["annotate", "--reviewed-by", "alice,bob", "a" * 64], catch_exceptions=False)
        assert r.exit_code == 0, r.output

        from muse.core.store import read_commit
        record = read_commit(repo, "a" * 64)
        assert record is not None
        # ORSet semantics: both reviewers should be present.
        assert "alice" in record.reviewed_by
        assert "bob" in record.reviewed_by

    def test_add_reviewer_idempotent(self, repo: pathlib.Path) -> None:
        _write_commit(repo, "a" * 64)
        runner.invoke(cli, ["annotate", "--reviewed-by", "alice", "a" * 64], catch_exceptions=False)
        runner.invoke(cli, ["annotate", "--reviewed-by", "alice", "a" * 64], catch_exceptions=False)

        from muse.core.store import read_commit
        record = read_commit(repo, "a" * 64)
        assert record is not None
        # ORSet: adding the same element twice should appear once.
        assert record.reviewed_by.count("alice") == 1

    def test_increment_test_run_counter(self, repo: pathlib.Path) -> None:
        _write_commit(repo, "a" * 64)
        runner.invoke(cli, ["annotate", "--test-run", "a" * 64], catch_exceptions=False)
        runner.invoke(cli, ["annotate", "--test-run", "a" * 64], catch_exceptions=False)

        from muse.core.store import read_commit
        record = read_commit(repo, "a" * 64)
        assert record is not None
        assert record.test_runs == 2  # GCounter: monotone increment

    def test_unknown_commit_exits_error(self, repo: pathlib.Path) -> None:
        (repo / ".muse" / "refs" / "heads" / "main").write_text("nosuchcommit")
        result = runner.invoke(cli, ["annotate", "nosuchcommit"])
        assert result.exit_code != 0

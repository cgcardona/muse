"""Tests for ``muse plumbing commit-graph`` (base functionality).

Covers: BFS traversal from HEAD, explicit ``--tip``, ``--stop-at`` pruning,
``--max`` truncation, text format (one ID per line), empty-branch error,
unknown tip error, truncated flag, and a stress case with 200 commits.

Enhancement flags (--count, --first-parent, --ancestry-path) are tested in
``tests/test_plumbing_commit_graph_enhancements.py``.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import pathlib

from tests.cli_test_helper import CliRunner

cli = None  # argparse migration — CliRunner ignores this arg
from muse.core.errors import ExitCode
from muse.core.store import CommitRecord, SnapshotRecord, write_commit, write_snapshot

runner = CliRunner()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sha(tag: str) -> str:
    return hashlib.sha256(tag.encode()).hexdigest()


def _init_repo(path: pathlib.Path) -> pathlib.Path:
    muse = path / ".muse"
    (muse / "commits").mkdir(parents=True)
    (muse / "snapshots").mkdir(parents=True)
    (muse / "objects").mkdir(parents=True)
    (muse / "refs" / "heads").mkdir(parents=True)
    (muse / "HEAD").write_text("ref: refs/heads/main", encoding="utf-8")
    (muse / "repo.json").write_text(
        json.dumps({"repo_id": "test-repo", "domain": "midi"}), encoding="utf-8"
    )
    return path


def _env(repo: pathlib.Path) -> dict[str, str]:
    return {"MUSE_REPO_ROOT": str(repo)}


def _snap(repo: pathlib.Path) -> str:
    sid = _sha("snap")
    write_snapshot(
        repo,
        SnapshotRecord(
            snapshot_id=sid,
            manifest={},
            created_at=datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc),
        ),
    )
    return sid


def _commit(
    repo: pathlib.Path, tag: str, sid: str, branch: str = "main", parent: str | None = None
) -> str:
    cid = _sha(tag)
    write_commit(
        repo,
        CommitRecord(
            commit_id=cid,
            repo_id="test-repo",
            branch=branch,
            snapshot_id=sid,
            message=tag,
            committed_at=datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc),
            author="tester",
            parent_commit_id=parent,
        ),
    )
    ref = repo / ".muse" / "refs" / "heads" / branch
    ref.parent.mkdir(parents=True, exist_ok=True)
    ref.write_text(cid, encoding="utf-8")
    return cid


def _linear_chain(repo: pathlib.Path, length: int) -> list[str]:
    """Return list of commit IDs from root (index 0) to tip (index length-1)."""
    sid = _snap(repo)
    cids: list[str] = []
    parent: str | None = None
    for i in range(length):
        cid = _commit(repo, f"c{i}", sid, parent=parent)
        cids.append(cid)
        parent = cid
    return cids


# ---------------------------------------------------------------------------
# Unit: empty branch and bad tip
# ---------------------------------------------------------------------------


class TestCommitGraphUnit:
    def test_empty_branch_exits_user_error(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        result = runner.invoke(cli, ["plumbing", "commit-graph"], env=_env(repo))
        assert result.exit_code == ExitCode.USER_ERROR
        assert "error" in json.loads(result.stdout)

    def test_unknown_tip_exits_user_error(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        ghost = _sha("ghost")
        result = runner.invoke(
            cli, ["plumbing", "commit-graph", "--tip", ghost], env=_env(repo)
        )
        assert result.exit_code == ExitCode.USER_ERROR

    def test_bad_format_exits_user_error(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        sid = _snap(repo)
        cid = _commit(repo, "c", sid)
        result = runner.invoke(
            cli, ["plumbing", "commit-graph", "--tip", cid, "--format", "toml"], env=_env(repo)
        )
        assert result.exit_code == ExitCode.USER_ERROR


# ---------------------------------------------------------------------------
# Integration: BFS traversal
# ---------------------------------------------------------------------------


class TestCommitGraphBFS:
    def test_single_commit_graph(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        sid = _snap(repo)
        cid = _commit(repo, "solo", sid)
        result = runner.invoke(
            cli, ["plumbing", "commit-graph", "--tip", cid], env=_env(repo)
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        assert data["count"] == 1
        assert data["commits"][0]["commit_id"] == cid
        assert data["tip"] == cid

    def test_linear_chain_all_commits_traversed(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        cids = _linear_chain(repo, 5)
        tip = cids[-1]
        result = runner.invoke(
            cli, ["plumbing", "commit-graph", "--tip", tip], env=_env(repo)
        )
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["count"] == 5
        found = {c["commit_id"] for c in data["commits"]}
        assert found == set(cids)

    def test_head_default_traverses_from_current_branch(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        cids = _linear_chain(repo, 3)
        result = runner.invoke(cli, ["plumbing", "commit-graph"], env=_env(repo))
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["count"] == 3

    def test_commit_node_has_required_fields(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        sid = _snap(repo)
        cid = _commit(repo, "fields-test", sid)
        result = runner.invoke(
            cli, ["plumbing", "commit-graph", "--tip", cid], env=_env(repo)
        )
        assert result.exit_code == 0
        node = json.loads(result.stdout)["commits"][0]
        for field in (
            "commit_id", "parent_commit_id", "parent2_commit_id",
            "message", "branch", "committed_at", "snapshot_id", "author",
        ):
            assert field in node, f"Missing field: {field}"


# ---------------------------------------------------------------------------
# Integration: --stop-at pruning
# ---------------------------------------------------------------------------


class TestCommitGraphStopAt:
    def test_stop_at_excludes_ancestor_commits(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        cids = _linear_chain(repo, 5)
        # cids = [c0, c1, c2, c3, c4]; stop at c2 — should see c4, c3 only.
        result = runner.invoke(
            cli,
            ["plumbing", "commit-graph", "--tip", cids[4], "--stop-at", cids[2]],
            env=_env(repo),
        )
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        found = {c["commit_id"] for c in data["commits"]}
        assert cids[4] in found
        assert cids[3] in found
        assert cids[2] not in found
        assert cids[1] not in found

    def test_stop_at_tip_yields_no_commits(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        cids = _linear_chain(repo, 3)
        result = runner.invoke(
            cli,
            ["plumbing", "commit-graph", "--tip", cids[2], "--stop-at", cids[2]],
            env=_env(repo),
        )
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["count"] == 0


# ---------------------------------------------------------------------------
# Integration: --max truncation
# ---------------------------------------------------------------------------


class TestCommitGraphMax:
    def test_max_limits_traversal(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        _linear_chain(repo, 10)
        result = runner.invoke(
            cli, ["plumbing", "commit-graph", "--max", "3"], env=_env(repo)
        )
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["count"] == 3
        assert data["truncated"] is True

    def test_truncated_false_when_all_fit(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        _linear_chain(repo, 5)
        result = runner.invoke(
            cli, ["plumbing", "commit-graph", "--max", "100"], env=_env(repo)
        )
        assert result.exit_code == 0
        assert json.loads(result.stdout)["truncated"] is False


# ---------------------------------------------------------------------------
# Integration: text format
# ---------------------------------------------------------------------------


class TestCommitGraphTextFormat:
    def test_text_format_emits_one_id_per_line(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        cids = _linear_chain(repo, 4)
        result = runner.invoke(
            cli, ["plumbing", "commit-graph", "--format", "text"], env=_env(repo)
        )
        assert result.exit_code == 0
        lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
        assert len(lines) == 4
        assert all(len(ln) == 64 for ln in lines)

    def test_short_format_flag(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        _linear_chain(repo, 2)
        result = runner.invoke(
            cli, ["plumbing", "commit-graph", "-f", "text"], env=_env(repo)
        )
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# Stress: 200-commit linear history
# ---------------------------------------------------------------------------


class TestCommitGraphStress:
    def test_200_commit_chain_fully_traversed(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        cids = _linear_chain(repo, 200)
        result = runner.invoke(cli, ["plumbing", "commit-graph"], env=_env(repo))
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["count"] == 200
        assert data["truncated"] is False

    def test_200_commit_chain_stop_at_midpoint(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        cids = _linear_chain(repo, 200)
        result = runner.invoke(
            cli,
            ["plumbing", "commit-graph", "--tip", cids[199], "--stop-at", cids[99]],
            env=_env(repo),
        )
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        # commits 100..199 (100 commits total)
        assert data["count"] == 100

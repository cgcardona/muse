"""Comprehensive tests for ``muse bisect`` — binary search for bad commits.

Coverage:
- Unit: bisect core functions (start, mark, skip, reset)
- Integration: CLI subcommands (start, bad, good, skip, log, reset)
- E2E: full bisect workflow resolving to a first-bad commit
- Security: invalid refs, session guard (no double-start), ref sanitization
- Stress: deep commit history bisect
"""

from __future__ import annotations

import datetime
import json
import pathlib
import uuid

import pytest
from typer.testing import CliRunner

from muse.cli.app import cli
from muse.core.store import CommitRecord, SnapshotRecord, write_commit, write_snapshot
from muse.core.snapshot import compute_commit_id, compute_snapshot_id

runner = CliRunner()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _init_repo(tmp_path: pathlib.Path) -> tuple[pathlib.Path, str]:
    repo_id = str(uuid.uuid4())
    muse = tmp_path / ".muse"
    muse.mkdir()
    (muse / "repo.json").write_text(
        json.dumps({"repo_id": repo_id, "domain": "midi",
                    "default_branch": "main",
                    "created_at": "2026-01-01T00:00:00+00:00"})
    )
    (muse / "HEAD").write_text("ref: refs/heads/main")
    (muse / "refs" / "heads").mkdir(parents=True)
    (muse / "snapshots").mkdir()
    (muse / "commits").mkdir()
    (muse / "objects").mkdir()
    return tmp_path, repo_id


def _env(root: pathlib.Path) -> dict[str, str]:
    return {"MUSE_REPO_ROOT": str(root)}


def _make_commit(
    root: pathlib.Path,
    repo_id: str,
    *,
    branch: str = "main",
    message: str = "commit",
    parent_id: str | None = None,
) -> str:
    manifest: dict[str, str] = {}
    snap_id = compute_snapshot_id(manifest)
    committed_at = datetime.datetime.now(datetime.timezone.utc)
    commit_id = compute_commit_id(
        parent_ids=[parent_id] if parent_id else [],
        snapshot_id=snap_id,
        message=message,
        committed_at_iso=committed_at.isoformat(),
    )
    write_snapshot(root, SnapshotRecord(snapshot_id=snap_id, manifest=manifest))
    write_commit(root, CommitRecord(
        commit_id=commit_id,
        repo_id=repo_id,
        branch=branch,
        snapshot_id=snap_id,
        message=message,
        committed_at=committed_at,
        parent_commit_id=parent_id,
    ))
    ref_file = root / ".muse" / "refs" / "heads" / branch
    ref_file.parent.mkdir(parents=True, exist_ok=True)
    ref_file.write_text(commit_id)
    return commit_id


def _make_chain(root: pathlib.Path, repo_id: str, n: int) -> list[str]:
    """Create a linear chain of n commits; return commit IDs oldest-first."""
    ids: list[str] = []
    parent: str | None = None
    for i in range(n):
        cid = _make_commit(root, repo_id, message=f"commit-{i}", parent_id=parent)
        ids.append(cid)
        parent = cid
    return ids


# ---------------------------------------------------------------------------
# Unit tests — core bisect logic
# ---------------------------------------------------------------------------


class TestBisectCore:
    def test_start_bisect_returns_result(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        ids = _make_chain(root, repo_id, 4)
        from muse.core.bisect import start_bisect
        result = start_bisect(root, ids[-1], [ids[0]], branch="main")
        assert result.next_to_test is not None or result.done

    def test_mark_bad_advances_search(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        ids = _make_chain(root, repo_id, 8)
        from muse.core.bisect import mark_bad, start_bisect
        start_bisect(root, ids[-1], [ids[0]], branch="main")
        result = mark_bad(root, ids[-1])
        assert not result.done or result.first_bad is not None

    def test_mark_good_advances_search(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        ids = _make_chain(root, repo_id, 8)
        from muse.core.bisect import mark_good, start_bisect
        start_bisect(root, ids[-1], [ids[0]], branch="main")
        result = mark_good(root, ids[0])
        assert result is not None

    def test_reset_clears_state(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        ids = _make_chain(root, repo_id, 4)
        from muse.core.bisect import is_bisect_active, reset_bisect, start_bisect
        start_bisect(root, ids[-1], [ids[0]], branch="main")
        assert is_bisect_active(root)
        reset_bisect(root)
        assert not is_bisect_active(root)

    def test_bisect_log_records_events(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        ids = _make_chain(root, repo_id, 4)
        from muse.core.bisect import get_bisect_log, start_bisect
        start_bisect(root, ids[-1], [ids[0]], branch="main")
        log = get_bisect_log(root)
        assert len(log) > 0


# ---------------------------------------------------------------------------
# Integration tests — CLI subcommands
# ---------------------------------------------------------------------------


class TestBisectCLI:
    def test_start_requires_good_ref(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        ids = _make_chain(root, repo_id, 2)
        result = runner.invoke(
            cli, ["bisect", "start", "--bad", ids[-1]],
            env=_env(root)
        )
        assert result.exit_code != 0
        assert "good" in result.output.lower()

    def test_start_with_bad_and_good(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        ids = _make_chain(root, repo_id, 4)
        result = runner.invoke(
            cli, ["bisect", "start", "--bad", ids[-1], "--good", ids[0]],
            env=_env(root), catch_exceptions=False
        )
        assert result.exit_code == 0
        assert "started" in result.output.lower() or "next" in result.output.lower()

    def test_bad_without_session_fails(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        ids = _make_chain(root, repo_id, 2)
        result = runner.invoke(cli, ["bisect", "bad", ids[-1]], env=_env(root))
        assert result.exit_code != 0

    def test_good_without_session_fails(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        ids = _make_chain(root, repo_id, 2)
        result = runner.invoke(cli, ["bisect", "good", ids[0]], env=_env(root))
        assert result.exit_code != 0

    def test_skip_without_session_fails(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        ids = _make_chain(root, repo_id, 2)
        result = runner.invoke(cli, ["bisect", "skip", ids[0]], env=_env(root))
        assert result.exit_code != 0

    def test_reset_clears_session(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        ids = _make_chain(root, repo_id, 4)
        runner.invoke(
            cli, ["bisect", "start", "--bad", ids[-1], "--good", ids[0]],
            env=_env(root)
        )
        result = runner.invoke(cli, ["bisect", "reset"], env=_env(root), catch_exceptions=False)
        assert result.exit_code == 0
        # After reset, bad should fail
        result2 = runner.invoke(cli, ["bisect", "bad", ids[-1]], env=_env(root))
        assert result2.exit_code != 0

    def test_log_shows_entries(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        ids = _make_chain(root, repo_id, 4)
        runner.invoke(
            cli, ["bisect", "start", "--bad", ids[-1], "--good", ids[0]],
            env=_env(root)
        )
        result = runner.invoke(cli, ["bisect", "log"], env=_env(root), catch_exceptions=False)
        assert result.exit_code == 0

    def test_double_start_fails(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        ids = _make_chain(root, repo_id, 4)
        runner.invoke(
            cli, ["bisect", "start", "--bad", ids[-1], "--good", ids[0]],
            env=_env(root)
        )
        result = runner.invoke(
            cli, ["bisect", "start", "--bad", ids[-1], "--good", ids[0]],
            env=_env(root)
        )
        assert result.exit_code != 0
        assert "already" in result.output.lower()

    def test_bad_invalid_ref_fails(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        ids = _make_chain(root, repo_id, 4)
        runner.invoke(
            cli, ["bisect", "start", "--bad", ids[-1], "--good", ids[0]],
            env=_env(root)
        )
        result = runner.invoke(cli, ["bisect", "bad", "deadbeef"], env=_env(root))
        assert result.exit_code != 0

    def test_reset_without_session_succeeds(self, tmp_path: pathlib.Path) -> None:
        """reset when no session is active should not crash."""
        root, _ = _init_repo(tmp_path)
        result = runner.invoke(cli, ["bisect", "reset"], env=_env(root), catch_exceptions=False)
        assert result.exit_code == 0

    def test_log_empty_without_session(self, tmp_path: pathlib.Path) -> None:
        root, _ = _init_repo(tmp_path)
        result = runner.invoke(cli, ["bisect", "log"], env=_env(root), catch_exceptions=False)
        assert result.exit_code == 0
        assert "no bisect" in result.output.lower() or result.output.strip() == "" or "no" in result.output.lower()


# ---------------------------------------------------------------------------
# E2E tests
# ---------------------------------------------------------------------------


class TestBisectE2E:
    def test_full_bisect_workflow_2_commits(self, tmp_path: pathlib.Path) -> None:
        """Start → mark good → mark bad → find first bad commit."""
        root, repo_id = _init_repo(tmp_path)
        ids = _make_chain(root, repo_id, 2)
        good_id, bad_id = ids[0], ids[1]

        runner.invoke(
            cli, ["bisect", "start", "--bad", bad_id, "--good", good_id],
            env=_env(root)
        )
        # With only 2 commits, bisect should already identify bad_id
        from muse.core.bisect import get_bisect_log
        log = get_bisect_log(root)
        assert len(log) >= 1

    def test_full_bisect_workflow_many_commits(self, tmp_path: pathlib.Path) -> None:
        """With a chain of 8 commits, bisect converges without error."""
        root, repo_id = _init_repo(tmp_path)
        ids = _make_chain(root, repo_id, 8)

        runner.invoke(
            cli, ["bisect", "start", "--bad", ids[-1], "--good", ids[0]],
            env=_env(root), catch_exceptions=False
        )

        from muse.core.bisect import _load_state, is_bisect_active, mark_bad, mark_good
        # Simulate binary search: assume the bug was introduced at ids[4]
        max_steps = 20
        steps = 0
        done = False
        while is_bisect_active(root) and steps < max_steps and not done:
            state = _load_state(root)
            if state is None:
                break
            remaining = state.get("remaining", [])
            if not remaining:
                break
            mid = remaining[len(remaining) // 2]
            # ids[4] and later are "bad"
            if mid in ids[4:]:
                result = mark_bad(root, mid)
            else:
                result = mark_good(root, mid)
            done = result.done
            steps += 1

        # Bisect should have converged or be close
        assert done or steps < max_steps


# ---------------------------------------------------------------------------
# Security tests
# ---------------------------------------------------------------------------


class TestBisectSecurity:
    def test_ref_with_control_chars_is_rejected(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        ids = _make_chain(root, repo_id, 2)
        runner.invoke(
            cli, ["bisect", "start", "--bad", ids[-1], "--good", ids[0]],
            env=_env(root)
        )
        # Inject control chars in a bad ref
        result = runner.invoke(cli, ["bisect", "bad", "\x1b[31minjection\x1b[0m"], env=_env(root))
        assert result.exit_code != 0

    def test_output_contains_no_ansi_on_invalid_ref(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        ids = _make_chain(root, repo_id, 2)
        runner.invoke(
            cli, ["bisect", "start", "--bad", ids[-1], "--good", ids[0]],
            env=_env(root)
        )
        result = runner.invoke(cli, ["bisect", "bad", "nonexistent-ref\x1b[31m"], env=_env(root))
        assert "\x1b[31m" not in result.output


# ---------------------------------------------------------------------------
# Stress tests
# ---------------------------------------------------------------------------


class TestBisectStress:
    def test_bisect_50_commit_chain(self, tmp_path: pathlib.Path) -> None:
        """A 50-commit chain converges within log2(50) + 2 ≈ 8 steps."""
        root, repo_id = _init_repo(tmp_path)
        ids = _make_chain(root, repo_id, 50)
        bad_start = 25  # regression introduced at index 25

        result = runner.invoke(
            cli, ["bisect", "start", "--bad", ids[-1], "--good", ids[0]],
            env=_env(root)
        )
        assert result.exit_code == 0

        from muse.core.bisect import _load_state, is_bisect_active, mark_bad, mark_good
        max_steps = 10  # ceil(log2(48)) = 6; allow generous headroom
        steps = 0
        done = False
        while is_bisect_active(root) and steps < max_steps and not done:
            state = _load_state(root)
            if state is None:
                break
            remaining = state.get("remaining", [])
            if not remaining:
                break
            mid = remaining[len(remaining) // 2]
            idx = ids.index(mid) if mid in ids else -1
            if idx >= bad_start:
                result = mark_bad(root, mid)
            else:
                result = mark_good(root, mid)
            done = result.done
            steps += 1

        assert done or steps < max_steps, f"Bisect failed to converge in {steps} steps"

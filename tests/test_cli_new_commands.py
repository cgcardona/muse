"""CLI integration tests for: reflog, gc, archive, bisect, blame, worktree, workspace."""

from __future__ import annotations

import datetime
import hashlib
import json
import pathlib

import pytest
from typer.testing import CliRunner

from muse.cli.app import cli

runner = CliRunner()


# ---------------------------------------------------------------------------
# Repo scaffold helpers
# ---------------------------------------------------------------------------


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _make_repo(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> pathlib.Path:
    """Create a minimal repo with one commit, one file tracked.  Sets cwd."""
    monkeypatch.chdir(tmp_path)
    muse = tmp_path / ".muse"
    for d in ("objects", "commits", "snapshots", "refs/heads", "logs/refs/heads"):
        (muse / d).mkdir(parents=True, exist_ok=True)

    (muse / "repo.json").write_text(json.dumps({"repo_id": "test-repo"}))
    (muse / "HEAD").write_text("ref: refs/heads/main\n")

    content = b"hello world\n"
    sha = _sha256(content)
    obj_dir = muse / "objects" / sha[:2]
    obj_dir.mkdir(parents=True, exist_ok=True)
    (obj_dir / sha[2:]).write_bytes(content)

    snap_id = "s" * 64
    (muse / "snapshots" / f"{snap_id}.json").write_text(
        json.dumps({"snapshot_id": snap_id, "manifest": {"hello.txt": sha}})
    )

    commit_id = "c" * 64
    (muse / "commits" / f"{commit_id}.json").write_text(json.dumps({
        "commit_id": commit_id,
        "repo_id": "test-repo",
        "branch": "main",
        "snapshot_id": snap_id,
        "message": "initial commit",
        "committed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "parent_commit_id": None,
        "parent2_commit_id": None,
        "author": "Test User",
        "metadata": {},
    }))
    (muse / "refs" / "heads" / "main").write_text(commit_id)
    return tmp_path


def _add_commits(repo: pathlib.Path, n: int, parent: str) -> list[str]:
    """Append *n* commits to the main branch, return all commit IDs."""
    commit_ids = [parent]
    prev = parent
    for i in range(n):
        cid = format(i + 1, "064x")
        snap_id = format(100 + i, "064x")
        (repo / ".muse" / "snapshots" / f"{snap_id}.json").write_text(
            json.dumps({"snapshot_id": snap_id, "manifest": {}})
        )
        (repo / ".muse" / "commits" / f"{cid}.json").write_text(json.dumps({
            "commit_id": cid, "repo_id": "test-repo", "branch": "main",
            "snapshot_id": snap_id, "message": f"commit {i + 1}",
            "committed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "parent_commit_id": prev, "parent2_commit_id": None,
            "author": "Test", "metadata": {},
        }))
        commit_ids.append(cid)
        prev = cid
    (repo / ".muse" / "refs" / "heads" / "main").write_text(commit_ids[-1])
    return commit_ids


# ---------------------------------------------------------------------------
# muse reflog
# ---------------------------------------------------------------------------


class TestReflogCli:
    def test_reflog_no_entries_exits_ok(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _make_repo(tmp_path, monkeypatch)
        result = runner.invoke(cli, ["reflog"], catch_exceptions=False)
        assert result.exit_code == 0
        assert "No reflog entries" in result.output

    def test_reflog_shows_entries(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from muse.core.reflog import append_reflog

        _make_repo(tmp_path, monkeypatch)
        append_reflog(tmp_path, "main", old_id=None, new_id="c" * 64, author="A", operation="commit: test")
        result = runner.invoke(cli, ["reflog"], catch_exceptions=False)
        assert result.exit_code == 0
        assert "commit: test" in result.output

    def test_reflog_all_flag(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from muse.core.reflog import append_reflog

        _make_repo(tmp_path, monkeypatch)
        append_reflog(tmp_path, "main", old_id=None, new_id="c" * 64, author="A", operation="commit: x")
        result = runner.invoke(cli, ["reflog", "--all"], catch_exceptions=False)
        assert result.exit_code == 0
        assert "refs/heads/main" in result.output

    def test_reflog_branch_filter(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from muse.core.reflog import append_reflog

        _make_repo(tmp_path, monkeypatch)
        append_reflog(tmp_path, "dev", old_id=None, new_id="d" * 64, author="A", operation="commit: dev")
        result = runner.invoke(cli, ["reflog", "--branch", "dev"], catch_exceptions=False)
        assert result.exit_code == 0
        assert "commit: dev" in result.output

    def test_reflog_limit(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from muse.core.reflog import append_reflog

        _make_repo(tmp_path, monkeypatch)
        for i in range(10):
            append_reflog(tmp_path, "main", old_id=None, new_id="c" * 64, author="A", operation=f"commit: {i}")
        result = runner.invoke(cli, ["reflog", "--limit", "3"], catch_exceptions=False)
        assert result.exit_code == 0
        # At most 3 @{N} entries.
        lines = [l for l in result.output.splitlines() if l.startswith("@{")]
        assert len(lines) <= 3

    def test_reflog_shows_at_index_format(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from muse.core.reflog import append_reflog

        _make_repo(tmp_path, monkeypatch)
        append_reflog(tmp_path, "main", old_id=None, new_id="c" * 64, author="A", operation="commit: x")
        result = runner.invoke(cli, ["reflog"], catch_exceptions=False)
        # Format is @{N:...} so just check the @ prefix.
        assert "@{" in result.output


# ---------------------------------------------------------------------------
# muse gc
# ---------------------------------------------------------------------------


class TestGcCli:
    def test_gc_empty_reports_zero(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _make_repo(tmp_path, monkeypatch)
        result = runner.invoke(cli, ["gc"], catch_exceptions=False)
        assert result.exit_code == 0
        assert "0 object" in result.output

    def test_gc_dry_run_does_not_delete(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _make_repo(tmp_path, monkeypatch)
        # Write an orphan object with a valid 2+62 path.
        orphan_content = b"totally orphaned"
        sha = _sha256(orphan_content)
        obj_dir = tmp_path / ".muse" / "objects" / sha[:2]
        obj_dir.mkdir(parents=True, exist_ok=True)
        obj_file = obj_dir / sha[2:]
        obj_file.write_bytes(orphan_content)

        result = runner.invoke(cli, ["gc", "--dry-run"], catch_exceptions=False)
        assert result.exit_code == 0
        assert "dry-run" in result.output
        assert obj_file.exists()

    def test_gc_removes_orphan(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _make_repo(tmp_path, monkeypatch)
        orphan_content = b"not referenced anywhere at all"
        sha = _sha256(orphan_content)
        obj_dir = tmp_path / ".muse" / "objects" / sha[:2]
        obj_dir.mkdir(parents=True, exist_ok=True)
        obj_file = obj_dir / sha[2:]
        obj_file.write_bytes(orphan_content)

        result = runner.invoke(cli, ["gc"], catch_exceptions=False)
        assert result.exit_code == 0
        assert not obj_file.exists()

    def test_gc_verbose_lists_objects(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _make_repo(tmp_path, monkeypatch)
        orphan_content = b"verbose orphan"
        sha = _sha256(orphan_content)
        obj_dir = tmp_path / ".muse" / "objects" / sha[:2]
        obj_dir.mkdir(parents=True, exist_ok=True)
        (obj_dir / sha[2:]).write_bytes(orphan_content)

        result = runner.invoke(cli, ["gc", "--verbose"], catch_exceptions=False)
        assert result.exit_code == 0
        assert sha in result.output

    def test_gc_preserves_reachable(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The hello.txt object in the initial commit must survive GC.
        _make_repo(tmp_path, monkeypatch)
        content = b"hello world\n"
        sha = _sha256(content)
        obj_path = tmp_path / ".muse" / "objects" / sha[:2] / sha[2:]
        result = runner.invoke(cli, ["gc"], catch_exceptions=False)
        assert result.exit_code == 0
        assert obj_path.exists()


# ---------------------------------------------------------------------------
# muse archive
# ---------------------------------------------------------------------------


class TestArchiveCli:
    def test_archive_creates_targz(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _make_repo(tmp_path, monkeypatch)
        out = str(tmp_path / "snap.tar.gz")
        result = runner.invoke(cli, ["archive", "--output", out], catch_exceptions=False)
        assert result.exit_code == 0
        assert pathlib.Path(out).exists()

    def test_archive_creates_zip(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _make_repo(tmp_path, monkeypatch)
        out = str(tmp_path / "snap.zip")
        result = runner.invoke(cli, ["archive", "--format", "zip", "--output", out], catch_exceptions=False)
        assert result.exit_code == 0
        assert pathlib.Path(out).exists()

    def test_archive_invalid_format(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _make_repo(tmp_path, monkeypatch)
        result = runner.invoke(cli, ["archive", "--format", "rar"])
        assert result.exit_code != 0
        assert "Unknown format" in result.output

    def test_archive_with_prefix(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _make_repo(tmp_path, monkeypatch)
        out = str(tmp_path / "out.tar.gz")
        result = runner.invoke(
            cli, ["archive", "--output", out, "--prefix", "myproject/"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        import tarfile
        with tarfile.open(out, "r:gz") as tar:
            names = tar.getnames()
        assert any("myproject/" in n for n in names)

    def test_archive_output_shows_commit_info(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _make_repo(tmp_path, monkeypatch)
        out = str(tmp_path / "out.tar.gz")
        result = runner.invoke(cli, ["archive", "--output", out], catch_exceptions=False)
        assert result.exit_code == 0
        assert "initial commit" in result.output

    def test_archive_default_name_is_sha_based(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _make_repo(tmp_path, monkeypatch)
        result = runner.invoke(cli, ["archive"], catch_exceptions=False)
        assert result.exit_code == 0
        # Should create a .tar.gz file.
        tar_files = list(tmp_path.glob("*.tar.gz"))
        assert len(tar_files) == 1


# ---------------------------------------------------------------------------
# muse bisect
# ---------------------------------------------------------------------------


class TestBisectCli:
    def _setup(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, n: int = 4
    ) -> list[str]:
        _make_repo(tmp_path, monkeypatch)
        initial = "c" * 64
        return _add_commits(tmp_path, n, initial)

    def test_bisect_start_requires_good(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        commits = self._setup(tmp_path, monkeypatch)
        result = runner.invoke(cli, ["bisect", "start", "--bad", commits[-1]])
        assert result.exit_code != 0

    def test_bisect_start_success(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        commits = self._setup(tmp_path, monkeypatch, n=4)
        result = runner.invoke(
            cli, ["bisect", "start", "--bad", commits[-1], "--good", commits[0]],
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        assert "Bisect session started" in result.output

    def test_bisect_reset(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        commits = self._setup(tmp_path, monkeypatch, n=4)
        runner.invoke(cli, ["bisect", "start", "--bad", commits[-1], "--good", commits[0]])
        result = runner.invoke(cli, ["bisect", "reset"], catch_exceptions=False)
        assert result.exit_code == 0
        assert "reset" in result.output

    def test_bisect_log_shows_entries(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        commits = self._setup(tmp_path, monkeypatch, n=4)
        runner.invoke(
            cli, ["bisect", "start", "--bad", commits[-1], "--good", commits[0]],
            catch_exceptions=False,
        )
        result = runner.invoke(cli, ["bisect", "log"], catch_exceptions=False)
        assert result.exit_code == 0
        assert "bad" in result.output or "good" in result.output

    def test_bisect_bad_without_session(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _make_repo(tmp_path, monkeypatch)
        result = runner.invoke(cli, ["bisect", "bad"])
        assert result.exit_code != 0

    def test_bisect_good_without_session(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _make_repo(tmp_path, monkeypatch)
        result = runner.invoke(cli, ["bisect", "good"])
        assert result.exit_code != 0

    def test_bisect_shows_next_to_test(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        commits = self._setup(tmp_path, monkeypatch, n=8)
        result = runner.invoke(
            cli, ["bisect", "start", "--bad", commits[-1], "--good", commits[0]],
            catch_exceptions=False,
        )
        assert "Next to test:" in result.output

    def test_bisect_skip(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        commits = self._setup(tmp_path, monkeypatch, n=4)
        runner.invoke(
            cli, ["bisect", "start", "--bad", commits[-1], "--good", commits[0]],
        )
        from muse.core.bisect import _load_state
        state = _load_state(tmp_path)
        assert state is not None
        remaining = state.get("remaining", [])
        if remaining:
            mid = remaining[len(remaining) // 2]
            result = runner.invoke(cli, ["bisect", "skip", mid], catch_exceptions=False)
            assert result.exit_code == 0


# ---------------------------------------------------------------------------
# muse blame (core VCS)
# ---------------------------------------------------------------------------


class TestBlameCli:
    def test_blame_missing_file(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _make_repo(tmp_path, monkeypatch)
        result = runner.invoke(cli, ["blame", "nonexistent.txt"])
        assert result.exit_code != 0
        assert "not found" in result.output

    def test_blame_existing_file(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _make_repo(tmp_path, monkeypatch)
        result = runner.invoke(cli, ["blame", "hello.txt"], catch_exceptions=False)
        assert result.exit_code == 0
        assert "hello world" in result.output

    def test_blame_shows_author(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _make_repo(tmp_path, monkeypatch)
        result = runner.invoke(cli, ["blame", "hello.txt"], catch_exceptions=False)
        assert result.exit_code == 0
        assert "Test User" in result.output

    def test_blame_porcelain_json_output(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _make_repo(tmp_path, monkeypatch)
        result = runner.invoke(cli, ["blame", "--porcelain", "hello.txt"], catch_exceptions=False)
        assert result.exit_code == 0
        lines = [l for l in result.output.strip().split("\n") if l.strip()]
        assert len(lines) >= 1
        parsed = json.loads(lines[0])
        assert "lineno" in parsed
        assert "commit_id" in parsed
        assert "content" in parsed

    def test_blame_lineno_starts_at_1(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _make_repo(tmp_path, monkeypatch)
        result = runner.invoke(cli, ["blame", "--porcelain", "hello.txt"], catch_exceptions=False)
        assert result.exit_code == 0
        parsed = json.loads(result.output.strip().split("\n")[0])
        assert parsed["lineno"] == 1


# ---------------------------------------------------------------------------
# muse worktree
# ---------------------------------------------------------------------------


class TestWorktreeCli:
    def _make_named_repo(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> pathlib.Path:
        """Create a repo in myproject/ subdirectory."""
        repo_dir = tmp_path / "myproject"
        repo_dir.mkdir()
        muse = repo_dir / ".muse"
        for d in ("objects", "commits", "snapshots", "refs/heads"):
            (muse / d).mkdir(parents=True, exist_ok=True)
        (muse / "repo.json").write_text(json.dumps({"repo_id": "test"}))
        (muse / "HEAD").write_text("ref: refs/heads/main\n")
        (muse / "refs" / "heads" / "main").write_text("0" * 64)
        monkeypatch.chdir(repo_dir)
        return repo_dir

    def test_worktree_list_shows_main(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._make_named_repo(tmp_path, monkeypatch)
        result = runner.invoke(cli, ["worktree", "list"], catch_exceptions=False)
        assert result.exit_code == 0
        assert "(main)" in result.output

    def test_worktree_add_and_list(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = self._make_named_repo(tmp_path, monkeypatch)
        (repo / ".muse" / "refs" / "heads" / "dev").write_text("0" * 64)
        result = runner.invoke(cli, ["worktree", "add", "mydev", "dev"], catch_exceptions=False)
        assert result.exit_code == 0
        result2 = runner.invoke(cli, ["worktree", "list"], catch_exceptions=False)
        assert "mydev" in result2.output

    def test_worktree_remove(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = self._make_named_repo(tmp_path, monkeypatch)
        (repo / ".muse" / "refs" / "heads" / "dev").write_text("0" * 64)
        runner.invoke(cli, ["worktree", "add", "mydev", "dev"])
        result = runner.invoke(cli, ["worktree", "remove", "mydev"], catch_exceptions=False)
        assert result.exit_code == 0
        assert "mydev" in result.output

    def test_worktree_prune_empty(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._make_named_repo(tmp_path, monkeypatch)
        result = runner.invoke(cli, ["worktree", "prune"], catch_exceptions=False)
        assert result.exit_code == 0
        assert "Nothing to prune" in result.output

    def test_worktree_remove_nonexistent(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._make_named_repo(tmp_path, monkeypatch)
        result = runner.invoke(cli, ["worktree", "remove", "nonexistent"])
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# muse workspace
# ---------------------------------------------------------------------------


class TestWorkspaceCli:
    def test_workspace_add_and_list(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _make_repo(tmp_path, monkeypatch)
        result = runner.invoke(
            cli, ["workspace", "add", "core", "https://musehub.ai/acme/core"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        assert "Added workspace member" in result.output

        result2 = runner.invoke(cli, ["workspace", "list"], catch_exceptions=False)
        assert result2.exit_code == 0
        assert "core" in result2.output

    def test_workspace_remove(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _make_repo(tmp_path, monkeypatch)
        runner.invoke(cli, ["workspace", "add", "core", "https://musehub.ai/acme/core"])
        result = runner.invoke(cli, ["workspace", "remove", "core"], catch_exceptions=False)
        assert result.exit_code == 0
        assert "Removed" in result.output

    def test_workspace_status_empty(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _make_repo(tmp_path, monkeypatch)
        result = runner.invoke(cli, ["workspace", "status"], catch_exceptions=False)
        assert result.exit_code == 0
        assert "No workspace members" in result.output

    def test_workspace_list_empty(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _make_repo(tmp_path, monkeypatch)
        result = runner.invoke(cli, ["workspace", "list"], catch_exceptions=False)
        assert result.exit_code == 0
        assert "No workspace members" in result.output

    def test_workspace_add_with_branch(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _make_repo(tmp_path, monkeypatch)
        runner.invoke(
            cli, ["workspace", "add", "data", "https://example.com/data", "--branch", "v2"],
        )
        result = runner.invoke(cli, ["workspace", "list"], catch_exceptions=False)
        assert "v2" in result.output

    def test_workspace_remove_nonexistent(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _make_repo(tmp_path, monkeypatch)
        runner.invoke(cli, ["workspace", "add", "core", "https://example.com/core"])
        result = runner.invoke(cli, ["workspace", "remove", "nonexistent"])
        assert result.exit_code != 0

    def test_workspace_sync_empty(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _make_repo(tmp_path, monkeypatch)
        result = runner.invoke(cli, ["workspace", "sync"], catch_exceptions=False)
        assert result.exit_code == 0
        assert "No members" in result.output

    def test_workspace_add_duplicate(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _make_repo(tmp_path, monkeypatch)
        runner.invoke(cli, ["workspace", "add", "core", "https://example.com/core"])
        result = runner.invoke(cli, ["workspace", "add", "core", "https://example.com/other"])
        assert result.exit_code != 0

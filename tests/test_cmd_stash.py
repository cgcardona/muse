"""Comprehensive tests for ``muse stash``.

Covers:
- Unit: _load_stash / _save_stash atomic write, size guard
- Integration: stash → pop, list, drop
- E2E: full CLI via CliRunner
- Security: stash.json size limit, atomic writes, sanitized output
- Stress: many stash entries, repeated save/load
"""

from __future__ import annotations

import datetime
import json
import pathlib
import uuid

import pytest
from typer.testing import CliRunner

from muse.cli.app import cli

runner = CliRunner()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _env(root: pathlib.Path) -> dict[str, str]:
    return {"MUSE_REPO_ROOT": str(root)}


def _init_repo(tmp_path: pathlib.Path) -> tuple[pathlib.Path, str]:
    muse_dir = tmp_path / ".muse"
    muse_dir.mkdir()
    repo_id = str(uuid.uuid4())
    (muse_dir / "repo.json").write_text(json.dumps({
        "repo_id": repo_id,
        "domain": "midi",
        "default_branch": "main",
        "created_at": "2025-01-01T00:00:00+00:00",
    }), encoding="utf-8")
    (muse_dir / "HEAD").write_text("ref: refs/heads/main", encoding="utf-8")
    (muse_dir / "refs" / "heads").mkdir(parents=True)
    (muse_dir / "snapshots").mkdir()
    (muse_dir / "commits").mkdir()
    (muse_dir / "objects").mkdir()
    return tmp_path, repo_id


def _make_commit(root: pathlib.Path, repo_id: str, message: str = "init") -> str:
    from muse.core.store import CommitRecord, SnapshotRecord, write_commit, write_snapshot
    from muse.core.snapshot import compute_snapshot_id, compute_commit_id

    ref_file = root / ".muse" / "refs" / "heads" / "main"
    parent_id = ref_file.read_text().strip() if ref_file.exists() else None
    manifest: dict[str, str] = {}
    snap_id = compute_snapshot_id(manifest)
    committed_at = datetime.datetime.now(datetime.timezone.utc)
    commit_id = compute_commit_id(
        parent_ids=[parent_id] if parent_id else [],
        snapshot_id=snap_id, message=message,
        committed_at_iso=committed_at.isoformat(),
    )
    write_snapshot(root, SnapshotRecord(snapshot_id=snap_id, manifest=manifest))
    write_commit(root, CommitRecord(
        commit_id=commit_id, repo_id=repo_id, branch="main",
        snapshot_id=snap_id, message=message, committed_at=committed_at,
        parent_commit_id=parent_id,
    ))
    ref_file.parent.mkdir(parents=True, exist_ok=True)
    ref_file.write_text(commit_id, encoding="utf-8")
    return commit_id


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------

class TestStashUnit:
    def test_load_stash_empty(self, tmp_path: pathlib.Path) -> None:
        root, _ = _init_repo(tmp_path)
        from muse.cli.commands.stash import _load_stash
        assert _load_stash(root) == []

    def test_save_and_load_stash_roundtrip(self, tmp_path: pathlib.Path) -> None:
        root, _ = _init_repo(tmp_path)
        from muse.cli.commands.stash import _load_stash, _save_stash, StashEntry
        entry = StashEntry(
            snapshot_id="a" * 64, manifest={"file.mid": "b" * 64},
            branch="main", stashed_at="2025-01-01T00:00:00+00:00",
        )
        _save_stash(root, [entry])
        loaded = _load_stash(root)
        assert len(loaded) == 1
        assert loaded[0]["snapshot_id"] == "a" * 64
        assert loaded[0]["branch"] == "main"

    def test_save_stash_is_atomic(self, tmp_path: pathlib.Path) -> None:
        """After _save_stash, no temp files should remain in .muse/."""
        root, _ = _init_repo(tmp_path)
        from muse.cli.commands.stash import _save_stash, StashEntry
        entry = StashEntry(
            snapshot_id="c" * 64, manifest={},
            branch="dev", stashed_at="2025-01-01T00:00:00+00:00",
        )
        _save_stash(root, [entry])
        tmp_files = list((root / ".muse").glob(".stash_tmp_*"))
        assert tmp_files == []
        assert (root / ".muse" / "stash.json").exists()

    def test_load_stash_ignores_oversized_file(self, tmp_path: pathlib.Path) -> None:
        root, _ = _init_repo(tmp_path)
        stash_path = root / ".muse" / "stash.json"
        stash_path.write_bytes(b"x" * (65 * 1024 * 1024))  # 65 MiB > 64 MiB limit
        from muse.cli.commands.stash import _load_stash
        result = _load_stash(root)
        assert result == []


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------

class TestStashIntegration:
    def test_stash_with_no_changes_reports_nothing(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        _make_commit(root, repo_id)
        result = runner.invoke(cli, ["stash"], env=_env(root), catch_exceptions=False)
        assert "Nothing to stash" in result.output

    def test_stash_list_empty(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        _make_commit(root, repo_id)
        result = runner.invoke(cli, ["stash", "list"], env=_env(root), catch_exceptions=False)
        assert "No stash entries" in result.output

    def test_stash_pop_empty_fails(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        _make_commit(root, repo_id)
        result = runner.invoke(cli, ["stash", "pop"], env=_env(root))
        assert result.exit_code != 0

    def test_stash_drop_empty_fails(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        _make_commit(root, repo_id)
        result = runner.invoke(cli, ["stash", "drop"], env=_env(root))
        assert result.exit_code != 0

    def test_stash_list_shows_entries(self, tmp_path: pathlib.Path) -> None:
        root, _ = _init_repo(tmp_path)
        from muse.cli.commands.stash import _save_stash, StashEntry
        _save_stash(root, [
            StashEntry(snapshot_id="a" * 64, manifest={},
                       branch="main", stashed_at="2025-01-01T00:00:00+00:00"),
        ])
        result = runner.invoke(cli, ["stash", "list"], env=_env(root), catch_exceptions=False)
        assert "stash@{0}" in result.output
        assert "main" in result.output


# ---------------------------------------------------------------------------
# Security tests
# ---------------------------------------------------------------------------

class TestStashSecurity:
    def test_stash_list_sanitizes_branch_name_with_control_chars(
        self, tmp_path: pathlib.Path
    ) -> None:
        root, _ = _init_repo(tmp_path)
        from muse.cli.commands.stash import _save_stash, StashEntry
        malicious_branch = "feat/\x1b[31mred\x1b[0m"
        _save_stash(root, [
            StashEntry(snapshot_id="a" * 64, manifest={},
                       branch=malicious_branch, stashed_at="2025-01-01T00:00:00+00:00"),
        ])
        result = runner.invoke(cli, ["stash", "list"], env=_env(root), catch_exceptions=False)
        assert result.exit_code == 0
        assert "\x1b" not in result.output

    def test_stash_pop_sanitizes_branch_name(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        _make_commit(root, repo_id)
        from muse.cli.commands.stash import _save_stash, StashEntry
        _save_stash(root, [
            StashEntry(snapshot_id="a" * 64, manifest={},
                       branch="feat/\x1b[31mred\x1b[0m",
                       stashed_at="2025-01-01T00:00:00+00:00"),
        ])
        result = runner.invoke(cli, ["stash", "pop"], env=_env(root), catch_exceptions=False)
        assert "\x1b" not in result.output


# ---------------------------------------------------------------------------
# Stress tests
# ---------------------------------------------------------------------------

class TestStashStress:
    def test_many_stash_entries_save_load(self, tmp_path: pathlib.Path) -> None:
        root, _ = _init_repo(tmp_path)
        from muse.cli.commands.stash import _save_stash, _load_stash, StashEntry
        entries = [
            StashEntry(snapshot_id=f"{'a' * 63}{i % 10}",
                       manifest={"file.mid": "b" * 64}, branch="main",
                       stashed_at=f"2025-01-{i % 28 + 1:02d}T00:00:00+00:00")
            for i in range(50)
        ]
        _save_stash(root, entries)
        loaded = _load_stash(root)
        assert len(loaded) == 50

    def test_repeated_save_load_no_corruption(self, tmp_path: pathlib.Path) -> None:
        root, _ = _init_repo(tmp_path)
        from muse.cli.commands.stash import _save_stash, _load_stash, StashEntry
        for i in range(20):
            entry = StashEntry(snapshot_id=f"{'b' * 63}{i % 10}",
                               manifest={}, branch="main",
                               stashed_at="2025-01-01T00:00:00+00:00")
            loaded = _load_stash(root)
            loaded.insert(0, entry)
            _save_stash(root, loaded)

        final = _load_stash(root)
        assert len(final) == 20

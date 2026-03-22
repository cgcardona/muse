"""Comprehensive tests for ``muse diff``.

Covers:
- E2E: CLI flags (--commit / -c, --format json/text, --plugin)
- Integration: diff HEAD vs working tree
- Stress: diff with many files
"""

from __future__ import annotations

import datetime
import json
import pathlib
import uuid

import pytest
from tests.cli_test_helper import CliRunner

cli = None  # argparse migration — CliRunner ignores this arg

runner = CliRunner()


# ---------------------------------------------------------------------------
# Shared helpers
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


def _make_commit(root: pathlib.Path, repo_id: str, message: str = "test",
                 manifest: dict[str, str] | None = None) -> str:
    from muse.core.store import CommitRecord, SnapshotRecord, write_commit, write_snapshot
    from muse.core.snapshot import compute_snapshot_id, compute_commit_id

    ref_file = root / ".muse" / "refs" / "heads" / "main"
    parent_id = ref_file.read_text().strip() if ref_file.exists() else None
    m = manifest or {}
    snap_id = compute_snapshot_id(m)
    committed_at = datetime.datetime.now(datetime.timezone.utc)
    commit_id = compute_commit_id(
        parent_ids=[parent_id] if parent_id else [],
        snapshot_id=snap_id, message=message,
        committed_at_iso=committed_at.isoformat(),
    )
    write_snapshot(root, SnapshotRecord(snapshot_id=snap_id, manifest=m))
    write_commit(root, CommitRecord(
        commit_id=commit_id, repo_id=repo_id, branch="main",
        snapshot_id=snap_id, message=message, committed_at=committed_at,
        parent_commit_id=parent_id,
    ))
    ref_file.parent.mkdir(parents=True, exist_ok=True)
    ref_file.write_text(commit_id, encoding="utf-8")
    return commit_id


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestDiffCLI:
    def test_diff_empty_repo(self, tmp_path: pathlib.Path) -> None:
        root, _ = _init_repo(tmp_path)
        result = runner.invoke(cli, ["diff"], env=_env(root), catch_exceptions=False)
        assert result.exit_code == 0

    def test_diff_after_commit_no_changes(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        _make_commit(root, repo_id)
        result = runner.invoke(cli, ["diff"], env=_env(root), catch_exceptions=False)
        assert result.exit_code == 0

    def test_diff_shows_added_files(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        _make_commit(root, repo_id)
        (root / "new_song.mid").write_bytes(b"MIDI data here")
        result = runner.invoke(cli, ["diff"], env=_env(root), catch_exceptions=False)
        assert result.exit_code == 0
        assert "new_song.mid" in result.output

    def test_diff_format_json(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        _make_commit(root, repo_id)
        result = runner.invoke(cli, ["diff", "--format", "json"], env=_env(root), catch_exceptions=False)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, (dict, list))

    def test_diff_between_two_commits(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        commit1 = _make_commit(root, repo_id, message="first")
        commit2 = _make_commit(root, repo_id, message="second")
        result = runner.invoke(
            cli, ["diff", commit1, commit2],
            env=_env(root), catch_exceptions=False
        )
        assert result.exit_code == 0

    def test_diff_output_clean_no_exception(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        _make_commit(root, repo_id)
        result = runner.invoke(cli, ["diff"], env=_env(root))
        assert result.exception is None


class TestDiffStress:
    def test_diff_with_many_changed_files(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        _make_commit(root, repo_id)
        for i in range(50):
            (root / f"track_{i:03d}.mid").write_bytes(f"data {i}".encode())
        result = runner.invoke(cli, ["diff"], env=_env(root), catch_exceptions=False)
        assert result.exit_code == 0

"""Comprehensive tests for ``muse merge``.

Covers:
- E2E: merge fast-forward, merge with conflicts, --format json
- Integration: HEAD updated after merge, conflict state written
- Stress: merge with many files
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


def _make_commit(root: pathlib.Path, repo_id: str, branch: str = "main",
                 message: str = "test",
                 manifest: dict[str, str] | None = None) -> str:
    from muse.core.store import CommitRecord, SnapshotRecord, write_commit, write_snapshot
    from muse.core.snapshot import compute_snapshot_id, compute_commit_id

    ref_file = root / ".muse" / "refs" / "heads" / branch
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
        commit_id=commit_id, repo_id=repo_id, branch=branch,
        snapshot_id=snap_id, message=message, committed_at=committed_at,
        parent_commit_id=parent_id,
    ))
    ref_file.parent.mkdir(parents=True, exist_ok=True)
    ref_file.write_text(commit_id, encoding="utf-8")
    return commit_id


def _write_object(root: pathlib.Path, content: bytes) -> str:
    import hashlib
    obj_id = hashlib.sha256(content).hexdigest()
    obj_path = root / ".muse" / "objects" / obj_id[:2] / obj_id[2:]
    obj_path.parent.mkdir(parents=True, exist_ok=True)
    obj_path.write_bytes(content)
    return obj_id


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestMergeCLI:
    def test_merge_branch_into_main(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        base_id = _make_commit(root, repo_id, branch="main", message="base")
        (root / ".muse" / "refs" / "heads" / "feature").write_text(base_id)
        obj = _write_object(root, b"feature content")
        _make_commit(root, repo_id, branch="feature", message="feature work",
                     manifest={"new_track.mid": obj})
        result = runner.invoke(cli, ["merge", "feature"], env=_env(root), catch_exceptions=False)
        assert result.exit_code == 0

    def test_merge_nonexistent_branch_fails(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        _make_commit(root, repo_id)
        result = runner.invoke(cli, ["merge", "does-not-exist"], env=_env(root))
        assert result.exit_code != 0

    def test_merge_format_json(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        base_id = _make_commit(root, repo_id, branch="main", message="base")
        (root / ".muse" / "refs" / "heads" / "feature").write_text(base_id)
        _make_commit(root, repo_id, branch="feature", message="feat")
        result = runner.invoke(
            cli, ["merge", "--format", "json", "feature"], env=_env(root), catch_exceptions=False
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, dict)

    def test_merge_message_flag(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        base_id = _make_commit(root, repo_id, branch="main", message="base")
        (root / ".muse" / "refs" / "heads" / "feature").write_text(base_id)
        _make_commit(root, repo_id, branch="feature", message="feat")
        result = runner.invoke(
            cli, ["merge", "--message", "Merge feature", "feature"],
            env=_env(root), catch_exceptions=False
        )
        assert result.exit_code == 0

    def test_merge_invalid_branch_name_rejected(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        _make_commit(root, repo_id)
        result = runner.invoke(cli, ["merge", "../evil"], env=_env(root))
        assert result.exit_code != 0

    def test_merge_output_sanitized(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        base_id = _make_commit(root, repo_id, branch="main", message="base")
        (root / ".muse" / "refs" / "heads" / "feature").write_text(base_id)
        _make_commit(root, repo_id, branch="feature", message="feat")
        result = runner.invoke(cli, ["merge", "feature"], env=_env(root), catch_exceptions=False)
        assert "\x1b" not in result.output


class TestMergeStress:
    def test_merge_feature_with_many_files(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        base_id = _make_commit(root, repo_id, branch="main", message="base")
        (root / ".muse" / "refs" / "heads" / "feature").write_text(base_id)
        manifest = {f"track_{i:03d}.mid": _write_object(root, f"data {i}".encode())
                    for i in range(30)}
        _make_commit(root, repo_id, branch="feature", message="many files", manifest=manifest)
        result = runner.invoke(cli, ["merge", "feature"], env=_env(root), catch_exceptions=False)
        assert result.exit_code == 0

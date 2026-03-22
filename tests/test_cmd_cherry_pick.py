"""Comprehensive tests for ``muse cherry-pick``.

Covers:
- E2E: cherry-pick a specific commit onto current branch
- Integration: commit is replayed, creates new commit
- Security: sanitized output for conflict paths
- Stress: cherry-pick many commits
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

class TestCherryPickCLI:
    def test_cherry_pick_commit_from_another_branch(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        base = _make_commit(root, repo_id, branch="main", message="base")
        (root / ".muse" / "refs" / "heads" / "feature").write_text(base)
        obj = _write_object(root, b"feature content")
        feature_commit = _make_commit(root, repo_id, branch="feature",
                                      message="feature work",
                                      manifest={"new.mid": obj})
        result = runner.invoke(
            cli, ["cherry-pick", feature_commit], env=_env(root), catch_exceptions=False
        )
        assert result.exit_code == 0

    def test_cherry_pick_invalid_commit_fails(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        _make_commit(root, repo_id)
        result = runner.invoke(cli, ["cherry-pick", "deadbeef" * 8], env=_env(root))
        assert result.exit_code != 0

    def test_cherry_pick_creates_new_commit(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        base = _make_commit(root, repo_id, branch="main", message="base")
        (root / ".muse" / "refs" / "heads" / "feature").write_text(base)
        obj = _write_object(root, b"cherry content")
        feature_commit = _make_commit(root, repo_id, branch="feature",
                                      message="cherry", manifest={"c.mid": obj})
        original_head = (root / ".muse" / "refs" / "heads" / "main").read_text().strip()
        runner.invoke(cli, ["cherry-pick", feature_commit], env=_env(root), catch_exceptions=False)
        new_head = (root / ".muse" / "refs" / "heads" / "main").read_text().strip()
        assert new_head != original_head

    def test_cherry_pick_format_json(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        base = _make_commit(root, repo_id, branch="main", message="base")
        (root / ".muse" / "refs" / "heads" / "feature").write_text(base)
        obj = _write_object(root, b"json pick")
        feature_commit = _make_commit(root, repo_id, branch="feature",
                                      message="json", manifest={"j.mid": obj})
        result = runner.invoke(
            cli, ["cherry-pick", "--format", "json", feature_commit],
            env=_env(root), catch_exceptions=False
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, dict)

    def test_cherry_pick_output_sanitized(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        base = _make_commit(root, repo_id, branch="main", message="base")
        (root / ".muse" / "refs" / "heads" / "feature").write_text(base)
        obj = _write_object(root, b"safe content")
        feature_commit = _make_commit(root, repo_id, branch="feature",
                                      message="safe", manifest={"s.mid": obj})
        result = runner.invoke(cli, ["cherry-pick", feature_commit], env=_env(root), catch_exceptions=False)
        assert "\x1b" not in result.output


class TestCherryPickStress:
    def test_cherry_pick_sequence(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        base = _make_commit(root, repo_id, branch="main", message="base")
        (root / ".muse" / "refs" / "heads" / "feature").write_text(base)
        commits = []
        for i in range(5):
            obj = _write_object(root, f"content {i}".encode())
            c = _make_commit(root, repo_id, branch="feature",
                             message=f"commit {i}", manifest={f"f{i}.mid": obj})
            commits.append(c)
        for commit_id in commits:
            result = runner.invoke(
                cli, ["cherry-pick", commit_id], env=_env(root), catch_exceptions=False
            )
            assert result.exit_code == 0

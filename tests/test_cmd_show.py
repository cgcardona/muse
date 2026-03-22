"""Comprehensive tests for ``muse show``.

Covers:
- Unit: _format_op for each DomainOp type
- Integration: show a commit in text and JSON
- E2E: full CLI round-trip
- Security: sanitize_display applied to message/author/metadata
- Stress: commits with large metadata sets
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


def _make_commit(
    root: pathlib.Path, repo_id: str, message: str = "initial commit",
    author: str = "Alice", metadata: dict[str, str] | None = None,
) -> str:
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
        parent_commit_id=parent_id, author=author, metadata=metadata or {},
    ))
    ref_file.parent.mkdir(parents=True, exist_ok=True)
    ref_file.write_text(commit_id, encoding="utf-8")
    return commit_id


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------

class TestShowUnit:
    def test_format_op_insert(self) -> None:
        from muse.cli.commands.show import _format_op
        from muse.domain import InsertOp
        op: InsertOp = {"op": "insert", "address": "track/1",
                        "position": None, "content_id": "abc", "content_summary": "new"}
        lines = _format_op(op)
        assert any("A" in l and "track/1" in l for l in lines)

    def test_format_op_delete(self) -> None:
        from muse.cli.commands.show import _format_op
        from muse.domain import DeleteOp
        op: DeleteOp = {"op": "delete", "address": "track/2",
                        "position": None, "content_id": "abc", "content_summary": "old"}
        lines = _format_op(op)
        assert any("D" in l and "track/2" in l for l in lines)

    def test_format_op_replace(self) -> None:
        from muse.cli.commands.show import _format_op
        from muse.domain import ReplaceOp
        op: ReplaceOp = {"op": "replace", "address": "track/3",
                         "old_content_id": "abc", "new_content_id": "def",
                         "old_summary": "old", "new_summary": "new"}
        lines = _format_op(op)
        assert any("M" in l for l in lines)


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------

class TestShowIntegration:
    def test_show_head_commit(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        commit_id = _make_commit(root, repo_id, message="Hello show")
        result = runner.invoke(cli, ["show"], env=_env(root), catch_exceptions=False)
        assert result.exit_code == 0
        assert commit_id in result.output
        assert "Hello show" in result.output

    def test_show_json_output(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        commit_id = _make_commit(root, repo_id, message="json test")
        result = runner.invoke(cli, ["show", "--json"], env=_env(root), catch_exceptions=False)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["commit_id"] == commit_id
        assert data["message"] == "json test"

    def test_show_specific_commit_by_sha(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        commit_id = _make_commit(root, repo_id, message="specific")
        result = runner.invoke(cli, ["show", commit_id[:12]], env=_env(root), catch_exceptions=False)
        assert result.exit_code == 0
        assert "specific" in result.output

    def test_show_nonexistent_commit_fails(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        _make_commit(root, repo_id)
        result = runner.invoke(cli, ["show", "deadbeef"], env=_env(root))
        assert result.exit_code != 0
        assert "not found" in result.output

    def test_show_no_stat(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        _make_commit(root, repo_id, message="no stat")
        result = runner.invoke(cli, ["show", "--no-stat"], env=_env(root), catch_exceptions=False)
        assert result.exit_code == 0
        assert "no stat" in result.output

    def test_show_author_in_output(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        _make_commit(root, repo_id, author="Bob Smith")
        result = runner.invoke(cli, ["show"], env=_env(root), catch_exceptions=False)
        assert "Bob Smith" in result.output

    def test_show_metadata_in_output(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        _make_commit(root, repo_id, metadata={"section": "chorus", "key": "Am"})
        result = runner.invoke(cli, ["show"], env=_env(root), catch_exceptions=False)
        assert "section" in result.output
        assert "chorus" in result.output


# ---------------------------------------------------------------------------
# Security tests
# ---------------------------------------------------------------------------

class TestShowSecurity:
    def test_commit_message_with_ansi_escaped(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        malicious = "Hello \x1b[31mRED\x1b[0m world"
        _make_commit(root, repo_id, message=malicious)
        result = runner.invoke(cli, ["show"], env=_env(root), catch_exceptions=False)
        assert result.exit_code == 0
        assert "\x1b" not in result.output

    def test_author_with_control_chars_escaped(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        _make_commit(root, repo_id, author="Alice\x1b[0m\x00Bob")
        result = runner.invoke(cli, ["show"], env=_env(root), catch_exceptions=False)
        assert result.exit_code == 0
        assert "\x1b" not in result.output
        assert "\x00" not in result.output

    def test_metadata_with_control_chars_escaped(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        _make_commit(root, repo_id, metadata={"key\x1b[31m": "val\x00ue"})
        result = runner.invoke(cli, ["show"], env=_env(root), catch_exceptions=False)
        assert result.exit_code == 0
        assert "\x1b" not in result.output


# ---------------------------------------------------------------------------
# Stress tests
# ---------------------------------------------------------------------------

class TestShowStress:
    def test_show_commit_with_large_metadata(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        large_meta = {f"key_{i}": f"value_{i}" for i in range(200)}
        _make_commit(root, repo_id, metadata=large_meta)
        result = runner.invoke(cli, ["show"], env=_env(root), catch_exceptions=False)
        assert result.exit_code == 0
        assert "key_0" in result.output
        assert "key_199" in result.output

    def test_show_json_with_large_metadata(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        large_meta = {f"k{i}": f"v{i}" for i in range(100)}
        _make_commit(root, repo_id, metadata=large_meta)
        result = runner.invoke(cli, ["show", "--json"], env=_env(root), catch_exceptions=False)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "k99" in data.get("metadata", {})

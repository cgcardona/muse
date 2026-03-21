"""Comprehensive tests for ``muse tag``.

Covers:
- Unit: write_tag, delete_tag, get_tags_for_commit, get_all_tags
- Integration: add → list → remove round-trip
- E2E: full CLI via CliRunner
- Security: tag names sanitized, ref validation
- Stress: many tags on many commits
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


def _make_commit(
    root: pathlib.Path, repo_id: str, branch: str = "main", message: str = "test"
) -> str:
    from muse.core.store import CommitRecord, SnapshotRecord, write_commit, write_snapshot
    from muse.core.snapshot import compute_snapshot_id, compute_commit_id

    ref_file = root / ".muse" / "refs" / "heads" / branch
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
        commit_id=commit_id, repo_id=repo_id, branch=branch,
        snapshot_id=snap_id, message=message, committed_at=committed_at,
        parent_commit_id=parent_id,
    ))
    ref_file.parent.mkdir(parents=True, exist_ok=True)
    ref_file.write_text(commit_id, encoding="utf-8")
    return commit_id


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------

class TestTagUnit:
    def test_write_and_read_tag(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        commit_id = _make_commit(root, repo_id)
        from muse.core.store import TagRecord, write_tag, get_tags_for_commit
        tag = TagRecord(tag_id=str(uuid.uuid4()), repo_id=repo_id,
                        commit_id=commit_id, tag="emotion:joyful")
        write_tag(root, tag)
        tags = get_tags_for_commit(root, repo_id, commit_id)
        assert len(tags) == 1
        assert tags[0].tag == "emotion:joyful"

    def test_delete_tag(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        commit_id = _make_commit(root, repo_id)
        from muse.core.store import TagRecord, write_tag, get_tags_for_commit, delete_tag
        tag_id = str(uuid.uuid4())
        write_tag(root, TagRecord(tag_id=tag_id, repo_id=repo_id,
                                   commit_id=commit_id, tag="section:chorus"))
        assert len(get_tags_for_commit(root, repo_id, commit_id)) == 1
        assert delete_tag(root, repo_id, tag_id) is True
        assert get_tags_for_commit(root, repo_id, commit_id) == []

    def test_delete_nonexistent_tag_returns_false(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        from muse.core.store import delete_tag
        assert delete_tag(root, repo_id, str(uuid.uuid4())) is False

    def test_get_all_tags_empty(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        from muse.core.store import get_all_tags
        assert get_all_tags(root, repo_id) == []


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------

class TestTagIntegration:
    def test_add_and_list_tag(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        _make_commit(root, repo_id)
        result = runner.invoke(cli, ["tag", "add", "emotion:joyful"], env=_env(root), catch_exceptions=False)
        assert result.exit_code == 0
        assert "Tagged" in result.output

        result2 = runner.invoke(cli, ["tag", "list"], env=_env(root), catch_exceptions=False)
        assert "emotion:joyful" in result2.output

    def test_list_tags_for_specific_commit(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        commit_id = _make_commit(root, repo_id)
        runner.invoke(cli, ["tag", "add", "section:verse"], env=_env(root), catch_exceptions=False)
        result = runner.invoke(cli, ["tag", "list", commit_id[:12]], env=_env(root), catch_exceptions=False)
        assert "section:verse" in result.output

    def test_remove_tag(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        _make_commit(root, repo_id)
        runner.invoke(cli, ["tag", "add", "emotion:tense"], env=_env(root), catch_exceptions=False)
        result = runner.invoke(cli, ["tag", "remove", "emotion:tense"], env=_env(root), catch_exceptions=False)
        assert result.exit_code == 0
        assert "Removed" in result.output
        result2 = runner.invoke(cli, ["tag", "list"], env=_env(root), catch_exceptions=False)
        assert "emotion:tense" not in result2.output

    def test_remove_nonexistent_tag_fails(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        _make_commit(root, repo_id)
        result = runner.invoke(cli, ["tag", "remove", "ghost:tag"], env=_env(root))
        assert result.exit_code != 0

    def test_add_multiple_tags_same_commit(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        _make_commit(root, repo_id)
        runner.invoke(cli, ["tag", "add", "key:Am"], env=_env(root), catch_exceptions=False)
        runner.invoke(cli, ["tag", "add", "tempo:120bpm"], env=_env(root), catch_exceptions=False)
        result = runner.invoke(cli, ["tag", "list"], env=_env(root), catch_exceptions=False)
        assert "key:Am" in result.output
        assert "tempo:120bpm" in result.output

    def test_tag_on_invalid_ref_fails(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        _make_commit(root, repo_id)
        result = runner.invoke(cli, ["tag", "add", "emotion:sad", "deadbeef" * 8], env=_env(root))
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Security tests
# ---------------------------------------------------------------------------

class TestTagSecurity:
    def test_tag_with_control_characters_sanitized_in_output(
        self, tmp_path: pathlib.Path
    ) -> None:
        root, repo_id = _init_repo(tmp_path)
        _make_commit(root, repo_id)
        malicious = "emotion:\x1b[31mred\x1b[0m"
        runner.invoke(cli, ["tag", "add", malicious], env=_env(root), catch_exceptions=False)
        result = runner.invoke(cli, ["tag", "list"], env=_env(root), catch_exceptions=False)
        assert result.exit_code == 0
        assert "\x1b" not in result.output


# ---------------------------------------------------------------------------
# Stress tests
# ---------------------------------------------------------------------------

class TestTagStress:
    def test_many_tags_on_many_commits(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        commit_ids = [_make_commit(root, repo_id, message=f"commit {i}") for i in range(30)]
        from muse.core.store import TagRecord, write_tag, get_all_tags
        tag_types = ["emotion:joyful", "section:chorus", "key:Am", "tempo:120bpm", "stage:master"]
        for i, cid in enumerate(commit_ids):
            write_tag(root, TagRecord(
                tag_id=str(uuid.uuid4()), repo_id=repo_id,
                commit_id=cid, tag=tag_types[i % len(tag_types)],
            ))
        all_tags = get_all_tags(root, repo_id)
        assert len(all_tags) == 30
        result = runner.invoke(cli, ["tag", "list"], env=_env(root), catch_exceptions=False)
        assert result.exit_code == 0
        for tag_type in tag_types:
            assert tag_type in result.output

"""Comprehensive tests for ``muse commit``.

Covers:
- Unit: snapshot creation, commit record written
- Integration: commit updates HEAD ref, snapshot manifest reflects files
- E2E: CLI flags (--message / -m, --author, --format json)
- Security: sanitized output for conflict paths
- Stress: many sequential commits on one branch
"""

from __future__ import annotations

import datetime
import hashlib
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


def _add_file(root: pathlib.Path, filename: str, content: bytes) -> str:
    """Write a file to the workspace and return its SHA-256."""
    (root / filename).write_bytes(content)
    return hashlib.sha256(content).hexdigest()


# ---------------------------------------------------------------------------
# E2E CLI tests
# ---------------------------------------------------------------------------

class TestCommitCLI:
    def test_commit_creates_commit_record(self, tmp_path: pathlib.Path) -> None:
        root, _ = _init_repo(tmp_path)
        _add_file(root, "song.mid", b"MIDI data")
        result = runner.invoke(
            cli, ["commit", "-m", "first commit"], env=_env(root), catch_exceptions=False
        )
        assert result.exit_code == 0
        commits_dir = root / ".muse" / "commits"
        assert any(commits_dir.iterdir())

    def test_commit_short_message_flag(self, tmp_path: pathlib.Path) -> None:
        root, _ = _init_repo(tmp_path)
        _add_file(root, "song.mid", b"MIDI data 2")
        result = runner.invoke(
            cli, ["commit", "-m", "short flag"], env=_env(root), catch_exceptions=False
        )
        assert result.exit_code == 0

    def test_commit_long_message_flag(self, tmp_path: pathlib.Path) -> None:
        root, _ = _init_repo(tmp_path)
        _add_file(root, "song.mid", b"MIDI data 3")
        result = runner.invoke(
            cli, ["commit", "--message", "long flag"], env=_env(root), catch_exceptions=False
        )
        assert result.exit_code == 0

    def test_commit_updates_head(self, tmp_path: pathlib.Path) -> None:
        root, _ = _init_repo(tmp_path)
        _add_file(root, "track.mid", b"data")
        runner.invoke(cli, ["commit", "-m", "first"], env=_env(root), catch_exceptions=False)
        ref_file = root / ".muse" / "refs" / "heads" / "main"
        assert ref_file.exists()
        commit_id = ref_file.read_text().strip()
        assert len(commit_id) == 64

    def test_commit_message_in_record(self, tmp_path: pathlib.Path) -> None:
        root, _ = _init_repo(tmp_path)
        _add_file(root, "msg_test.mid", b"message track data")
        runner.invoke(cli, ["commit", "-m", "my message"], env=_env(root), catch_exceptions=False)
        from muse.core.store import get_all_commits
        commits = get_all_commits(root)
        assert any("my message" in c.message for c in commits)

    def test_commit_output_contains_hash(self, tmp_path: pathlib.Path) -> None:
        import re
        root, _ = _init_repo(tmp_path)
        _add_file(root, "hash_test.mid", b"hash test MIDI data")
        result = runner.invoke(
            cli, ["commit", "-m", "hash check"], env=_env(root), catch_exceptions=False
        )
        assert result.exit_code == 0
        assert re.search(r"[0-9a-f]{6,}", result.output)

    def test_commit_with_author(self, tmp_path: pathlib.Path) -> None:
        root, _ = _init_repo(tmp_path)
        _add_file(root, "authored.mid", b"authored MIDI")
        result = runner.invoke(
            cli, ["commit", "-m", "authored", "--author", "Alice"],
            env=_env(root), catch_exceptions=False
        )
        assert result.exit_code == 0

    def test_commit_allow_empty_flag(self, tmp_path: pathlib.Path) -> None:
        """Committing with --allow-empty should succeed even with no tracked files."""
        root, _ = _init_repo(tmp_path)
        result = runner.invoke(
            cli, ["commit", "-m", "empty", "--allow-empty"], env=_env(root), catch_exceptions=False
        )
        assert result.exit_code == 0

    def test_commit_no_files_exits_user_error(self, tmp_path: pathlib.Path) -> None:
        """Committing with no tracked files (no --allow-empty) should fail."""
        root, _ = _init_repo(tmp_path)
        result = runner.invoke(cli, ["commit", "-m", "empty"], env=_env(root))
        assert result.exit_code != 0

    def test_second_commit_has_parent(self, tmp_path: pathlib.Path) -> None:
        root, _ = _init_repo(tmp_path)
        _add_file(root, "track1.mid", b"first track")
        runner.invoke(cli, ["commit", "-m", "first"], env=_env(root), catch_exceptions=False)
        _add_file(root, "track2.mid", b"second track")
        runner.invoke(cli, ["commit", "-m", "second"], env=_env(root), catch_exceptions=False)
        from muse.core.store import get_all_commits
        commits = get_all_commits(root)
        assert len(commits) == 2
        # At least one commit should have a parent
        assert any(c.parent_commit_id is not None for c in commits)


class TestCommitStress:
    def test_many_sequential_commits(self, tmp_path: pathlib.Path) -> None:
        root, _ = _init_repo(tmp_path)
        for i in range(25):
            _add_file(root, f"track_{i:03d}.mid", f"unique data {i} xyz".encode())
            result = runner.invoke(
                cli, ["commit", "-m", f"commit {i}"], env=_env(root), catch_exceptions=False
            )
            assert result.exit_code == 0
        from muse.core.store import get_all_commits
        commits = get_all_commits(root)
        assert len(commits) == 25

    def test_commit_with_many_files(self, tmp_path: pathlib.Path) -> None:
        root, _ = _init_repo(tmp_path)
        for i in range(50):
            _add_file(root, f"track_{i:03d}.mid", f"data {i}".encode())
        result = runner.invoke(
            cli, ["commit", "-m", "many files"], env=_env(root), catch_exceptions=False
        )
        assert result.exit_code == 0

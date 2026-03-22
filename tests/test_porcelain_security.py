"""Security-focused regression tests for all porcelain hardening fixes.

These tests verify the specific security improvements made during the
porcelain hardening pass:

- ReDoS guard in content-grep (pattern length limit)
- Zip-slip prevention in archive and snapshot export
- validate_branch_name added to checkout and rebase
- sanitize_display applied to all user-sourced echoed strings
- Atomic stash writes (no temp file corruption)
- Snapshot ID glob prefix sanitisation
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
# Shared repo setup helper
# ---------------------------------------------------------------------------

def _env(root: pathlib.Path) -> dict[str, str]:
    return {"MUSE_REPO_ROOT": str(root)}


def _init_repo(tmp_path: pathlib.Path, domain: str = "midi") -> tuple[pathlib.Path, str]:
    muse_dir = tmp_path / ".muse"
    muse_dir.mkdir()
    repo_id = str(uuid.uuid4())
    (muse_dir / "repo.json").write_text(json.dumps({
        "repo_id": repo_id,
        "domain": domain,
        "default_branch": "main",
        "created_at": "2025-01-01T00:00:00+00:00",
    }), encoding="utf-8")
    (muse_dir / "HEAD").write_text("ref: refs/heads/main", encoding="utf-8")
    (muse_dir / "refs" / "heads").mkdir(parents=True)
    (muse_dir / "snapshots").mkdir()
    (muse_dir / "commits").mkdir()
    (muse_dir / "objects").mkdir()
    return tmp_path, repo_id


def _make_commit(root: pathlib.Path, repo_id: str, message: str = "test") -> str:
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
# content-grep: ReDoS guard
# ---------------------------------------------------------------------------

class TestContentGrepSecurity:
    def test_pattern_too_long_rejected(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        _make_commit(root, repo_id)
        long_pattern = "a" * 501  # > 500 char limit
        result = runner.invoke(cli, ["content-grep", "--pattern", long_pattern], env=_env(root))
        assert result.exit_code != 0
        assert "too long" in result.output or "Pattern" in result.output

    def test_pattern_exactly_500_chars_accepted(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        _make_commit(root, repo_id)
        pattern_500 = "a" * 500
        result = runner.invoke(cli, ["content-grep", "--pattern", pattern_500], env=_env(root))
        # No match → exit 1, but not a ReDoS validation failure
        assert result.exit_code in (0, 1)

    def test_invalid_regex_rejected(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        _make_commit(root, repo_id)
        result = runner.invoke(cli, ["content-grep", "--pattern", "[invalid regex"], env=_env(root))
        assert result.exit_code != 0
        assert "regex" in result.output.lower() or "invalid" in result.output.lower()

    def test_output_sanitized_no_ansi_injection(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        content = b"normal line\n\x1b[31mRED\x1b[0m line\nanother\n"
        obj_id = hashlib.sha256(content).hexdigest()
        obj_path = root / ".muse" / "objects" / obj_id[:2] / obj_id[2:]
        obj_path.parent.mkdir(parents=True, exist_ok=True)
        obj_path.write_bytes(content)

        from muse.core.store import SnapshotRecord, CommitRecord, write_snapshot, write_commit
        from muse.core.snapshot import compute_snapshot_id, compute_commit_id

        manifest = {"file.txt": obj_id}
        snap_id = compute_snapshot_id(manifest)
        committed_at = datetime.datetime.now(datetime.timezone.utc)
        commit_id = compute_commit_id([], snap_id, "test", committed_at.isoformat())
        write_snapshot(root, SnapshotRecord(snapshot_id=snap_id, manifest=manifest))
        write_commit(root, CommitRecord(
            commit_id=commit_id, repo_id=repo_id, branch="main",
            snapshot_id=snap_id, message="test", committed_at=committed_at,
            parent_commit_id=None,
        ))
        (root / ".muse" / "refs" / "heads" / "main").write_text(commit_id)

        result = runner.invoke(cli, ["content-grep", "--pattern", "RED"], env=_env(root))
        if result.exit_code == 0:
            assert "\x1b" not in result.output


# ---------------------------------------------------------------------------
# archive: zip-slip guard
# ---------------------------------------------------------------------------

class TestArchiveSecurity:
    def test_archive_prefix_with_dotdot_rejected(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        _make_commit(root, repo_id)
        result = runner.invoke(cli, ["archive", "--prefix", "../../evil"], env=_env(root))
        assert result.exit_code != 0

    def test_zip_slip_guard_in_safe_arcname(self) -> None:
        from muse.cli.commands.archive import _safe_arcname
        assert _safe_arcname("safe", "../../../etc/passwd") is None
        assert _safe_arcname("safe", "/etc/passwd") is None
        assert _safe_arcname("safe", "normal/path.txt") == "safe/normal/path.txt"


# ---------------------------------------------------------------------------
# snapshot: glob prefix sanitisation
# ---------------------------------------------------------------------------

class TestSnapshotSecurity:
    def test_validate_snapshot_id_prefix_strips_metacharacters(self) -> None:
        from muse.cli.commands.snapshot_cmd import _validate_snapshot_id_prefix
        prefix = _validate_snapshot_id_prefix("*bad[0-9]?glob*")
        assert "*" not in prefix
        assert "[" not in prefix
        assert "?" not in prefix
        assert all(c in "0123456789abcdef" for c in prefix)

    def test_snapshot_show_with_glob_meta_no_injection(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Glob metacharacters in the snapshot ID prefix must be sanitised."""
        root, repo_id = _init_repo(tmp_path)
        _make_commit(root, repo_id)
        # The '*' prefix is sanitised to empty string (no hex chars), so the
        # command finds nothing but must not raise an exception or expose paths.
        result = runner.invoke(cli, ["snapshot", "show", "*"], env=_env(root))
        # Should not crash; may exit 0 (empty match) or non-zero (not found)
        assert "\x1b" not in result.output
        assert result.exception is None

    def test_safe_arcname_in_snapshot(self) -> None:
        from muse.cli.commands.snapshot_cmd import _safe_arcname
        assert _safe_arcname("", "../../../etc/passwd") is None
        assert _safe_arcname("prefix", "safe.txt") == "prefix/safe.txt"


# ---------------------------------------------------------------------------
# checkout: validate_branch_name on switch
# ---------------------------------------------------------------------------

class TestCheckoutSecurity:
    def test_checkout_invalid_branch_name_rejected(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        _make_commit(root, repo_id)
        result = runner.invoke(cli, ["checkout", "../evil"], env=_env(root))
        assert result.exit_code != 0

    def test_checkout_double_dot_rejected(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        _make_commit(root, repo_id)
        result = runner.invoke(cli, ["checkout", ".."], env=_env(root))
        assert result.exit_code != 0

    def test_checkout_valid_existing_branch_works(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        _make_commit(root, repo_id)
        # Create a second branch and switch to it
        (root / ".muse" / "refs" / "heads" / "dev").write_text(
            (root / ".muse" / "refs" / "heads" / "main").read_text()
        )
        result = runner.invoke(cli, ["checkout", "dev"], env=_env(root), catch_exceptions=False)
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# rebase: validate_branch_name on upstream/onto
# ---------------------------------------------------------------------------

class TestRebaseSecurity:
    def test_rebase_invalid_upstream_fails(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        _make_commit(root, repo_id)
        result = runner.invoke(cli, ["rebase", "../../../etc/passwd"], env=_env(root))
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# stash: atomic write regression
# ---------------------------------------------------------------------------

class TestStashAtomicWrite:
    def test_no_temp_files_after_save(self, tmp_path: pathlib.Path) -> None:
        root, _ = _init_repo(tmp_path)
        from muse.cli.commands.stash import _save_stash, StashEntry
        entries = [
            StashEntry(
                snapshot_id="a" * 64, manifest={},
                branch="main", stashed_at="2025-01-01T00:00:00+00:00"
            )
        ]
        _save_stash(root, entries)
        assert list((root / ".muse").glob(".stash_tmp_*")) == []
        assert (root / ".muse" / "stash.json").exists()

    def test_stash_file_contents_after_atomic_write(self, tmp_path: pathlib.Path) -> None:
        root, _ = _init_repo(tmp_path)
        from muse.cli.commands.stash import _save_stash, _load_stash, StashEntry
        _save_stash(root, [
            StashEntry(snapshot_id="b" * 64, manifest={},
                       branch="main", stashed_at="2025-06-01T12:00:00+00:00")
        ])
        loaded = _load_stash(root)
        assert len(loaded) == 1
        assert loaded[0]["snapshot_id"] == "b" * 64


# ---------------------------------------------------------------------------
# show: sanitize_display regression
# ---------------------------------------------------------------------------

class TestShowDisplaySanitize:
    def test_commit_message_ansi_not_in_output(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        from muse.core.store import CommitRecord, SnapshotRecord, write_commit, write_snapshot
        from muse.core.snapshot import compute_snapshot_id, compute_commit_id

        snap_id = compute_snapshot_id({})
        committed_at = datetime.datetime.now(datetime.timezone.utc)
        commit_id = compute_commit_id([], snap_id, "clean", committed_at.isoformat())
        write_snapshot(root, SnapshotRecord(snapshot_id=snap_id, manifest={}))
        write_commit(root, CommitRecord(
            commit_id=commit_id, repo_id=repo_id, branch="main",
            snapshot_id=snap_id,
            message="evil\x1b[31mRED\x1b[0m message",
            committed_at=committed_at, parent_commit_id=None,
            author="Alice\x1b[0m",
        ))
        (root / ".muse" / "refs" / "heads" / "main").write_text(commit_id)

        result = runner.invoke(cli, ["show"], env=_env(root), catch_exceptions=False)
        assert result.exit_code == 0
        assert "\x1b" not in result.output


# ---------------------------------------------------------------------------
# reflog: operation sanitization regression
# ---------------------------------------------------------------------------

class TestReflogSanitize:
    def test_operation_ansi_not_in_output(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        from muse.core.reflog import append_reflog
        _make_commit(root, repo_id)
        append_reflog(
            root, "main",
            old_id="0" * 64, new_id="a" * 64,
            author="user", operation="evil\x1b[31mRED\x1b[0m",
        )
        result = runner.invoke(cli, ["reflog"], env=_env(root), catch_exceptions=False)
        assert "\x1b" not in result.output

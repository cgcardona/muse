"""Comprehensive tests for ``muse archive``.

Covers:
- Unit: _safe_arcname zip-slip guard
- Integration: archive a commit to tar.gz and zip
- E2E: full CLI via CliRunner with output path
- Security: --prefix validation, zip-slip prevention in manifest paths
- Stress: archive with many tracked files
"""

from __future__ import annotations

import datetime
import hashlib
import json
import pathlib
import tarfile
import uuid
import zipfile

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


def _make_commit_with_files(
    root: pathlib.Path, repo_id: str, files: dict[str, bytes] | None = None
) -> str:
    from muse.core.store import CommitRecord, SnapshotRecord, write_commit, write_snapshot
    from muse.core.snapshot import compute_snapshot_id, compute_commit_id

    ref_file = root / ".muse" / "refs" / "heads" / "main"
    parent_id = ref_file.read_text().strip() if ref_file.exists() else None

    manifest: dict[str, str] = {}
    if files:
        for rel_path, content in files.items():
            obj_id = hashlib.sha256(content).hexdigest()
            obj_path = root / ".muse" / "objects" / obj_id[:2] / obj_id[2:]
            obj_path.parent.mkdir(parents=True, exist_ok=True)
            obj_path.write_bytes(content)
            manifest[rel_path] = obj_id

    snap_id = compute_snapshot_id(manifest)
    committed_at = datetime.datetime.now(datetime.timezone.utc)
    commit_id = compute_commit_id(
        parent_ids=[parent_id] if parent_id else [],
        snapshot_id=snap_id, message="archive test",
        committed_at_iso=committed_at.isoformat(),
    )
    write_snapshot(root, SnapshotRecord(snapshot_id=snap_id, manifest=manifest))
    write_commit(root, CommitRecord(
        commit_id=commit_id, repo_id=repo_id, branch="main",
        snapshot_id=snap_id, message="archive test",
        committed_at=committed_at, parent_commit_id=parent_id,
    ))
    ref_file.parent.mkdir(parents=True, exist_ok=True)
    ref_file.write_text(commit_id, encoding="utf-8")
    return commit_id


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------

class TestArchiveUnit:
    def test_safe_arcname_normal_path(self) -> None:
        from muse.cli.commands.archive import _safe_arcname
        assert _safe_arcname("myproject", "state/song.mid") == "myproject/state/song.mid"

    def test_safe_arcname_no_prefix(self) -> None:
        from muse.cli.commands.archive import _safe_arcname
        assert _safe_arcname("", "state/song.mid") == "state/song.mid"

    def test_safe_arcname_traversal_in_rel_path_rejected(self) -> None:
        from muse.cli.commands.archive import _safe_arcname
        assert _safe_arcname("prefix", "../../../etc/passwd") is None

    def test_safe_arcname_absolute_rel_path_rejected(self) -> None:
        from muse.cli.commands.archive import _safe_arcname
        assert _safe_arcname("prefix", "/etc/passwd") is None

    def test_safe_arcname_traversal_in_prefix_rejected(self) -> None:
        from muse.cli.commands.archive import _safe_arcname
        assert _safe_arcname("../evil", "file.txt") is None

    def test_safe_arcname_trailing_slash_normalised(self) -> None:
        from muse.cli.commands.archive import _safe_arcname
        assert _safe_arcname("myproject/", "file.txt") == "myproject/file.txt"


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------

class TestArchiveIntegration:
    def test_archive_empty_commit(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        _make_commit_with_files(root, repo_id, files={})
        out = tmp_path / "out.tar.gz"
        result = runner.invoke(cli, ["archive", "--output", str(out)], env=_env(root), catch_exceptions=False)
        assert result.exit_code == 0
        assert out.exists()

    def test_archive_tar_gz_contains_files(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        _make_commit_with_files(root, repo_id, files={"state/song.mid": b"\x00\x00MIDI"})
        out = tmp_path / "archive.tar.gz"
        result = runner.invoke(cli, ["archive", "--output", str(out)], env=_env(root), catch_exceptions=False)
        assert result.exit_code == 0
        with tarfile.open(out, "r:gz") as tf:
            names = tf.getnames()
        assert any("song.mid" in n for n in names)

    def test_archive_zip_contains_files(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        _make_commit_with_files(root, repo_id, files={"track.mid": b"MIDIdata"})
        out = tmp_path / "archive.zip"
        result = runner.invoke(
            cli, ["archive", "--format", "zip", "--output", str(out)],
            env=_env(root), catch_exceptions=False,
        )
        assert result.exit_code == 0
        with zipfile.ZipFile(out, "r") as zf:
            names = zf.namelist()
        assert any("track.mid" in n for n in names)

    def test_archive_with_prefix(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        _make_commit_with_files(root, repo_id, files={"song.mid": b"data"})
        out = tmp_path / "prefixed.tar.gz"
        result = runner.invoke(
            cli, ["archive", "--output", str(out), "--prefix", "myband-v1.0/"],
            env=_env(root), catch_exceptions=False,
        )
        assert result.exit_code == 0
        with tarfile.open(out, "r:gz") as tf:
            names = tf.getnames()
        assert any("myband-v1.0" in n for n in names)

    def test_archive_unknown_format_fails(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        _make_commit_with_files(root, repo_id)
        result = runner.invoke(cli, ["archive", "--format", "rar"], env=_env(root))
        assert result.exit_code != 0

    def test_archive_no_commits_fails(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        result = runner.invoke(cli, ["archive"], env=_env(root))
        assert result.exit_code != 0

    def test_archive_short_flags(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        _make_commit_with_files(root, repo_id, files={"test.mid": b"data"})
        out = tmp_path / "short.tar.gz"
        result = runner.invoke(
            cli, ["archive", "-f", "tar.gz", "-o", str(out)],
            env=_env(root), catch_exceptions=False,
        )
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# Security tests
# ---------------------------------------------------------------------------

class TestArchiveSecurity:
    def test_prefix_traversal_rejected(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        _make_commit_with_files(root, repo_id, files={"song.mid": b"data"})
        out = tmp_path / "evil.tar.gz"
        result = runner.invoke(
            cli, ["archive", "--output", str(out), "--prefix", "../evil/"],
            env=_env(root),
        )
        assert result.exit_code != 0

    def test_zip_slip_manifest_path_skipped(self, tmp_path: pathlib.Path) -> None:
        """A manifest entry with '../' is skipped, not written to archive."""
        root, repo_id = _init_repo(tmp_path)
        from muse.cli.commands.archive import _build_tar
        content = b"evil content"
        obj_id = hashlib.sha256(content).hexdigest()
        obj_path = root / ".muse" / "objects" / obj_id[:2] / obj_id[2:]
        obj_path.parent.mkdir(parents=True, exist_ok=True)
        obj_path.write_bytes(content)

        out = tmp_path / "safe.tar.gz"
        manifest = {"../../../etc/passwd": obj_id, "safe.txt": obj_id}
        count = _build_tar(root, manifest, out, prefix="")
        assert count == 1  # only safe.txt
        with tarfile.open(out, "r:gz") as tf:
            names = tf.getnames()
        assert all("etc" not in n for n in names)


# ---------------------------------------------------------------------------
# Stress tests
# ---------------------------------------------------------------------------

class TestArchiveStress:
    def test_archive_many_files(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        files = {f"track_{i:03d}.mid": f"MIDI{i}".encode() for i in range(50)}
        _make_commit_with_files(root, repo_id, files=files)
        out = tmp_path / "many.tar.gz"
        result = runner.invoke(cli, ["archive", "--output", str(out)], env=_env(root), catch_exceptions=False)
        assert result.exit_code == 0
        with tarfile.open(out, "r:gz") as tf:
            names = tf.getnames()
        assert len(names) == 50

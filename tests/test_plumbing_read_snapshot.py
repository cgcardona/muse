"""Tests for ``muse plumbing read-snapshot``.

Covers: valid-snapshot lookup, manifest contents, file-count, created_at
timestamp, snapshot-not-found, invalid-ID format, and a stress case with
a large manifest.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import pathlib

from typer.testing import CliRunner

from muse.cli.app import cli
from muse.core.errors import ExitCode
from muse.core.store import SnapshotRecord, write_snapshot

runner = CliRunner()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sha(tag: str) -> str:
    return hashlib.sha256(tag.encode()).hexdigest()


def _init_repo(path: pathlib.Path) -> pathlib.Path:
    muse = path / ".muse"
    (muse / "commits").mkdir(parents=True)
    (muse / "snapshots").mkdir(parents=True)
    (muse / "objects").mkdir(parents=True)
    (muse / "refs" / "heads").mkdir(parents=True)
    (muse / "HEAD").write_text("ref: refs/heads/main", encoding="utf-8")
    (muse / "repo.json").write_text(
        json.dumps({"repo_id": "test-repo", "domain": "midi"}), encoding="utf-8"
    )
    return path


def _env(repo: pathlib.Path) -> dict[str, str]:
    return {"MUSE_REPO_ROOT": str(repo)}


def _snap(repo: pathlib.Path, manifest: dict[str, str], tag: str = "snap") -> str:
    sid = _sha(f"sid-{tag}-{json.dumps(sorted(manifest.items()))}")
    write_snapshot(
        repo,
        SnapshotRecord(
            snapshot_id=sid,
            manifest=manifest,
            created_at=datetime.datetime(2026, 3, 15, 12, 0, 0, tzinfo=datetime.timezone.utc),
        ),
    )
    return sid


# ---------------------------------------------------------------------------
# Unit: ID validation
# ---------------------------------------------------------------------------


class TestReadSnapshotUnit:
    def test_invalid_id_format_exits_user_error(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        result = runner.invoke(cli, ["plumbing", "read-snapshot", "not-hex"], env=_env(repo))
        assert result.exit_code == ExitCode.USER_ERROR
        assert "error" in json.loads(result.stdout)

    def test_snapshot_not_found_exits_user_error(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        ghost = _sha("ghost")
        result = runner.invoke(cli, ["plumbing", "read-snapshot", ghost], env=_env(repo))
        assert result.exit_code == ExitCode.USER_ERROR
        assert "error" in json.loads(result.stdout)


# ---------------------------------------------------------------------------
# Integration: snapshot retrieval
# ---------------------------------------------------------------------------


class TestReadSnapshotRetrieval:
    def test_returns_snapshot_id_in_output(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        sid = _snap(repo, {})
        result = runner.invoke(cli, ["plumbing", "read-snapshot", sid], env=_env(repo))
        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        assert data["snapshot_id"] == sid

    def test_returns_correct_file_count(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        manifest = {"a.mid": _sha("a"), "b.mid": _sha("b"), "c.mid": _sha("c")}
        sid = _snap(repo, manifest)
        result = runner.invoke(cli, ["plumbing", "read-snapshot", sid], env=_env(repo))
        assert result.exit_code == 0
        assert json.loads(result.stdout)["file_count"] == 3

    def test_manifest_paths_and_ids_match(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        oid = _sha("drums-content")
        manifest = {"drums.mid": oid}
        sid = _snap(repo, manifest)
        result = runner.invoke(cli, ["plumbing", "read-snapshot", sid], env=_env(repo))
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["manifest"]["drums.mid"] == oid

    def test_empty_manifest_reports_zero_files(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        sid = _snap(repo, {})
        result = runner.invoke(cli, ["plumbing", "read-snapshot", sid], env=_env(repo))
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["file_count"] == 0
        assert data["manifest"] == {}

    def test_created_at_is_iso8601_string(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        sid = _snap(repo, {})
        result = runner.invoke(cli, ["plumbing", "read-snapshot", sid], env=_env(repo))
        assert result.exit_code == 0
        ts = json.loads(result.stdout)["created_at"]
        # Must be parseable as an ISO 8601 datetime.
        dt = datetime.datetime.fromisoformat(ts)
        assert dt.year == 2026

    def test_output_is_pretty_printed_json(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        sid = _snap(repo, {"f.mid": _sha("f")})
        result = runner.invoke(cli, ["plumbing", "read-snapshot", sid], env=_env(repo))
        assert result.exit_code == 0
        # Pretty-printed JSON contains newlines.
        assert "\n" in result.stdout

    def test_multiple_snapshots_independent(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        sid1 = _snap(repo, {"a.mid": _sha("a")}, "s1")
        sid2 = _snap(repo, {"b.mid": _sha("b")}, "s2")
        r1 = runner.invoke(cli, ["plumbing", "read-snapshot", sid1], env=_env(repo))
        r2 = runner.invoke(cli, ["plumbing", "read-snapshot", sid2], env=_env(repo))
        assert r1.exit_code == 0 and r2.exit_code == 0
        assert "a.mid" in json.loads(r1.stdout)["manifest"]
        assert "b.mid" in json.loads(r2.stdout)["manifest"]


# ---------------------------------------------------------------------------
# Stress: 1000-file manifest
# ---------------------------------------------------------------------------


class TestReadSnapshotStress:
    def test_1000_file_manifest_reads_correctly(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        manifest = {f"track_{i:04d}.mid": _sha(f"obj-{i}") for i in range(1000)}
        sid = _snap(repo, manifest)
        result = runner.invoke(cli, ["plumbing", "read-snapshot", sid], env=_env(repo))
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["file_count"] == 1000
        assert len(data["manifest"]) == 1000

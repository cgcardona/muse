"""Tests for ``muse plumbing ls-files``.

Covers: default HEAD listing, explicit ``--commit`` flag, text-format output,
empty manifest, commit-not-found and snapshot-not-found error paths, sorted
output, and a stress case with a 500-file manifest.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import pathlib

from tests.cli_test_helper import CliRunner

cli = None  # argparse migration — CliRunner ignores this arg
from muse.core.errors import ExitCode
from muse.core.store import CommitRecord, SnapshotRecord, write_commit, write_snapshot

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


def _snap(repo: pathlib.Path, manifest: dict[str, str], tag: str = "s") -> str:
    sid = _sha(f"snap-{tag}-{json.dumps(sorted(manifest.items()))}")
    write_snapshot(
        repo,
        SnapshotRecord(
            snapshot_id=sid,
            manifest=manifest,
            created_at=datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc),
        ),
    )
    return sid


def _commit(
    repo: pathlib.Path, tag: str, sid: str, branch: str = "main", parent: str | None = None
) -> str:
    cid = _sha(tag)
    write_commit(
        repo,
        CommitRecord(
            commit_id=cid,
            repo_id="test-repo",
            branch=branch,
            snapshot_id=sid,
            message=tag,
            committed_at=datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc),
            author="tester",
            parent_commit_id=parent,
        ),
    )
    ref = repo / ".muse" / "refs" / "heads" / branch
    ref.parent.mkdir(parents=True, exist_ok=True)
    ref.write_text(cid, encoding="utf-8")
    return cid


# ---------------------------------------------------------------------------
# Integration: default HEAD
# ---------------------------------------------------------------------------


class TestLsFilesHead:
    def test_default_lists_head_manifest(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        manifest = {"tracks/drums.mid": _sha("drums"), "tracks/bass.mid": _sha("bass")}
        sid = _snap(repo, manifest)
        cid = _commit(repo, "c1", sid)
        result = runner.invoke(cli, ["plumbing", "ls-files"], env=_env(repo))
        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        assert data["commit_id"] == cid
        assert data["snapshot_id"] == sid
        assert data["file_count"] == 2
        paths = {f["path"] for f in data["files"]}
        assert paths == set(manifest.keys())

    def test_object_ids_match_manifest(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        oid = _sha("drums-content")
        sid = _snap(repo, {"drums.mid": oid})
        _commit(repo, "c2", sid)
        result = runner.invoke(cli, ["plumbing", "ls-files"], env=_env(repo))
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["files"][0]["object_id"] == oid

    def test_empty_manifest_reports_zero_files(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        sid = _snap(repo, {})
        _commit(repo, "empty", sid)
        result = runner.invoke(cli, ["plumbing", "ls-files"], env=_env(repo))
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["file_count"] == 0
        assert data["files"] == []

    def test_no_commits_exits_user_error(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        result = runner.invoke(cli, ["plumbing", "ls-files"], env=_env(repo))
        assert result.exit_code == ExitCode.USER_ERROR


# ---------------------------------------------------------------------------
# Integration: --commit flag
# ---------------------------------------------------------------------------


class TestLsFilesCommitFlag:
    def test_explicit_commit_lists_that_commits_manifest(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        oid_a = _sha("a")
        oid_b = _sha("b")
        sid1 = _snap(repo, {"a.mid": oid_a}, "s1")
        sid2 = _snap(repo, {"b.mid": oid_b}, "s2")
        cid1 = _commit(repo, "c1", sid1)
        cid2 = _commit(repo, "c2", sid2, parent=cid1)
        result = runner.invoke(
            cli, ["plumbing", "ls-files", "--commit", cid1], env=_env(repo)
        )
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["commit_id"] == cid1
        assert {f["path"] for f in data["files"]} == {"a.mid"}

    def test_short_commit_flag(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        sid = _snap(repo, {"x.mid": _sha("x")})
        cid = _commit(repo, "cx", sid)
        result = runner.invoke(cli, ["plumbing", "ls-files", "-c", cid], env=_env(repo))
        assert result.exit_code == 0
        assert json.loads(result.stdout)["commit_id"] == cid

    def test_nonexistent_commit_exits_user_error(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        ghost = _sha("ghost")
        result = runner.invoke(cli, ["plumbing", "ls-files", "--commit", ghost], env=_env(repo))
        assert result.exit_code == ExitCode.USER_ERROR
        assert "error" in json.loads(result.stdout)


# ---------------------------------------------------------------------------
# Integration: output formats
# ---------------------------------------------------------------------------


class TestLsFilesFormats:
    def test_text_format_shows_tab_separated_lines(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        oid = _sha("t")
        sid = _snap(repo, {"track.mid": oid})
        _commit(repo, "tf", sid)
        result = runner.invoke(
            cli, ["plumbing", "ls-files", "--format", "text"], env=_env(repo)
        )
        assert result.exit_code == 0
        assert "\t" in result.stdout
        assert "track.mid" in result.stdout

    def test_text_format_short_flag(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        sid = _snap(repo, {"f.mid": _sha("f")})
        _commit(repo, "ftf", sid)
        result = runner.invoke(cli, ["plumbing", "ls-files", "-f", "text"], env=_env(repo))
        assert result.exit_code == 0

    def test_bad_format_exits_user_error(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        sid = _snap(repo, {})
        _commit(repo, "bfmt", sid)
        result = runner.invoke(
            cli, ["plumbing", "ls-files", "--format", "csv"], env=_env(repo)
        )
        assert result.exit_code == ExitCode.USER_ERROR

    def test_files_sorted_lexicographically(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        manifest = {
            "zzz/z.mid": _sha("z"),
            "aaa/a.mid": _sha("a"),
            "mmm/m.mid": _sha("m"),
        }
        sid = _snap(repo, manifest)
        _commit(repo, "sorted", sid)
        result = runner.invoke(cli, ["plumbing", "ls-files"], env=_env(repo))
        assert result.exit_code == 0
        paths = [f["path"] for f in json.loads(result.stdout)["files"]]
        assert paths == sorted(paths)


# ---------------------------------------------------------------------------
# Stress: 500-file manifest
# ---------------------------------------------------------------------------


class TestLsFilesStress:
    def test_500_file_manifest_all_reported(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        manifest = {f"track_{i:04d}.mid": _sha(f"oid-{i}") for i in range(500)}
        sid = _snap(repo, manifest)
        _commit(repo, "big-manifest", sid)
        result = runner.invoke(cli, ["plumbing", "ls-files"], env=_env(repo))
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["file_count"] == 500
        assert len(data["files"]) == 500

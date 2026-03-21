"""Tests for all Tier 1 plumbing commands under ``muse plumbing …``.

Each plumbing command is tested via the Typer CliRunner so tests exercise the
full CLI stack including argument parsing, error handling, and JSON output
format.  All commands are accessed through the ``plumbing`` sub-namespace.

The ``MUSE_REPO_ROOT`` env-var is used to point repo-discovery at the test
fixture without requiring ``os.chdir``.
"""

from __future__ import annotations

import datetime
import json
import pathlib

import pytest
from typer.testing import CliRunner

from muse.cli.app import cli
from muse.core.errors import ExitCode
from muse.core.object_store import write_object
from muse.core.pack import build_pack
from muse.core.store import (
    CommitRecord,
    SnapshotRecord,
    write_commit,
    write_snapshot,
)

runner = CliRunner()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _init_repo(path: pathlib.Path) -> pathlib.Path:
    """Create a minimal .muse/ directory structure."""
    muse = path / ".muse"
    (muse / "commits").mkdir(parents=True)
    (muse / "snapshots").mkdir(parents=True)
    (muse / "objects").mkdir(parents=True)
    (muse / "refs" / "heads").mkdir(parents=True)
    muse.joinpath("HEAD").write_text("ref: refs/heads/main")
    muse.joinpath("repo.json").write_text(
        json.dumps({"repo_id": "test-repo-id", "domain": "generic"})
    )
    return path


def _make_object(repo: pathlib.Path, content: bytes) -> str:
    import hashlib

    oid = hashlib.sha256(content).hexdigest()
    write_object(repo, oid, content)
    return oid


def _make_snapshot(
    repo: pathlib.Path, snap_id: str, manifest: dict[str, str]
) -> SnapshotRecord:
    snap = SnapshotRecord(
        snapshot_id=snap_id,
        manifest=manifest,
        created_at=datetime.datetime(2026, 3, 18, tzinfo=datetime.timezone.utc),
    )
    write_snapshot(repo, snap)
    return snap


def _make_commit(
    repo: pathlib.Path,
    commit_id: str,
    snapshot_id: str,
    *,
    branch: str = "main",
    parent_commit_id: str | None = None,
    message: str = "test commit",
) -> CommitRecord:
    rec = CommitRecord(
        commit_id=commit_id,
        repo_id="test-repo-id",
        branch=branch,
        snapshot_id=snapshot_id,
        message=message,
        committed_at=datetime.datetime(2026, 3, 18, tzinfo=datetime.timezone.utc),
        author="tester",
        parent_commit_id=parent_commit_id,
    )
    write_commit(repo, rec)
    return rec


def _set_head(repo: pathlib.Path, branch: str, commit_id: str) -> None:
    ref = repo / ".muse" / "refs" / "heads" / branch
    ref.parent.mkdir(parents=True, exist_ok=True)
    ref.write_text(commit_id)


def _repo_env(repo: pathlib.Path) -> dict[str, str]:
    """Return env dict that sets MUSE_REPO_ROOT to the given path."""
    return {"MUSE_REPO_ROOT": str(repo)}


# ---------------------------------------------------------------------------
# hash-object
# ---------------------------------------------------------------------------


class TestHashObject:
    def test_hash_file_json_output(self, tmp_path: pathlib.Path) -> None:
        f = tmp_path / "test.txt"
        f.write_bytes(b"hello world")
        result = runner.invoke(cli, ["plumbing", "hash-object", str(f)])
        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        assert "object_id" in data
        assert len(data["object_id"]) == 64
        assert data["stored"] is False

    def test_hash_file_text_format(self, tmp_path: pathlib.Path) -> None:
        f = tmp_path / "data.bin"
        f.write_bytes(b"test bytes")
        result = runner.invoke(
            cli, ["plumbing", "hash-object", "--format", "text", str(f)]
        )
        assert result.exit_code == 0, result.output
        assert len(result.stdout.strip()) == 64

    def test_hash_and_write(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path / "repo")
        f = repo / "sample.txt"
        f.write_bytes(b"write me")
        result = runner.invoke(
            cli,
            ["plumbing", "hash-object", "--write", str(f)],
            env=_repo_env(repo),
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        assert data["stored"] is True

    def test_missing_file_errors(self, tmp_path: pathlib.Path) -> None:
        result = runner.invoke(cli, ["plumbing", "hash-object", str(tmp_path / "no.txt")])
        assert result.exit_code == ExitCode.USER_ERROR

    def test_directory_errors(self, tmp_path: pathlib.Path) -> None:
        result = runner.invoke(cli, ["plumbing", "hash-object", str(tmp_path)])
        assert result.exit_code == ExitCode.USER_ERROR


# ---------------------------------------------------------------------------
# cat-object
# ---------------------------------------------------------------------------


class TestCatObject:
    def test_cat_raw_bytes(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        content = b"raw content data"
        oid = _make_object(repo, content)
        result = runner.invoke(
            cli, ["plumbing", "cat-object", oid],
            env=_repo_env(repo),
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output
        assert result.stdout_bytes == content

    def test_cat_info_format(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        content = b"info content"
        oid = _make_object(repo, content)
        result = runner.invoke(
            cli, ["plumbing", "cat-object", "--format", "info", oid],
            env=_repo_env(repo),
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        assert data["object_id"] == oid
        assert data["present"] is True
        assert data["size_bytes"] == len(content)

    def test_missing_object_errors(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        result = runner.invoke(
            cli, ["plumbing", "cat-object", "a" * 64],
            env=_repo_env(repo),
        )
        assert result.exit_code == ExitCode.USER_ERROR

    def test_missing_object_info_format(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        oid = "b" * 64
        result = runner.invoke(
            cli, ["plumbing", "cat-object", "--format", "info", oid],
            env=_repo_env(repo),
        )
        assert result.exit_code == ExitCode.USER_ERROR
        data = json.loads(result.stdout)
        assert data["present"] is False


# ---------------------------------------------------------------------------
# rev-parse
# ---------------------------------------------------------------------------


class TestRevParse:
    def test_resolve_branch(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        oid = _make_object(repo, b"data")
        _make_snapshot(repo, "s" * 64, {"f": oid})
        _make_commit(repo, "c" * 64, "s" * 64)
        _set_head(repo, "main", "c" * 64)

        result = runner.invoke(
            cli, ["plumbing", "rev-parse", "main"],
            env=_repo_env(repo),
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        assert data["commit_id"] == "c" * 64
        assert data["ref"] == "main"

    def test_resolve_head(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        oid = _make_object(repo, b"data")
        _make_snapshot(repo, "s" * 64, {"f": oid})
        _make_commit(repo, "d" * 64, "s" * 64)
        _set_head(repo, "main", "d" * 64)

        result = runner.invoke(
            cli, ["plumbing", "rev-parse", "HEAD"],
            env=_repo_env(repo),
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        assert data["commit_id"] == "d" * 64

    def test_resolve_text_format(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        oid = _make_object(repo, b"data")
        _make_snapshot(repo, "s" * 64, {"f": oid})
        _make_commit(repo, "e" * 64, "s" * 64)
        _set_head(repo, "main", "e" * 64)

        result = runner.invoke(
            cli, ["plumbing", "rev-parse", "--format", "text", "main"],
            env=_repo_env(repo),
        )
        assert result.exit_code == 0, result.output
        assert result.stdout.strip() == "e" * 64

    def test_unknown_ref_errors(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        result = runner.invoke(
            cli, ["plumbing", "rev-parse", "nonexistent"],
            env=_repo_env(repo),
        )
        assert result.exit_code == ExitCode.USER_ERROR
        data = json.loads(result.stdout)
        assert data["commit_id"] is None


# ---------------------------------------------------------------------------
# ls-files
# ---------------------------------------------------------------------------


class TestLsFiles:
    def test_lists_files_json(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        oid = _make_object(repo, b"track data")
        _make_snapshot(repo, "s" * 64, {"tracks/drums.mid": oid})
        _make_commit(repo, "f" * 64, "s" * 64)
        _set_head(repo, "main", "f" * 64)

        result = runner.invoke(
            cli, ["plumbing", "ls-files"],
            env=_repo_env(repo),
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        assert data["file_count"] == 1
        assert data["files"][0]["path"] == "tracks/drums.mid"
        assert data["files"][0]["object_id"] == oid

    def test_lists_files_text(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        oid = _make_object(repo, b"data")
        _make_snapshot(repo, "s" * 64, {"a.txt": oid})
        _make_commit(repo, "a" * 64, "s" * 64)
        _set_head(repo, "main", "a" * 64)

        result = runner.invoke(
            cli, ["plumbing", "ls-files", "--format", "text"],
            env=_repo_env(repo),
        )
        assert result.exit_code == 0, result.output
        assert "a.txt" in result.stdout

    def test_with_explicit_commit(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        oid = _make_object(repo, b"data")
        _make_snapshot(repo, "s" * 64, {"x.mid": oid})
        _make_commit(repo, "1" * 64, "s" * 64)

        result = runner.invoke(
            cli, ["plumbing", "ls-files", "--commit", "1" * 64],
            env=_repo_env(repo),
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        assert data["commit_id"] == "1" * 64

    def test_no_commits_errors(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        result = runner.invoke(
            cli, ["plumbing", "ls-files"],
            env=_repo_env(repo),
        )
        assert result.exit_code == ExitCode.USER_ERROR


# ---------------------------------------------------------------------------
# read-commit
# ---------------------------------------------------------------------------


class TestReadCommit:
    def test_reads_commit_json(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        oid = _make_object(repo, b"data")
        _make_snapshot(repo, "s" * 64, {"f": oid})
        _make_commit(repo, "2" * 64, "s" * 64, message="my message")

        result = runner.invoke(
            cli, ["plumbing", "read-commit", "2" * 64],
            env=_repo_env(repo),
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        assert data["commit_id"] == "2" * 64
        assert data["message"] == "my message"
        assert data["snapshot_id"] == "s" * 64

    def test_missing_commit_errors(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        result = runner.invoke(
            cli, ["plumbing", "read-commit", "z" * 64],
            env=_repo_env(repo),
        )
        assert result.exit_code == ExitCode.USER_ERROR
        data = json.loads(result.stdout)
        assert "error" in data


# ---------------------------------------------------------------------------
# read-snapshot
# ---------------------------------------------------------------------------


class TestReadSnapshot:
    def test_reads_snapshot_json(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        oid = _make_object(repo, b"snap data")
        _make_snapshot(repo, "9" * 64, {"track.mid": oid})

        result = runner.invoke(
            cli, ["plumbing", "read-snapshot", "9" * 64],
            env=_repo_env(repo),
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        assert data["snapshot_id"] == "9" * 64
        assert data["file_count"] == 1
        assert "track.mid" in data["manifest"]

    def test_missing_snapshot_errors(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        result = runner.invoke(
            cli, ["plumbing", "read-snapshot", "nothere"],
            env=_repo_env(repo),
        )
        assert result.exit_code == ExitCode.USER_ERROR


# ---------------------------------------------------------------------------
# commit-tree
# ---------------------------------------------------------------------------


class TestCommitTree:
    def test_creates_commit_from_snapshot(self, tmp_path: pathlib.Path) -> None:
        import hashlib
        repo = _init_repo(tmp_path)
        oid = _make_object(repo, b"content")
        snap_id = hashlib.sha256(b"snapshot-1").hexdigest()
        _make_snapshot(repo, snap_id, {"file.txt": oid})

        result = runner.invoke(
            cli,
            [
                "plumbing", "commit-tree",
                "--snapshot", snap_id,
                "--message", "plumbing commit",
                "--author", "bot",
            ],
            env=_repo_env(repo),
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        assert "commit_id" in data
        assert len(data["commit_id"]) == 64

    def test_with_parent(self, tmp_path: pathlib.Path) -> None:
        import hashlib
        repo = _init_repo(tmp_path)
        oid = _make_object(repo, b"data")
        snap_id_1 = hashlib.sha256(b"snapshot-a").hexdigest()
        snap_id_2 = hashlib.sha256(b"snapshot-b").hexdigest()
        parent_id = hashlib.sha256(b"parent-commit").hexdigest()
        _make_snapshot(repo, snap_id_1, {"a": oid})
        _make_commit(repo, parent_id, snap_id_1)

        _make_snapshot(repo, snap_id_2, {"b": oid})
        result = runner.invoke(
            cli,
            [
                "plumbing", "commit-tree",
                "--snapshot", snap_id_2,
                "--parent", parent_id,
                "--message", "child",
            ],
            env=_repo_env(repo),
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        assert "commit_id" in data

    def test_missing_snapshot_errors(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        result = runner.invoke(
            cli,
            ["plumbing", "commit-tree", "--snapshot", "nosuch"],
            env=_repo_env(repo),
        )
        assert result.exit_code == ExitCode.USER_ERROR


# ---------------------------------------------------------------------------
# update-ref
# ---------------------------------------------------------------------------


class TestUpdateRef:
    def test_creates_branch_ref(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        oid = _make_object(repo, b"x")
        _make_snapshot(repo, "1" * 64, {"x": oid})
        _make_commit(repo, "3" * 64, "1" * 64)

        result = runner.invoke(
            cli,
            ["plumbing", "update-ref", "feature", "3" * 64],
            env=_repo_env(repo),
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        assert data["branch"] == "feature"
        assert data["commit_id"] == "3" * 64
        ref = repo / ".muse" / "refs" / "heads" / "feature"
        assert ref.read_text() == "3" * 64

    def test_updates_existing_ref(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        oid = _make_object(repo, b"y")
        _make_snapshot(repo, "1" * 64, {"y": oid})
        _make_commit(repo, "4" * 64, "1" * 64)
        _make_commit(repo, "5" * 64, "1" * 64, parent_commit_id="4" * 64)
        _set_head(repo, "main", "4" * 64)

        result = runner.invoke(
            cli,
            ["plumbing", "update-ref", "main", "5" * 64],
            env=_repo_env(repo),
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        assert data["previous"] == "4" * 64
        assert data["commit_id"] == "5" * 64

    def test_delete_ref(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        _set_head(repo, "todelete", "x" * 64)
        result = runner.invoke(
            cli,
            ["plumbing", "update-ref", "--delete", "todelete"],
            env=_repo_env(repo),
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        assert data["deleted"] is True
        ref = repo / ".muse" / "refs" / "heads" / "todelete"
        assert not ref.exists()

    def test_verify_commit_not_found_errors(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        result = runner.invoke(
            cli,
            ["plumbing", "update-ref", "main", "0" * 64],
            env=_repo_env(repo),
        )
        assert result.exit_code == ExitCode.USER_ERROR

    def test_no_verify_skips_commit_check(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        result = runner.invoke(
            cli,
            ["plumbing", "update-ref", "--no-verify", "feature", "9" * 64],
            env=_repo_env(repo),
        )
        assert result.exit_code == 0, result.output
        ref = repo / ".muse" / "refs" / "heads" / "feature"
        assert ref.read_text() == "9" * 64


# ---------------------------------------------------------------------------
# commit-graph
# ---------------------------------------------------------------------------


class TestCommitGraph:
    def test_linear_graph(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        oid = _make_object(repo, b"data")
        _make_snapshot(repo, "1" * 64, {"f": oid})
        _make_commit(repo, "c1" + "0" * 62, "1" * 64, message="first")
        _make_commit(
            repo,
            "c2" + "0" * 62,
            "1" * 64,
            message="second",
            parent_commit_id="c1" + "0" * 62,
        )
        _set_head(repo, "main", "c2" + "0" * 62)

        result = runner.invoke(
            cli, ["plumbing", "commit-graph"],
            env=_repo_env(repo),
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        assert data["count"] == 2
        commit_ids = [c["commit_id"] for c in data["commits"]]
        assert "c2" + "0" * 62 in commit_ids
        assert "c1" + "0" * 62 in commit_ids

    def test_text_format(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        oid = _make_object(repo, b"data")
        _make_snapshot(repo, "1" * 64, {"f": oid})
        _make_commit(repo, "e1" + "0" * 62, "1" * 64)
        _set_head(repo, "main", "e1" + "0" * 62)

        result = runner.invoke(
            cli, ["plumbing", "commit-graph", "--format", "text"],
            env=_repo_env(repo),
        )
        assert result.exit_code == 0, result.output
        assert "e1" + "0" * 62 in result.stdout

    def test_explicit_tip(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        oid = _make_object(repo, b"data")
        _make_snapshot(repo, "1" * 64, {"f": oid})
        _make_commit(repo, "t1" + "0" * 62, "1" * 64)

        result = runner.invoke(
            cli, ["plumbing", "commit-graph", "--tip", "t1" + "0" * 62],
            env=_repo_env(repo),
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        assert data["tip"] == "t1" + "0" * 62

    def test_no_commits_errors(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        result = runner.invoke(
            cli, ["plumbing", "commit-graph"],
            env=_repo_env(repo),
        )
        assert result.exit_code == ExitCode.USER_ERROR


# ---------------------------------------------------------------------------
# pack-objects
# ---------------------------------------------------------------------------


class TestPackObjects:
    def test_packs_head(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        oid = _make_object(repo, b"pack me")
        _make_snapshot(repo, "1" * 64, {"f.mid": oid})
        _make_commit(repo, "p" + "0" * 63, "1" * 64)
        _set_head(repo, "main", "p" + "0" * 63)

        result = runner.invoke(
            cli, ["plumbing", "pack-objects", "HEAD"],
            env=_repo_env(repo),
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        assert "commits" in data
        assert len(data["commits"]) >= 1

    def test_packs_explicit_commit(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        oid = _make_object(repo, b"explicit")
        _make_snapshot(repo, "1" * 64, {"g": oid})
        commit_id = "q" + "0" * 63
        _make_commit(repo, commit_id, "1" * 64)

        result = runner.invoke(
            cli, ["plumbing", "pack-objects", commit_id],
            env=_repo_env(repo),
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        commit_ids = [c["commit_id"] for c in data["commits"]]
        assert commit_id in commit_ids

    def test_no_commits_on_head_errors(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        result = runner.invoke(
            cli, ["plumbing", "pack-objects", "HEAD"],
            env=_repo_env(repo),
        )
        assert result.exit_code == ExitCode.USER_ERROR


# ---------------------------------------------------------------------------
# unpack-objects
# ---------------------------------------------------------------------------


class TestUnpackObjects:
    def test_unpacks_valid_bundle(self, tmp_path: pathlib.Path) -> None:
        source = _init_repo(tmp_path / "src")
        dest = _init_repo(tmp_path / "dst")

        oid = _make_object(source, b"unpack me")
        _make_snapshot(source, "1" * 64, {"h.mid": oid})
        commit_id = "u" + "0" * 63
        _make_commit(source, commit_id, "1" * 64)

        bundle = build_pack(source, [commit_id])
        bundle_json = json.dumps(bundle)

        result = runner.invoke(
            cli,
            ["plumbing", "unpack-objects"],
            input=bundle_json,
            env=_repo_env(dest),
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        assert "objects_written" in data
        assert data["commits_written"] == 1
        assert data["objects_written"] == 1

    def test_invalid_json_errors(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        result = runner.invoke(
            cli,
            ["plumbing", "unpack-objects"],
            input="NOT_VALID_JSON",
            env=_repo_env(repo),
        )
        assert result.exit_code == ExitCode.USER_ERROR

    def test_idempotent_unpack(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        oid = _make_object(repo, b"idempotent")
        _make_snapshot(repo, "1" * 64, {"i.txt": oid})
        commit_id = "i" + "0" * 63
        _make_commit(repo, commit_id, "1" * 64)

        bundle = build_pack(repo, [commit_id])
        bundle_json = json.dumps(bundle)

        result1 = runner.invoke(
            cli, ["plumbing", "unpack-objects"],
            input=bundle_json,
            env=_repo_env(repo),
        )
        assert result1.exit_code == 0, result1.output

        result2 = runner.invoke(
            cli, ["plumbing", "unpack-objects"],
            input=bundle_json,
            env=_repo_env(repo),
        )
        assert result2.exit_code == 0, result2.output
        data = json.loads(result2.stdout)
        assert data["objects_written"] == 0
        assert data["objects_skipped"] == 1


# ---------------------------------------------------------------------------
# ls-remote (moved to plumbing namespace)
# ---------------------------------------------------------------------------


class TestLsRemote:
    def test_bare_url_transport_error(self) -> None:
        """Bare URL to a non-existent server produces exit code INTERNAL_ERROR."""
        result = runner.invoke(
            cli,
            ["plumbing", "ls-remote", "https://localhost:0/no-such-server"],
        )
        assert result.exit_code == ExitCode.INTERNAL_ERROR

    def test_non_url_non_remote_errors(self, tmp_path: pathlib.Path) -> None:
        """A non-URL, non-configured remote name exits with code USER_ERROR."""
        repo = _init_repo(tmp_path)
        result = runner.invoke(
            cli,
            ["plumbing", "ls-remote", "not-a-url-or-remote"],
            env=_repo_env(repo),
        )
        assert result.exit_code == ExitCode.USER_ERROR

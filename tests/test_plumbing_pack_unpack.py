"""Tests for ``muse plumbing pack-objects`` and ``muse plumbing unpack-objects``.

Covers: single-commit pack, HEAD expansion, ``--have`` pruning, pack-unpack
round-trip (idempotent), invalid-JSON stdin rejection, empty stdin, JSON output
schema, counts reported by unpack-objects, and a stress round-trip with 50
commits and 50 objects.
"""

from __future__ import annotations

import base64
import datetime
import hashlib
import json
import pathlib
import sys

import pytest
from typer.testing import CliRunner

from muse.cli.app import cli
from muse.core.errors import ExitCode
from muse.core.object_store import write_object
from muse.core.store import CommitRecord, SnapshotRecord, write_commit, write_snapshot

runner = CliRunner()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sha(tag: str) -> str:
    return hashlib.sha256(tag.encode()).hexdigest()


def _sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


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


def _snap(repo: pathlib.Path, manifest: dict[str, str] | None = None, tag: str = "s") -> str:
    m = manifest or {}
    sid = _sha(f"snap-{tag}-{sorted(m.items())}")
    write_snapshot(
        repo,
        SnapshotRecord(
            snapshot_id=sid,
            manifest=m,
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


def _obj(repo: pathlib.Path, content: bytes) -> str:
    oid = _sha_bytes(content)
    write_object(repo, oid, content)
    return oid


def _pack(repo: pathlib.Path, cid: str) -> str:
    """Run pack-objects for a single commit and return the raw JSON bundle."""
    result = runner.invoke(cli, ["plumbing", "pack-objects", cid], env=_env(repo))
    assert result.exit_code == 0, result.output
    return result.stdout


def _unpack(repo: pathlib.Path, bundle_json: str) -> dict[str, int]:
    result = runner.invoke(
        cli, ["plumbing", "unpack-objects"], input=bundle_json, env=_env(repo)
    )
    assert result.exit_code == 0, result.output
    parsed: dict[str, int] = json.loads(result.stdout)
    return parsed


# ---------------------------------------------------------------------------
# Unit: pack-objects validation
# ---------------------------------------------------------------------------


class TestPackObjectsUnit:
    def test_head_resolves_correctly(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        sid = _snap(repo)
        cid = _commit(repo, "head-test", sid)
        result = runner.invoke(cli, ["plumbing", "pack-objects", "HEAD"], env=_env(repo))
        assert result.exit_code == 0, result.output
        bundle = json.loads(result.stdout)
        assert any(c["commit_id"] == cid for c in bundle.get("commits", []))

    def test_head_on_empty_branch_exits_user_error(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        result = runner.invoke(cli, ["plumbing", "pack-objects", "HEAD"], env=_env(repo))
        assert result.exit_code == ExitCode.USER_ERROR


# ---------------------------------------------------------------------------
# Integration: pack schema
# ---------------------------------------------------------------------------


class TestPackObjectsSchema:
    def test_bundle_has_commits_snapshots_objects_keys(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        sid = _snap(repo)
        cid = _commit(repo, "schema", sid)
        result = runner.invoke(cli, ["plumbing", "pack-objects", cid], env=_env(repo))
        assert result.exit_code == 0
        bundle = json.loads(result.stdout)
        assert "commits" in bundle
        assert "snapshots" in bundle
        assert "objects" in bundle

    def test_objects_are_base64_encoded(self, tmp_path: pathlib.Path) -> None:
        content = b"hello object"
        repo = _init_repo(tmp_path)
        oid = _obj(repo, content)
        sid = _snap(repo, {"f.mid": oid})
        cid = _commit(repo, "obj-base64", sid)
        result = runner.invoke(cli, ["plumbing", "pack-objects", cid], env=_env(repo))
        assert result.exit_code == 0
        bundle = json.loads(result.stdout)
        obj_entry = next(o for o in bundle["objects"] if o["object_id"] == oid)
        decoded = base64.b64decode(obj_entry["content_b64"])
        assert decoded == content

    def test_bundle_commit_record_present(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        sid = _snap(repo)
        cid = _commit(repo, "bundled", sid)
        result = runner.invoke(cli, ["plumbing", "pack-objects", cid], env=_env(repo))
        assert result.exit_code == 0
        bundle = json.loads(result.stdout)
        commit_ids = [c["commit_id"] for c in bundle["commits"]]
        assert cid in commit_ids


# ---------------------------------------------------------------------------
# Integration: --have pruning
# ---------------------------------------------------------------------------


class TestPackObjectsHave:
    def test_have_prunes_ancestor_commits(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        sid = _snap(repo)
        c0 = _commit(repo, "c0", sid)
        c1 = _commit(repo, "c1", sid, parent=c0)
        # Pack c1 but tell the remote it already has c0.
        result = runner.invoke(
            cli, ["plumbing", "pack-objects", "--have", c0, c1], env=_env(repo)
        )
        assert result.exit_code == 0
        bundle = json.loads(result.stdout)
        commit_ids = {c["commit_id"] for c in bundle.get("commits", [])}
        assert c1 in commit_ids
        assert c0 not in commit_ids


# ---------------------------------------------------------------------------
# Integration: unpack-objects
# ---------------------------------------------------------------------------


class TestUnpackObjects:
    def test_unpack_returns_count_dict(self, tmp_path: pathlib.Path) -> None:
        src = _init_repo(tmp_path / "src")
        dst = _init_repo(tmp_path / "dst")
        sid = _snap(src)
        cid = _commit(src, "to-unpack", sid)
        bundle = _pack(src, cid)
        counts = _unpack(dst, bundle)
        assert "commits_written" in counts
        assert "snapshots_written" in counts
        assert "objects_written" in counts
        assert "objects_skipped" in counts

    def test_round_trip_commit_appears_in_dst_store(self, tmp_path: pathlib.Path) -> None:
        from muse.core.store import read_commit

        src = _init_repo(tmp_path / "src")
        dst = _init_repo(tmp_path / "dst")
        sid = _snap(src)
        cid = _commit(src, "round-trip", sid)
        bundle = _pack(src, cid)
        _unpack(dst, bundle)
        assert read_commit(dst, cid) is not None

    def test_round_trip_snapshot_appears_in_dst(self, tmp_path: pathlib.Path) -> None:
        from muse.core.store import read_snapshot

        src = _init_repo(tmp_path / "src")
        dst = _init_repo(tmp_path / "dst")
        sid = _snap(src)
        cid = _commit(src, "snap-rt", sid)
        bundle = _pack(src, cid)
        _unpack(dst, bundle)
        assert read_snapshot(dst, sid) is not None

    def test_round_trip_objects_present_in_dst(self, tmp_path: pathlib.Path) -> None:
        from muse.core.object_store import has_object

        src = _init_repo(tmp_path / "src")
        dst = _init_repo(tmp_path / "dst")
        oid = _obj(src, b"transferable blob")
        sid = _snap(src, {"f.mid": oid})
        cid = _commit(src, "obj-rt", sid)
        bundle = _pack(src, cid)
        _unpack(dst, bundle)
        assert has_object(dst, oid)

    def test_unpack_idempotent_second_application(self, tmp_path: pathlib.Path) -> None:
        src = _init_repo(tmp_path / "src")
        dst = _init_repo(tmp_path / "dst")
        sid = _snap(src)
        cid = _commit(src, "idempotent", sid)
        bundle = _pack(src, cid)
        counts1 = _unpack(dst, bundle)
        counts2 = _unpack(dst, bundle)
        # Second unpack: commits/snapshots already exist, nothing extra written.
        assert counts1["commits_written"] == 1
        assert counts2["commits_written"] == 0

    def test_invalid_json_stdin_exits_user_error(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        result = runner.invoke(
            cli, ["plumbing", "unpack-objects"], input="NOT JSON!", env=_env(repo)
        )
        assert result.exit_code == ExitCode.USER_ERROR

    def test_empty_bundle_unpacks_cleanly(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        empty = json.dumps({"commits": [], "snapshots": [], "objects": [], "branch_heads": {}})
        counts = _unpack(repo, empty)
        assert counts["commits_written"] == 0
        assert counts["objects_written"] == 0


# ---------------------------------------------------------------------------
# Stress: 50-commit round-trip
# ---------------------------------------------------------------------------


class TestPackUnpackStress:
    def test_50_commit_chain_round_trip(self, tmp_path: pathlib.Path) -> None:
        from muse.core.store import read_commit

        src = _init_repo(tmp_path / "src")
        dst = _init_repo(tmp_path / "dst")
        sid = _snap(src)
        parent: str | None = None
        cids: list[str] = []
        for i in range(50):
            cid = _commit(src, f"c{i}", sid, parent=parent)
            cids.append(cid)
            parent = cid

        bundle_json = runner.invoke(
            cli, ["plumbing", "pack-objects", cids[-1]], env=_env(src)
        ).stdout
        counts = _unpack(dst, bundle_json)
        assert counts["commits_written"] == 50

        # All 50 commits readable in destination.
        for cid in cids:
            assert read_commit(dst, cid) is not None

    def test_50_object_round_trip(self, tmp_path: pathlib.Path) -> None:
        from muse.core.object_store import has_object

        src = _init_repo(tmp_path / "src")
        dst = _init_repo(tmp_path / "dst")
        oids = [_obj(src, f"blob-{i}".encode()) for i in range(50)]
        manifest = {f"f{i}.mid": oids[i] for i in range(50)}
        sid = _snap(src, manifest)
        cid = _commit(src, "50-objs", sid)
        bundle_json = runner.invoke(
            cli, ["plumbing", "pack-objects", cid], env=_env(src)
        ).stdout
        counts = _unpack(dst, bundle_json)
        assert counts["objects_written"] == 50
        for oid in oids:
            assert has_object(dst, oid)

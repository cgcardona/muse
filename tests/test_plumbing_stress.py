"""Stress and scale tests for the Muse plumbing layer.

These tests exercise plumbing commands at a scale that would reveal
O(n²) performance regressions, memory leaks, and missing edge-case
handling. Every test in this module is designed to complete in under
10 seconds on a modern laptop when running from an in-memory temp
directory — if any test consistently takes longer, it signals a
performance regression worth investigating.

Scenarios:
- commit-graph BFS on a 500-commit linear history
- merge-base on a 300-deep dag (shared ancestor at the root)
- name-rev multi-source BFS on a 200-commit diamond graph
- snapshot-diff on manifests with 2000 files each
- verify-object on 200 objects
- ls-files on a 2000-file snapshot
- for-each-ref on 100 branches
- show-ref on 100 branches
- pack-objects → unpack-objects with 100 commits and 100 objects
- read-commit on 200 sequential commits
"""

from __future__ import annotations

import datetime
import hashlib
import json
import pathlib

from typer.testing import CliRunner

from muse.cli.app import cli
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
        json.dumps({"repo_id": "stress-repo", "domain": "midi"}), encoding="utf-8"
    )
    return path


def _env(repo: pathlib.Path) -> dict[str, str]:
    return {"MUSE_REPO_ROOT": str(repo)}


def _snap(repo: pathlib.Path, manifest: dict[str, str] | None = None, tag: str = "s") -> str:
    m = manifest or {}
    sid = _sha(f"snap-{tag}")
    write_snapshot(
        repo,
        SnapshotRecord(
            snapshot_id=sid,
            manifest=m,
            created_at=datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc),
        ),
    )
    return sid


def _commit_raw(
    repo: pathlib.Path,
    cid: str,
    sid: str,
    message: str,
    branch: str = "main",
    parent: str | None = None,
    parent2: str | None = None,
) -> None:
    write_commit(
        repo,
        CommitRecord(
            commit_id=cid,
            repo_id="stress-repo",
            branch=branch,
            snapshot_id=sid,
            message=message,
            committed_at=datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc),
            author="stress-tester",
            parent_commit_id=parent,
            parent2_commit_id=parent2,
        ),
    )


def _set_branch(repo: pathlib.Path, branch: str, cid: str) -> None:
    ref = repo / ".muse" / "refs" / "heads" / branch
    ref.parent.mkdir(parents=True, exist_ok=True)
    ref.write_text(cid, encoding="utf-8")


def _linear_chain(repo: pathlib.Path, n: int, sid: str, branch: str = "main") -> list[str]:
    """Build a linear chain of n commits. Returns list root→tip."""
    cids: list[str] = []
    parent: str | None = None
    for i in range(n):
        cid = _sha(f"linear-{branch}-{i}")
        _commit_raw(repo, cid, sid, f"commit {i}", branch=branch, parent=parent)
        cids.append(cid)
        parent = cid
    _set_branch(repo, branch, cids[-1])
    return cids


def _obj(repo: pathlib.Path, tag: str) -> str:
    content = tag.encode()
    oid = _sha_bytes(content)
    write_object(repo, oid, content)
    return oid


# ---------------------------------------------------------------------------
# Stress: commit-graph
# ---------------------------------------------------------------------------


class TestCommitGraphStress:
    def test_500_commit_linear_chain_full_traversal(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        sid = _snap(repo)
        cids = _linear_chain(repo, 500, sid)
        result = runner.invoke(cli, ["plumbing", "commit-graph"], env=_env(repo))
        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        assert data["count"] == 500
        assert data["truncated"] is False

    def test_500_commit_chain_stop_at_midpoint(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        sid = _snap(repo)
        cids = _linear_chain(repo, 500, sid)
        result = runner.invoke(
            cli,
            ["plumbing", "commit-graph", "--tip", cids[499], "--stop-at", cids[249]],
            env=_env(repo),
        )
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["count"] == 250

    def test_count_flag_on_500_commits(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        sid = _snap(repo)
        _linear_chain(repo, 500, sid)
        result = runner.invoke(cli, ["plumbing", "commit-graph", "--count"], env=_env(repo))
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["count"] == 500
        assert "commits" not in data  # --count suppresses node list


# ---------------------------------------------------------------------------
# Stress: merge-base
# ---------------------------------------------------------------------------


class TestMergeBaseStress:
    def test_merge_base_300_deep_shared_root(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        sid = _snap(repo)

        # Shared root
        root_cid = _sha("shared-root")
        _commit_raw(repo, root_cid, sid, "root")

        # Two 150-commit chains from the same root
        main_chain = [root_cid]
        feat_chain = [root_cid]
        for i in range(150):
            mc = _sha(f"main-{i}")
            _commit_raw(repo, mc, sid, f"main-{i}", branch="main", parent=main_chain[-1])
            main_chain.append(mc)
            fc = _sha(f"feat-{i}")
            _commit_raw(repo, fc, sid, f"feat-{i}", branch="feat", parent=feat_chain[-1])
            feat_chain.append(fc)

        _set_branch(repo, "main", main_chain[-1])
        _set_branch(repo, "feat", feat_chain[-1])
        (repo / ".muse" / "HEAD").write_text("ref: refs/heads/main", encoding="utf-8")

        result = runner.invoke(
            cli, ["plumbing", "merge-base", "main", "feat"], env=_env(repo)
        )
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["merge_base"] == root_cid


# ---------------------------------------------------------------------------
# Stress: name-rev
# ---------------------------------------------------------------------------


class TestNameRevStress:
    def test_name_rev_200_commit_chain_all_named(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        sid = _snap(repo)
        cids = _linear_chain(repo, 200, sid)

        result = runner.invoke(cli, ["plumbing", "name-rev", *cids], env=_env(repo))
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert len(data["results"]) == 200
        for entry in data["results"]:
            assert not entry["undefined"]

    def test_name_rev_tip_has_no_tilde_suffix(self, tmp_path: pathlib.Path) -> None:
        """distance=0 means the tip is the branch tip itself; name is bare branch name."""
        repo = _init_repo(tmp_path)
        sid = _snap(repo)
        cids = _linear_chain(repo, 10, sid)
        tip = cids[-1]

        result = runner.invoke(cli, ["plumbing", "name-rev", tip], env=_env(repo))
        assert result.exit_code == 0
        entry = json.loads(result.stdout)["results"][0]
        # name-rev emits "<branch>" (no ~0) for the exact branch tip.
        assert entry["name"] == "main"
        assert entry["distance"] == 0


# ---------------------------------------------------------------------------
# Stress: snapshot-diff
# ---------------------------------------------------------------------------


class TestSnapshotDiffStress:
    def test_diff_2000_file_manifests(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        oid = _sha("shared-blob")

        # Manifest A: 2000 files
        manifest_a = {f"track_{i:04d}.mid": oid for i in range(2000)}
        # Manifest B: same 2000 files but first 200 have new IDs (modified)
        new_oid = _sha("new-blob")
        manifest_b = {f"track_{i:04d}.mid": (new_oid if i < 200 else oid) for i in range(2000)}

        sid_a = _sha("big-snap-a")
        sid_b = _sha("big-snap-b")
        write_snapshot(
            repo,
            SnapshotRecord(
                snapshot_id=sid_a,
                manifest=manifest_a,
                created_at=datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc),
            ),
        )
        write_snapshot(
            repo,
            SnapshotRecord(
                snapshot_id=sid_b,
                manifest=manifest_b,
                created_at=datetime.datetime(2026, 1, 2, tzinfo=datetime.timezone.utc),
            ),
        )

        result = runner.invoke(cli, ["plumbing", "snapshot-diff", sid_a, sid_b], env=_env(repo))
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["total_changes"] == 200
        assert len(data["modified"]) == 200
        assert data["added"] == []
        assert data["deleted"] == []


# ---------------------------------------------------------------------------
# Stress: verify-object
# ---------------------------------------------------------------------------


class TestVerifyObjectStress:
    def test_200_objects_all_verified(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        oids = [_obj(repo, f"stress-obj-{i}") for i in range(200)]
        result = runner.invoke(cli, ["plumbing", "verify-object", *oids], env=_env(repo))
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["all_ok"] is True
        assert data["checked"] == 200
        assert data["failed"] == 0

    def test_verify_1mib_object_no_crash(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        content = b"Z" * (1024 * 1024)
        oid = _sha_bytes(content)
        write_object(repo, oid, content)
        result = runner.invoke(cli, ["plumbing", "verify-object", oid], env=_env(repo))
        assert result.exit_code == 0
        assert json.loads(result.stdout)["all_ok"] is True


# ---------------------------------------------------------------------------
# Stress: ls-files
# ---------------------------------------------------------------------------


class TestLsFilesStress:
    def test_ls_files_2000_file_snapshot(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        oid = _sha("common-oid")
        manifest = {f"track_{i:04d}.mid": oid for i in range(2000)}
        sid = _snap(repo, manifest, "big")
        cid = _sha("big-commit")
        _commit_raw(repo, cid, sid, "big manifest", branch="main")
        _set_branch(repo, "main", cid)

        result = runner.invoke(cli, ["plumbing", "ls-files"], env=_env(repo))
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["file_count"] == 2000


# ---------------------------------------------------------------------------
# Stress: for-each-ref and show-ref
# ---------------------------------------------------------------------------


class TestRefCommandsStress:
    def _build_100_branches(self, repo: pathlib.Path) -> None:
        sid = _snap(repo, tag="multi-branch")
        for i in range(100):
            branch = f"feature-{i:03d}"
            cid = _sha(f"branch-tip-{i}")
            _commit_raw(repo, cid, sid, f"tip of {branch}", branch=branch)
            _set_branch(repo, branch, cid)

    def test_for_each_ref_100_branches(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        self._build_100_branches(repo)
        result = runner.invoke(cli, ["plumbing", "for-each-ref"], env=_env(repo))
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert len(data["refs"]) == 100

    def test_show_ref_100_branches(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        self._build_100_branches(repo)
        result = runner.invoke(cli, ["plumbing", "show-ref"], env=_env(repo))
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["count"] == 100

    def test_for_each_ref_pattern_filter_on_100(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        self._build_100_branches(repo)
        result = runner.invoke(
            cli,
            ["plumbing", "for-each-ref", "--pattern", "refs/heads/feature-00*"],
            env=_env(repo),
        )
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        # feature-000 through feature-009 = 10 branches
        assert len(data["refs"]) == 10


# ---------------------------------------------------------------------------
# Stress: pack-objects → unpack-objects
# ---------------------------------------------------------------------------


class TestPackUnpackStress:
    def test_100_commit_100_object_round_trip(self, tmp_path: pathlib.Path) -> None:
        from muse.core.object_store import has_object
        from muse.core.store import read_commit

        src = _init_repo(tmp_path / "src")
        dst = _init_repo(tmp_path / "dst")

        # Build 100 objects
        oids = [_obj(src, f"blob-{i}") for i in range(100)]
        manifest = {f"f{i}.mid": oids[i] for i in range(100)}
        sid = _snap(src, manifest, "big-pack")

        # Build 100-commit linear chain referencing that snapshot
        parent: str | None = None
        cids: list[str] = []
        for i in range(100):
            cid = _sha(f"pack-commit-{i}")
            _commit_raw(src, cid, sid, f"pack-{i}", parent=parent)
            cids.append(cid)
            parent = cid
        _set_branch(src, "main", cids[-1])

        # Pack tip → unpack into dst
        pack_result = runner.invoke(
            cli, ["plumbing", "pack-objects", cids[-1]], env=_env(src)
        )
        assert pack_result.exit_code == 0

        unpack_result = runner.invoke(
            cli,
            ["plumbing", "unpack-objects"],
            input=pack_result.stdout,
            env=_env(dst),
        )
        assert unpack_result.exit_code == 0
        counts = json.loads(unpack_result.stdout)
        assert counts["commits_written"] == 100
        assert counts["objects_written"] == 100

        for cid in cids:
            assert read_commit(dst, cid) is not None
        for oid in oids:
            assert has_object(dst, oid)


# ---------------------------------------------------------------------------
# Stress: read-commit sequential
# ---------------------------------------------------------------------------


class TestReadCommitStress:
    def test_200_commits_all_readable(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        sid = _snap(repo)
        cids = _linear_chain(repo, 200, sid)
        for cid in cids:
            result = runner.invoke(cli, ["plumbing", "read-commit", cid], env=_env(repo))
            assert result.exit_code == 0
            data = json.loads(result.stdout)
            assert data["commit_id"] == cid

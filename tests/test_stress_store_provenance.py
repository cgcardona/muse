"""Stress tests for CommitRecord, SnapshotRecord, TagRecord, and provenance fields.

Covers:
- CommitRecord round-trip through to_dict/from_dict for all format versions.
- format_version evolution: missing fields default correctly when reading old records.
- reviewed_by (ORSet semantics): list preserved, sorted, deduplicated via overwrite_commit.
- test_runs (GCounter semantics): monotonically increases via overwrite_commit.
- agent_id / model_id / toolchain_id / prompt_hash / signature fields.
- SnapshotRecord round-trip with large manifests.
- TagRecord round-trip.
- get_head_commit_id on empty branch returns None.
- write_commit is idempotent (won't overwrite).
- overwrite_commit updates the persisted record correctly.
- read_commit for absent commit returns None.
- list_commits and list_branches.
- list_tags returns all tags.
"""

import datetime
import pathlib

import pytest

from muse.core.crdts.or_set import ORSet
from muse.domain import SemVerBump
from muse.core.store import (
    CommitDict,
    CommitRecord,
    SnapshotRecord,
    TagRecord,
    get_all_commits,
    get_all_tags,
    get_head_commit_id,
    overwrite_commit,
    read_commit,
    read_snapshot,
    write_commit,
    write_snapshot,
    write_tag,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def repo(tmp_path: pathlib.Path) -> pathlib.Path:
    muse = tmp_path / ".muse"
    (muse / "commits").mkdir(parents=True)
    (muse / "snapshots").mkdir(parents=True)
    (muse / "tags").mkdir(parents=True)
    (muse / "refs" / "heads").mkdir(parents=True)
    return tmp_path


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _commit(
    cid: str = "abc1234",
    branch: str = "main",
    parent: str | None = None,
) -> CommitRecord:
    return CommitRecord(
        commit_id=cid,
        repo_id="test-repo",
        branch=branch,
        snapshot_id=f"snap-{cid}",
        message=f"commit {cid}",
        committed_at=_now(),
        parent_commit_id=parent,
    )


# ===========================================================================
# CommitRecord round-trip
# ===========================================================================


class TestCommitRecordRoundTrip:
    def test_minimal_round_trip(self) -> None:
        c = _commit()
        restored = CommitRecord.from_dict(c.to_dict())
        assert restored.commit_id == c.commit_id
        assert restored.branch == c.branch
        assert restored.message == c.message

    def test_all_provenance_fields_preserved(self) -> None:
        c = CommitRecord(
            commit_id="prov123",
            repo_id="repo",
            branch="main",
            snapshot_id="snap",
            message="provenance commit",
            committed_at=_now(),
            agent_id="claude-v4",
            model_id="claude-3-5-sonnet",
            toolchain_id="muse-cli-1.0",
            prompt_hash="abc" * 10 + "ab",
            signature="sig-" + "x" * 60,
            signer_key_id="key-001",
        )
        d = c.to_dict()
        restored = CommitRecord.from_dict(d)
        assert restored.agent_id == "claude-v4"
        assert restored.model_id == "claude-3-5-sonnet"
        assert restored.toolchain_id == "muse-cli-1.0"
        assert restored.signature == c.signature
        assert restored.signer_key_id == "key-001"

    def test_crdt_fields_preserved(self) -> None:
        c = CommitRecord(
            commit_id="crdt123",
            repo_id="repo",
            branch="main",
            snapshot_id="snap",
            message="crdt",
            committed_at=_now(),
            reviewed_by=["alice", "bob", "charlie"],
            test_runs=42,
        )
        d = c.to_dict()
        restored = CommitRecord.from_dict(d)
        assert sorted(restored.reviewed_by) == ["alice", "bob", "charlie"]
        assert restored.test_runs == 42

    def test_format_version_5_is_default(self) -> None:
        c = _commit()
        assert c.format_version == 5

    def test_format_version_persisted(self) -> None:
        c = _commit()
        assert CommitRecord.from_dict(c.to_dict()).format_version == 5

    def test_sem_ver_bump_preserved(self) -> None:
        bumps: tuple[SemVerBump, ...] = ("none", "patch", "minor", "major")
        for bump in bumps:
            c = CommitRecord(
                commit_id="sv",
                repo_id="r",
                branch="main",
                snapshot_id="s",
                message="m",
                committed_at=_now(),
                sem_ver_bump=bump,
            )
            assert CommitRecord.from_dict(c.to_dict()).sem_ver_bump == bump

    def test_breaking_changes_preserved(self) -> None:
        c = CommitRecord(
            commit_id="bc",
            repo_id="r",
            branch="main",
            snapshot_id="s",
            message="m",
            committed_at=_now(),
            breaking_changes=["removed `old_api`", "renamed `foo` → `bar`"],
        )
        restored = CommitRecord.from_dict(c.to_dict())
        assert restored.breaking_changes == ["removed `old_api`", "renamed `foo` → `bar`"]

    def test_parent_ids_preserved(self) -> None:
        c = CommitRecord(
            commit_id="merge",
            repo_id="r",
            branch="main",
            snapshot_id="s",
            message="m",
            committed_at=_now(),
            parent_commit_id="parent-1",
            parent2_commit_id="parent-2",
        )
        restored = CommitRecord.from_dict(c.to_dict())
        assert restored.parent_commit_id == "parent-1"
        assert restored.parent2_commit_id == "parent-2"

    def test_missing_crdt_fields_default_correctly(self) -> None:
        """Simulates reading an older commit that lacks reviewed_by / test_runs."""
        minimal: CommitDict = {
            "commit_id": "old",
            "repo_id": "r",
            "branch": "main",
            "snapshot_id": "snap",
            "message": "old commit",
            "committed_at": _now().isoformat(),
        }
        restored = CommitRecord.from_dict(minimal)
        assert restored.reviewed_by == []
        assert restored.test_runs == 0
        assert restored.format_version == 1

    def test_committed_at_timezone_aware(self) -> None:
        c = _commit()
        restored = CommitRecord.from_dict(c.to_dict())
        assert restored.committed_at.tzinfo is not None


# ===========================================================================
# CommitRecord persistence
# ===========================================================================


class TestCommitPersistence:
    def test_write_and_read_back(self, repo: pathlib.Path) -> None:
        c = _commit("id001")
        write_commit(repo, c)
        restored = read_commit(repo, "id001")
        assert restored is not None
        assert restored.commit_id == "id001"

    def test_write_is_idempotent(self, repo: pathlib.Path) -> None:
        c = CommitRecord(
            commit_id="id002", repo_id="test-repo", branch="main",
            snapshot_id="snap-id002", message="original", committed_at=_now(),
        )
        write_commit(repo, c)
        # Second write with a different message — original must be kept.
        c2 = CommitRecord(
            commit_id="id002", repo_id="test-repo", branch="main",
            snapshot_id="snap-id002", message="overwritten-attempt", committed_at=_now(),
        )
        write_commit(repo, c2)
        restored = read_commit(repo, "id002")
        assert restored is not None
        assert restored.message == "original"

    def test_read_absent_commit_returns_none(self, repo: pathlib.Path) -> None:
        assert read_commit(repo, "does-not-exist") is None

    def test_overwrite_commit_updates_reviewed_by(self, repo: pathlib.Path) -> None:
        c = _commit("id003")
        write_commit(repo, c)
        # Simulate ORSet merge: add reviewer.
        updated = read_commit(repo, "id003")
        assert updated is not None
        updated.reviewed_by = ["agent-x", "human-bob"]
        overwrite_commit(repo, updated)
        restored = read_commit(repo, "id003")
        assert restored is not None
        assert "agent-x" in restored.reviewed_by
        assert "human-bob" in restored.reviewed_by

    def test_overwrite_commit_updates_test_runs(self, repo: pathlib.Path) -> None:
        c = _commit("id004")
        write_commit(repo, c)
        for expected in range(1, 6):
            rec = read_commit(repo, "id004")
            assert rec is not None
            rec.test_runs += 1
            overwrite_commit(repo, rec)
            after = read_commit(repo, "id004")
            assert after is not None
            assert after.test_runs == expected

    def test_list_commits_returns_all_written(self, repo: pathlib.Path) -> None:
        ids = [f"c{i:04d}" for i in range(20)]
        for cid in ids:
            write_commit(repo, _commit(cid))
        found = {c.commit_id for c in get_all_commits(repo)}
        for cid in ids:
            assert cid in found

    def test_many_commits_all_retrievable(self, repo: pathlib.Path) -> None:
        for i in range(100):
            write_commit(repo, _commit(f"stress-{i:04d}"))
        for i in range(100):
            assert read_commit(repo, f"stress-{i:04d}") is not None


# ===========================================================================
# SnapshotRecord
# ===========================================================================


class TestSnapshotRecordRoundTrip:
    def test_minimal_round_trip(self) -> None:
        s = SnapshotRecord(snapshot_id="snap-1", manifest={"f.mid": "hash1"})
        restored = SnapshotRecord.from_dict(s.to_dict())
        assert restored.snapshot_id == "snap-1"
        assert restored.manifest == {"f.mid": "hash1"}

    def test_large_manifest(self) -> None:
        manifest = {f"track_{i:04d}.mid": f"hash-{i:064d}" for i in range(500)}
        s = SnapshotRecord(snapshot_id="big-snap", manifest=manifest)
        restored = SnapshotRecord.from_dict(s.to_dict())
        assert len(restored.manifest) == 500
        assert restored.manifest["track_0000.mid"] == f"hash-{0:064d}"

    def test_write_and_read_back(self, repo: pathlib.Path) -> None:
        s = SnapshotRecord(snapshot_id="snap-rw", manifest={"a.mid": "h1", "b.mid": "h2"})
        write_snapshot(repo, s)
        restored = read_snapshot(repo, "snap-rw")
        assert restored is not None
        assert restored.manifest == {"a.mid": "h1", "b.mid": "h2"}

    def test_empty_manifest_round_trip(self) -> None:
        s = SnapshotRecord(snapshot_id="empty-snap", manifest={})
        restored = SnapshotRecord.from_dict(s.to_dict())
        assert restored.manifest == {}


# ===========================================================================
# TagRecord
# ===========================================================================


class TestTagRecord:
    def test_round_trip(self) -> None:
        t = TagRecord(
            tag_id="tag-001",
            repo_id="repo",
            commit_id="abc123",
            tag="v1.0.0",
        )
        restored = TagRecord.from_dict(t.to_dict())
        assert restored.tag_id == "tag-001"
        assert restored.tag == "v1.0.0"
        assert restored.commit_id == "abc123"

    def test_write_and_list(self, repo: pathlib.Path) -> None:
        for i in range(10):
            write_tag(repo, TagRecord(
                tag_id=f"tag-{i:04d}",
                repo_id="repo",
                commit_id=f"commit-{i:04d}",
                tag=f"v{i}.0.0",
            ))
        tags = get_all_tags(repo, "repo")
        assert len(tags) == 10

    def test_created_at_preserved(self) -> None:
        ts = datetime.datetime(2025, 6, 15, 12, 0, 0, tzinfo=datetime.timezone.utc)
        t = TagRecord(tag_id="t", repo_id="r", commit_id="c", tag="v1", created_at=ts)
        restored = TagRecord.from_dict(t.to_dict())
        assert abs((restored.created_at - ts).total_seconds()) < 1.0


# ===========================================================================
# get_head_commit_id
# ===========================================================================


class TestGetHeadCommitId:
    def test_empty_branch_returns_none(self, repo: pathlib.Path) -> None:
        assert get_head_commit_id(repo, "nonexistent-branch") is None

    def test_returns_id_after_writing_head_ref(self, repo: pathlib.Path) -> None:
        ref_path = repo / ".muse" / "refs" / "heads" / "main"
        ref_path.write_text("abc1234\n")
        assert get_head_commit_id(repo, "main") == "abc1234"

    def test_strips_whitespace(self, repo: pathlib.Path) -> None:
        ref_path = repo / ".muse" / "refs" / "heads" / "feature"
        ref_path.write_text("  deadbeef  \n")
        assert get_head_commit_id(repo, "feature") == "deadbeef"


# ===========================================================================
# CRDT semantics on CommitRecord fields
# ===========================================================================


class TestCRDTAnnotationSemantics:
    def test_reviewed_by_orset_union_semantics(self, repo: pathlib.Path) -> None:
        """ORSet union: multiple overwrite_commit calls accumulate reviewers."""
        c = _commit("crdt-or-001")
        write_commit(repo, c)

        # Agent 1 adds their name.
        rec = read_commit(repo, "crdt-or-001")
        assert rec is not None
        s, tok1 = ORSet().add("agent-alpha")
        rec.reviewed_by = list(s.elements())
        overwrite_commit(repo, rec)

        # Agent 2 independently adds their name.
        rec2 = read_commit(repo, "crdt-or-001")
        assert rec2 is not None
        s2 = ORSet()
        for name in rec2.reviewed_by:
            s2, _ = s2.add(name)
        s2, tok2 = s2.add("agent-beta")
        rec2.reviewed_by = sorted(s2.elements())
        overwrite_commit(repo, rec2)

        final = read_commit(repo, "crdt-or-001")
        assert final is not None
        assert "agent-alpha" in final.reviewed_by
        assert "agent-beta" in final.reviewed_by

    def test_test_runs_gcounter_monotone(self, repo: pathlib.Path) -> None:
        """GCounter: test_runs must never decrease."""
        c = _commit("crdt-gc-001")
        write_commit(repo, c)
        prev = 0
        for _ in range(50):
            rec = read_commit(repo, "crdt-gc-001")
            assert rec is not None
            rec.test_runs += 1
            overwrite_commit(repo, rec)
            current = read_commit(repo, "crdt-gc-001")
            assert current is not None
            assert current.test_runs >= prev
            prev = current.test_runs
        assert prev == 50

    def test_all_provenance_fields_default_to_empty_string(self) -> None:
        c = _commit()
        assert c.agent_id == ""
        assert c.model_id == ""
        assert c.toolchain_id == ""
        assert c.prompt_hash == ""
        assert c.signature == ""
        assert c.signer_key_id == ""

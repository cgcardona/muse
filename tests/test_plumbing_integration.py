"""Cross-command integration tests for the Muse plumbing layer.

These tests chain multiple plumbing commands together the way real agent
pipelines and scripts would, verifying that the output of one command is
correctly consumed by the next and that the whole chain is self-consistent.

Pipelines tested:
- hash-object → cat-object → verify-object (object write/read/integrity)
- commit-tree → update-ref → rev-parse (commit creation end-to-end)
- pack-objects → unpack-objects round-trip (transport)
- snapshot-diff → ls-files cross-check (diff vs. manifest consistency)
- show-ref → for-each-ref consistency (ref listing cross-check)
- symbolic-ref → rev-parse → read-commit (HEAD dereference chain)
- merge-base → snapshot-diff (divergence analysis)
- commit-graph → name-rev (graph walk + naming)
"""

from __future__ import annotations

import datetime
import hashlib
import json
import pathlib

from tests.cli_test_helper import CliRunner

cli = None  # argparse migration — CliRunner ignores this arg
from muse.core.object_store import write_object
from muse.core.store import CommitRecord, SnapshotRecord, write_commit, write_snapshot

runner = CliRunner()


# ---------------------------------------------------------------------------
# Shared helpers
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


def _invoke(args: list[str], repo: pathlib.Path, stdin: str | None = None) -> dict[str, str | bool | int | None | list[str] | list[dict[str, str | bool | int | None]]]:
    result = runner.invoke(cli, args, env=_env(repo), input=stdin)
    assert result.exit_code == 0, f"Command {args!r} failed: {result.output}"
    parsed = json.loads(result.stdout)
    assert isinstance(parsed, dict)
    return parsed


def _invoke_text(args: list[str], repo: pathlib.Path) -> str:
    result = runner.invoke(cli, args, env=_env(repo))
    assert result.exit_code == 0, f"Command {args!r} failed: {result.output}"
    return result.stdout.strip()


# ---------------------------------------------------------------------------
# Pipeline 1: hash-object → cat-object → verify-object
# ---------------------------------------------------------------------------


class TestHashCatVerifyPipeline:
    def test_write_then_cat_returns_same_bytes(self, tmp_path: pathlib.Path) -> None:
        content = b"pipeline test content"
        f = tmp_path / "src.mid"
        f.write_bytes(content)
        repo = _init_repo(tmp_path / "repo")

        # Step 1: hash-object --write
        ho = _invoke(["plumbing", "hash-object", "--write", str(f)], repo)
        oid = ho["object_id"]
        assert ho["stored"] is True

        # Step 2: cat-object --format info → size matches
        info = _invoke(["plumbing", "cat-object", "--format", "info", oid], repo)
        assert info["size_bytes"] == len(content)
        assert info["present"] is True

        # Step 3: verify-object → all_ok
        vfy = _invoke(["plumbing", "verify-object", oid], repo)
        assert vfy["all_ok"] is True
        assert vfy["failed"] == 0

    def test_hash_without_write_not_in_store(self, tmp_path: pathlib.Path) -> None:
        content = b"no-write"
        f = tmp_path / "nw.mid"
        f.write_bytes(content)
        repo = _init_repo(tmp_path / "repo")

        ho = _invoke(["plumbing", "hash-object", str(f)], repo)
        oid = ho["object_id"]

        # cat-object with --format info should report present=False
        result = runner.invoke(
            cli,
            ["plumbing", "cat-object", "--format", "info", oid],
            env=_env(repo),
        )
        assert result.exit_code != 0
        assert json.loads(result.stdout)["present"] is False


# ---------------------------------------------------------------------------
# Pipeline 2: commit-tree → update-ref → rev-parse
# ---------------------------------------------------------------------------


class TestCommitTreeUpdateRefRevParse:
    def test_full_commit_creation_pipeline(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        sid = _snap(repo)

        # Step 1: commit-tree
        ct = _invoke(
            ["plumbing", "commit-tree", "--snapshot", sid, "--message", "pipeline"],
            repo,
        )
        cid = ct["commit_id"]

        # Step 2: update-ref
        ur = _invoke(["plumbing", "update-ref", "main", cid], repo)
        assert ur["commit_id"] == cid

        # Step 3: rev-parse HEAD → should resolve to the same commit
        rp = _invoke(["plumbing", "rev-parse", "HEAD"], repo)
        assert rp["commit_id"] == cid

    def test_two_commit_chain_rev_parse_follows_ref(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        sid1 = _snap(repo, tag="s1")
        sid2 = _snap(repo, tag="s2")

        ct1 = _invoke(["plumbing", "commit-tree", "--snapshot", sid1, "--message", "c1"], repo)
        cid1 = ct1["commit_id"]
        _invoke(["plumbing", "update-ref", "main", cid1], repo)

        ct2 = _invoke(
            ["plumbing", "commit-tree", "--snapshot", sid2, "--message", "c2", "--parent", cid1],
            repo,
        )
        cid2 = ct2["commit_id"]
        _invoke(["plumbing", "update-ref", "main", cid2], repo)

        rp = _invoke(["plumbing", "rev-parse", "main"], repo)
        assert rp["commit_id"] == cid2


# ---------------------------------------------------------------------------
# Pipeline 3: pack-objects → unpack-objects round-trip
# ---------------------------------------------------------------------------


class TestPackUnpackPipeline:
    def test_all_objects_survive_transport(self, tmp_path: pathlib.Path) -> None:
        from muse.core.object_store import has_object
        from muse.core.store import read_commit, read_snapshot

        src = _init_repo(tmp_path / "src")
        dst = _init_repo(tmp_path / "dst")

        content = b"MIDI blob for transport"
        oid = _obj(src, content)
        sid = _snap(src, {"track.mid": oid})
        cid = _commit(src, "transport-test", sid)

        pack_result = runner.invoke(cli, ["plumbing", "pack-objects", cid], env=_env(src))
        assert pack_result.exit_code == 0
        bundle_json = pack_result.stdout

        unpack_result = runner.invoke(
            cli, ["plumbing", "unpack-objects"], input=bundle_json, env=_env(dst)
        )
        assert unpack_result.exit_code == 0

        assert read_commit(dst, cid) is not None
        assert read_snapshot(dst, sid) is not None
        assert has_object(dst, oid)

    def test_pack_then_verify_object_in_dst(self, tmp_path: pathlib.Path) -> None:
        src = _init_repo(tmp_path / "src")
        dst = _init_repo(tmp_path / "dst")
        oid = _obj(src, b"verify after unpack")
        sid = _snap(src, {"v.mid": oid})
        cid = _commit(src, "verify-after", sid)

        bundle_json = runner.invoke(
            cli, ["plumbing", "pack-objects", cid], env=_env(src)
        ).stdout
        runner.invoke(cli, ["plumbing", "unpack-objects"], input=bundle_json, env=_env(dst))

        vfy = _invoke(["plumbing", "verify-object", oid], dst)
        assert vfy["all_ok"] is True


# ---------------------------------------------------------------------------
# Pipeline 4: snapshot-diff vs. ls-files cross-check
# ---------------------------------------------------------------------------


class TestSnapshotDiffLsFilesCrossCheck:
    def test_added_files_in_diff_appear_in_new_ls_files(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        oid_a = _sha("obj-a")
        oid_b = _sha("obj-b")

        sid1 = _snap(repo, {"a.mid": oid_a}, "s1")
        sid2 = _snap(repo, {"a.mid": oid_a, "b.mid": oid_b}, "s2")
        cid1 = _commit(repo, "c1", sid1)
        cid2 = _commit(repo, "c2", sid2, parent=cid1)

        diff = _invoke(["plumbing", "snapshot-diff", sid1, sid2], repo)
        added_paths = {e["path"] for e in diff["added"]}

        ls = _invoke(["plumbing", "ls-files", "--commit", cid2], repo)
        ls_paths = {f["path"] for f in ls["files"]}

        assert added_paths.issubset(ls_paths)

    def test_deleted_files_absent_from_new_ls_files(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        oid = _sha("obj")
        sid1 = _snap(repo, {"gone.mid": oid}, "s1")
        sid2 = _snap(repo, {}, "s2")
        cid1 = _commit(repo, "d1", sid1)
        cid2 = _commit(repo, "d2", sid2, parent=cid1)

        diff = _invoke(["plumbing", "snapshot-diff", sid1, sid2], repo)
        deleted_paths = {e["path"] for e in diff["deleted"]}

        ls = _invoke(["plumbing", "ls-files", "--commit", cid2], repo)
        ls_paths = {f["path"] for f in ls["files"]}

        assert deleted_paths.isdisjoint(ls_paths)


# ---------------------------------------------------------------------------
# Pipeline 5: show-ref ↔ for-each-ref consistency
# ---------------------------------------------------------------------------


class TestShowRefForEachRefConsistency:
    def test_both_commands_report_same_commit_ids(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        sid = _snap(repo)
        cid_main = _commit(repo, "main-tip", sid, branch="main")
        cid_dev = _commit(repo, "dev-tip", sid, branch="dev")

        show = _invoke(["plumbing", "show-ref"], repo)
        show_ids = {r["commit_id"] for r in show["refs"]}

        each = _invoke(["plumbing", "for-each-ref"], repo)
        each_ids = {r["commit_id"] for r in each["refs"]}

        assert show_ids == each_ids

    def test_both_commands_report_same_branch_count(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        sid = _snap(repo)
        for branch in ("main", "dev", "feat"):
            _commit(repo, f"{branch}-tip", sid, branch=branch)

        show = _invoke(["plumbing", "show-ref"], repo)
        each = _invoke(["plumbing", "for-each-ref"], repo)
        assert show["count"] == len(each["refs"])


# ---------------------------------------------------------------------------
# Pipeline 6: symbolic-ref → rev-parse → read-commit
# ---------------------------------------------------------------------------


class TestSymbolicRefRevParseReadCommit:
    def test_symbolic_ref_branch_matches_rev_parse_commit(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        sid = _snap(repo)
        cid = _commit(repo, "head-chain", sid)

        sym = _invoke(["plumbing", "symbolic-ref"], repo)
        branch = sym["branch"]

        rp = _invoke(["plumbing", "rev-parse", branch], repo)
        assert rp["commit_id"] == cid

        rc = _invoke(["plumbing", "read-commit", cid], repo)
        assert rc["branch"] == branch

    def test_set_and_read_symbolic_ref_consistent(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        sid = _snap(repo)
        _commit(repo, "main-c", sid, branch="main")
        _commit(repo, "dev-c", sid, branch="dev")

        # Switch HEAD to dev
        result = runner.invoke(
            cli, ["plumbing", "symbolic-ref", "--set", "dev"], env=_env(repo)
        )
        assert result.exit_code == 0

        sym = _invoke(["plumbing", "symbolic-ref"], repo)
        assert sym["branch"] == "dev"

        rp = _invoke(["plumbing", "rev-parse", "HEAD"], repo)
        assert rp["commit_id"] == _sha("dev-c")


# ---------------------------------------------------------------------------
# Pipeline 7: merge-base → snapshot-diff (divergence analysis)
# ---------------------------------------------------------------------------


class TestMergeBaseSnapshotDiff:
    def test_diff_between_branches_using_merge_base(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        oid_common = _sha("common")
        oid_main = _sha("main-only")
        oid_feat = _sha("feat-only")

        sid_base = _snap(repo, {"common.mid": oid_common}, "base")
        sid_main = _snap(repo, {"common.mid": oid_common, "main.mid": oid_main}, "main")
        sid_feat = _snap(repo, {"common.mid": oid_common, "feat.mid": oid_feat}, "feat")

        c_base = _commit(repo, "base-commit", sid_base)
        c_main = _commit(repo, "main-commit", sid_main, branch="main", parent=c_base)
        c_feat = _commit(repo, "feat-commit", sid_feat, branch="feat", parent=c_base)

        mb = _invoke(["plumbing", "merge-base", "main", "feat"], repo)
        base_cid = mb["merge_base"]
        assert base_cid == c_base

        # Snapshot of the merge base
        rc_base = _invoke(["plumbing", "read-commit", base_cid], repo)
        sid_at_base = rc_base["snapshot_id"]

        # Diff main's snapshot vs. base — should show main.mid as added
        diff_main = _invoke(["plumbing", "snapshot-diff", str(sid_at_base), str(sid_main)], repo)
        added = {e["path"] for e in diff_main["added"]}
        assert "main.mid" in added


# ---------------------------------------------------------------------------
# Pipeline 8: commit-graph → name-rev
# ---------------------------------------------------------------------------


class TestCommitGraphNameRev:
    def test_graph_tip_named_branch_tilde_zero(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        sid = _snap(repo)
        c0 = _commit(repo, "c0", sid)
        c1 = _commit(repo, "c1", sid, parent=c0)
        c2 = _commit(repo, "c2", sid, parent=c1)

        graph = _invoke(["plumbing", "commit-graph"], repo)
        tip = graph["tip"]

        nr = _invoke(["plumbing", "name-rev", tip], repo)
        named = nr["results"][0]
        assert named["commit_id"] == tip
        # Tip commit: distance=0, name is just the branch name (no ~0 suffix).
        assert named["name"] == "main"

    def test_all_graph_commits_nameable(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        sid = _snap(repo)
        parent: str | None = None
        cids: list[str] = []
        for i in range(5):
            cid = _commit(repo, f"chain-{i}", sid, parent=parent)
            cids.append(cid)
            parent = cid

        graph = _invoke(["plumbing", "commit-graph"], repo)
        graph_ids = [c["commit_id"] for c in graph["commits"]]

        nr = _invoke(["plumbing", "name-rev", *graph_ids], repo)
        for entry in nr["results"]:
            assert not entry["undefined"], f"Commit {entry['commit_id']} is undefined"

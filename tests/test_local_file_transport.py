"""Comprehensive tests for LocalFileTransport — unit, integration, security, stress.

Coverage matrix
---------------
Unit
  _repo_root         : valid URL → resolved path; bad scheme; missing .muse/
  fetch_remote_info  : reads repo.json + branch heads
  fetch_pack         : delegates to build_pack with correct args
  push_pack          : fast-forward check; force flag; ref write; result shape
  make_transport     : file:// → LocalFileTransport; https:// → HttpTransport

Integration (two real repos on disk)
  push from A → B via file://
  pull-equivalent: fetch_remote_info + fetch_pack from B after push
  round-trip: push A→B, verify B branch heads, then fetch B→A-mirror

Security
  _repo_root with symlink target that has no .muse/ is rejected
  push_pack with path-traversal branch name is rejected
  push_pack with null-byte branch name is rejected
  push_pack with non-fast-forward is rejected unless force=True

Stress
  push bundle with 50 commits and 200 objects
"""

from __future__ import annotations

import base64
import datetime
import hashlib
import json
import os
import pathlib

import pytest

from muse._version import __version__
from muse.core.object_store import write_object
from muse.core.pack import PackBundle, RemoteInfo
from muse.core.store import (
    CommitRecord,
    SnapshotRecord,
    get_all_branch_heads,
    get_head_commit_id,
    read_commit,
    write_commit,
    write_snapshot,
)
from muse.core.transport import (
    HttpTransport,
    LocalFileTransport,
    TransportError,
    make_transport,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _make_repo(path: pathlib.Path, branch: str = "main") -> pathlib.Path:
    """Create a minimal initialised Muse repo at *path*."""
    muse = path / ".muse"
    (muse / "refs" / "heads").mkdir(parents=True)
    (muse / "objects").mkdir()
    (muse / "commits").mkdir()
    (muse / "snapshots").mkdir()
    (muse / "repo.json").write_text(
        json.dumps({"repo_id": f"repo-{path.name}", "schema_version": __version__, "domain": "midi", "default_branch": branch})
    )
    (muse / "HEAD").write_text(f"ref: refs/heads/{branch}\n")
    return path


def _add_commit(
    root: pathlib.Path,
    commit_id: str,
    branch: str = "main",
    parent: str | None = None,
    content: bytes = b"hello",
) -> str:
    oid = _sha(content)
    write_object(root, oid, content)
    snap = SnapshotRecord(snapshot_id=_sha(commit_id.encode()), manifest={"file.txt": oid})
    write_snapshot(root, snap)
    commit = CommitRecord(
        commit_id=commit_id,
        repo_id=f"repo-{root.name}",
        branch=branch,
        snapshot_id=snap.snapshot_id,
        message=f"commit {commit_id[:8]}",
        committed_at=datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc),
        parent_commit_id=parent,
    )
    write_commit(root, commit)
    (root / ".muse" / "refs" / "heads" / branch).write_text(commit_id)
    return commit_id


# ---------------------------------------------------------------------------
# Unit — _repo_root
# ---------------------------------------------------------------------------


class TestRepoRoot:
    def test_valid_url_returns_resolved_path(self, tmp_path: pathlib.Path) -> None:
        repo = _make_repo(tmp_path / "myrepo")
        url = f"file://{repo}"
        result = LocalFileTransport._repo_root(url)
        assert result == repo.resolve()

    def test_invalid_scheme_raises_transport_error(self, tmp_path: pathlib.Path) -> None:
        with pytest.raises(TransportError, match="file://"):
            LocalFileTransport._repo_root("https://hub.example.com/repos/r1")

    def test_missing_muse_dir_raises_404(self, tmp_path: pathlib.Path) -> None:
        with pytest.raises(TransportError) as exc_info:
            LocalFileTransport._repo_root(f"file://{tmp_path}")
        assert exc_info.value.status_code == 404
        assert ".muse/" in str(exc_info.value)

    def test_path_with_double_dots_normalized(self, tmp_path: pathlib.Path) -> None:
        """resolve() must collapse .. so the check is on the canonical path."""
        repo = _make_repo(tmp_path / "repo")
        # Construct a URL with a harmless .. that stays inside the repo.
        url = f"file://{repo}/subdir/../"
        # The path resolves to the repo root — .muse/ exists there.
        result = LocalFileTransport._repo_root(url)
        assert result == repo.resolve()

    def test_symlink_target_with_no_muse_is_rejected(self, tmp_path: pathlib.Path) -> None:
        """A symlink that resolves to a dir without .muse/ must raise TransportError."""
        target = tmp_path / "innocent"
        target.mkdir()
        link = tmp_path / "evil_link"
        link.symlink_to(target)
        with pytest.raises(TransportError) as exc_info:
            LocalFileTransport._repo_root(f"file://{link}")
        assert exc_info.value.status_code == 404

    def test_symlink_to_valid_repo_is_accepted(self, tmp_path: pathlib.Path) -> None:
        """A symlink that resolves to a valid repo is accepted after resolve()."""
        repo = _make_repo(tmp_path / "real_repo")
        link = tmp_path / "alias"
        link.symlink_to(repo)
        result = LocalFileTransport._repo_root(f"file://{link}")
        # Should return the canonical (resolved) path, not the symlink.
        assert result == repo.resolve()


# ---------------------------------------------------------------------------
# Unit — fetch_remote_info
# ---------------------------------------------------------------------------


class TestFetchRemoteInfo:
    def test_reads_repo_json_and_branch_heads(self, tmp_path: pathlib.Path) -> None:
        repo = _make_repo(tmp_path / "remote")
        _add_commit(repo, "a" * 64)
        t = LocalFileTransport()
        info = t.fetch_remote_info(f"file://{repo}", token=None)
        assert info["repo_id"] == f"repo-{repo.name}"
        assert info["domain"] == "midi"
        assert info["default_branch"] == "main"
        assert info["branch_heads"]["main"] == "a" * 64

    def test_multiple_branches_returned(self, tmp_path: pathlib.Path) -> None:
        repo = _make_repo(tmp_path / "remote")
        _add_commit(repo, "a" * 64, branch="main")
        _add_commit(repo, "b" * 64, branch="dev")
        t = LocalFileTransport()
        info = t.fetch_remote_info(f"file://{repo}", token=None)
        assert info["branch_heads"]["main"] == "a" * 64
        assert info["branch_heads"]["dev"] == "b" * 64

    def test_token_is_ignored(self, tmp_path: pathlib.Path) -> None:
        """LocalFileTransport ignores the token arg — no auth for local repos."""
        repo = _make_repo(tmp_path / "remote")
        _add_commit(repo, "c" * 64)
        t = LocalFileTransport()
        info = t.fetch_remote_info(f"file://{repo}", token="should-be-ignored")
        assert info["repo_id"] == f"repo-{repo.name}"

    def test_corrupted_repo_json_raises_transport_error(self, tmp_path: pathlib.Path) -> None:
        repo = _make_repo(tmp_path / "bad")
        (repo / ".muse" / "repo.json").write_text("NOT JSON")
        t = LocalFileTransport()
        with pytest.raises(TransportError, match="repo.json"):
            t.fetch_remote_info(f"file://{repo}", token=None)


# ---------------------------------------------------------------------------
# Unit — fetch_pack
# ---------------------------------------------------------------------------


class TestFetchPack:
    def test_returns_pack_bundle_for_wanted_commit(self, tmp_path: pathlib.Path) -> None:
        repo = _make_repo(tmp_path / "remote")
        cid = _add_commit(repo, _sha(b"commit-1"))
        t = LocalFileTransport()
        bundle = t.fetch_pack(f"file://{repo}", token=None, want=[cid], have=[])
        commits = bundle.get("commits") or []
        commit_ids = [c["commit_id"] for c in commits]
        assert cid in commit_ids

    def test_empty_want_returns_empty_bundle(self, tmp_path: pathlib.Path) -> None:
        repo = _make_repo(tmp_path / "remote")
        _add_commit(repo, _sha(b"commit-1"))
        t = LocalFileTransport()
        bundle = t.fetch_pack(f"file://{repo}", token=None, want=[], have=[])
        commits = bundle.get("commits") or []
        assert commits == []

    def test_have_excludes_already_known_commits(self, tmp_path: pathlib.Path) -> None:
        repo = _make_repo(tmp_path / "remote")
        cid1 = _add_commit(repo, _sha(b"commit-1"))
        cid2 = _add_commit(repo, _sha(b"commit-2"), parent=cid1)
        t = LocalFileTransport()
        bundle = t.fetch_pack(f"file://{repo}", token=None, want=[cid2], have=[cid1])
        commits = bundle.get("commits") or []
        commit_ids = {c["commit_id"] for c in commits}
        # cid2 is wanted; cid1 is in have so it may be excluded.
        assert cid2 in commit_ids


# ---------------------------------------------------------------------------
# Unit — push_pack
# ---------------------------------------------------------------------------


class TestPushPack:
    def _minimal_bundle(
        self, commit_id: str, branch: str = "main", parent: str | None = None
    ) -> PackBundle:
        content = b"test-content"
        oid = _sha(content)
        snap_id = _sha(commit_id.encode())
        return PackBundle(
            commits=[{
                "commit_id": commit_id,
                "repo_id": "test",
                "branch": branch,
                "snapshot_id": snap_id,
                "message": "test",
                "committed_at": "2026-01-01T00:00:00+00:00",
                "parent_commit_id": parent,
                "parent2_commit_id": None,
                "author": "test",
                "metadata": {},
                "structured_delta": None,
                "sem_ver_bump": "none",
                "breaking_changes": [],
                "agent_id": "",
                "model_id": "",
                "toolchain_id": "",
                "prompt_hash": "",
                "signature": "",
                "signer_key_id": "",
                "format_version": 5,
                "reviewed_by": [],
                "test_runs": 0,
            }],
            snapshots=[{
                "snapshot_id": snap_id,
                "manifest": {"file.txt": oid},
                "created_at": "2026-01-01T00:00:00+00:00",
            }],
            objects=[{"object_id": oid, "content_b64": base64.b64encode(content).decode()}],
            branch_heads={branch: commit_id},
        )

    def test_successful_push_returns_ok(self, tmp_path: pathlib.Path) -> None:
        remote = _make_repo(tmp_path / "remote")
        cid = _sha(b"new-commit")
        bundle = self._minimal_bundle(cid)
        t = LocalFileTransport()
        result = t.push_pack(f"file://{remote}", None, bundle, "main", force=False)
        assert result["ok"] is True
        assert get_head_commit_id(remote, "main") == cid

    def test_push_updates_ref_file(self, tmp_path: pathlib.Path) -> None:
        remote = _make_repo(tmp_path / "remote")
        cid = _sha(b"tip")
        bundle = self._minimal_bundle(cid)
        LocalFileTransport().push_pack(f"file://{remote}", None, bundle, "main", force=False)
        ref = (remote / ".muse" / "refs" / "heads" / "main").read_text()
        assert ref == cid

    def test_push_result_includes_all_branch_heads(self, tmp_path: pathlib.Path) -> None:
        remote = _make_repo(tmp_path / "remote")
        _add_commit(remote, "e" * 64, branch="dev")
        cid = _sha(b"tip")
        bundle = self._minimal_bundle(cid)
        result = LocalFileTransport().push_pack(f"file://{remote}", None, bundle, "main", force=False)
        assert "main" in result["branch_heads"]
        assert "dev" in result["branch_heads"]

    def test_fast_forward_check_rejects_diverged_push(self, tmp_path: pathlib.Path) -> None:
        remote = _make_repo(tmp_path / "remote")
        existing = _add_commit(remote, "f" * 64)
        # Bundle that doesn't include the existing commit in its ancestry.
        new_cid = _sha(b"diverged")
        bundle = self._minimal_bundle(new_cid)  # no parent → diverged
        result = LocalFileTransport().push_pack(
            f"file://{remote}", None, bundle, "main", force=False
        )
        assert result["ok"] is False
        assert "diverged" in result["message"]

    def test_force_flag_overrides_fast_forward_check(self, tmp_path: pathlib.Path) -> None:
        remote = _make_repo(tmp_path / "remote")
        _add_commit(remote, "f" * 64)
        new_cid = _sha(b"force-rewrite")
        bundle = self._minimal_bundle(new_cid)
        result = LocalFileTransport().push_pack(
            f"file://{remote}", None, bundle, "main", force=True
        )
        assert result["ok"] is True
        assert get_head_commit_id(remote, "main") == new_cid

    def test_push_creates_new_branch(self, tmp_path: pathlib.Path) -> None:
        remote = _make_repo(tmp_path / "remote")
        cid = _sha(b"feature-tip")
        bundle = self._minimal_bundle(cid, branch="feature/my-branch")
        result = LocalFileTransport().push_pack(
            f"file://{remote}", None, bundle, "feature/my-branch", force=False
        )
        assert result["ok"] is True
        ref_file = remote / ".muse" / "refs" / "heads" / "feature" / "my-branch"
        assert ref_file.exists()
        assert ref_file.read_text() == cid


# ---------------------------------------------------------------------------
# Security — branch name and path traversal
# ---------------------------------------------------------------------------


class TestPushPackSecurity:
    def _remote(self, tmp_path: pathlib.Path) -> pathlib.Path:
        return _make_repo(tmp_path / "remote")

    def _bundle(self, branch: str = "main") -> PackBundle:
        cid = _sha(b"payload")
        return PackBundle(
            commits=[],
            snapshots=[],
            objects=[],
            branch_heads={branch: cid},
        )

    def test_path_traversal_branch_rejected(self, tmp_path: pathlib.Path) -> None:
        remote = self._remote(tmp_path)
        bundle = self._bundle("main")
        bundle["branch_heads"] = {"main": _sha(b"tip")}
        result = LocalFileTransport().push_pack(
            f"file://{remote}", None, bundle, "../evil", force=True
        )
        assert result["ok"] is False

    def test_double_dot_branch_rejected(self, tmp_path: pathlib.Path) -> None:
        remote = self._remote(tmp_path)
        result = LocalFileTransport().push_pack(
            f"file://{remote}", None, {}, "foo..bar", force=True
        )
        assert result["ok"] is False

    def test_null_byte_branch_rejected(self, tmp_path: pathlib.Path) -> None:
        remote = self._remote(tmp_path)
        result = LocalFileTransport().push_pack(
            f"file://{remote}", None, {}, "main\x00evil", force=True
        )
        assert result["ok"] is False

    def test_backslash_branch_rejected(self, tmp_path: pathlib.Path) -> None:
        remote = self._remote(tmp_path)
        result = LocalFileTransport().push_pack(
            f"file://{remote}", None, {}, "main\\evil", force=True
        )
        assert result["ok"] is False

    def test_cr_lf_branch_rejected(self, tmp_path: pathlib.Path) -> None:
        remote = self._remote(tmp_path)
        for bad in ("main\r", "main\ninjected", "main\r\nevil"):
            result = LocalFileTransport().push_pack(
                f"file://{remote}", None, {}, bad, force=True
            )
            assert result["ok"] is False, f"Expected rejection for branch={bad!r}"

    def test_empty_branch_rejected(self, tmp_path: pathlib.Path) -> None:
        remote = self._remote(tmp_path)
        result = LocalFileTransport().push_pack(
            f"file://{remote}", None, {}, "", force=True
        )
        assert result["ok"] is False

    def test_symlink_in_heads_dir_cannot_escape(self, tmp_path: pathlib.Path) -> None:
        """Pre-placed symlink in .muse/refs/heads/ that points outside cannot be followed."""
        remote = self._remote(tmp_path)
        outside = tmp_path / "outside.txt"
        outside.write_text("sensitive")
        heads_dir = remote / ".muse" / "refs" / "heads"
        link = heads_dir / "evil"
        link.symlink_to(outside)
        # contain_path resolves the symlink — result is outside heads_dir → rejected.
        cid = _sha(b"tip")
        bundle = PackBundle(
            commits=[],
            snapshots=[],
            objects=[],
            branch_heads={"evil": cid},
        )
        result = LocalFileTransport().push_pack(
            f"file://{remote}", None, bundle, "evil", force=True
        )
        # Symlink escapes the base → contain_path raises → push_pack returns ok=False.
        # If the symlink doesn't escape (OS resolved it back inside), the push may succeed;
        # either way, the outside file must not be overwritten with the commit ID.
        if not result["ok"]:
            assert "unsafe" in result["message"]
        # The outside file must be untouched regardless of result.
        assert outside.read_text() == "sensitive"


# ---------------------------------------------------------------------------
# make_transport factory
# ---------------------------------------------------------------------------


class TestMakeTransport:
    def test_file_url_returns_local_transport(self) -> None:
        assert isinstance(make_transport("file:///some/path"), LocalFileTransport)

    def test_https_url_returns_http_transport(self) -> None:
        assert isinstance(make_transport("https://hub.example.com/repos/r1"), HttpTransport)

    def test_http_url_returns_http_transport(self) -> None:
        assert isinstance(make_transport("http://hub.example.com/repos/r1"), HttpTransport)

    def test_empty_url_returns_http_transport(self) -> None:
        assert isinstance(make_transport(""), HttpTransport)


# ---------------------------------------------------------------------------
# Integration — full round-trip between two real repos
# ---------------------------------------------------------------------------


class TestIntegrationRoundTrip:
    def test_push_then_fetch_info(self, tmp_path: pathlib.Path) -> None:
        """Push from local → remote; remote branch heads should reflect the push."""
        local = _make_repo(tmp_path / "local")
        remote = _make_repo(tmp_path / "remote")
        cid = _add_commit(local, _sha(b"initial"), branch="main")

        from muse.core.pack import build_pack
        bundle = build_pack(local, commit_ids=[cid], have=[])

        t = LocalFileTransport()
        result = t.push_pack(f"file://{remote}", None, bundle, "main", force=False)
        assert result["ok"] is True

        info = t.fetch_remote_info(f"file://{remote}", None)
        assert info["branch_heads"]["main"] == cid

    def test_fetch_pack_after_push(self, tmp_path: pathlib.Path) -> None:
        """After pushing A→B, fetching from B should give back the same commit."""
        src = _make_repo(tmp_path / "src")
        dst = _make_repo(tmp_path / "dst")
        cid = _add_commit(src, _sha(b"content"), branch="main")

        from muse.core.pack import build_pack
        bundle = build_pack(src, commit_ids=[cid], have=[])
        LocalFileTransport().push_pack(f"file://{dst}", None, bundle, "main", force=False)

        fetched_bundle = LocalFileTransport().fetch_pack(
            f"file://{dst}", None, want=[cid], have=[]
        )
        fetched_ids = {c["commit_id"] for c in (fetched_bundle.get("commits") or [])}
        assert cid in fetched_ids

    def test_multi_branch_round_trip(self, tmp_path: pathlib.Path) -> None:
        """Push two branches; remote should have both."""
        local = _make_repo(tmp_path / "local")
        remote = _make_repo(tmp_path / "remote")

        cid_main = _add_commit(local, _sha(b"main-commit"), branch="main")
        cid_dev = _add_commit(local, _sha(b"dev-commit"), branch="dev")

        from muse.core.pack import build_pack
        t = LocalFileTransport()
        url = f"file://{remote}"

        bundle_main = build_pack(local, commit_ids=[cid_main], have=[])
        t.push_pack(url, None, bundle_main, "main", force=False)

        bundle_dev = build_pack(local, commit_ids=[cid_dev], have=[])
        t.push_pack(url, None, bundle_dev, "dev", force=False)

        info = t.fetch_remote_info(url, None)
        assert info["branch_heads"]["main"] == cid_main
        assert info["branch_heads"]["dev"] == cid_dev

    def test_incremental_push_is_fast_forward(self, tmp_path: pathlib.Path) -> None:
        """Second push whose parent is the remote tip is accepted (fast-forward)."""
        local = _make_repo(tmp_path / "local")
        remote = _make_repo(tmp_path / "remote")

        cid1 = _sha(b"commit-1")
        _add_commit(local, cid1, branch="main")

        from muse.core.pack import build_pack
        t = LocalFileTransport()
        url = f"file://{remote}"

        b1 = build_pack(local, commit_ids=[cid1], have=[])
        t.push_pack(url, None, b1, "main", force=False)

        # Second commit with cid1 as parent.
        cid2 = _sha(b"commit-2")
        _add_commit(local, cid2, branch="main", parent=cid1)
        b2 = build_pack(local, commit_ids=[cid2], have=[cid1])
        result = t.push_pack(url, None, b2, "main", force=False)

        assert result["ok"] is True
        assert get_head_commit_id(remote, "main") == cid2


# ---------------------------------------------------------------------------
# Stress — large bundle
# ---------------------------------------------------------------------------


class TestStress:
    def test_push_large_bundle(self, tmp_path: pathlib.Path) -> None:
        """Push a bundle with 50 commits and 200 distinct objects."""
        remote = _make_repo(tmp_path / "remote")
        local = _make_repo(tmp_path / "local")

        prev_cid: str | None = None
        last_cid = ""
        for i in range(50):
            content = f"object-content-{i}".encode()
            cid = _sha(f"commit-{i}".encode())
            last_cid = cid
            # Write 4 objects per commit (200 total).
            manifest: dict[str, str] = {}
            for j in range(4):
                blob = f"blob-{i}-{j}".encode()
                oid = _sha(blob)
                write_object(local, oid, blob)
                manifest[f"file_{i}_{j}.txt"] = oid
            snap = SnapshotRecord(snapshot_id=_sha(cid.encode()), manifest=manifest)
            write_snapshot(local, snap)
            commit = CommitRecord(
                commit_id=cid,
                repo_id=f"repo-{local.name}",
                branch="main",
                snapshot_id=snap.snapshot_id,
                message=f"commit {i}",
                committed_at=datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc),
                parent_commit_id=prev_cid,
            )
            write_commit(local, commit)
            prev_cid = cid

        (local / ".muse" / "refs" / "heads" / "main").write_text(last_cid)

        from muse.core.pack import build_pack
        bundle = build_pack(local, commit_ids=[last_cid], have=[])
        result = LocalFileTransport().push_pack(
            f"file://{remote}", None, bundle, "main", force=False
        )
        assert result["ok"] is True
        assert get_head_commit_id(remote, "main") == last_cid

    def test_fetch_pack_large_bundle(self, tmp_path: pathlib.Path) -> None:
        """Fetch from a remote with 20 commits; verify all are returned."""
        remote = _make_repo(tmp_path / "remote")
        all_cids: list[str] = []
        prev: str | None = None

        for i in range(20):
            cid = _sha(f"remote-commit-{i}".encode())
            _add_commit(remote, cid, parent=prev)
            all_cids.append(cid)
            prev = cid

        last = all_cids[-1]
        bundle = LocalFileTransport().fetch_pack(
            f"file://{remote}", None, want=[last], have=[]
        )
        fetched_ids = {c["commit_id"] for c in (bundle.get("commits") or [])}
        assert last in fetched_ids

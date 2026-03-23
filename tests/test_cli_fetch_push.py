"""Tests for muse fetch, push, pull, and ls-remote CLI commands.

All network calls are mocked — no real HTTP traffic occurs.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import pathlib
import unittest.mock

import pytest
from tests.cli_test_helper import CliRunner

from muse._version import __version__
cli = None  # argparse migration — CliRunner ignores this arg
from muse.cli.config import get_remote_head, get_upstream, set_remote_head
from muse.core.object_store import write_object
from muse.core.pack import ObjectPayload, PackBundle, PushResult, RemoteInfo
from muse.core.store import (
    CommitRecord,
    SnapshotRecord,
    get_head_commit_id,
    read_commit,
    write_commit,
    write_snapshot,
)
from muse.core.transport import FilterResponse, NegotiateResponse, PresignResponse, TransportError

runner = CliRunner()


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


@pytest.fixture
def repo(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    """Fully-initialised .muse/ repo with one commit on main."""
    muse_dir = tmp_path / ".muse"
    (muse_dir / "refs" / "heads").mkdir(parents=True)
    (muse_dir / "objects").mkdir()
    (muse_dir / "commits").mkdir()
    (muse_dir / "snapshots").mkdir()
    (muse_dir / "repo.json").write_text(
        json.dumps({"repo_id": "test-repo", "schema_version": __version__, "domain": "midi"})
    )
    (muse_dir / "HEAD").write_text("ref: refs/heads/main\n")

    # Write one object + snapshot + commit so there is something to push.
    content = b"hello"
    oid = _sha(content)
    write_object(tmp_path, oid, content)
    snap = SnapshotRecord(snapshot_id="s" * 64, manifest={"file.txt": oid})
    write_snapshot(tmp_path, snap)
    commit = CommitRecord(
        commit_id="commit1",
        repo_id="test-repo",
        branch="main",
        snapshot_id="s" * 64,
        message="initial",
        committed_at=datetime.datetime.now(datetime.timezone.utc),
    )
    write_commit(tmp_path, commit)
    (muse_dir / "refs" / "heads" / "main").write_text("commit1")
    (muse_dir / "config.toml").write_text(
        '[remotes.origin]\nurl = "https://hub.example.com/repos/r1"\n'
    )

    monkeypatch.setenv("MUSE_REPO_ROOT", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _make_remote_info(
    branch_heads: dict[str, str] | None = None,
) -> RemoteInfo:
    return RemoteInfo(
        repo_id="remote-repo",
        domain="midi",
        default_branch="main",
        branch_heads=branch_heads or {"main": "remote_commit1"},
    )


def _make_bundle(commit_id: str = "remote_commit1") -> PackBundle:
    content = b"remote content"
    oid = _sha(content)
    return PackBundle(
        commits=[
            {
                "commit_id": commit_id,
                "repo_id": "test-repo",
                "branch": "main",
                "snapshot_id": "remote_snap1",
                "message": "remote",
                "committed_at": "2026-01-01T00:00:00+00:00",
                "parent_commit_id": None,
                "parent2_commit_id": None,
                "author": "remote",
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
            }
        ],
        snapshots=[
            {
                "snapshot_id": "remote_snap1",
                "manifest": {"remote.txt": oid},
                "created_at": "2026-01-01T00:00:00+00:00",
            }
        ],
        objects=[ObjectPayload(object_id=oid, content=content)],
        branch_heads={"main": commit_id},
    )


def _push_transport_mock(
    push_result: PushResult | None = None,
    missing_ids: list[str] | None = None,
) -> unittest.mock.MagicMock:
    """Return a transport mock pre-configured for MWP push tests."""
    if push_result is None:
        push_result = PushResult(ok=True, message="ok", branch_heads={"main": "commit1"})
    transport = unittest.mock.MagicMock()
    transport.push_pack.return_value = push_result
    # filter_objects: server reports given IDs as missing (triggers upload).
    transport.filter_objects.return_value = missing_ids if missing_ids is not None else []
    # presign_objects: local backend — return all as inline (no presigned URLs).
    transport.presign_objects.return_value = PresignResponse(presigned={}, inline=[])
    # push_objects: return success counts.
    transport.push_objects.return_value = {"stored": 1, "skipped": 0}
    # negotiate: report ready immediately for pull tests.
    transport.negotiate.return_value = NegotiateResponse(ack=[], common_base=None, ready=True)
    return transport


# ---------------------------------------------------------------------------
# muse fetch
# ---------------------------------------------------------------------------


class TestFetch:
    def test_fetch_updates_tracking_head(self, repo: pathlib.Path) -> None:
        info = _make_remote_info({"main": "remote_commit1"})
        bundle = _make_bundle("remote_commit1")
        transport_mock = unittest.mock.MagicMock()
        transport_mock.fetch_remote_info.return_value = info
        transport_mock.fetch_pack.return_value = bundle
        transport_mock.negotiate.return_value = NegotiateResponse(
            ack=[], common_base=None, ready=True
        )

        with unittest.mock.patch(
            "muse.cli.commands.fetch.make_transport", return_value=transport_mock
        ):
            result = runner.invoke(cli, ["fetch", "origin"])

        assert result.exit_code == 0
        assert "Fetched" in result.output
        tracking = get_remote_head("origin", "main", repo)
        assert tracking == "remote_commit1"

    def test_fetch_defaults_to_current_branch_not_upstream_name(
        self, repo: pathlib.Path
    ) -> None:
        """Regression: fetch with no --branch must use the current branch name,
        not the upstream *remote* name (which get_upstream() returns)."""
        (repo / ".muse" / "config.toml").write_text(
            '[remotes.origin]\nurl = "https://hub.example.com/repos/r1"\nbranch = "main"\n'
        )
        info = _make_remote_info({"main": "remote_commit1"})
        bundle = _make_bundle("remote_commit1")
        transport_mock = unittest.mock.MagicMock()
        transport_mock.fetch_remote_info.return_value = info
        transport_mock.fetch_pack.return_value = bundle
        transport_mock.negotiate.return_value = NegotiateResponse(
            ack=[], common_base=None, ready=True
        )

        with unittest.mock.patch(
            "muse.cli.commands.fetch.make_transport", return_value=transport_mock
        ):
            result = runner.invoke(cli, ["fetch", "origin"])

        assert result.exit_code == 0, result.output
        assert "Fetched" in result.output

    def test_fetch_no_remote_configured_fails(
        self, repo: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        result = runner.invoke(cli, ["fetch", "nonexistent"])
        assert result.exit_code != 0
        assert "not configured" in result.output

    def test_fetch_branch_not_on_remote_fails(self, repo: pathlib.Path) -> None:
        info = _make_remote_info({"main": "abc"})
        transport_mock = unittest.mock.MagicMock()
        transport_mock.fetch_remote_info.return_value = info

        with unittest.mock.patch(
            "muse.cli.commands.fetch.make_transport", return_value=transport_mock
        ):
            result = runner.invoke(cli, ["fetch", "--branch", "nonexistent", "origin"])

        assert result.exit_code != 0
        assert "does not exist on remote" in result.output

    def test_fetch_branch_not_on_remote_shows_available(self, repo: pathlib.Path) -> None:
        """Error output should hint at which branches actually exist."""
        info = _make_remote_info({"main": "abc", "dev": "def"})
        transport_mock = unittest.mock.MagicMock()
        transport_mock.fetch_remote_info.return_value = info

        with unittest.mock.patch(
            "muse.cli.commands.fetch.make_transport", return_value=transport_mock
        ):
            result = runner.invoke(cli, ["fetch", "--branch", "nonexistent", "origin"])

        assert result.exit_code != 0
        assert "Available branches" in result.output

    def test_fetch_transport_error_propagates(self, repo: pathlib.Path) -> None:
        transport_mock = unittest.mock.MagicMock()
        transport_mock.fetch_remote_info.side_effect = TransportError("timeout", 0)

        with unittest.mock.patch(
            "muse.cli.commands.fetch.make_transport", return_value=transport_mock
        ):
            result = runner.invoke(cli, ["fetch", "origin"])

        assert result.exit_code != 0
        assert "Cannot reach remote" in result.output

    def test_fetch_already_up_to_date(self, repo: pathlib.Path) -> None:
        """When local tracking ref matches remote HEAD, no pack is fetched."""
        set_remote_head("origin", "main", "remote_commit1", repo)
        info = _make_remote_info({"main": "remote_commit1"})
        transport_mock = unittest.mock.MagicMock()
        transport_mock.fetch_remote_info.return_value = info

        with unittest.mock.patch(
            "muse.cli.commands.fetch.make_transport", return_value=transport_mock
        ):
            result = runner.invoke(cli, ["fetch", "origin"])

        assert result.exit_code == 0
        assert "up to date" in result.output
        transport_mock.fetch_pack.assert_not_called()

    def test_fetch_prune_removes_stale_refs(self, repo: pathlib.Path) -> None:
        """--prune deletes tracking refs for branches that no longer exist on remote."""
        set_remote_head("origin", "old-feature", "deadbeef", repo)
        info = _make_remote_info({"main": "remote_commit1"})
        bundle = _make_bundle("remote_commit1")
        transport_mock = unittest.mock.MagicMock()
        transport_mock.fetch_remote_info.return_value = info
        transport_mock.fetch_pack.return_value = bundle
        transport_mock.negotiate.return_value = NegotiateResponse(
            ack=[], common_base=None, ready=True
        )

        with unittest.mock.patch(
            "muse.cli.commands.fetch.make_transport", return_value=transport_mock
        ):
            result = runner.invoke(cli, ["fetch", "--prune", "origin"])

        assert result.exit_code == 0
        assert "deleted" in result.output
        assert get_remote_head("origin", "old-feature", repo) is None

    def test_fetch_dry_run_writes_nothing(self, repo: pathlib.Path) -> None:
        """--dry-run must not write objects or update any tracking ref."""
        info = _make_remote_info({"main": "remote_commit1"})
        transport_mock = unittest.mock.MagicMock()
        transport_mock.fetch_remote_info.return_value = info

        with unittest.mock.patch(
            "muse.cli.commands.fetch.make_transport", return_value=transport_mock
        ):
            result = runner.invoke(cli, ["fetch", "--dry-run", "origin"])

        assert result.exit_code == 0
        assert "Would fetch" in result.output
        transport_mock.fetch_pack.assert_not_called()
        assert get_remote_head("origin", "main", repo) is None

    def test_fetch_all_fetches_every_remote(self, repo: pathlib.Path) -> None:
        """--all must contact every configured remote."""
        config_path = repo / ".muse" / "config.toml"
        config_path.write_text(
            '[remotes.origin]\nurl = "https://hub.example.com/repos/r1"\n'
            '[remotes.upstream]\nurl = "https://hub.example.com/repos/r2"\n'
        )
        info = _make_remote_info({"main": "remote_commit1"})
        bundle = _make_bundle("remote_commit1")
        transport_mock = unittest.mock.MagicMock()
        transport_mock.fetch_remote_info.return_value = info
        transport_mock.fetch_pack.return_value = bundle
        transport_mock.negotiate.return_value = NegotiateResponse(
            ack=[], common_base=None, ready=True
        )

        with unittest.mock.patch(
            "muse.cli.commands.fetch.make_transport", return_value=transport_mock
        ):
            result = runner.invoke(cli, ["fetch", "--all"])

        assert result.exit_code == 0
        assert transport_mock.fetch_remote_info.call_count == 2


# ---------------------------------------------------------------------------
# muse push
# ---------------------------------------------------------------------------


class TestPush:
    def test_push_sends_commits(self, repo: pathlib.Path) -> None:
        transport_mock = _push_transport_mock()

        with unittest.mock.patch(
            "muse.cli.commands.push.make_transport", return_value=transport_mock
        ):
            result = runner.invoke(cli, ["push", "origin"])

        assert result.exit_code == 0, result.output
        assert "Pushed" in result.output
        transport_mock.push_pack.assert_called_once()

    def test_push_calls_filter_objects(self, repo: pathlib.Path) -> None:
        """MWP Phase 1: push must call filter_objects before building the pack."""
        transport_mock = _push_transport_mock()

        with unittest.mock.patch(
            "muse.cli.commands.push.make_transport", return_value=transport_mock
        ):
            result = runner.invoke(cli, ["push", "origin"])

        assert result.exit_code == 0, result.output
        transport_mock.filter_objects.assert_called_once()

    def test_push_uploads_only_missing_objects(self, repo: pathlib.Path) -> None:
        """When filter_objects returns a non-empty list, push_objects is called."""
        content = b"hello"
        oid = _sha(content)
        transport_mock = _push_transport_mock(missing_ids=[oid])

        with unittest.mock.patch(
            "muse.cli.commands.push.make_transport", return_value=transport_mock
        ):
            result = runner.invoke(cli, ["push", "origin"])

        assert result.exit_code == 0, result.output
        transport_mock.push_objects.assert_called_once()

    def test_push_skips_upload_when_all_present(self, repo: pathlib.Path) -> None:
        """When filter_objects returns empty list, push_objects is never called."""
        transport_mock = _push_transport_mock(missing_ids=[])

        with unittest.mock.patch(
            "muse.cli.commands.push.make_transport", return_value=transport_mock
        ):
            result = runner.invoke(cli, ["push", "origin"])

        assert result.exit_code == 0, result.output
        transport_mock.push_objects.assert_not_called()

    def test_push_filter_objects_fallback_on_transport_error(
        self, repo: pathlib.Path
    ) -> None:
        """When filter_objects raises TransportError, push falls back to full upload."""
        transport_mock = _push_transport_mock()
        transport_mock.filter_objects.side_effect = TransportError("not found", 404)

        with unittest.mock.patch(
            "muse.cli.commands.push.make_transport", return_value=transport_mock
        ):
            result = runner.invoke(cli, ["push", "origin"])

        assert result.exit_code == 0, result.output

    def test_push_no_remote_configured_fails(self, repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["push", "nonexistent"])
        assert result.exit_code != 0
        assert "not configured" in result.output

    def test_push_set_upstream_records_tracking(self, repo: pathlib.Path) -> None:
        transport_mock = _push_transport_mock()

        with unittest.mock.patch(
            "muse.cli.commands.push.make_transport", return_value=transport_mock
        ):
            result = runner.invoke(cli, ["push", "-u", "origin"])

        assert result.exit_code == 0, result.output
        assert get_upstream("main", repo) == "origin"

    def test_push_conflict_409_shows_helpful_message(self, repo: pathlib.Path) -> None:
        transport_mock = _push_transport_mock()
        transport_mock.push_pack.side_effect = TransportError("non-fast-forward", 409)

        with unittest.mock.patch(
            "muse.cli.commands.push.make_transport", return_value=transport_mock
        ):
            result = runner.invoke(cli, ["push", "origin"])

        assert result.exit_code != 0
        assert "diverged" in result.output

    def test_push_already_up_to_date(self, repo: pathlib.Path) -> None:
        # Remote reports the same HEAD as our local branch → nothing to push.
        transport_mock = _push_transport_mock()
        transport_mock.fetch_remote_info.return_value = _make_remote_info({"main": "commit1"})
        with unittest.mock.patch(
            "muse.cli.commands.push.make_transport", return_value=transport_mock
        ):
            result = runner.invoke(cli, ["push", "origin"])

        assert result.exit_code == 0
        assert "up to date" in result.output
        transport_mock.push_pack.assert_not_called()

    def test_push_force_flag_passed_to_transport(self, repo: pathlib.Path) -> None:
        transport_mock = _push_transport_mock()

        with unittest.mock.patch(
            "muse.cli.commands.push.make_transport", return_value=transport_mock
        ):
            result = runner.invoke(cli, ["push", "--force", "origin"])

        assert result.exit_code == 0, result.output
        call_kwargs = transport_mock.push_pack.call_args
        assert call_kwargs[0][4] is True  # force=True positional arg


# ---------------------------------------------------------------------------
# muse ls-remote
# ---------------------------------------------------------------------------


class TestLsRemote:
    def test_ls_remote_prints_branches(self, repo: pathlib.Path) -> None:
        info = _make_remote_info({"main": "abc123", "dev": "def456"})
        transport_mock = unittest.mock.MagicMock()
        transport_mock.fetch_remote_info.return_value = info

        with unittest.mock.patch(
            "muse.cli.commands.plumbing.ls_remote.HttpTransport",
            return_value=transport_mock,
        ):
            result = runner.invoke(cli, ["plumbing", "ls-remote", "origin"])

        assert result.exit_code == 0
        assert "abc123" in result.output
        assert "main" in result.output

    def test_ls_remote_json_output(self, repo: pathlib.Path) -> None:
        info = _make_remote_info({"main": "abc123"})
        transport_mock = unittest.mock.MagicMock()
        transport_mock.fetch_remote_info.return_value = info

        with unittest.mock.patch(
            "muse.cli.commands.plumbing.ls_remote.HttpTransport",
            return_value=transport_mock,
        ):
            result = runner.invoke(cli, ["plumbing", "ls-remote", "--format", "json", "origin"])

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["branches"]["main"] == "abc123"
        assert "repo_id" in data

    def test_ls_remote_unknown_name_fails(self, repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["plumbing", "ls-remote", "ghost"])
        assert result.exit_code != 0

    def test_ls_remote_bare_url_accepted(self, repo: pathlib.Path) -> None:
        info = _make_remote_info({"main": "abc123"})
        transport_mock = unittest.mock.MagicMock()
        transport_mock.fetch_remote_info.return_value = info

        with unittest.mock.patch(
            "muse.cli.commands.plumbing.ls_remote.HttpTransport",
            return_value=transport_mock,
        ):
            result = runner.invoke(
                cli, ["plumbing", "ls-remote", "https://hub.example.com/repos/r1"]
            )

        assert result.exit_code == 0
        assert "abc123" in result.output


# ---------------------------------------------------------------------------
# MWP — LocalFileTransport: filter_objects, presign_objects, negotiate
# ---------------------------------------------------------------------------


class TestLocalTransportMwp2:
    """End-to-end tests for MWP methods on LocalFileTransport (no network)."""

    def _make_remote(self, path: pathlib.Path) -> pathlib.Path:
        muse_dir = path / ".muse"
        for d in ("objects", "refs/heads", "commits", "snapshots"):
            (muse_dir / d).mkdir(parents=True, exist_ok=True)
        (muse_dir / "repo.json").write_text(
            json.dumps({"repo_id": "r", "schema_version": "1", "domain": "code"})
        )
        (muse_dir / "HEAD").write_text("ref: refs/heads/main\n")
        return path

    def test_filter_objects_returns_missing_ids(self, tmp_path: pathlib.Path) -> None:
        """filter_objects returns only IDs not present in the remote store."""
        from muse.core.transport import LocalFileTransport

        remote = self._make_remote(tmp_path / "remote")
        present_content = b"present"
        present_id = _sha(present_content)
        write_object(remote, present_id, present_content)
        missing_id = "a" * 64

        transport = LocalFileTransport()
        result = transport.filter_objects(f"file://{remote}", None, [present_id, missing_id])

        assert missing_id in result
        assert present_id not in result

    def test_presign_objects_returns_all_inline(self, tmp_path: pathlib.Path) -> None:
        """LocalFileTransport has no cloud backend — everything is inline."""
        from muse.core.transport import LocalFileTransport

        remote = self._make_remote(tmp_path / "remote")
        transport = LocalFileTransport()
        resp = transport.presign_objects(f"file://{remote}", None, ["id1", "id2"], "put")

        assert resp["presigned"] == {}
        assert set(resp["inline"]) == {"id1", "id2"}

    def test_negotiate_returns_ready_when_no_have(self, tmp_path: pathlib.Path) -> None:
        """When client has no local commits, negotiate should return ready=True."""
        from muse.core.transport import LocalFileTransport

        remote = self._make_remote(tmp_path / "remote")
        transport = LocalFileTransport()
        resp = transport.negotiate(f"file://{remote}", None, want=["abc"], have=[])

        assert resp["ready"] is True
        assert resp["ack"] == []

    def test_negotiate_acks_known_commits(self, tmp_path: pathlib.Path) -> None:
        """negotiate acks commit IDs that exist in the remote's store."""
        from muse.core.transport import LocalFileTransport

        remote = self._make_remote(tmp_path / "remote")
        snap = SnapshotRecord(snapshot_id="s" * 64, manifest={})
        write_snapshot(remote, snap)
        commit = CommitRecord(
            commit_id="known_commit",
            repo_id="r",
            branch="main",
            snapshot_id="s" * 64,
            message="seed",
            committed_at=datetime.datetime.now(datetime.timezone.utc),
        )
        write_commit(remote, commit)

        transport = LocalFileTransport()
        resp = transport.negotiate(
            f"file://{remote}", None,
            want=["unknown_tip"],
            have=["known_commit", "unknown_local"],
        )

        assert "known_commit" in resp["ack"]
        assert "unknown_local" not in resp["ack"]


# ---------------------------------------------------------------------------
# MWP — ObjectPayload shape and pack helpers
# ---------------------------------------------------------------------------


class TestMwp2PackHelpers:
    def test_object_payload_has_content_bytes(self) -> None:
        """ObjectPayload must use 'content: bytes', not 'content_b64: str'."""
        payload = ObjectPayload(object_id="abc", content=b"hello")
        assert payload["content"] == b"hello"
        assert "content_b64" not in payload

    def test_build_pack_only_objects_filters_correctly(
        self, tmp_path: pathlib.Path
    ) -> None:
        """build_pack only_objects param includes only requested objects."""
        from muse.core.pack import build_pack

        muse_dir = tmp_path / ".muse"
        for d in ("objects", "refs/heads", "commits", "snapshots"):
            (muse_dir / d).mkdir(parents=True, exist_ok=True)
        (muse_dir / "repo.json").write_text(
            json.dumps({"repo_id": "r", "schema_version": "1", "domain": "code"})
        )
        (muse_dir / "HEAD").write_text("ref: refs/heads/main\n")

        content_a = b"object_a"
        oid_a = _sha(content_a)
        content_b = b"object_b"
        oid_b = _sha(content_b)
        write_object(tmp_path, oid_a, content_a)
        write_object(tmp_path, oid_b, content_b)

        snap = SnapshotRecord(
            snapshot_id="s" * 64, manifest={"a.txt": oid_a, "b.txt": oid_b}
        )
        write_snapshot(tmp_path, snap)
        commit = CommitRecord(
            commit_id="c1",
            repo_id="r",
            branch="main",
            snapshot_id="s" * 64,
            message="test",
            committed_at=datetime.datetime.now(datetime.timezone.utc),
        )
        write_commit(tmp_path, commit)
        (muse_dir / "refs" / "heads" / "main").write_text("c1")

        bundle = build_pack(tmp_path, ["c1"], only_objects={oid_a})
        object_ids = {obj["object_id"] for obj in (bundle.get("objects") or [])}
        assert oid_a in object_ids
        assert oid_b not in object_ids

    def test_collect_object_ids_excludes_have(self, tmp_path: pathlib.Path) -> None:
        """collect_object_ids stops at have commits, not including their objects."""
        from muse.core.pack import collect_object_ids

        muse_dir = tmp_path / ".muse"
        for d in ("objects", "refs/heads", "commits", "snapshots"):
            (muse_dir / d).mkdir(parents=True, exist_ok=True)
        (muse_dir / "repo.json").write_text(
            json.dumps({"repo_id": "r", "schema_version": "1", "domain": "code"})
        )
        (muse_dir / "HEAD").write_text("ref: refs/heads/main\n")

        content_old = b"old"
        oid_old = _sha(content_old)
        write_object(tmp_path, oid_old, content_old)
        snap_old = SnapshotRecord(snapshot_id="so" * 32, manifest={"old.txt": oid_old})
        write_snapshot(tmp_path, snap_old)
        commit_old = CommitRecord(
            commit_id="c_old",
            repo_id="r",
            branch="main",
            snapshot_id="so" * 32,
            message="old",
            committed_at=datetime.datetime.now(datetime.timezone.utc),
        )
        write_commit(tmp_path, commit_old)

        content_new = b"new"
        oid_new = _sha(content_new)
        write_object(tmp_path, oid_new, content_new)
        snap_new = SnapshotRecord(
            snapshot_id="sn" * 32, manifest={"old.txt": oid_old, "new.txt": oid_new}
        )
        write_snapshot(tmp_path, snap_new)
        commit_new = CommitRecord(
            commit_id="c_new",
            repo_id="r",
            branch="main",
            snapshot_id="sn" * 32,
            message="new",
            committed_at=datetime.datetime.now(datetime.timezone.utc),
            parent_commit_id="c_old",
        )
        write_commit(tmp_path, commit_new)

        ids = collect_object_ids(tmp_path, ["c_new"], have=["c_old"])
        assert oid_new in ids

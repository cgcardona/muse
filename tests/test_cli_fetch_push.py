"""Tests for muse fetch, push, pull, and ls-remote CLI commands.

All network calls are mocked — no real HTTP traffic occurs.
"""

from __future__ import annotations

import base64
import datetime
import hashlib
import json
import pathlib
import unittest.mock

import pytest
from typer.testing import CliRunner

from muse.cli.app import cli
from muse.cli.config import get_remote_head, get_upstream
from muse.core.object_store import write_object
from muse.core.pack import PackBundle, RemoteInfo
from muse.core.store import (
    CommitRecord,
    SnapshotRecord,
    get_head_commit_id,
    read_commit,
    write_commit,
    write_snapshot,
)
from muse.core.transport import TransportError

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
        json.dumps({"repo_id": "test-repo", "schema_version": "1", "domain": "midi"})
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
        objects=[
            ObjectPayload(object_id=oid, content_b64=base64.b64encode(content).decode())
        ],
        branch_heads={"main": commit_id},
    )


# Import ObjectPayload here to avoid circular issues in the fixture above.
from muse.core.pack import ObjectPayload  # noqa: E402


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

        with unittest.mock.patch(
            "muse.cli.commands.fetch.HttpTransport", return_value=transport_mock
        ):
            result = runner.invoke(cli, ["fetch", "origin"])

        assert result.exit_code == 0
        assert "Fetched" in result.output
        tracking = get_remote_head("origin", "main", repo)
        assert tracking == "remote_commit1"

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
            "muse.cli.commands.fetch.HttpTransport", return_value=transport_mock
        ):
            # Options must precede positional args in add_typer groups.
            result = runner.invoke(cli, ["fetch", "--branch", "nonexistent", "origin"])

        assert result.exit_code != 0
        assert "does not exist on remote" in result.output

    def test_fetch_transport_error_propagates(self, repo: pathlib.Path) -> None:
        transport_mock = unittest.mock.MagicMock()
        transport_mock.fetch_remote_info.side_effect = TransportError("timeout", 0)

        with unittest.mock.patch(
            "muse.cli.commands.fetch.HttpTransport", return_value=transport_mock
        ):
            result = runner.invoke(cli, ["fetch", "origin"])

        assert result.exit_code != 0
        assert "Cannot reach remote" in result.output


# ---------------------------------------------------------------------------
# muse push
# ---------------------------------------------------------------------------


class TestPush:
    def test_push_sends_commits(self, repo: pathlib.Path) -> None:
        push_result = {"ok": True, "message": "ok", "branch_heads": {"main": "commit1"}}
        transport_mock = unittest.mock.MagicMock()
        transport_mock.push_pack.return_value = push_result

        with unittest.mock.patch(
            "muse.cli.commands.push.HttpTransport", return_value=transport_mock
        ):
            result = runner.invoke(cli, ["push", "origin"])

        assert result.exit_code == 0
        assert "Pushed" in result.output
        transport_mock.push_pack.assert_called_once()

    def test_push_no_remote_configured_fails(self, repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["push", "nonexistent"])
        assert result.exit_code != 0
        assert "not configured" in result.output

    def test_push_set_upstream_records_tracking(self, repo: pathlib.Path) -> None:
        push_result = {"ok": True, "message": "", "branch_heads": {"main": "commit1"}}
        transport_mock = unittest.mock.MagicMock()
        transport_mock.push_pack.return_value = push_result

        with unittest.mock.patch(
            "muse.cli.commands.push.HttpTransport", return_value=transport_mock
        ):
            # Options must precede positional args in add_typer groups.
            result = runner.invoke(cli, ["push", "-u", "origin"])

        assert result.exit_code == 0
        assert get_upstream("main", repo) == "origin"

    def test_push_conflict_409_shows_helpful_message(self, repo: pathlib.Path) -> None:
        transport_mock = unittest.mock.MagicMock()
        transport_mock.push_pack.side_effect = TransportError("non-fast-forward", 409)

        with unittest.mock.patch(
            "muse.cli.commands.push.HttpTransport", return_value=transport_mock
        ):
            result = runner.invoke(cli, ["push", "origin"])

        assert result.exit_code != 0
        assert "diverged" in result.output

    def test_push_already_up_to_date(self, repo: pathlib.Path) -> None:
        # Set tracking head to the same commit as local HEAD.
        (repo / ".muse" / "remotes" / "origin").mkdir(parents=True)
        (repo / ".muse" / "remotes" / "origin" / "main").write_text("commit1")

        transport_mock = unittest.mock.MagicMock()
        with unittest.mock.patch(
            "muse.cli.commands.push.HttpTransport", return_value=transport_mock
        ):
            result = runner.invoke(cli, ["push", "origin"])

        assert result.exit_code == 0
        assert "up to date" in result.output
        transport_mock.push_pack.assert_not_called()

    def test_push_force_flag_passed_to_transport(self, repo: pathlib.Path) -> None:
        push_result = {"ok": True, "message": "", "branch_heads": {"main": "commit1"}}
        transport_mock = unittest.mock.MagicMock()
        transport_mock.push_pack.return_value = push_result

        with unittest.mock.patch(
            "muse.cli.commands.push.HttpTransport", return_value=transport_mock
        ):
            result = runner.invoke(cli, ["push", "--force", "origin"])

        assert result.exit_code == 0
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
            # --json option must precede positional arg in add_typer groups.
            result = runner.invoke(cli, ["plumbing", "ls-remote", "--json", "origin"])

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

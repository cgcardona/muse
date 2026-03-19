"""Tests for muse clone CLI command."""

from __future__ import annotations

import base64
import hashlib
import json
import pathlib
import unittest.mock

import pytest
from typer.testing import CliRunner

from muse.cli.app import cli
from muse.cli.config import get_remote, get_upstream
from muse.core.pack import ObjectPayload, PackBundle, RemoteInfo
from muse.core.store import read_commit, read_snapshot
from muse.core.transport import TransportError

runner = CliRunner()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _make_remote_info(
    domain: str = "midi",
    default_branch: str = "main",
    branch_heads: dict[str, str] | None = None,
    repo_id: str = "remote-repo-id",
) -> RemoteInfo:
    return RemoteInfo(
        repo_id=repo_id,
        domain=domain,
        default_branch=default_branch,
        branch_heads={"main": "c1"} if branch_heads is None else branch_heads,
    )


def _make_bundle(commit_id: str = "c1", branch: str = "main") -> PackBundle:
    content = b"cloned content"
    oid = _sha(content)
    return PackBundle(
        commits=[
            {
                "commit_id": commit_id,
                "repo_id": "remote-repo-id",
                "branch": branch,
                "snapshot_id": "s1",
                "message": "initial",
                "committed_at": "2026-01-01T00:00:00+00:00",
                "parent_commit_id": None,
                "parent2_commit_id": None,
                "author": "alice",
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
                "snapshot_id": "s1",
                "manifest": {"hello.txt": oid},
                "created_at": "2026-01-01T00:00:00+00:00",
            }
        ],
        objects=[
            ObjectPayload(object_id=oid, content_b64=base64.b64encode(content).decode())
        ],
        branch_heads={"main": commit_id},
    )


def _mock_transport(info: RemoteInfo, bundle: PackBundle) -> unittest.mock.MagicMock:
    mock = unittest.mock.MagicMock()
    mock.fetch_remote_info.return_value = info
    mock.fetch_pack.return_value = bundle
    return mock


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestClone:
    def test_clone_creates_muse_dir(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        info = _make_remote_info()
        bundle = _make_bundle()
        mock = _mock_transport(info, bundle)

        with unittest.mock.patch("muse.cli.commands.clone.HttpTransport", return_value=mock):
            result = runner.invoke(
                cli, ["clone", "https://hub.example.com/repos/r1", "my-repo"]
            )

        assert result.exit_code == 0, result.output
        assert (tmp_path / "my-repo" / ".muse").is_dir()

    def test_clone_sets_origin_remote(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        info = _make_remote_info()
        bundle = _make_bundle()
        mock = _mock_transport(info, bundle)

        with unittest.mock.patch("muse.cli.commands.clone.HttpTransport", return_value=mock):
            runner.invoke(
                cli, ["clone", "https://hub.example.com/repos/r1", "my-repo"]
            )

        origin = get_remote("origin", tmp_path / "my-repo")
        assert origin == "https://hub.example.com/repos/r1"

    def test_clone_sets_upstream_tracking(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        info = _make_remote_info()
        bundle = _make_bundle()
        mock = _mock_transport(info, bundle)

        with unittest.mock.patch("muse.cli.commands.clone.HttpTransport", return_value=mock):
            runner.invoke(
                cli, ["clone", "https://hub.example.com/repos/r1", "my-repo"]
            )

        upstream = get_upstream("main", tmp_path / "my-repo")
        assert upstream == "origin"

    def test_clone_writes_commits_and_snapshots(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        info = _make_remote_info()
        bundle = _make_bundle()
        mock = _mock_transport(info, bundle)

        with unittest.mock.patch("muse.cli.commands.clone.HttpTransport", return_value=mock):
            runner.invoke(
                cli, ["clone", "https://hub.example.com/repos/r1", "dest"]
            )

        dest = tmp_path / "dest"
        assert read_commit(dest, "c1") is not None
        assert read_snapshot(dest, "s1") is not None

    def test_clone_propagates_domain(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        info = _make_remote_info(domain="code")
        bundle = _make_bundle()
        mock = _mock_transport(info, bundle)

        with unittest.mock.patch("muse.cli.commands.clone.HttpTransport", return_value=mock):
            runner.invoke(
                cli, ["clone", "https://hub.example.com/repos/r1", "dest"]
            )

        dest = tmp_path / "dest"
        repo_meta = json.loads((dest / ".muse" / "repo.json").read_text())
        assert repo_meta["domain"] == "code"

    def test_clone_uses_remote_repo_id(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        info = _make_remote_info(repo_id="the-real-repo-id")
        bundle = _make_bundle()
        mock = _mock_transport(info, bundle)

        with unittest.mock.patch("muse.cli.commands.clone.HttpTransport", return_value=mock):
            runner.invoke(
                cli, ["clone", "https://hub.example.com/repos/r1", "dest"]
            )

        dest = tmp_path / "dest"
        repo_meta = json.loads((dest / ".muse" / "repo.json").read_text())
        assert repo_meta["repo_id"] == "the-real-repo-id"

    def test_clone_infers_directory_name_from_url(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        info = _make_remote_info()
        bundle = _make_bundle()
        mock = _mock_transport(info, bundle)

        with unittest.mock.patch("muse.cli.commands.clone.HttpTransport", return_value=mock):
            runner.invoke(cli, ["clone", "https://hub.example.com/repos/my-project"])

        assert (tmp_path / "my-project" / ".muse").is_dir()

    def test_clone_transport_error_fails_cleanly(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        mock = unittest.mock.MagicMock()
        mock.fetch_remote_info.side_effect = TransportError("connection refused", 0)

        with unittest.mock.patch("muse.cli.commands.clone.HttpTransport", return_value=mock):
            result = runner.invoke(
                cli, ["clone", "https://hub.example.com/repos/r1", "dest"]
            )

        assert result.exit_code != 0
        assert "Cannot reach remote" in result.output

    def test_clone_existing_repo_fails(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        # Pre-create a .muse directory.
        (tmp_path / "dest" / ".muse").mkdir(parents=True)

        mock = unittest.mock.MagicMock()
        with unittest.mock.patch("muse.cli.commands.clone.HttpTransport", return_value=mock):
            result = runner.invoke(
                cli, ["clone", "https://hub.example.com/repos/r1", "dest"]
            )

        assert result.exit_code != 0
        assert "already a Muse repository" in result.output

    def test_clone_empty_repo_fails(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        info = _make_remote_info(branch_heads={})  # no branches
        mock = unittest.mock.MagicMock()
        mock.fetch_remote_info.return_value = info

        with unittest.mock.patch("muse.cli.commands.clone.HttpTransport", return_value=mock):
            result = runner.invoke(
                cli, ["clone", "https://hub.example.com/repos/r1", "dest"]
            )

        assert result.exit_code != 0
        assert "empty" in result.output.lower() or "no branches" in result.output.lower()

    def test_clone_branch_flag_selects_branch(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        info = _make_remote_info(
            default_branch="main",
            branch_heads={"main": "c1", "dev": "c2"},
        )
        bundle = _make_bundle(commit_id="c2", branch="dev")
        mock = _mock_transport(info, bundle)

        with unittest.mock.patch("muse.cli.commands.clone.HttpTransport", return_value=mock):
            # Options must precede positional args in add_typer groups.
            result = runner.invoke(
                cli,
                ["clone", "--branch", "dev", "https://hub.example.com/repos/r1", "dest"],
            )

        assert result.exit_code == 0, result.output
        dest = tmp_path / "dest"
        head_ref = (dest / ".muse" / "HEAD").read_text().strip()
        assert "dev" in head_ref

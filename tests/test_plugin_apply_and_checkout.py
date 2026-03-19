"""Tests for plugin.apply() and the incremental checkout that wires it in.

Covers:
- MidiPlugin.apply() with a workdir path (files already updated on disk)
- MidiPlugin.apply() with a snapshot dict (in-memory removals)
- checkout incremental delta: only changed files are touched
- revert reuses parent snapshot_id (no re-scan)
- cherry-pick uses merged_manifest directly (no re-scan)
"""

import pathlib

import pytest
from typer.testing import CliRunner

from muse.cli.app import cli
from muse.core.store import get_head_commit_id, read_commit, read_snapshot
from muse.domain import DeleteOp, SnapshotManifest, StructuredDelta
from muse.plugins.midi.plugin import MidiPlugin

runner = CliRunner()
plugin = MidiPlugin()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def repo(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MUSE_REPO_ROOT", str(tmp_path))
    result = runner.invoke(cli, ["init"])
    assert result.exit_code == 0, result.output
    return tmp_path


def _write(repo: pathlib.Path, filename: str, content: str = "data") -> None:
    (repo / "muse-work" / filename).write_text(content)


def _commit(msg: str = "commit") -> None:
    result = runner.invoke(cli, ["commit", "-m", msg])
    assert result.exit_code == 0, result.output


def _head_id(repo: pathlib.Path) -> str:
    cid = get_head_commit_id(repo, "main")
    assert cid is not None
    return cid


# ---------------------------------------------------------------------------
# MidiPlugin.apply() — unit tests
# ---------------------------------------------------------------------------


def _empty_delta() -> StructuredDelta:
    return StructuredDelta(domain="midi", ops=[], summary="no changes")


class TestMidiPluginApplyPath:
    """apply() with a workdir path rescans the directory for ground truth.

    When live_state is a pathlib.Path, apply() ignores the delta and simply
    rescans the directory — the physical filesystem is the source of truth.
    """

    def test_apply_returns_snapshot_of_workdir(self, tmp_path: pathlib.Path) -> None:
        workdir = tmp_path / "work"
        workdir.mkdir()
        (workdir / "a.mid").write_bytes(b"midi-a")
        (workdir / "b.mid").write_bytes(b"midi-b")

        # Simulate remove b.mid physically before calling apply.
        (workdir / "b.mid").unlink()

        result = plugin.apply(_empty_delta(), workdir)

        assert "b.mid" not in result["files"]
        assert "a.mid" in result["files"]

    def test_apply_picks_up_added_file(self, tmp_path: pathlib.Path) -> None:
        workdir = tmp_path / "work"
        workdir.mkdir()
        (workdir / "a.mid").write_bytes(b"original")

        # Add file physically before calling apply.
        (workdir / "new.mid").write_bytes(b"new content")
        result = plugin.apply(_empty_delta(), workdir)

        assert "new.mid" in result["files"]

    def test_apply_picks_up_modified_content(self, tmp_path: pathlib.Path) -> None:
        workdir = tmp_path / "work"
        workdir.mkdir()
        (workdir / "a.mid").write_bytes(b"v1")

        snap_v1 = plugin.snapshot(workdir)

        # Modify physically, then apply rescans.
        (workdir / "a.mid").write_bytes(b"v2")
        result = plugin.apply(_empty_delta(), workdir)

        assert result["files"]["a.mid"] != snap_v1["files"]["a.mid"]

    def test_apply_empty_delta_returns_current_state(self, tmp_path: pathlib.Path) -> None:
        workdir = tmp_path / "work"
        workdir.mkdir()
        (workdir / "a.mid").write_bytes(b"data")

        result = plugin.apply(_empty_delta(), workdir)
        expected = plugin.snapshot(workdir)
        assert result["files"] == expected["files"]


class TestMidiPluginApplyDict:
    """apply() with a snapshot dict applies ops in-memory."""

    def _delete(self, address: str, content_id: str = "x") -> DeleteOp:
        return DeleteOp(
            op="delete", address=address, position=None,
            content_id=content_id, content_summary=f"deleted: {address}",
        )

    def test_apply_removes_deleted_paths(self) -> None:
        snap = SnapshotManifest(files={"a.mid": "aaa", "b.mid": "bbb"}, domain="midi")
        delta = StructuredDelta(
            domain="midi",
            ops=[self._delete("b.mid", "bbb")],
            summary="1 file removed",
        )
        result = plugin.apply(delta, snap)
        assert "b.mid" not in result["files"]
        assert "a.mid" in result["files"]

    def test_apply_removes_multiple_paths(self) -> None:
        snap = SnapshotManifest(files={"a.mid": "aaa", "b.mid": "bbb", "c.mid": "ccc"}, domain="midi")
        delta = StructuredDelta(
            domain="midi",
            ops=[self._delete("a.mid", "aaa"), self._delete("c.mid", "ccc")],
            summary="2 files removed",
        )
        result = plugin.apply(delta, snap)
        assert result["files"] == {"b.mid": "bbb"}

    def test_apply_nonexistent_remove_is_noop(self) -> None:
        snap = SnapshotManifest(files={"a.mid": "aaa"}, domain="midi")
        delta = StructuredDelta(
            domain="midi",
            ops=[self._delete("ghost.mid")],
            summary="1 file removed",
        )
        result = plugin.apply(delta, snap)
        assert result["files"] == {"a.mid": "aaa"}

    def test_apply_preserves_domain(self) -> None:
        snap = SnapshotManifest(files={}, domain="midi")
        delta = _empty_delta()
        result = plugin.apply(delta, snap)
        assert result["domain"] == "midi"


# ---------------------------------------------------------------------------
# Incremental checkout via plugin.apply()
# ---------------------------------------------------------------------------


class TestIncrementalCheckout:
    def test_checkout_branch_only_changes_delta(self, repo: pathlib.Path) -> None:
        """Files unchanged between branches are not touched on disk."""
        _write(repo, "shared.mid", "shared")
        _write(repo, "main-only.mid", "main")
        _commit("main initial")

        runner.invoke(cli, ["branch", "feature"])
        runner.invoke(cli, ["checkout", "feature"])
        _write(repo, "feature-only.mid", "feature")
        _commit("feature commit")

        runner.invoke(cli, ["checkout", "main"])
        assert (repo / "muse-work" / "shared.mid").exists()
        assert (repo / "muse-work" / "main-only.mid").exists()
        assert not (repo / "muse-work" / "feature-only.mid").exists()

    def test_checkout_restores_correct_content(self, repo: pathlib.Path) -> None:
        """Modified files get the correct content after checkout."""
        _write(repo, "beat.mid", "v1")
        _commit("v1")

        runner.invoke(cli, ["branch", "feature"])
        runner.invoke(cli, ["checkout", "feature"])
        _write(repo, "beat.mid", "v2")
        _commit("v2 on feature")

        runner.invoke(cli, ["checkout", "main"])
        assert (repo / "muse-work" / "beat.mid").read_text() == "v1"

    def test_checkout_commit_id_incremental(self, repo: pathlib.Path) -> None:
        """Detached HEAD checkout also uses incremental apply."""
        _write(repo, "a.mid", "original")
        _commit("first")
        first_id = _head_id(repo)

        _write(repo, "b.mid", "new")
        _commit("second")

        runner.invoke(cli, ["checkout", first_id])
        assert (repo / "muse-work" / "a.mid").exists()
        assert not (repo / "muse-work" / "b.mid").exists()

    def test_checkout_to_new_branch_keeps_workdir(self, repo: pathlib.Path) -> None:
        """Switching to a brand-new (no-commit) branch keeps the current workdir intact."""
        _write(repo, "beat.mid")
        _commit("some work")

        runner.invoke(cli, ["branch", "empty-branch"])
        runner.invoke(cli, ["checkout", "empty-branch"])
        # workdir is preserved — new branch inherits from where we branched
        assert (repo / "muse-work" / "beat.mid").exists()


# ---------------------------------------------------------------------------
# Revert reuses parent snapshot (no re-scan)
# ---------------------------------------------------------------------------


class TestRevertSnapshotReuse:
    def test_revert_commit_points_to_parent_snapshot(self, repo: pathlib.Path) -> None:
        """The revert commit's snapshot_id is the same as the reverted commit's parent."""
        _write(repo, "beat.mid", "base")
        _commit("base")
        base_id = _head_id(repo)
        base_commit = read_commit(repo, base_id)
        assert base_commit is not None
        parent_snapshot_id = base_commit.snapshot_id

        _write(repo, "lead.mid", "new")
        _commit("add lead")
        lead_id = _head_id(repo)

        runner.invoke(cli, ["revert", lead_id])
        revert_id = _head_id(repo)
        revert_commit = read_commit(repo, revert_id)
        assert revert_commit is not None
        # The revert commit points to the same snapshot as "base" — no re-scan.
        assert revert_commit.snapshot_id == parent_snapshot_id

    def test_revert_snapshot_already_in_store(self, repo: pathlib.Path) -> None:
        """After revert, the referenced snapshot is already in the store (not re-created)."""
        _write(repo, "beat.mid", "base")
        _commit("base")
        base_id = _head_id(repo)
        base_commit = read_commit(repo, base_id)
        assert base_commit is not None

        _write(repo, "lead.mid", "new")
        _commit("add lead")
        lead_id = _head_id(repo)

        runner.invoke(cli, ["revert", lead_id])
        revert_id = _head_id(repo)
        revert_commit = read_commit(repo, revert_id)
        assert revert_commit is not None

        snap = read_snapshot(repo, revert_commit.snapshot_id)
        assert snap is not None
        assert "beat.mid" in snap.manifest
        assert "lead.mid" not in snap.manifest


# ---------------------------------------------------------------------------
# Cherry-pick uses merged_manifest (no re-scan)
# ---------------------------------------------------------------------------


class TestCherryPickManifestReuse:
    def test_cherry_pick_commit_has_correct_snapshot(self, repo: pathlib.Path) -> None:
        """Cherry-picked commit's snapshot contains only the right files."""
        _write(repo, "base.mid", "base")
        _commit("base")

        runner.invoke(cli, ["branch", "feature"])
        runner.invoke(cli, ["checkout", "feature"])
        _write(repo, "feature.mid", "feature content")
        _commit("feature addition")
        feature_id = get_head_commit_id(repo, "feature")
        assert feature_id is not None

        runner.invoke(cli, ["checkout", "main"])
        result = runner.invoke(cli, ["cherry-pick", feature_id])
        assert result.exit_code == 0, result.output

        picked_id = _head_id(repo)
        picked_commit = read_commit(repo, picked_id)
        assert picked_commit is not None
        snap = read_snapshot(repo, picked_commit.snapshot_id)
        assert snap is not None
        assert "feature.mid" in snap.manifest
        assert "base.mid" in snap.manifest

    def test_cherry_pick_snapshot_objects_in_store(self, repo: pathlib.Path) -> None:
        """Objects in the cherry-pick snapshot are already in the store."""
        _write(repo, "base.mid", "base")
        _commit("base")

        runner.invoke(cli, ["branch", "feature"])
        runner.invoke(cli, ["checkout", "feature"])
        _write(repo, "extra.mid", "extra")
        _commit("feature extra")
        feature_id = get_head_commit_id(repo, "feature")
        assert feature_id is not None

        runner.invoke(cli, ["checkout", "main"])
        runner.invoke(cli, ["cherry-pick", feature_id])

        picked_id = _head_id(repo)
        picked_commit = read_commit(repo, picked_id)
        assert picked_commit is not None
        snap = read_snapshot(repo, picked_commit.snapshot_id)
        assert snap is not None

        from muse.core.object_store import has_object
        for oid in snap.manifest.values():
            assert has_object(repo, oid), f"Object {oid[:8]} missing from store"

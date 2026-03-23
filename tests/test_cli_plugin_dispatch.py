"""Integration tests verifying CLI commands dispatch through the domain plugin.

Each test confirms that the relevant plugin method is called when the CLI
command runs, and that the command's output matches the plugin's semantics.
These tests use unittest.mock.patch to intercept plugin calls and also
perform end-to-end output assertions.
"""

import pathlib
from unittest.mock import MagicMock, patch

import pytest
from tests.cli_test_helper import CliRunner

cli = None  # argparse migration — CliRunner ignores this arg
from muse.domain import (
    DeleteOp,
    DriftReport,
    InsertOp,
    LiveState,
    MergeResult,
    MuseDomainPlugin,
    SnapshotManifest,
    StateSnapshot,
    StructuredDelta,
)
from muse.plugins.midi.plugin import MidiPlugin

runner = CliRunner()


@pytest.fixture
def repo(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    """Initialise a fresh Muse repo in tmp_path and set it as cwd."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MUSE_REPO_ROOT", str(tmp_path))
    result = runner.invoke(cli, ["init"])
    assert result.exit_code == 0, result.output
    return tmp_path


def _write(repo: pathlib.Path, filename: str, content: str = "data") -> None:
    (repo / filename).write_text(content)


def _commit(msg: str = "initial") -> None:
    result = runner.invoke(cli, ["commit", "-m", msg])
    assert result.exit_code == 0, result.output


# ---------------------------------------------------------------------------
# commit
# ---------------------------------------------------------------------------


class TestCommitDispatch:
    def test_commit_calls_plugin_snapshot(self, repo: pathlib.Path) -> None:
        _write(repo, "beat.mid", "drums")
        with patch("muse.cli.commands.commit.resolve_plugin") as mock_resolve:
            real_plugin = MidiPlugin()
            mock_plugin = MagicMock(spec=MuseDomainPlugin)
            mock_plugin.snapshot.side_effect = real_plugin.snapshot
            mock_resolve.return_value = mock_plugin

            result = runner.invoke(cli, ["commit", "-m", "test"])
            assert result.exit_code == 0, result.output
            mock_plugin.snapshot.assert_called_once()

    def test_commit_snapshot_argument_is_workdir_path(self, repo: pathlib.Path) -> None:
        _write(repo, "beat.mid", "drums")
        captured_args: list[LiveState] = []

        with patch("muse.cli.commands.commit.resolve_plugin") as mock_resolve:
            real_plugin = MidiPlugin()
            mock_plugin = MagicMock(spec=MuseDomainPlugin)

            def capture_snapshot(live_state: LiveState) -> SnapshotManifest:
                captured_args.append(live_state)
                return real_plugin.snapshot(live_state)

            mock_plugin.snapshot.side_effect = capture_snapshot
            mock_resolve.return_value = mock_plugin

            runner.invoke(cli, ["commit", "-m", "test"])
            assert len(captured_args) == 1
            assert isinstance(captured_args[0], pathlib.Path)
            # snapshot() receives the repository root (the working tree), not a subdirectory
            assert (captured_args[0] / ".muse").exists()

    def test_commit_uses_snapshot_files_for_manifest(self, repo: pathlib.Path) -> None:
        _write(repo, "track.mid", "content")
        result = runner.invoke(cli, ["commit", "-m", "via plugin"])
        assert result.exit_code == 0
        assert "via plugin" in result.output


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


class TestStatusDispatch:
    def test_status_calls_plugin_drift(self, repo: pathlib.Path) -> None:
        _write(repo, "beat.mid")
        _commit()
        _write(repo, "new.mid", "extra")

        with patch("muse.cli.commands.status.resolve_plugin") as mock_resolve:
            real_plugin = MidiPlugin()
            mock_plugin = MagicMock(spec=MuseDomainPlugin)
            mock_plugin.drift.side_effect = real_plugin.drift
            mock_resolve.return_value = mock_plugin

            result = runner.invoke(cli, ["status"])
            assert result.exit_code == 0
            mock_plugin.drift.assert_called_once()

    def test_status_clean_tree_via_drift(self, repo: pathlib.Path) -> None:
        _write(repo, "beat.mid")
        _commit()
        result = runner.invoke(cli, ["status"])
        assert result.exit_code == 0
        assert "clean" in result.output

    def test_status_shows_new_file(self, repo: pathlib.Path) -> None:
        _write(repo, "beat.mid")
        _commit()
        _write(repo, "new.mid", "extra")
        result = runner.invoke(cli, ["status"])
        assert result.exit_code == 0
        assert "new.mid" in result.output

    def test_status_shows_deleted_file(self, repo: pathlib.Path) -> None:
        _write(repo, "beat.mid")
        _commit()
        (repo / "beat.mid").unlink()
        result = runner.invoke(cli, ["status"])
        assert result.exit_code == 0
        assert "beat.mid" in result.output

    def test_status_drift_report_drives_output(self, repo: pathlib.Path) -> None:
        """Patch drift() to return a controlled DriftReport and verify CLI echoes it."""
        _write(repo, "beat.mid")
        _commit()

        fake_delta = StructuredDelta(
            domain="midi",
            ops=[InsertOp(op="insert", address="injected.mid", position=None,
                          content_id="abc123", content_summary="new file: injected.mid")],
            summary="1 file added",
        )
        fake_report = DriftReport(has_drift=True, summary="1 added", delta=fake_delta)

        with patch("muse.cli.commands.status.resolve_plugin") as mock_resolve:
            mock_plugin = MagicMock(spec=MuseDomainPlugin)
            mock_plugin.drift.return_value = fake_report
            mock_resolve.return_value = mock_plugin

            result = runner.invoke(cli, ["status"])
            assert "injected.mid" in result.output


# ---------------------------------------------------------------------------
# diff
# ---------------------------------------------------------------------------


class TestDiffDispatch:
    def test_diff_calls_plugin_diff(self, repo: pathlib.Path) -> None:
        _write(repo, "beat.mid")
        _commit()
        _write(repo, "lead.mid", "solo")

        with patch("muse.cli.commands.diff.resolve_plugin") as mock_resolve:
            real_plugin = MidiPlugin()
            mock_plugin = MagicMock(spec=MuseDomainPlugin)
            mock_plugin.snapshot.side_effect = real_plugin.snapshot
            mock_plugin.diff.side_effect = real_plugin.diff
            mock_resolve.return_value = mock_plugin

            result = runner.invoke(cli, ["diff"])
            assert result.exit_code == 0
            mock_plugin.snapshot.assert_called_once()
            mock_plugin.diff.assert_called_once()

    def test_diff_calls_plugin_snapshot_for_workdir(self, repo: pathlib.Path) -> None:
        _write(repo, "beat.mid")
        _commit()
        _write(repo, "extra.mid", "new")

        captured: list[LiveState] = []
        with patch("muse.cli.commands.diff.resolve_plugin") as mock_resolve:
            real_plugin = MidiPlugin()
            mock_plugin = MagicMock(spec=MuseDomainPlugin)

            def cap_snapshot(ls: LiveState) -> SnapshotManifest:
                captured.append(ls)
                return real_plugin.snapshot(ls)

            mock_plugin.snapshot.side_effect = cap_snapshot
            mock_plugin.diff.side_effect = real_plugin.diff
            mock_resolve.return_value = mock_plugin

            runner.invoke(cli, ["diff"])
            assert any(isinstance(a, pathlib.Path) for a in captured)

    def test_diff_shows_added_file(self, repo: pathlib.Path) -> None:
        _write(repo, "beat.mid")
        _commit()
        _write(repo, "new.mid", "extra")
        result = runner.invoke(cli, ["diff"])
        assert result.exit_code == 0
        assert "new.mid" in result.output

    def test_diff_no_differences(self, repo: pathlib.Path) -> None:
        _write(repo, "beat.mid")
        _commit()
        result = runner.invoke(cli, ["diff"])
        assert result.exit_code == 0
        assert "No differences" in result.output

    def test_diff_delta_drives_output(self, repo: pathlib.Path) -> None:
        """Patch plugin.diff() to return a controlled delta and verify CLI output."""
        _write(repo, "beat.mid")
        _commit()

        fake_delta = StructuredDelta(
            domain="midi",
            ops=[
                InsertOp(op="insert", address="injected.mid", position=None,
                         content_id="abc123", content_summary="new file: injected.mid"),
                DeleteOp(op="delete", address="gone.mid", position=None,
                         content_id="def456", content_summary="deleted: gone.mid"),
            ],
            summary="1 file added, 1 file removed",
        )
        with patch("muse.cli.commands.diff.resolve_plugin") as mock_resolve:
            real_plugin = MidiPlugin()
            mock_plugin = MagicMock(spec=MuseDomainPlugin)
            mock_plugin.snapshot.side_effect = real_plugin.snapshot
            mock_plugin.diff.return_value = fake_delta
            mock_resolve.return_value = mock_plugin

            result = runner.invoke(cli, ["diff"])
            assert "injected.mid" in result.output
            assert "gone.mid" in result.output


# ---------------------------------------------------------------------------
# merge
# ---------------------------------------------------------------------------


class TestMergeDispatch:
    def test_merge_calls_plugin_merge(self, repo: pathlib.Path) -> None:
        _write(repo, "beat.mid", "v1")
        _commit("base")

        runner.invoke(cli, ["branch", "feature"])
        runner.invoke(cli, ["checkout", "feature"])
        _write(repo, "lead.mid", "solo")
        _commit("add lead")

        runner.invoke(cli, ["checkout", "main"])
        # Add a commit on main so both branches have diverged — forces a real merge.
        _write(repo, "bass.mid", "bass line")
        _commit("add bass on main")

        with patch("muse.cli.commands.merge.resolve_plugin") as mock_resolve:
            real_plugin = MidiPlugin()
            mock_plugin = MagicMock(spec=MuseDomainPlugin)
            mock_plugin.merge.side_effect = real_plugin.merge
            mock_resolve.return_value = mock_plugin

            result = runner.invoke(cli, ["merge", "feature"])
            assert result.exit_code == 0
            mock_plugin.merge.assert_called_once()

    def test_merge_plugin_merge_result_drives_outcome(self, repo: pathlib.Path) -> None:
        _write(repo, "beat.mid", "v1")
        _commit("base")

        runner.invoke(cli, ["branch", "feature"])
        runner.invoke(cli, ["checkout", "feature"])
        _write(repo, "lead.mid", "solo")
        _commit("add lead")

        runner.invoke(cli, ["checkout", "main"])
        _write(repo, "bass.mid", "bass line")
        _commit("add bass on main")

        fake_result = MergeResult(
            merged=SnapshotManifest(files={"injected.mid": "a" * 64}, domain="midi"),
            conflicts=[],
        )
        with patch("muse.cli.commands.merge.resolve_plugin") as mock_resolve:
            mock_plugin = MagicMock(spec=MuseDomainPlugin)
            mock_plugin.merge.return_value = fake_result
            mock_resolve.return_value = mock_plugin

            result = runner.invoke(cli, ["merge", "feature"])
            assert result.exit_code == 0
            assert "Merge" in result.output

    def test_merge_conflict_uses_plugin_conflict_paths(self, repo: pathlib.Path) -> None:
        _write(repo, "beat.mid", "original")
        _commit("base")

        runner.invoke(cli, ["branch", "feature"])
        runner.invoke(cli, ["checkout", "feature"])
        _write(repo, "beat.mid", "feature-version")
        _commit("feature changes beat")

        runner.invoke(cli, ["checkout", "main"])
        _write(repo, "beat.mid", "main-version")
        _commit("main changes beat")

        result = runner.invoke(cli, ["merge", "feature"])
        assert result.exit_code != 0
        assert "beat.mid" in result.output

    def test_merge_conflict_paths_come_from_plugin(self, repo: pathlib.Path) -> None:
        _write(repo, "beat.mid", "original")
        _commit("base")
        runner.invoke(cli, ["branch", "feature"])
        runner.invoke(cli, ["checkout", "feature"])
        _write(repo, "beat.mid", "feature-version")
        _commit("feature")
        runner.invoke(cli, ["checkout", "main"])
        _write(repo, "beat.mid", "main-version")
        _commit("main")

        fake_result = MergeResult(
            merged=SnapshotManifest(files={}, domain="midi"),
            conflicts=["plugin-conflict.mid"],
        )
        with patch("muse.cli.commands.merge.resolve_plugin") as mock_resolve:
            mock_plugin = MagicMock(spec=MuseDomainPlugin)
            mock_plugin.merge.return_value = fake_result
            mock_resolve.return_value = mock_plugin

            result = runner.invoke(cli, ["merge", "feature"])
            assert result.exit_code != 0
            assert "plugin-conflict.mid" in result.output


# ---------------------------------------------------------------------------
# cherry-pick
# ---------------------------------------------------------------------------


class TestCherryPickDispatch:
    def test_cherry_pick_calls_plugin_merge(self, repo: pathlib.Path) -> None:
        _write(repo, "beat.mid", "v1")
        _commit("initial")

        runner.invoke(cli, ["branch", "feature"])
        runner.invoke(cli, ["checkout", "feature"])
        _write(repo, "lead.mid", "solo")
        _commit("add lead on feature")

        from muse.core.store import get_head_commit_id
        from muse.core.repo import require_repo
        import os
        os.chdir(repo)
        feature_tip = get_head_commit_id(repo, "feature")
        assert feature_tip is not None

        runner.invoke(cli, ["checkout", "main"])

        with patch("muse.cli.commands.cherry_pick.resolve_plugin") as mock_resolve:
            real_plugin = MidiPlugin()
            mock_plugin = MagicMock(spec=MuseDomainPlugin)
            mock_plugin.merge.side_effect = real_plugin.merge
            mock_resolve.return_value = mock_plugin

            result = runner.invoke(cli, ["cherry-pick", feature_tip])
            assert result.exit_code == 0, result.output
            mock_plugin.merge.assert_called_once()

    def test_cherry_pick_three_way_args_are_snapshot_manifests(
        self, repo: pathlib.Path
    ) -> None:
        _write(repo, "beat.mid", "v1")
        _commit("initial")

        runner.invoke(cli, ["branch", "feature"])
        runner.invoke(cli, ["checkout", "feature"])
        _write(repo, "lead.mid", "solo")
        _commit("add lead")

        import os
        os.chdir(repo)
        from muse.core.store import get_head_commit_id
        feature_tip = get_head_commit_id(repo, "feature")
        assert feature_tip is not None

        runner.invoke(cli, ["checkout", "main"])

        captured_args: list[tuple[StateSnapshot, StateSnapshot, StateSnapshot]] = []
        with patch("muse.cli.commands.cherry_pick.resolve_plugin") as mock_resolve:
            real_plugin = MidiPlugin()
            mock_plugin = MagicMock(spec=MuseDomainPlugin)

            def cap_merge(
                base: StateSnapshot, left: StateSnapshot, right: StateSnapshot
            ) -> MergeResult:
                captured_args.append((base, left, right))
                return real_plugin.merge(base, left, right)

            mock_plugin.merge.side_effect = cap_merge
            mock_resolve.return_value = mock_plugin

            runner.invoke(cli, ["cherry-pick", feature_tip])
            assert len(captured_args) == 1
            base, left, right = captured_args[0]
            assert isinstance(base, dict) and "files" in base
            assert isinstance(left, dict) and "files" in left
            assert isinstance(right, dict) and "files" in right


# ---------------------------------------------------------------------------
# stash
# ---------------------------------------------------------------------------


class TestStashDispatch:
    def test_stash_calls_plugin_snapshot(self, repo: pathlib.Path) -> None:
        _write(repo, "beat.mid")
        _commit()
        _write(repo, "unsaved.mid", "wip")

        with patch("muse.cli.commands.stash.resolve_plugin") as mock_resolve:
            real_plugin = MidiPlugin()
            mock_plugin = MagicMock(spec=MuseDomainPlugin)
            mock_plugin.snapshot.side_effect = real_plugin.snapshot
            mock_resolve.return_value = mock_plugin

            result = runner.invoke(cli, ["stash"])
            assert result.exit_code == 0
            mock_plugin.snapshot.assert_called_once()

    def test_stash_snapshot_argument_is_workdir_path(self, repo: pathlib.Path) -> None:
        _write(repo, "beat.mid")
        _commit()
        _write(repo, "unsaved.mid", "wip")

        captured: list[LiveState] = []
        with patch("muse.cli.commands.stash.resolve_plugin") as mock_resolve:
            real_plugin = MidiPlugin()
            mock_plugin = MagicMock(spec=MuseDomainPlugin)

            def cap_snapshot(ls: LiveState) -> SnapshotManifest:
                captured.append(ls)
                return real_plugin.snapshot(ls)

            mock_plugin.snapshot.side_effect = cap_snapshot
            mock_resolve.return_value = mock_plugin

            runner.invoke(cli, ["stash"])
            assert len(captured) == 1
            assert isinstance(captured[0], pathlib.Path)

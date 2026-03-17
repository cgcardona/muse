"""Tests for muse.plugins.music.plugin — the MuseDomainPlugin reference implementation."""
from __future__ import annotations

import pathlib

import pytest

from muse.domain import DriftReport, MergeResult, MuseDomainPlugin, SnapshotManifest
from muse.plugins.music.plugin import MusicPlugin, content_hash, plugin


class TestProtocolConformance:
    def test_plugin_satisfies_protocol(self) -> None:
        assert isinstance(plugin, MuseDomainPlugin)

    def test_module_singleton_is_music_plugin(self) -> None:
        assert isinstance(plugin, MusicPlugin)


class TestSnapshot:
    def test_from_dict_passthrough(self) -> None:
        snap = SnapshotManifest(files={"a.mid": "h1"}, domain="music")
        assert plugin.snapshot(snap) is snap

    def test_from_workdir(self, tmp_path: pathlib.Path) -> None:
        workdir = tmp_path / "muse-work"
        workdir.mkdir()
        (workdir / "beat.mid").write_bytes(b"drums")
        snap = plugin.snapshot(workdir)
        assert "files" in snap
        assert "beat.mid" in snap["files"]
        assert snap["domain"] == "music"

    def test_empty_workdir(self, tmp_path: pathlib.Path) -> None:
        workdir = tmp_path / "muse-work"
        workdir.mkdir()
        snap = plugin.snapshot(workdir)
        assert snap["files"] == {}


class TestDiff:
    def test_no_change(self) -> None:
        snap = SnapshotManifest(files={"a.mid": "h1"}, domain="music")
        delta = plugin.diff(snap, snap)
        assert delta["added"] == []
        assert delta["removed"] == []
        assert delta["modified"] == []

    def test_added_file(self) -> None:
        base = SnapshotManifest(files={}, domain="music")
        target = SnapshotManifest(files={"new.mid": "h1"}, domain="music")
        delta = plugin.diff(base, target)
        assert "new.mid" in delta["added"]

    def test_removed_file(self) -> None:
        base = SnapshotManifest(files={"old.mid": "h1"}, domain="music")
        target = SnapshotManifest(files={}, domain="music")
        delta = plugin.diff(base, target)
        assert "old.mid" in delta["removed"]

    def test_modified_file(self) -> None:
        base = SnapshotManifest(files={"f.mid": "old"}, domain="music")
        target = SnapshotManifest(files={"f.mid": "new"}, domain="music")
        delta = plugin.diff(base, target)
        assert "f.mid" in delta["modified"]


class TestMerge:
    def _snap(self, files: dict[str, str]) -> SnapshotManifest:
        return SnapshotManifest(files=files, domain="music")

    def test_clean_merge(self) -> None:
        base = self._snap({"a.mid": "h0", "b.mid": "h0"})
        left = self._snap({"a.mid": "h_left", "b.mid": "h0"})
        right = self._snap({"a.mid": "h0", "b.mid": "h_right"})
        result = plugin.merge(base, left, right)
        assert isinstance(result, MergeResult)
        assert result.is_clean
        assert result.merged["files"]["a.mid"] == "h_left"
        assert result.merged["files"]["b.mid"] == "h_right"

    def test_conflict_detected(self) -> None:
        base = self._snap({"a.mid": "h0"})
        left = self._snap({"a.mid": "h_left"})
        right = self._snap({"a.mid": "h_right"})
        result = plugin.merge(base, left, right)
        assert not result.is_clean
        assert result.conflicts == ["a.mid"]

    def test_both_delete_same_file(self) -> None:
        base = self._snap({"a.mid": "h0", "b.mid": "h0"})
        left = self._snap({"b.mid": "h0"})
        right = self._snap({"b.mid": "h0"})
        result = plugin.merge(base, left, right)
        assert result.is_clean
        assert "a.mid" not in result.merged["files"]


class TestDrift:
    def _snap(self, files: dict[str, str]) -> SnapshotManifest:
        return SnapshotManifest(files=files, domain="music")

    def test_no_drift(self) -> None:
        snap = self._snap({"a.mid": "h1"})
        report = plugin.drift(snap, snap)
        assert isinstance(report, DriftReport)
        assert not report.has_drift

    def test_has_drift_on_addition(self) -> None:
        committed = self._snap({"a.mid": "h1"})
        live = self._snap({"a.mid": "h1", "b.mid": "h2"})
        report = plugin.drift(committed, live)
        assert report.has_drift
        assert "added" in report.summary


class TestContentHash:
    def test_deterministic(self) -> None:
        snap = SnapshotManifest(files={"a.mid": "h1"}, domain="music")
        assert content_hash(snap) == content_hash(snap)

    def test_different_content_different_hash(self) -> None:
        a = SnapshotManifest(files={"a.mid": "h1"}, domain="music")
        b = SnapshotManifest(files={"a.mid": "h2"}, domain="music")
        assert content_hash(a) != content_hash(b)

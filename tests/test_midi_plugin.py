"""Tests for muse.plugins.midi.plugin — the MuseDomainPlugin reference implementation."""

import pathlib

import pytest

from muse.domain import DriftReport, MergeResult, MuseDomainPlugin, SnapshotManifest
from muse.plugins.midi.plugin import MidiPlugin, content_hash, plugin


def _snap(files: dict[str, str]) -> SnapshotManifest:
    return SnapshotManifest(files=files, domain="midi")


class TestProtocolConformance:
    def test_plugin_satisfies_protocol(self) -> None:
        assert isinstance(plugin, MuseDomainPlugin)

    def test_module_singleton_is_music_plugin(self) -> None:
        assert isinstance(plugin, MidiPlugin)


class TestSnapshot:
    def test_from_dict_passthrough(self) -> None:
        snap = SnapshotManifest(files={"a.mid": "h1"}, domain="midi")
        assert plugin.snapshot(snap) is snap

    def test_from_workdir(self, tmp_path: pathlib.Path) -> None:
        workdir = tmp_path / "state"
        workdir.mkdir()
        (workdir / "beat.mid").write_bytes(b"drums")
        snap = plugin.snapshot(workdir)
        assert "files" in snap
        assert "beat.mid" in snap["files"]
        assert snap["domain"] == "midi"

    def test_empty_workdir(self, tmp_path: pathlib.Path) -> None:
        workdir = tmp_path / "state"
        workdir.mkdir()
        snap = plugin.snapshot(workdir)
        assert snap["files"] == {}


class TestDiff:
    """diff() returns a StructuredDelta with typed ops — not the old DeltaManifest."""

    def test_no_change_empty_ops(self) -> None:
        snap = _snap({"a.mid": "h1"})
        delta = plugin.diff(snap, snap)
        assert delta["ops"] == []

    def test_no_change_summary(self) -> None:
        snap = _snap({"a.mid": "h1"})
        delta = plugin.diff(snap, snap)
        assert delta["summary"] == "no changes"

    def test_added_file_produces_insert_op(self) -> None:
        base = _snap({})
        target = _snap({"new.mid": "h1"})
        delta = plugin.diff(base, target)
        insert_ops = [op for op in delta["ops"] if op["op"] == "insert"]
        assert any(op["address"] == "new.mid" for op in insert_ops)

    def test_removed_file_produces_delete_op(self) -> None:
        base = _snap({"old.mid": "h1"})
        target = _snap({})
        delta = plugin.diff(base, target)
        delete_ops = [op for op in delta["ops"] if op["op"] == "delete"]
        assert any(op["address"] == "old.mid" for op in delete_ops)

    def test_modified_file_produces_replace_or_patch_op(self) -> None:
        base = _snap({"f.mid": "old"})
        target = _snap({"f.mid": "new"})
        delta = plugin.diff(base, target)
        changed_ops = [op for op in delta["ops"] if op["op"] in ("replace", "patch")]
        assert any(op["address"] == "f.mid" for op in changed_ops)

    def test_domain_is_music(self) -> None:
        snap = _snap({"a.mid": "h"})
        delta = plugin.diff(snap, snap)
        assert delta["domain"] == "midi"


class TestMerge:
    def test_clean_merge(self) -> None:
        base = _snap({"a.mid": "h0", "b.mid": "h0"})
        left = _snap({"a.mid": "h_left", "b.mid": "h0"})
        right = _snap({"a.mid": "h0", "b.mid": "h_right"})
        result = plugin.merge(base, left, right)
        assert isinstance(result, MergeResult)
        assert result.is_clean
        assert result.merged["files"]["a.mid"] == "h_left"
        assert result.merged["files"]["b.mid"] == "h_right"

    def test_conflict_detected(self) -> None:
        base = _snap({"a.mid": "h0"})
        left = _snap({"a.mid": "h_left"})
        right = _snap({"a.mid": "h_right"})
        result = plugin.merge(base, left, right)
        assert not result.is_clean
        assert result.conflicts == ["a.mid"]

    def test_both_delete_same_file(self) -> None:
        base = _snap({"a.mid": "h0", "b.mid": "h0"})
        left = _snap({"b.mid": "h0"})
        right = _snap({"b.mid": "h0"})
        result = plugin.merge(base, left, right)
        assert result.is_clean
        assert "a.mid" not in result.merged["files"]


class TestDrift:
    def test_no_drift(self) -> None:
        snap = _snap({"a.mid": "h1"})
        report = plugin.drift(snap, snap)
        assert isinstance(report, DriftReport)
        assert not report.has_drift

    def test_has_drift_on_addition(self) -> None:
        committed = _snap({"a.mid": "h1"})
        live = _snap({"a.mid": "h1", "b.mid": "h2"})
        report = plugin.drift(committed, live)
        assert report.has_drift
        assert "added" in report.summary

    def test_drift_delta_is_structured(self) -> None:
        snap = _snap({"a.mid": "h1"})
        report = plugin.drift(snap, snap)
        assert "ops" in report.delta
        assert "summary" in report.delta
        assert "domain" in report.delta


class TestContentHash:
    def test_deterministic(self) -> None:
        snap = SnapshotManifest(files={"a.mid": "h1"}, domain="midi")
        assert content_hash(snap) == content_hash(snap)

    def test_different_content_different_hash(self) -> None:
        a = SnapshotManifest(files={"a.mid": "h1"}, domain="midi")
        b = SnapshotManifest(files={"a.mid": "h2"}, domain="midi")
        assert content_hash(a) != content_hash(b)

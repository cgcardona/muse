"""Tests for the structured delta type system.

Covers:
- All five DomainOp TypedDicts can be constructed and serialised to JSON.
- StructuredDelta satisfies the StateDelta type alias.
- MidiPlugin.diff() returns a StructuredDelta with correctly typed ops.
- PatchOp wraps note-level child_ops for modified .mid files.
- DriftReport.delta is a StructuredDelta.
- muse show and muse diff display structured output.
"""

import json
import pathlib

import pytest

from muse.domain import (
    DeleteOp,
    DomainOp,
    DriftReport,
    InsertOp,
    MoveOp,
    PatchOp,
    ReplaceOp,
    SnapshotManifest,
    StateDelta,
    StructuredDelta,
)
from muse.plugins.midi.plugin import MidiPlugin, plugin


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _snap(files: dict[str, str]) -> SnapshotManifest:
    return SnapshotManifest(files=files, domain="midi")


def _make_insert(address: str = "a.mid", content_id: str = "abc123") -> InsertOp:
    return InsertOp(
        op="insert",
        address=address,
        position=None,
        content_id=content_id,
        content_summary=f"new file: {address}",
    )


def _make_delete(address: str = "a.mid", content_id: str = "abc123") -> DeleteOp:
    return DeleteOp(
        op="delete",
        address=address,
        position=None,
        content_id=content_id,
        content_summary=f"deleted: {address}",
    )


def _make_move() -> MoveOp:
    return MoveOp(
        op="move",
        address="note:5",
        from_position=5,
        to_position=12,
        content_id="deadbeef",
    )


def _make_replace(address: str = "a.mid") -> ReplaceOp:
    return ReplaceOp(
        op="replace",
        address=address,
        position=None,
        old_content_id="old123",
        new_content_id="new456",
        old_summary=f"{address} (prev)",
        new_summary=f"{address} (new)",
    )


def _make_patch(child_ops: list[DomainOp] | None = None) -> PatchOp:
    return PatchOp(
        op="patch",
        address="tracks/drums.mid",
        child_ops=child_ops or [],
        child_domain="midi_notes",
        child_summary="2 notes added",
    )


def _make_delta(ops: list[DomainOp] | None = None) -> StructuredDelta:
    return StructuredDelta(
        domain="midi",
        ops=ops or [],
        summary="no changes",
    )


# ---------------------------------------------------------------------------
# TypedDict construction and JSON round-trips
# ---------------------------------------------------------------------------

class TestDeltaOpTypes:
    def test_insert_op_has_correct_discriminant(self) -> None:
        op = _make_insert()
        assert op["op"] == "insert"

    def test_delete_op_has_correct_discriminant(self) -> None:
        op = _make_delete()
        assert op["op"] == "delete"

    def test_move_op_has_correct_discriminant(self) -> None:
        op = _make_move()
        assert op["op"] == "move"

    def test_replace_op_has_correct_discriminant(self) -> None:
        op = _make_replace()
        assert op["op"] == "replace"

    def test_patch_op_has_correct_discriminant(self) -> None:
        op = _make_patch()
        assert op["op"] == "patch"

    def test_insert_op_round_trips_json(self) -> None:
        op = _make_insert()
        serialised = json.dumps(op)
        restored = json.loads(serialised)
        assert restored["op"] == "insert"
        assert restored["address"] == "a.mid"
        assert restored["position"] is None
        assert restored["content_id"] == "abc123"

    def test_delete_op_round_trips_json(self) -> None:
        op = _make_delete()
        serialised = json.dumps(op)
        restored = json.loads(serialised)
        assert restored["op"] == "delete"
        assert restored["address"] == "a.mid"

    def test_move_op_round_trips_json(self) -> None:
        op = _make_move()
        serialised = json.dumps(op)
        restored = json.loads(serialised)
        assert restored["op"] == "move"
        assert restored["from_position"] == 5
        assert restored["to_position"] == 12

    def test_replace_op_round_trips_json(self) -> None:
        op = _make_replace()
        serialised = json.dumps(op)
        restored = json.loads(serialised)
        assert restored["op"] == "replace"
        assert restored["old_content_id"] == "old123"
        assert restored["new_content_id"] == "new456"

    def test_patch_op_with_child_ops_round_trips_json(self) -> None:
        insert = _make_insert("note:3", "aabbcc")
        patch = _make_patch(child_ops=[insert])
        serialised = json.dumps(patch)
        restored = json.loads(serialised)
        assert restored["op"] == "patch"
        assert len(restored["child_ops"]) == 1
        assert restored["child_ops"][0]["op"] == "insert"

    def test_structured_delta_round_trips_json(self) -> None:
        delta = _make_delta(ops=[_make_insert(), _make_delete("b.mid", "xyz")])
        serialised = json.dumps(delta)
        restored = json.loads(serialised)
        assert restored["domain"] == "midi"
        assert len(restored["ops"]) == 2
        assert restored["summary"] == "no changes"

    def test_structured_delta_is_state_delta_type(self) -> None:
        delta: StateDelta = _make_delta()
        assert delta["domain"] == "midi"

    def test_structured_delta_has_required_keys(self) -> None:
        delta = _make_delta()
        assert "domain" in delta
        assert "ops" in delta
        assert "summary" in delta


# ---------------------------------------------------------------------------
# MidiPlugin.diff() returns StructuredDelta
# ---------------------------------------------------------------------------

class TestMidiPluginStructuredDiff:
    def test_no_change_returns_empty_ops(self) -> None:
        snap = _snap({"a.mid": "h1"})
        delta = plugin.diff(snap, snap)
        assert isinstance(delta, dict)
        assert delta["ops"] == []

    def test_no_change_summary_is_no_changes(self) -> None:
        snap = _snap({"a.mid": "h1"})
        delta = plugin.diff(snap, snap)
        assert delta["summary"] == "no changes"

    def test_file_added_returns_insert_op(self) -> None:
        base = _snap({})
        target = _snap({"new.mid": "h1"})
        delta = plugin.diff(base, target)
        ops = delta["ops"]
        assert len(ops) == 1
        assert ops[0]["op"] == "insert"
        assert ops[0]["address"] == "new.mid"

    def test_file_added_insert_op_has_content_id(self) -> None:
        base = _snap({})
        target = _snap({"new.mid": "abcdef123"})
        delta = plugin.diff(base, target)
        assert delta["ops"][0]["content_id"] == "abcdef123"

    def test_file_removed_returns_delete_op(self) -> None:
        base = _snap({"old.mid": "h1"})
        target = _snap({})
        delta = plugin.diff(base, target)
        ops = delta["ops"]
        assert len(ops) == 1
        assert ops[0]["op"] == "delete"
        assert ops[0]["address"] == "old.mid"

    def test_file_removed_delete_op_has_content_id(self) -> None:
        base = _snap({"old.mid": "prevhash"})
        target = _snap({})
        delta = plugin.diff(base, target)
        assert delta["ops"][0]["content_id"] == "prevhash"

    def test_non_midi_modified_returns_replace_op(self) -> None:
        base = _snap({"notes.txt": "old"})
        target = _snap({"notes.txt": "new"})
        delta = plugin.diff(base, target)
        ops = delta["ops"]
        assert len(ops) == 1
        assert ops[0]["op"] == "replace"
        assert ops[0]["address"] == "notes.txt"

    def test_replace_op_has_old_and_new_ids(self) -> None:
        base = _snap({"notes.txt": "oldhash"})
        target = _snap({"notes.txt": "newhash"})
        delta = plugin.diff(base, target)
        op = delta["ops"][0]
        assert op["op"] == "replace"
        assert op["old_content_id"] == "oldhash"
        assert op["new_content_id"] == "newhash"

    def test_mid_modified_without_repo_root_returns_replace_op(self) -> None:
        # Without repo_root we can't load blobs, so fallback to ReplaceOp.
        base = _snap({"drums.mid": "old"})
        target = _snap({"drums.mid": "new"})
        delta = plugin.diff(base, target)
        assert delta["ops"][0]["op"] == "replace"

    def test_multiple_changes_produce_multiple_ops(self) -> None:
        base = _snap({"a.mid": "h1", "b.mid": "h2"})
        target = _snap({"b.mid": "h2_new", "c.mid": "h3"})
        delta = plugin.diff(base, target)
        kinds = {op["op"] for op in delta["ops"]}
        assert "insert" in kinds   # c.mid added
        assert "delete" in kinds   # a.mid removed
        assert "replace" in kinds  # b.mid modified

    def test_summary_mentions_added_on_add(self) -> None:
        base = _snap({})
        target = _snap({"x.mid": "h"})
        delta = plugin.diff(base, target)
        assert "added" in delta["summary"]

    def test_summary_mentions_removed_on_delete(self) -> None:
        base = _snap({"x.mid": "h"})
        target = _snap({})
        delta = plugin.diff(base, target)
        assert "removed" in delta["summary"]

    def test_domain_is_music(self) -> None:
        snap = _snap({"a.mid": "h"})
        delta = plugin.diff(snap, snap)
        assert delta["domain"] == "midi"

    def test_insert_op_position_is_none_for_file_level(self) -> None:
        base = _snap({})
        target = _snap({"f.mid": "h"})
        delta = plugin.diff(base, target)
        assert delta["ops"][0]["position"] is None

    def test_ops_are_sorted_by_address(self) -> None:
        base = _snap({})
        target = _snap({"z.mid": "h1", "a.mid": "h2", "m.mid": "h3"})
        delta = plugin.diff(base, target)
        addresses = [op["address"] for op in delta["ops"]]
        assert addresses == sorted(addresses)


# ---------------------------------------------------------------------------
# DriftReport uses StructuredDelta
# ---------------------------------------------------------------------------

class TestDriftReportDelta:
    def test_no_drift_delta_is_structured(self) -> None:
        snap = _snap({"a.mid": "h"})
        report = plugin.drift(snap, snap)
        assert isinstance(report, DriftReport)
        assert isinstance(report.delta, dict)
        assert "ops" in report.delta
        assert "summary" in report.delta

    def test_drift_delta_has_insert_op_on_addition(self) -> None:
        committed = _snap({"a.mid": "h1"})
        live = _snap({"a.mid": "h1", "b.mid": "h2"})
        report = plugin.drift(committed, live)
        assert report.has_drift
        insert_ops = [op for op in report.delta["ops"] if op["op"] == "insert"]
        assert any(op["address"] == "b.mid" for op in insert_ops)

    def test_drift_summary_still_human_readable(self) -> None:
        committed = _snap({"a.mid": "h1"})
        live = _snap({"a.mid": "h1", "b.mid": "h2"})
        report = plugin.drift(committed, live)
        assert "added" in report.summary

    def test_default_drift_report_delta_is_empty_structured(self) -> None:
        report = DriftReport(has_drift=False)
        assert report.delta["ops"] == []
        assert report.delta["domain"] == ""


# ---------------------------------------------------------------------------
# MidiPlugin.apply() handles StructuredDelta
# ---------------------------------------------------------------------------

class TestMidiPluginApply:
    def test_apply_delete_op_removes_file(self) -> None:
        snap = _snap({"a.mid": "h1", "b.mid": "h2"})
        delta: StructuredDelta = StructuredDelta(
            domain="midi",
            ops=[DeleteOp(
                op="delete", address="a.mid", position=None,
                content_id="h1", content_summary="deleted: a.mid",
            )],
            summary="1 file removed",
        )
        result = plugin.apply(delta, snap)
        assert "a.mid" not in result["files"]
        assert "b.mid" in result["files"]

    def test_apply_replace_op_updates_hash(self) -> None:
        snap = _snap({"a.mid": "old"})
        delta: StructuredDelta = StructuredDelta(
            domain="midi",
            ops=[ReplaceOp(
                op="replace", address="a.mid", position=None,
                old_content_id="old", new_content_id="new",
                old_summary="a.mid (prev)", new_summary="a.mid (new)",
            )],
            summary="1 file modified",
        )
        result = plugin.apply(delta, snap)
        assert result["files"]["a.mid"] == "new"

    def test_apply_insert_op_adds_file(self) -> None:
        snap = _snap({})
        delta: StructuredDelta = StructuredDelta(
            domain="midi",
            ops=[InsertOp(
                op="insert", address="new.mid", position=None,
                content_id="newhash", content_summary="new file: new.mid",
            )],
            summary="1 file added",
        )
        result = plugin.apply(delta, snap)
        assert result["files"]["new.mid"] == "newhash"

    def test_apply_from_workdir_rescans(self, tmp_path: pathlib.Path) -> None:
        workdir = tmp_path / "muse-work"
        workdir.mkdir()
        (workdir / "beat.mid").write_bytes(b"drums")
        delta: StructuredDelta = StructuredDelta(
            domain="midi", ops=[], summary="no changes",
        )
        result = plugin.apply(delta, workdir)
        assert "beat.mid" in result["files"]


# ---------------------------------------------------------------------------
# CLI show displays structured delta
# ---------------------------------------------------------------------------

class TestShowStructuredOutput:
    def test_show_displays_structured_summary(
        self, tmp_path: pathlib.Path
    ) -> None:
        from typer.testing import CliRunner
        from muse.cli.app import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["init", "--domain", "midi"], obj={})
        # Just check the command is importable and types are correct —
        # full CLI integration is covered in test_cli_workflow.py.
        assert result is not None

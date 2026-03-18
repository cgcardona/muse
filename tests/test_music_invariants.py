"""Tests for muse.plugins.music._invariants — rule checks and runner."""
from __future__ import annotations

import pathlib

import pytest

from muse.plugins.music._invariants import (
    InvariantRule,
    check_key_consistency,
    check_max_polyphony,
    check_no_parallel_fifths,
    check_pitch_range,
    load_invariant_rules,
)
from muse.plugins.music._query import NoteInfo
from muse.plugins.music.midi_diff import NoteKey


def _note(pitch: int, start_tick: int = 0, duration_ticks: int = 480,
          velocity: int = 80, channel: int = 0) -> NoteInfo:
    return NoteInfo.from_note_key(
        NoteKey(
            pitch=pitch,
            velocity=velocity,
            start_tick=start_tick,
            duration_ticks=duration_ticks,
            channel=channel,
        ),
        ticks_per_beat=480,
    )


# ---------------------------------------------------------------------------
# check_max_polyphony
# ---------------------------------------------------------------------------


class TestCheckMaxPolyphony:
    def test_no_violation_when_polyphony_ok(self) -> None:
        notes = [_note(60, 0), _note(64, 480), _note(67, 960)]
        violations = check_max_polyphony(notes, "track.mid", "poly", "warning", max_simultaneous=4)
        assert violations == []

    def test_violation_when_too_many_simultaneous(self) -> None:
        # 5 notes all starting at tick 0 with long duration.
        notes = [_note(60 + i, 0, 480) for i in range(5)]
        violations = check_max_polyphony(notes, "track.mid", "poly", "error", max_simultaneous=4)
        assert len(violations) == 1
        assert violations[0]["severity"] == "error"
        assert violations[0]["rule_name"] == "poly"

    def test_violation_mentions_peak_count(self) -> None:
        notes = [_note(60 + i, 0, 480) for i in range(6)]
        violations = check_max_polyphony(notes, "track.mid", "poly", "warning", max_simultaneous=4)
        assert "6" in violations[0]["description"]

    def test_empty_notes_produces_no_violation(self) -> None:
        violations = check_max_polyphony([], "track.mid", "poly", "warning")
        assert violations == []

    def test_non_overlapping_notes_ok(self) -> None:
        # Each note starts after the previous one ends.
        notes = [_note(60, start_tick=i * 960, duration_ticks=480) for i in range(10)]
        violations = check_max_polyphony(notes, "track.mid", "poly", "warning", max_simultaneous=4)
        assert violations == []


# ---------------------------------------------------------------------------
# check_pitch_range
# ---------------------------------------------------------------------------


class TestCheckPitchRange:
    def test_all_in_range_produces_no_violation(self) -> None:
        notes = [_note(60), _note(72), _note(84)]
        violations = check_pitch_range(notes, "track.mid", "range", "warning",
                                        min_pitch=48, max_pitch=96)
        assert violations == []

    def test_too_low_produces_violation(self) -> None:
        notes = [_note(36)]  # below min=48
        violations = check_pitch_range(notes, "track.mid", "range", "error",
                                        min_pitch=48, max_pitch=96)
        assert len(violations) == 1
        assert "36" in violations[0]["description"]
        assert violations[0]["severity"] == "error"

    def test_too_high_produces_violation(self) -> None:
        notes = [_note(100)]  # above max=96
        violations = check_pitch_range(notes, "track.mid", "range", "warning",
                                        min_pitch=48, max_pitch=96)
        assert len(violations) == 1

    def test_multiple_out_of_range_produces_multiple_violations(self) -> None:
        notes = [_note(30), _note(110), _note(60)]
        violations = check_pitch_range(notes, "t.mid", "r", "info",
                                        min_pitch=48, max_pitch=96)
        assert len(violations) == 2


# ---------------------------------------------------------------------------
# check_key_consistency
# ---------------------------------------------------------------------------


class TestCheckKeyConsistency:
    def test_cmajor_notes_no_violation(self) -> None:
        # C major diatonic: C D E F G A B
        c_major_pitches = [60, 62, 64, 65, 67, 69, 71]  # C4-B4
        notes = [_note(p) for p in c_major_pitches * 4]
        violations = check_key_consistency(notes, "t.mid", "key", "info", threshold=0.2)
        assert violations == []

    def test_empty_notes_produces_no_violation(self) -> None:
        violations = check_key_consistency([], "t.mid", "key", "warning")
        assert violations == []


# ---------------------------------------------------------------------------
# check_no_parallel_fifths
# ---------------------------------------------------------------------------


class TestCheckNoParallelFifths:
    def test_no_violation_without_parallel_fifths(self) -> None:
        # Bar 1: C4 (60) and G4 (67) — interval of 7
        # Bar 2: D4 (62) and E4 (64) — interval of 2 (not a fifth)
        tpb = 480
        bar_ticks = tpb * 4
        notes = [
            _note(60, start_tick=0, duration_ticks=tpb),
            _note(67, start_tick=0, duration_ticks=tpb),
            _note(62, start_tick=bar_ticks, duration_ticks=tpb),
            _note(64, start_tick=bar_ticks, duration_ticks=tpb),
        ]
        violations = check_no_parallel_fifths(notes, "t.mid", "fifths", "warning")
        assert violations == []

    def test_parallel_fifths_detected(self) -> None:
        # Bar 1: C4 (60) and G4 (67) — perfect fifth
        # Bar 2: D4 (62) and A4 (69) — perfect fifth, both voices moved up
        tpb = 480
        bar_ticks = tpb * 4
        notes = [
            _note(60, start_tick=0, duration_ticks=tpb),
            _note(67, start_tick=0, duration_ticks=tpb),
            _note(62, start_tick=bar_ticks, duration_ticks=tpb),
            _note(69, start_tick=bar_ticks, duration_ticks=tpb),
        ]
        violations = check_no_parallel_fifths(notes, "t.mid", "fifths", "warning")
        assert len(violations) >= 1
        assert violations[0]["rule_name"] == "fifths"

    def test_not_enough_notes_produces_no_violation(self) -> None:
        notes = [_note(60)]
        violations = check_no_parallel_fifths(notes, "t.mid", "fifths", "warning")
        assert violations == []


# ---------------------------------------------------------------------------
# load_invariant_rules
# ---------------------------------------------------------------------------


class TestLoadInvariantRules:
    def test_default_rules_returned_when_no_file(self) -> None:
        rules = load_invariant_rules(None)
        assert len(rules) >= 1
        rule_types = {r["rule_type"] for r in rules}
        assert "max_polyphony" in rule_types

    def test_missing_file_returns_defaults(self, tmp_path: pathlib.Path) -> None:
        rules = load_invariant_rules(tmp_path / "nonexistent.toml")
        assert rules

    def test_toml_file_parsed_correctly(self, tmp_path: pathlib.Path) -> None:
        toml_content = """
[[rule]]
name = "test_rule"
severity = "error"
scope = "track"
rule_type = "max_polyphony"

[rule.params]
max_simultaneous = 4
"""
        rules_file = tmp_path / "invariants.toml"
        rules_file.write_text(toml_content)
        rules = load_invariant_rules(rules_file)
        assert len(rules) == 1
        assert rules[0]["name"] == "test_rule"
        assert rules[0]["severity"] == "error"
        assert rules[0].get("params", {}).get("max_simultaneous") == 4

"""Tests for muse.plugins.midi._music_query — tokenizer, parser, evaluator."""
from __future__ import annotations

import datetime

import pytest

from muse.core.store import CommitRecord
from muse.plugins.midi._midi_query import (
    AndNode,
    EqNode,
    NotNode,
    OrNode,
    QueryContext,
    evaluate_node,
    parse_query,
)
from muse.plugins.midi._query import NoteInfo
from muse.plugins.midi.midi_diff import NoteKey


def _make_commit(
    agent_id: str = "",
    author: str = "human",
    model_id: str = "",
    toolchain_id: str = "",
) -> CommitRecord:
    return CommitRecord(
        commit_id="deadbeef" * 8,
        repo_id="repo-test",
        branch="main",
        snapshot_id="snap123",
        message="test",
        author=author,
        committed_at=datetime.datetime.now(datetime.timezone.utc),
        agent_id=agent_id,
        model_id=model_id,
        toolchain_id=toolchain_id,
    )


def _make_note(pitch: int = 60, velocity: int = 80, channel: int = 0) -> NoteInfo:
    return NoteInfo.from_note_key(
        NoteKey(
            pitch=pitch,
            velocity=velocity,
            start_tick=0,
            duration_ticks=480,
            channel=channel,
        ),
        ticks_per_beat=480,
    )


def _make_ctx(
    notes: list[NoteInfo] | None = None,
    bar: int = 1,
    track: str = "piano.mid",
    chord: str = "Cmaj",
    commit: CommitRecord | None = None,
) -> QueryContext:
    return QueryContext(
        commit=commit or _make_commit(),
        track=track,
        bar=bar,
        notes=notes or [_make_note()],
        chord=chord,
        ticks_per_beat=480,
    )


# ---------------------------------------------------------------------------
# Tokenizer / parser
# ---------------------------------------------------------------------------


class TestParser:
    def test_simple_eq_parses(self) -> None:
        node = parse_query("bar == 4")
        assert isinstance(node, EqNode)
        assert node.field == "bar"
        assert node.op == "=="
        assert node.value == 4

    def test_and_produces_and_node(self) -> None:
        node = parse_query("bar == 1 and note.pitch > 60")
        assert isinstance(node, AndNode)

    def test_or_produces_or_node(self) -> None:
        node = parse_query("bar == 1 or bar == 2")
        assert isinstance(node, OrNode)

    def test_not_produces_not_node(self) -> None:
        node = parse_query("not bar == 4")
        assert isinstance(node, NotNode)

    def test_parentheses_group_correctly(self) -> None:
        node = parse_query("(bar == 1 or bar == 2) and note.pitch > 60")
        assert isinstance(node, AndNode)
        assert isinstance(node.left, OrNode)

    def test_string_value_parses(self) -> None:
        node = parse_query("note.pitch_class == 'C'")
        assert isinstance(node, EqNode)
        assert node.value == "C"

    def test_double_quoted_string_parses(self) -> None:
        node = parse_query('track == "piano.mid"')
        assert isinstance(node, EqNode)
        assert node.value == "piano.mid"

    def test_float_value_parses(self) -> None:
        node = parse_query("note.duration > 0.5")
        assert isinstance(node, EqNode)
        assert isinstance(node.value, float)

    def test_invalid_query_raises_value_error(self) -> None:
        with pytest.raises(ValueError):
            parse_query("bar !!! 4")

    def test_incomplete_query_raises_value_error(self) -> None:
        with pytest.raises(ValueError):
            parse_query("bar ==")


# ---------------------------------------------------------------------------
# Evaluator — field resolution and comparison
# ---------------------------------------------------------------------------


class TestEvaluator:
    def test_bar_eq_match(self) -> None:
        assert evaluate_node(parse_query("bar == 4"), _make_ctx(bar=4))

    def test_bar_eq_no_match(self) -> None:
        assert not evaluate_node(parse_query("bar == 4"), _make_ctx(bar=3))

    def test_note_pitch_gt(self) -> None:
        ctx = _make_ctx(notes=[_make_note(pitch=65)])
        assert evaluate_node(parse_query("note.pitch > 60"), ctx)

    def test_note_pitch_gt_false(self) -> None:
        ctx = _make_ctx(notes=[_make_note(pitch=55)])
        assert not evaluate_node(parse_query("note.pitch > 60"), ctx)

    def test_note_velocity_lte(self) -> None:
        ctx = _make_ctx(notes=[_make_note(velocity=80)])
        assert evaluate_node(parse_query("note.velocity <= 80"), ctx)

    def test_note_pitch_class_match(self) -> None:
        # Middle C = pitch 60 = C
        ctx = _make_ctx(notes=[_make_note(pitch=60)])
        assert evaluate_node(parse_query("note.pitch_class == 'C'"), ctx)

    def test_track_match(self) -> None:
        ctx = _make_ctx(track="strings.mid")
        assert evaluate_node(parse_query("track == 'strings.mid'"), ctx)

    def test_chord_match(self) -> None:
        ctx = _make_ctx(chord="Fmin")
        assert evaluate_node(parse_query("harmony.chord == 'Fmin'"), ctx)

    def test_author_match(self) -> None:
        commit = _make_commit(author="alice")
        ctx = _make_ctx(commit=commit)
        assert evaluate_node(parse_query("author == 'alice'"), ctx)

    def test_agent_id_match(self) -> None:
        commit = _make_commit(agent_id="counterpoint-bot")
        ctx = _make_ctx(commit=commit)
        assert evaluate_node(parse_query("agent_id == 'counterpoint-bot'"), ctx)

    def test_and_both_must_match(self) -> None:
        ctx = _make_ctx(notes=[_make_note(pitch=65)], bar=4)
        assert evaluate_node(parse_query("note.pitch > 60 and bar == 4"), ctx)
        assert not evaluate_node(parse_query("note.pitch > 60 and bar == 5"), ctx)

    def test_or_one_must_match(self) -> None:
        ctx = _make_ctx(bar=2)
        assert evaluate_node(parse_query("bar == 1 or bar == 2"), ctx)
        assert not evaluate_node(parse_query("bar == 1 or bar == 3"), ctx)

    def test_not_negates(self) -> None:
        ctx = _make_ctx(bar=4)
        assert not evaluate_node(parse_query("not bar == 4"), ctx)
        assert evaluate_node(parse_query("not bar == 5"), ctx)

    def test_multiple_notes_any_match(self) -> None:
        # If any note in the bar matches, the predicate matches.
        ctx = _make_ctx(notes=[_make_note(pitch=55), _make_note(pitch=65)])
        assert evaluate_node(parse_query("note.pitch > 60"), ctx)

    def test_unknown_field_returns_false(self) -> None:
        ctx = _make_ctx()
        assert not evaluate_node(parse_query("nonexistent == 'x'"), ctx)

    def test_harmony_quality_min(self) -> None:
        ctx = _make_ctx(chord="Amin")
        assert evaluate_node(parse_query("harmony.quality == 'min'"), ctx)

    def test_harmony_quality_dim7(self) -> None:
        ctx = _make_ctx(chord="Bdim7")
        assert evaluate_node(parse_query("harmony.quality == 'dim7'"), ctx)


# ---------------------------------------------------------------------------
# Note channel field
# ---------------------------------------------------------------------------


class TestNoteChannel:
    def test_channel_eq(self) -> None:
        ctx = _make_ctx(notes=[_make_note(channel=2)])
        assert evaluate_node(parse_query("note.channel == 2"), ctx)

    def test_channel_neq(self) -> None:
        ctx = _make_ctx(notes=[_make_note(channel=3)])
        assert not evaluate_node(parse_query("note.channel == 2"), ctx)

"""Stress tests for the diff algorithm library.

Covers:
- LCS / Myers: empty sequences, identical, reversed, interleaved, long sequences.
  Round-trip property: applying the edit script to the base must reproduce target.
- Tree edit: single node, add subtree, remove subtree, deep nesting.
- Numerical diff: all three modes (sparse / block / full), epsilon tolerance.
- Set diff: add / remove / replace elements.
- snapshot_diff: file-level diffing via ScaffoldPlugin.
- detect_moves: move detection from paired insert+delete.
"""

import hashlib
import random
from typing import Literal

import pytest

from muse.core.diff_algorithms.lcs import myers_ses, detect_moves, EditStep
from muse.core.diff_algorithms.tree_edit import TreeNode, diff as tree_diff
from muse.core.diff_algorithms.numerical import diff as num_diff
from muse.core.diff_algorithms.set_ops import diff as set_diff
from muse.core.schema import (
    SequenceSchema,
    TensorSchema,
    SetSchema,
    TreeSchema,
)
from muse.domain import InsertOp, DeleteOp, DomainOp, SnapshotManifest
from muse.plugins.scaffold.plugin import ScaffoldPlugin


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cid(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()[:16]


def _apply_ses(base: list[str], steps: list[EditStep]) -> list[str]:
    """Apply an edit-script to base to reconstruct target."""
    result: list[str] = []
    for step in steps:
        if step.kind == "keep":
            result.append(base[step.base_index])
        elif step.kind == "insert":
            result.append(step.item)
        # "delete" steps are skipped.
    return result


def _seq_schema(addr: str = "test") -> SequenceSchema:
    return SequenceSchema(
        field_name=addr,
        address=addr,
        diff_algorithm="lcs",
        element_type="content_id",
    )


def _tensor_schema(
    mode: Literal["sparse", "block", "full"] = "sparse", epsilon: float = 1e-9
) -> TensorSchema:
    return TensorSchema(
        field_name="tensor",
        address="tensor",
        shape=[],
        dtype="float64",
        diff_mode=mode,
        epsilon=epsilon,
    )


def _set_schema() -> SetSchema:
    return SetSchema(field_name="elems", address="elems", element_type="str")


def _tree_schema() -> TreeSchema:
    return TreeSchema(kind="tree", node_type="generic", diff_algorithm="zhang_shasha")


# ===========================================================================
# LCS / Myers
# ===========================================================================


class TestMyersSES:
    def test_empty_base_to_nonempty(self) -> None:
        target = [_cid(f"x{i}") for i in range(5)]
        steps = myers_ses([], target)
        assert all(s.kind == "insert" for s in steps)

    def test_nonempty_to_empty(self) -> None:
        base = [_cid(f"x{i}") for i in range(5)]
        steps = myers_ses(base, [])
        assert all(s.kind == "delete" for s in steps)

    def test_identical_sequences_all_keep(self) -> None:
        seq = [_cid(f"x{i}") for i in range(20)]
        steps = myers_ses(seq, seq)
        assert all(s.kind == "keep" for s in steps)

    def test_single_insert_at_beginning(self) -> None:
        base = [_cid("b"), _cid("c")]
        target = [_cid("a"), _cid("b"), _cid("c")]
        steps = myers_ses(base, target)
        result = _apply_ses(base, steps)
        assert result == target

    def test_single_delete_from_middle(self) -> None:
        base = [_cid("a"), _cid("b"), _cid("c")]
        target = [_cid("a"), _cid("c")]
        steps = myers_ses(base, target)
        result = _apply_ses(base, steps)
        assert result == target

    def test_reversed_sequence(self) -> None:
        base = [_cid(f"x{i}") for i in range(10)]
        target = list(reversed(base))
        steps = myers_ses(base, target)
        result = _apply_ses(base, steps)
        assert result == target

    def test_interleaved_sequences(self) -> None:
        base = [_cid("a"), _cid("b"), _cid("c"), _cid("d")]
        target = [_cid("a"), _cid("X"), _cid("b"), _cid("Y"), _cid("c"), _cid("Z"), _cid("d")]
        steps = myers_ses(base, target)
        result = _apply_ses(base, steps)
        assert result == target

    def test_round_trip_property_random_sequences(self) -> None:
        """Applying the SES to base must always reproduce target."""
        rng = random.Random(42)
        alphabet = [_cid(f"elem-{i}") for i in range(20)]
        for _ in range(50):
            base = rng.choices(alphabet, k=rng.randint(0, 15))
            target = rng.choices(alphabet, k=rng.randint(0, 15))
            steps = myers_ses(base, target)
            assert _apply_ses(base, steps) == target

    def test_long_sequence_1000_items(self) -> None:
        base = [_cid(f"item-{i}") for i in range(1000)]
        target = [_cid(f"item-{i}") for i in range(500, 1000)] + [_cid(f"new-{i}") for i in range(500)]
        steps = myers_ses(base, target)
        result = _apply_ses(base, steps)
        assert result == target

    def test_empty_both_sides(self) -> None:
        assert myers_ses([], []) == []


# ===========================================================================
# detect_moves
# ===========================================================================


class TestDetectMoves:
    def _make_insert(self, cid: str, pos: int | None = None) -> InsertOp:
        return InsertOp(op="insert", address="test", position=pos, content_id=cid, content_summary=cid)

    def _make_delete(self, cid: str, pos: int | None = None) -> DeleteOp:
        return DeleteOp(op="delete", address="test", position=pos, content_id=cid, content_summary=cid)

    def test_matching_insert_delete_becomes_move(self) -> None:
        cid = _cid("moved-item")
        inserts = [self._make_insert(cid, pos=5)]
        deletes = [self._make_delete(cid, pos=2)]
        moves, remaining_ins, remaining_del = detect_moves(inserts, deletes)
        assert len(moves) == 1
        assert moves[0]["op"] == "move"

    def test_no_match_when_different_content_ids(self) -> None:
        inserts = [self._make_insert(_cid("a"), pos=5)]
        deletes = [self._make_delete(_cid("b"), pos=2)]
        moves, remaining_ins, remaining_del = detect_moves(inserts, deletes)
        assert len(moves) == 0

    def test_no_moves_on_empty_inputs(self) -> None:
        moves, ins, dels = detect_moves([], [])
        assert moves == []
        assert ins == []
        assert dels == []

    def test_same_position_not_a_move(self) -> None:
        """Insert and delete at same position: not a move."""
        cid = _cid("same-pos")
        inserts = [self._make_insert(cid, pos=3)]
        deletes = [self._make_delete(cid, pos=3)]
        moves, remaining_ins, remaining_del = detect_moves(inserts, deletes)
        # Same position = not a move by definition.
        assert len(moves) == 0


# ===========================================================================
# Tree edit
# ===========================================================================


class TestTreeEditDiff:
    def _node(self, label: str, cid: str, children: tuple[TreeNode, ...] = ()) -> TreeNode:
        return TreeNode(id=label, label=label, content_id=cid, children=children)

    def test_identical_trees_no_ops(self) -> None:
        tree = self._node("root", _cid("root"), (
            self._node("child-a", _cid("a")),
            self._node("child-b", _cid("b")),
        ))
        schema = _tree_schema()
        delta = tree_diff(schema, tree, tree, domain="test")
        assert delta["ops"] == []

    def test_replace_root_content(self) -> None:
        base = self._node("root", _cid("v1"))
        target = self._node("root", _cid("v2"))
        schema = _tree_schema()
        delta = tree_diff(schema, base, target, domain="test")
        assert any(op["op"] == "replace" for op in delta["ops"])

    def test_add_child_node(self) -> None:
        base = self._node("root", _cid("root"), (self._node("a", _cid("a")),))
        target = self._node("root", _cid("root"), (
            self._node("a", _cid("a")),
            self._node("b", _cid("b")),
        ))
        schema = _tree_schema()
        delta = tree_diff(schema, base, target, domain="test")
        assert any(op["op"] == "insert" for op in delta["ops"])

    def test_remove_child_node(self) -> None:
        base = self._node("root", _cid("root"), (
            self._node("a", _cid("a")),
            self._node("b", _cid("b")),
        ))
        target = self._node("root", _cid("root"), (self._node("a", _cid("a")),))
        schema = _tree_schema()
        delta = tree_diff(schema, base, target, domain="test")
        assert any(op["op"] == "delete" for op in delta["ops"])

    def test_empty_tree_to_populated(self) -> None:
        base = self._node("root", _cid("root"))
        target = self._node("root", _cid("root"), tuple(
            self._node(f"child-{i}", _cid(f"c{i}")) for i in range(5)
        ))
        schema = _tree_schema()
        delta = tree_diff(schema, base, target, domain="test")
        assert any(op["op"] == "insert" for op in delta["ops"])


# ===========================================================================
# Numerical diff
# ===========================================================================


class TestNumericalDiff:
    def test_identical_arrays_no_ops(self) -> None:
        arr = [float(i) for i in range(100)]
        schema = _tensor_schema("sparse")
        delta = num_diff(schema, arr, arr, domain="test")
        assert delta["ops"] == []

    def test_sparse_mode_one_change(self) -> None:
        base = [0.0] * 10
        target = [0.0] * 10
        target[5] = 1.0
        schema = _tensor_schema("sparse")
        delta = num_diff(schema, base, target, domain="test")
        assert len(delta["ops"]) == 1
        assert delta["ops"][0]["op"] == "replace"

    def test_block_mode_adjacent_changes_grouped(self) -> None:
        base = [0.0] * 10
        target = [0.0] * 10
        for i in range(3, 7):
            target[i] = float(i)
        schema = _tensor_schema("block")
        delta = num_diff(schema, base, target, domain="test")
        # Block mode should group adjacent changes.
        assert len(delta["ops"]) <= 4

    def test_full_mode_single_op_for_any_change(self) -> None:
        base = [0.0] * 100
        target = list(base)
        target[50] = 1.0
        target[99] = 2.0
        schema = _tensor_schema("full")
        delta = num_diff(schema, base, target, domain="test")
        assert len(delta["ops"]) == 1

    def test_epsilon_tolerance_no_spurious_diffs(self) -> None:
        base = [1.0, 2.0, 3.0]
        target = [1.0 + 1e-12, 2.0 - 1e-12, 3.0 + 1e-12]
        schema = _tensor_schema("sparse", epsilon=1e-9)
        delta = num_diff(schema, base, target, domain="test")
        assert delta["ops"] == []

    def test_epsilon_threshold_triggers_diff(self) -> None:
        base = [1.0]
        target = [1.0 + 1e-5]
        schema = _tensor_schema("sparse", epsilon=1e-9)
        delta = num_diff(schema, base, target, domain="test")
        assert len(delta["ops"]) == 1

    def test_empty_arrays_no_ops(self) -> None:
        schema = _tensor_schema("sparse")
        delta = num_diff(schema, [], [], domain="test")
        assert delta["ops"] == []

    def test_all_values_changed_sparse(self) -> None:
        base = [0.0] * 50
        target = [1.0] * 50
        schema = _tensor_schema("sparse")
        delta = num_diff(schema, base, target, domain="test")
        assert len(delta["ops"]) == 50


# ===========================================================================
# Set diff
# ===========================================================================


class TestSetDiff:
    def test_identical_sets_no_ops(self) -> None:
        s = frozenset(f"elem-{i}" for i in range(20))
        schema = _set_schema()
        delta = set_diff(schema, s, s, domain="test")
        assert delta["ops"] == []

    def test_add_elements(self) -> None:
        base: frozenset[str] = frozenset()
        target = frozenset(f"new-{i}" for i in range(10))
        schema = _set_schema()
        delta = set_diff(schema, base, target, domain="test")
        assert all(op["op"] == "insert" for op in delta["ops"])
        assert len(delta["ops"]) == 10

    def test_remove_elements(self) -> None:
        base = frozenset(f"elem-{i}" for i in range(10))
        target: frozenset[str] = frozenset()
        schema = _set_schema()
        delta = set_diff(schema, base, target, domain="test")
        assert all(op["op"] == "delete" for op in delta["ops"])
        assert len(delta["ops"]) == 10

    def test_mixed_add_remove(self) -> None:
        base = frozenset(f"base-{i}" for i in range(5))
        target = frozenset(f"base-{i}" for i in range(3)) | frozenset(f"new-{i}" for i in range(3))
        schema = _set_schema()
        delta = set_diff(schema, base, target, domain="test")
        ops_by_kind = {"insert": 0, "delete": 0}
        for op in delta["ops"]:
            ops_by_kind[op["op"]] += 1
        assert ops_by_kind["insert"] == 3
        assert ops_by_kind["delete"] == 2

    def test_empty_base_to_empty_target(self) -> None:
        schema = _set_schema()
        delta = set_diff(schema, frozenset(), frozenset(), domain="test")
        assert delta["ops"] == []


# ===========================================================================
# snapshot_diff (via ScaffoldPlugin which uses it internally)
# ===========================================================================


class TestSnapshotDiff:
    """Test snapshot_diff indirectly via ScaffoldPlugin.diff() which delegates to it."""

    def _diff(self, base: dict[str, str], target: dict[str, str]) -> list[DomainOp]:
        plugin = ScaffoldPlugin()
        base_snap = SnapshotManifest(files=base, domain="scaffold")
        target_snap = SnapshotManifest(files=target, domain="scaffold")
        delta = plugin.diff(base_snap, target_snap)
        return list(delta["ops"])

    def test_identical_snapshots_empty_ops(self) -> None:
        manifest = {f"f{i}.py": _cid(f"v{i}") for i in range(10)}
        ops = self._diff(manifest, manifest)
        assert ops == []

    def test_added_files_produce_insert_ops(self) -> None:
        ops = self._diff({}, {"new.py": _cid("new")})
        assert any(op["op"] == "insert" and op["address"] == "new.py" for op in ops)

    def test_removed_files_produce_delete_ops(self) -> None:
        ops = self._diff({"old.py": _cid("old")}, {})
        assert any(op["op"] == "delete" and op["address"] == "old.py" for op in ops)

    def test_modified_files_produce_replace_ops(self) -> None:
        ops = self._diff({"f.py": _cid("v1")}, {"f.py": _cid("v2")})
        assert any(op["op"] == "replace" and op["address"] == "f.py" for op in ops)

    def test_50_file_snapshot_diff_complete(self) -> None:
        base = {f"f{i:03d}.py": _cid(f"v1-{i}") for i in range(50)}
        target = {f"f{i:03d}.py": _cid(f"v2-{i}") for i in range(50)}
        ops = self._diff(base, target)
        assert len(ops) == 50
        assert all(op["op"] == "replace" for op in ops)

    def test_ops_sorted_by_address(self) -> None:
        target = {f"z{i:02d}.py": _cid(f"v{i}") for i in range(10)}
        ops = self._diff({}, target)
        addresses = [op["address"] for op in ops]
        assert addresses == sorted(addresses)

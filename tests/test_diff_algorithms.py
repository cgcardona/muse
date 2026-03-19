"""Tests for the diff algorithm library.

Covers all four algorithm modules (lcs, tree_edit, numerical, set_ops) and
the schema-driven dispatch in ``muse.core.diff_algorithms``.

Each algorithm is tested at three levels:
1. **Unit** — the core function in isolation.
2. **Output shape** — the returned ``StructuredDelta`` is well-formed.
3. **Dispatch** — ``diff_by_schema`` routes correctly for each schema kind.
"""

import hashlib

import pytest
from typing import Literal

from muse.core.diff_algorithms import (
    DiffInput,
    MapInput,
    SequenceInput,
    SetInput,
    TensorInput,
    TreeInput,
    TreeNode,
    diff_by_schema,
    snapshot_diff,
)
from muse.core.diff_algorithms import lcs as lcs_mod
from muse.core.diff_algorithms import numerical as numerical_mod
from muse.core.diff_algorithms import set_ops as set_ops_mod
from muse.core.diff_algorithms import tree_edit as tree_edit_mod
from muse.core.schema import (
    DomainSchema,
    DimensionSpec,
    MapSchema,
    SequenceSchema,
    SetSchema,
    TensorSchema,
    TreeSchema,
)
from muse.domain import SnapshotManifest, StructuredDelta


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cid(s: str) -> str:
    """Return a deterministic SHA-256 hex for a short string."""
    return hashlib.sha256(s.encode()).hexdigest()


def _seq_schema(element_type: str = "item") -> SequenceSchema:
    return SequenceSchema(
        kind="sequence",
        element_type=element_type,
        identity="by_position",
        diff_algorithm="lcs",
        alphabet=None,
    )


def _set_schema(element_type: str = "file") -> SetSchema:
    return SetSchema(kind="set", element_type=element_type, identity="by_content")


DiffMode = Literal["sparse", "block", "full"]


def _tensor_schema(
    mode: DiffMode = "sparse", epsilon: float = 0.0
) -> TensorSchema:
    return TensorSchema(
        kind="tensor",
        dtype="float32",
        rank=1,
        epsilon=epsilon,
        diff_mode=mode,
    )


def _tree_schema() -> TreeSchema:
    return TreeSchema(kind="tree", node_type="node", diff_algorithm="zhang_shasha")


def _map_schema() -> MapSchema:
    return MapSchema(
        kind="map",
        key_type="key",
        value_schema=_seq_schema(),
        identity="by_key",
    )


def _leaf(label: str) -> TreeNode:
    return TreeNode(id=label, label=label, content_id=_cid(label), children=())


def _node(label: str, *children: TreeNode) -> TreeNode:
    return TreeNode(
        id=label, label=label, content_id=_cid(label), children=tuple(children)
    )


def _is_valid_delta(d: StructuredDelta) -> bool:
    return isinstance(d["ops"], list) and isinstance(d["summary"], str)


# ===========================================================================
# LCS / Myers tests
# ===========================================================================


class TestLCSMyersSES:
    def test_empty_to_empty_returns_no_steps(self) -> None:
        steps = lcs_mod.myers_ses([], [])
        assert steps == []

    def test_empty_to_sequence_all_inserts(self) -> None:
        steps = lcs_mod.myers_ses([], ["a", "b", "c"])
        kinds = [s.kind for s in steps]
        assert kinds == ["insert", "insert", "insert"]

    def test_sequence_to_empty_all_deletes(self) -> None:
        steps = lcs_mod.myers_ses(["a", "b", "c"], [])
        kinds = [s.kind for s in steps]
        assert kinds == ["delete", "delete", "delete"]

    def test_identical_sequences_all_keeps(self) -> None:
        ids = ["a", "b", "c"]
        steps = lcs_mod.myers_ses(ids, ids)
        assert all(s.kind == "keep" for s in steps)
        assert len(steps) == 3

    def test_single_insert_in_middle(self) -> None:
        base = ["a", "c"]
        target = ["a", "b", "c"]
        steps = lcs_mod.myers_ses(base, target)
        inserts = [s for s in steps if s.kind == "insert"]
        assert len(inserts) == 1
        assert inserts[0].item == "b"

    def test_single_delete_in_middle(self) -> None:
        base = ["a", "b", "c"]
        target = ["a", "c"]
        steps = lcs_mod.myers_ses(base, target)
        deletes = [s for s in steps if s.kind == "delete"]
        assert len(deletes) == 1
        assert deletes[0].item == "b"

    def test_lcs_is_minimal(self) -> None:
        base = ["a", "b", "c", "d"]
        target = ["a", "x", "c", "d"]
        steps = lcs_mod.myers_ses(base, target)
        keeps = [s for s in steps if s.kind == "keep"]
        inserts = [s for s in steps if s.kind == "insert"]
        deletes = [s for s in steps if s.kind == "delete"]
        assert len(keeps) == 3  # a, c, d are kept
        assert len(inserts) == 1
        assert len(deletes) == 1

    def test_step_indices_are_consistent(self) -> None:
        base = ["x", "y", "z"]
        target = ["y", "z", "w"]
        steps = lcs_mod.myers_ses(base, target)
        for s in steps:
            if s.kind == "delete":
                assert s.item == base[s.base_index]
            elif s.kind == "insert":
                assert s.item == target[s.target_index]


class TestLCSDetectMoves:
    def test_paired_delete_insert_becomes_move(self) -> None:
        from muse.domain import DeleteOp, InsertOp

        cid = _cid("note")
        ins_op = InsertOp(op="insert", address="", position=3, content_id=cid, content_summary="")
        del_op = DeleteOp(op="delete", address="", position=0, content_id=cid, content_summary="")
        moves, rem_ins, rem_del = lcs_mod.detect_moves([ins_op], [del_op])
        assert len(moves) == 1
        assert moves[0]["op"] == "move"
        assert moves[0]["from_position"] == 0
        assert moves[0]["to_position"] == 3
        assert len(rem_ins) == 0
        assert len(rem_del) == 0

    def test_same_position_not_a_move(self) -> None:
        from muse.domain import DeleteOp, InsertOp

        cid = _cid("item")
        ins_op = InsertOp(op="insert", address="", position=1, content_id=cid, content_summary="")
        del_op = DeleteOp(op="delete", address="", position=1, content_id=cid, content_summary="")
        moves, rem_ins, rem_del = lcs_mod.detect_moves([ins_op], [del_op])
        assert len(moves) == 0
        assert len(rem_ins) == 1
        assert len(rem_del) == 1

    def test_no_paired_content_no_moves(self) -> None:
        from muse.domain import DeleteOp, InsertOp

        ins_op = InsertOp(op="insert", address="", position=0, content_id=_cid("a"), content_summary="")
        del_op = DeleteOp(op="delete", address="", position=0, content_id=_cid("b"), content_summary="")
        moves, rem_ins, rem_del = lcs_mod.detect_moves([ins_op], [del_op])
        assert len(moves) == 0
        assert len(rem_ins) == 1
        assert len(rem_del) == 1


class TestLCSDiff:
    def test_empty_to_sequence_is_all_inserts(self) -> None:
        delta = lcs_mod.diff(_seq_schema(), [], ["a", "b"], domain="test")
        ops = [op for op in delta["ops"] if op["op"] == "insert"]
        assert len(ops) == 2

    def test_sequence_to_empty_is_all_deletes(self) -> None:
        delta = lcs_mod.diff(_seq_schema(), ["a", "b"], [], domain="test")
        ops = [op for op in delta["ops"] if op["op"] == "delete"]
        assert len(ops) == 2

    def test_identical_sequences_returns_no_ops(self) -> None:
        delta = lcs_mod.diff(_seq_schema(), ["a", "b", "c"], ["a", "b", "c"], domain="test")
        assert delta["ops"] == []

    def test_produces_valid_structured_delta(self) -> None:
        delta = lcs_mod.diff(_seq_schema("note"), ["x"], ["x", "y"], domain="midi")
        assert _is_valid_delta(delta)
        assert delta["domain"] == "midi"

    def test_move_detected_from_delete_plus_insert(self) -> None:
        a, b, c = _cid("a"), _cid("b"), _cid("c")
        delta = lcs_mod.diff(_seq_schema(), [a, b, c], [b, c, a], domain="test")
        ops_by_kind = {op["op"] for op in delta["ops"]}
        assert "move" in ops_by_kind

    def test_summary_is_human_readable(self) -> None:
        delta = lcs_mod.diff(_seq_schema("note"), ["a"], ["a", "b"], domain="test")
        assert "note" in delta["summary"]
        assert "added" in delta["summary"]


# ===========================================================================
# Tree edit tests
# ===========================================================================


class TestTreeEditDiff:
    def test_identical_trees_returns_no_ops(self) -> None:
        root = _node("root", _leaf("A"), _leaf("B"))
        delta = tree_edit_mod.diff(_tree_schema(), root, root, domain="test")
        assert delta["ops"] == []

    def test_leaf_relabel_is_replace(self) -> None:
        base = _leaf("A")
        old_cid = _cid("A")
        new_node = TreeNode(id="A", label="A", content_id=_cid("A_new"), children=())
        target = TreeNode(id="root", label="root", content_id=_cid("root"),
                          children=(new_node,))
        base_root = TreeNode(id="root", label="root", content_id=_cid("root"),
                             children=(base,))
        delta = tree_edit_mod.diff(_tree_schema(), base_root, target, domain="test")
        replace_ops = [op for op in delta["ops"] if op["op"] == "replace"]
        assert len(replace_ops) == 1

    def test_node_insert(self) -> None:
        base = _node("root", _leaf("A"))
        target = _node("root", _leaf("A"), _leaf("B"))
        delta = tree_edit_mod.diff(_tree_schema(), base, target, domain="test")
        insert_ops = [op for op in delta["ops"] if op["op"] == "insert"]
        assert len(insert_ops) >= 1

    def test_node_delete(self) -> None:
        base = _node("root", _leaf("A"), _leaf("B"))
        target = _node("root", _leaf("A"))
        delta = tree_edit_mod.diff(_tree_schema(), base, target, domain="test")
        delete_ops = [op for op in delta["ops"] if op["op"] == "delete"]
        assert len(delete_ops) >= 1

    def test_subtree_move(self) -> None:
        leaf_a = _leaf("A")
        leaf_b = _leaf("B")
        base = _node("root", leaf_a, leaf_b)
        # Move: leaf_b before leaf_a
        target = _node("root", leaf_b, leaf_a)
        delta = tree_edit_mod.diff(_tree_schema(), base, target, domain="test")
        # Should produce a move or a pair of delete/insert
        op_kinds = {op["op"] for op in delta["ops"]}
        assert op_kinds & {"move", "insert", "delete"}

    def test_produces_valid_structured_delta(self) -> None:
        base = _node("root", _leaf("X"))
        target = _node("root", _leaf("Y"))
        delta = tree_edit_mod.diff(_tree_schema(), base, target, domain="midi")
        assert _is_valid_delta(delta)
        assert delta["domain"] == "midi"

    def test_summary_is_human_readable(self) -> None:
        base = _node("root", _leaf("A"))
        target = _node("root", _leaf("A"), _leaf("B"))
        delta = tree_edit_mod.diff(_tree_schema(), base, target, domain="test")
        assert isinstance(delta["summary"], str)
        assert len(delta["summary"]) > 0


# ===========================================================================
# Numerical diff tests
# ===========================================================================


class TestNumericalDiff:
    def test_within_epsilon_returns_no_ops(self) -> None:
        schema = _tensor_schema(epsilon=1.0)
        delta = numerical_mod.diff(schema, [1.0, 2.0, 3.0], [1.4, 2.0, 3.0], domain="test")
        assert delta["ops"] == []

    def test_outside_epsilon_returns_replace(self) -> None:
        schema = _tensor_schema(epsilon=0.1)
        delta = numerical_mod.diff(schema, [1.0, 2.0, 3.0], [1.0, 5.0, 3.0], domain="test")
        assert len(delta["ops"]) == 1
        assert delta["ops"][0]["op"] == "replace"

    def test_identical_arrays_returns_no_ops(self) -> None:
        schema = _tensor_schema()
        delta = numerical_mod.diff(schema, [1.0, 2.0], [1.0, 2.0], domain="test")
        assert delta["ops"] == []

    def test_sparse_mode_one_op_per_element(self) -> None:
        schema = _tensor_schema(mode="sparse", epsilon=0.0)
        base = [1.0, 2.0, 3.0]
        target = [9.0, 2.0, 9.0]
        delta = numerical_mod.diff(schema, base, target, domain="test")
        assert len(delta["ops"]) == 2  # positions 0 and 2
        for op in delta["ops"]:
            assert op["op"] == "replace"

    def test_block_mode_groups_adjacent(self) -> None:
        schema = _tensor_schema(mode="block", epsilon=0.0)
        base = [1.0, 2.0, 3.0, 4.0, 5.0]
        target = [9.0, 9.0, 3.0, 9.0, 9.0]
        delta = numerical_mod.diff(schema, base, target, domain="test")
        # Changes at 0,1 and 3,4 → two blocks
        assert len(delta["ops"]) == 2

    def test_full_mode_single_op(self) -> None:
        schema = _tensor_schema(mode="full", epsilon=0.0)
        base = [1.0, 2.0, 3.0]
        target = [1.0, 99.0, 3.0]
        delta = numerical_mod.diff(schema, base, target, domain="test")
        assert len(delta["ops"]) == 1
        assert delta["ops"][0]["op"] == "replace"

    def test_length_mismatch_returns_single_replace(self) -> None:
        schema = _tensor_schema()
        delta = numerical_mod.diff(schema, [1.0, 2.0], [1.0, 2.0, 3.0], domain="test")
        assert len(delta["ops"]) == 1
        assert delta["ops"][0]["op"] == "replace"

    def test_produces_valid_structured_delta(self) -> None:
        schema = _tensor_schema(epsilon=0.5)
        delta = numerical_mod.diff(schema, [0.0, 1.0], [0.0, 2.0], domain="midi")
        assert _is_valid_delta(delta)
        assert delta["domain"] == "midi"


# ===========================================================================
# Set ops tests
# ===========================================================================


class TestSetOpsDiff:
    def test_add_returns_insert(self) -> None:
        schema = _set_schema()
        base: frozenset[str] = frozenset()
        target = frozenset({_cid("file_a")})
        delta = set_ops_mod.diff(schema, base, target, domain="test")
        assert len(delta["ops"]) == 1
        assert delta["ops"][0]["op"] == "insert"

    def test_remove_returns_delete(self) -> None:
        schema = _set_schema()
        cid = _cid("file_a")
        base = frozenset({cid})
        target: frozenset[str] = frozenset()
        delta = set_ops_mod.diff(schema, base, target, domain="test")
        assert len(delta["ops"]) == 1
        assert delta["ops"][0]["op"] == "delete"

    def test_no_change_returns_empty(self) -> None:
        schema = _set_schema()
        cids = frozenset({_cid("a"), _cid("b")})
        delta = set_ops_mod.diff(schema, cids, cids, domain="test")
        assert delta["ops"] == []

    def test_all_ops_have_none_position(self) -> None:
        schema = _set_schema()
        base: frozenset[str] = frozenset()
        target = frozenset({_cid("x"), _cid("y")})
        delta = set_ops_mod.diff(schema, base, target, domain="test")
        for op in delta["ops"]:
            assert op["position"] is None

    def test_produces_valid_structured_delta(self) -> None:
        schema = _set_schema("audio_file")
        base = frozenset({_cid("drums"), _cid("bass")})
        target = frozenset({_cid("drums"), _cid("guitar")})
        delta = set_ops_mod.diff(schema, base, target, domain="midi")
        assert _is_valid_delta(delta)
        assert delta["domain"] == "midi"
        assert "audio_file" in delta["summary"]


# ===========================================================================
# Schema dispatch (diff_by_schema) tests
# ===========================================================================


class TestDiffBySchema:
    def test_dispatch_sequence_schema_calls_lcs(self) -> None:
        schema = _seq_schema("note")
        base: DiffInput = SequenceInput(kind="sequence", items=["a"])
        target: DiffInput = SequenceInput(kind="sequence", items=["a", "b"])
        delta = diff_by_schema(schema, base, target, domain="test")
        assert delta["domain"] == "test"
        insert_ops = [op for op in delta["ops"] if op["op"] == "insert"]
        assert len(insert_ops) == 1

    def test_dispatch_set_schema_calls_set_ops(self) -> None:
        schema = _set_schema("file")
        cid_a = _cid("a")
        base: DiffInput = SetInput(kind="set", items=frozenset({cid_a}))
        target: DiffInput = SetInput(kind="set", items=frozenset())
        delta = diff_by_schema(schema, base, target, domain="test")
        delete_ops = [op for op in delta["ops"] if op["op"] == "delete"]
        assert len(delete_ops) == 1

    def test_dispatch_tensor_schema_calls_numerical(self) -> None:
        schema = _tensor_schema(epsilon=0.0)
        base: DiffInput = TensorInput(kind="tensor", values=[1.0, 2.0])
        target: DiffInput = TensorInput(kind="tensor", values=[1.0, 9.0])
        delta = diff_by_schema(schema, base, target, domain="test")
        replace_ops = [op for op in delta["ops"] if op["op"] == "replace"]
        assert len(replace_ops) == 1

    def test_dispatch_tree_schema_calls_tree_edit(self) -> None:
        schema = _tree_schema()
        base_tree = _node("root", _leaf("A"))
        target_tree = _node("root", _leaf("A"), _leaf("B"))
        base: DiffInput = TreeInput(kind="tree", root=base_tree)
        target: DiffInput = TreeInput(kind="tree", root=target_tree)
        delta = diff_by_schema(schema, base, target, domain="test")
        assert _is_valid_delta(delta)

    def test_dispatch_map_schema_recurses(self) -> None:
        schema = _map_schema()
        cid_a, cid_b = _cid("va"), _cid("vb")
        base: DiffInput = MapInput(kind="map", entries={"key1": cid_a})
        target: DiffInput = MapInput(kind="map", entries={"key1": cid_b, "key2": cid_a})
        delta = diff_by_schema(schema, base, target, domain="test")
        assert _is_valid_delta(delta)
        # key2 added → insert op; key1 changed → replace op
        op_kinds = [op["op"] for op in delta["ops"]]
        assert "insert" in op_kinds
        assert "replace" in op_kinds

    def test_type_error_on_mismatched_schema_and_input(self) -> None:
        schema = _seq_schema()
        wrong_input: DiffInput = SetInput(kind="set", items=frozenset())
        with pytest.raises(TypeError, match="sequence schema requires SequenceInput"):
            diff_by_schema(schema, wrong_input, wrong_input, domain="test")

    def test_identical_sequence_produces_no_ops(self) -> None:
        schema = _seq_schema()
        items = ["a", "b", "c"]
        base: DiffInput = SequenceInput(kind="sequence", items=items)
        target: DiffInput = SequenceInput(kind="sequence", items=items)
        delta = diff_by_schema(schema, base, target, domain="test")
        assert delta["ops"] == []

    def test_map_add_key_is_insert(self) -> None:
        schema = _map_schema()
        base: DiffInput = MapInput(kind="map", entries={})
        target: DiffInput = MapInput(kind="map", entries={"chr1": _cid("seq")})
        delta = diff_by_schema(schema, base, target, domain="genomics")
        assert delta["ops"][0]["op"] == "insert"

    def test_map_remove_key_is_delete(self) -> None:
        schema = _map_schema()
        base: DiffInput = MapInput(kind="map", entries={"chr1": _cid("seq")})
        target: DiffInput = MapInput(kind="map", entries={})
        delta = diff_by_schema(schema, base, target, domain="genomics")
        assert delta["ops"][0]["op"] == "delete"

    def test_map_unchanged_returns_no_ops(self) -> None:
        schema = _map_schema()
        entries = {"k1": _cid("v1"), "k2": _cid("v2")}
        base: DiffInput = MapInput(kind="map", entries=entries)
        target: DiffInput = MapInput(kind="map", entries=entries)
        delta = diff_by_schema(schema, base, target, domain="test")
        assert delta["ops"] == []


# ---------------------------------------------------------------------------
# snapshot_diff — schema-driven auto-diff for SnapshotManifests
# ---------------------------------------------------------------------------


def _minimal_schema(domain: str) -> DomainSchema:
    """Minimal DomainSchema for snapshot_diff tests."""
    return DomainSchema(
        domain=domain,
        description="Test domain",
        dimensions=[],
        top_level=SetSchema(kind="set", element_type="file", identity="by_content"),
        merge_mode="three_way",
        schema_version=1,
    )


class TestSnapshotDiff:
    """snapshot_diff provides schema-driven file-level diffs for any plugin."""

    def test_added_file_is_insert_op(self) -> None:
        schema = _minimal_schema("mydomain")
        base: SnapshotManifest = {"files": {}, "domain": "mydomain"}
        target: SnapshotManifest = {"files": {"data.txt": _cid("hello")}, "domain": "mydomain"}
        delta = snapshot_diff(schema, base, target)
        assert len(delta["ops"]) == 1
        assert delta["ops"][0]["op"] == "insert"
        assert delta["ops"][0]["address"] == "data.txt"

    def test_removed_file_is_delete_op(self) -> None:
        schema = _minimal_schema("mydomain")
        base: SnapshotManifest = {"files": {"data.txt": _cid("hello")}, "domain": "mydomain"}
        target: SnapshotManifest = {"files": {}, "domain": "mydomain"}
        delta = snapshot_diff(schema, base, target)
        assert len(delta["ops"]) == 1
        assert delta["ops"][0]["op"] == "delete"

    def test_modified_file_is_replace_op(self) -> None:
        schema = _minimal_schema("mydomain")
        base: SnapshotManifest = {"files": {"data.txt": _cid("v1")}, "domain": "mydomain"}
        target: SnapshotManifest = {"files": {"data.txt": _cid("v2")}, "domain": "mydomain"}
        delta = snapshot_diff(schema, base, target)
        assert len(delta["ops"]) == 1
        assert delta["ops"][0]["op"] == "replace"

    def test_identical_snapshots_have_no_ops(self) -> None:
        schema = _minimal_schema("mydomain")
        manifest: SnapshotManifest = {"files": {"a.txt": _cid("a"), "b.txt": _cid("b")}, "domain": "mydomain"}
        delta = snapshot_diff(schema, manifest, manifest)
        assert delta["ops"] == []
        assert delta["summary"] == "no changes"

    def test_domain_tag_taken_from_schema(self) -> None:
        schema = _minimal_schema("myplugin")
        base: SnapshotManifest = {"files": {}, "domain": "myplugin"}
        target: SnapshotManifest = {"files": {"f.txt": _cid("x")}, "domain": "myplugin"}
        delta = snapshot_diff(schema, base, target)
        assert delta["domain"] == "myplugin"

    def test_multiple_changes_produce_correct_op_mix(self) -> None:
        schema = _minimal_schema("mydomain")
        base: SnapshotManifest = {
            "files": {
                "keep.txt": _cid("same"),
                "modify.txt": _cid("old"),
                "delete.txt": _cid("gone"),
            },
            "domain": "mydomain",
        }
        target: SnapshotManifest = {
            "files": {
                "keep.txt": _cid("same"),
                "modify.txt": _cid("new"),
                "add.txt": _cid("fresh"),
            },
            "domain": "mydomain",
        }
        delta = snapshot_diff(schema, base, target)
        ops_by_kind = {op["op"] for op in delta["ops"]}
        assert "insert" in ops_by_kind   # add.txt
        assert "delete" in ops_by_kind   # delete.txt
        assert "replace" in ops_by_kind  # modify.txt
        assert len(delta["ops"]) == 3

    def test_scaffold_plugin_uses_snapshot_diff(self) -> None:
        """ScaffoldPlugin.diff() delegates to snapshot_diff — no custom set-algebra needed."""
        from muse.plugins.scaffold.plugin import ScaffoldPlugin

        plugin = ScaffoldPlugin()
        base: SnapshotManifest = {"files": {"a.scaffold": _cid("v1")}, "domain": "scaffold"}
        target: SnapshotManifest = {
            "files": {"a.scaffold": _cid("v2"), "b.scaffold": _cid("new")},
            "domain": "scaffold",
        }
        delta = plugin.diff(base, target)
        op_types = {op["op"] for op in delta["ops"]}
        assert "replace" in op_types
        assert "insert" in op_types
        assert delta["domain"] == "scaffold"

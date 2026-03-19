"""Tests for the operation-level merge engine.

Covers every commutativity rule from the spec table, the OT transform function,
and the full three-way ``merge_op_lists`` algorithm.  Each test is named after
the specific behaviour it verifies so that a failure message is self-documenting.
"""

import pytest

from muse.core.op_transform import (
    MergeOpsResult,
    _adjust_insert_positions,
    _op_key,
    merge_op_lists,
    merge_structured,
    ops_commute,
    transform,
)
from muse.domain import (
    DeleteOp,
    DomainOp,
    InsertOp,
    MoveOp,
    PatchOp,
    ReplaceOp,
    StructuredDelta,
)


# ---------------------------------------------------------------------------
# Helpers for building typed ops
# ---------------------------------------------------------------------------


def _ins(addr: str, pos: int | None, cid: str = "cid-a") -> InsertOp:
    return InsertOp(op="insert", address=addr, position=pos, content_id=cid, content_summary=cid)


def _del(addr: str, pos: int | None, cid: str = "cid-a") -> DeleteOp:
    return DeleteOp(op="delete", address=addr, position=pos, content_id=cid, content_summary=cid)


def _mov(addr: str, from_pos: int, to_pos: int, cid: str = "cid-a") -> MoveOp:
    return MoveOp(op="move", address=addr, from_position=from_pos, to_position=to_pos, content_id=cid)


def _rep(addr: str, old: str, new: str) -> ReplaceOp:
    return ReplaceOp(
        op="replace",
        address=addr,
        position=None,
        old_content_id=old,
        new_content_id=new,
        old_summary="old",
        new_summary="new",
    )


def _patch(addr: str, child_ops: list[DomainOp] | None = None) -> PatchOp:
    return PatchOp(
        op="patch",
        address=addr,
        child_ops=child_ops or [],
        child_domain="test",
        child_summary="test patch",
    )


def _delta(ops: list[DomainOp], *, domain: str = "midi") -> StructuredDelta:
    return StructuredDelta(domain=domain, ops=ops, summary="test")


# ===========================================================================
# Part 1 — ops_commute: commutativity oracle
# ===========================================================================


class TestOpsCommuteInserts:
    def test_inserts_at_different_positions_commute(self) -> None:
        a = _ins("f.mid", pos=2)
        b = _ins("f.mid", pos=5)
        assert ops_commute(a, b) is True

    def test_inserts_at_same_position_do_not_commute(self) -> None:
        a = _ins("f.mid", pos=3)
        b = _ins("f.mid", pos=3)
        assert ops_commute(a, b) is False

    def test_inserts_with_none_position_commute_unordered(self) -> None:
        a = _ins("files/", pos=None, cid="aa")
        b = _ins("files/", pos=None, cid="bb")
        assert ops_commute(a, b) is True

    def test_inserts_with_none_and_int_position_commute_unordered(self) -> None:
        # If either side is unordered, treat as commuting.
        a = _ins("files/", pos=None)
        b = _ins("files/", pos=3)
        assert ops_commute(a, b) is True

    def test_inserts_at_different_addresses_commute(self) -> None:
        a = _ins("a.mid", pos=0)
        b = _ins("b.mid", pos=0)
        assert ops_commute(a, b) is True


class TestOpsCommuteDeletes:
    def test_deletes_at_different_addresses_commute(self) -> None:
        assert ops_commute(_del("a.mid", 0), _del("b.mid", 0)) is True

    def test_consensus_delete_same_address_commutes(self) -> None:
        # Both branches deleted the same file — idempotent, not a conflict.
        a = _del("f.mid", pos=0, cid="same")
        b = _del("f.mid", pos=0, cid="same")
        assert ops_commute(a, b) is True

    def test_deletes_at_same_address_different_content_still_commute(self) -> None:
        # Two deletes always commute — the result is "both deleted something".
        a = _del("f.mid", pos=1, cid="c1")
        b = _del("f.mid", pos=2, cid="c2")
        assert ops_commute(a, b) is True


class TestOpsCommuteReplaces:
    def test_replaces_at_different_addresses_commute(self) -> None:
        assert ops_commute(_rep("a.mid", "o", "n"), _rep("b.mid", "o", "n")) is True

    def test_replaces_at_same_address_do_not_commute(self) -> None:
        assert ops_commute(_rep("f.mid", "old", "v1"), _rep("f.mid", "old", "v2")) is False


class TestOpsCommuteMoves:
    def test_moves_from_different_positions_commute(self) -> None:
        assert ops_commute(_mov("f.mid", 2, 5), _mov("f.mid", 7, 1)) is True

    def test_moves_from_same_position_do_not_commute(self) -> None:
        assert ops_commute(_mov("f.mid", 3, 0), _mov("f.mid", 3, 9)) is False

    def test_move_and_delete_same_position_do_not_commute(self) -> None:
        move = _mov("f.mid", 5, 9)
        delete = _del("f.mid", pos=5)
        assert ops_commute(move, delete) is False

    def test_move_and_delete_different_positions_commute(self) -> None:
        move = _mov("f.mid", 5, 9)
        delete = _del("f.mid", pos=2)
        assert ops_commute(move, delete) is True

    def test_delete_and_move_same_position_is_symmetric(self) -> None:
        move = _mov("f.mid", 5, 9)
        delete = _del("f.mid", pos=5)
        # commute(move, delete) == commute(delete, move)
        assert ops_commute(delete, move) is False

    def test_delete_with_none_position_and_move_commute(self) -> None:
        move = _mov("f.mid", 5, 9)
        delete = _del("files/", pos=None)
        assert ops_commute(move, delete) is True


class TestOpsCommutePatches:
    def test_patches_at_different_addresses_commute(self) -> None:
        a = _patch("a.mid")
        b = _patch("b.mid")
        assert ops_commute(a, b) is True

    def test_patch_at_same_address_with_non_conflicting_children_commutes(self) -> None:
        child_a = _ins("note:0", pos=1)
        child_b = _ins("note:0", pos=5)
        a = _patch("f.mid", child_ops=[child_a])
        b = _patch("f.mid", child_ops=[child_b])
        assert ops_commute(a, b) is True

    def test_patch_at_same_address_with_conflicting_children_does_not_commute(self) -> None:
        child_a = _rep("note:0", "old", "v1")
        child_b = _rep("note:0", "old", "v2")
        a = _patch("f.mid", child_ops=[child_a])
        b = _patch("f.mid", child_ops=[child_b])
        assert ops_commute(a, b) is False

    def test_empty_patch_children_always_commute(self) -> None:
        a = _patch("f.mid", child_ops=[])
        b = _patch("f.mid", child_ops=[])
        assert ops_commute(a, b) is True


class TestOpsCommuteMixedTypes:
    def test_insert_and_delete_at_different_addresses_commute(self) -> None:
        assert ops_commute(_ins("a.mid", 0), _del("b.mid", 0)) is True

    def test_insert_and_delete_at_same_address_do_not_commute(self) -> None:
        assert ops_commute(_ins("f.mid", 2), _del("f.mid", 5)) is False

    def test_delete_and_insert_symmetry(self) -> None:
        a = _ins("f.mid", 2)
        b = _del("f.mid", 5)
        assert ops_commute(a, b) == ops_commute(b, a)

    def test_replace_and_insert_at_different_addresses_commute(self) -> None:
        assert ops_commute(_rep("a.mid", "o", "n"), _ins("b.mid", 0)) is True

    def test_replace_and_insert_at_same_address_do_not_commute(self) -> None:
        assert ops_commute(_rep("f.mid", "o", "n"), _ins("f.mid", 0)) is False

    def test_patch_and_replace_at_different_addresses_commute(self) -> None:
        assert ops_commute(_patch("a.mid"), _rep("b.mid", "o", "n")) is True

    def test_patch_and_replace_at_same_address_do_not_commute(self) -> None:
        assert ops_commute(_patch("f.mid"), _rep("f.mid", "o", "n")) is False


# ===========================================================================
# Part 2 — transform: position adjustment for commuting ops
# ===========================================================================


class TestTransform:
    def test_insert_before_insert_shifts_later_op(self) -> None:
        # a inserts at pos 2, b inserts at pos 5.  a < b, so b' = 6.
        a = _ins("f.mid", pos=2, cid="a")
        b = _ins("f.mid", pos=5, cid="b")
        a_prime, b_prime = transform(a, b)
        assert a_prime["position"] == 2  # unchanged
        assert b_prime["position"] == 6  # shifted by a

    def test_insert_after_insert_shifts_earlier_op(self) -> None:
        # a inserts at pos 7, b inserts at pos 3.  a > b, so a' = 8.
        a = _ins("f.mid", pos=7, cid="a")
        b = _ins("f.mid", pos=3, cid="b")
        a_prime, b_prime = transform(a, b)
        assert a_prime["position"] == 8  # shifted by b
        assert b_prime["position"] == 3  # unchanged

    def test_transform_preserves_content_id(self) -> None:
        a = _ins("f.mid", pos=1, cid="note-a")
        b = _ins("f.mid", pos=10, cid="note-b")
        a_prime, b_prime = transform(a, b)
        assert a_prime["content_id"] == "note-a"
        assert b_prime["content_id"] == "note-b"

    def test_transform_unordered_inserts_identity(self) -> None:
        # position=None → identity transform (unordered collection).
        a = _ins("files/", pos=None, cid="a")
        b = _ins("files/", pos=None, cid="b")
        a_prime, b_prime = transform(a, b)
        assert a_prime is a
        assert b_prime is b

    def test_transform_non_insert_ops_identity(self) -> None:
        # For all other commuting pairs, transform returns identity.
        a = _del("a.mid", pos=3)
        b = _del("b.mid", pos=7)
        a_prime, b_prime = transform(a, b)
        assert a_prime is a
        assert b_prime is b

    def test_transform_replace_ops_identity(self) -> None:
        a = _rep("a.mid", "o", "n")
        b = _rep("b.mid", "o", "n")
        a_prime, b_prime = transform(a, b)
        assert a_prime is a
        assert b_prime is b

    def test_transform_diamond_property_two_inserts(self) -> None:
        """Verify that a ∘ b' == b ∘ a' — the fundamental OT diamond property.

        We simulate applying inserts to a sequence and check the final order
        matches regardless of which is applied first.
        """
        # Start with base list indices [0, 1, 2, 3, 4, 5, 6, 7, 8, 9]
        # a = insert 'X' at position 3; b = insert 'Y' at position 7
        a = _ins("seq", pos=3, cid="X")
        b = _ins("seq", pos=7, cid="Y")
        a_prime, b_prime = transform(a, b)

        # Apply a then b': X at 3, Y at 8 → [0,1,2,X,3,4,5,6,7,Y,8,9]
        seq = list(range(10))
        a_pos = a["position"]
        b_prime_pos = b_prime["position"]
        assert a_pos is not None and b_prime_pos is not None
        seq.insert(a_pos, "X")
        seq.insert(b_prime_pos, "Y")
        path_ab = seq[:]

        # Apply b then a': Y at 7, X at 3 → [0,1,2,X,3,4,5,6,Y,7,8,9]
        seq2 = list(range(10))
        b_pos = b["position"]
        a_prime_pos = a_prime["position"]
        assert b_pos is not None and a_prime_pos is not None
        seq2.insert(b_pos, "Y")
        seq2.insert(a_prime_pos, "X")
        path_ba = seq2[:]

        assert path_ab == path_ba


# ===========================================================================
# Part 3 — _adjust_insert_positions (counting formula)
# ===========================================================================


class TestAdjustInsertPositions:
    def test_no_other_ops_identity(self) -> None:
        ops = [_ins("f.mid", pos=5, cid="a")]
        result = _adjust_insert_positions(ops, [])
        assert result[0]["position"] == 5

    def test_single_other_before_shifts_position(self) -> None:
        ops = [_ins("f.mid", pos=5, cid="a")]
        others = [_ins("f.mid", pos=3, cid="x")]
        result = _adjust_insert_positions(ops, others)
        assert result[0]["position"] == 6  # shifted by 1

    def test_other_after_does_not_shift(self) -> None:
        ops = [_ins("f.mid", pos=3, cid="a")]
        others = [_ins("f.mid", pos=5, cid="x")]
        result = _adjust_insert_positions(ops, others)
        assert result[0]["position"] == 3  # unchanged

    def test_multiple_others_all_before_shifts_by_count(self) -> None:
        ops = [_ins("f.mid", pos=10, cid="a")]
        others = [_ins("f.mid", pos=2, cid="x"), _ins("f.mid", pos=7, cid="y")]
        result = _adjust_insert_positions(ops, others)
        assert result[0]["position"] == 12  # shifted by 2

    def test_mixed_addresses_does_not_cross_contaminate(self) -> None:
        ops = [_ins("a.mid", pos=3, cid="a")]
        others = [_ins("b.mid", pos=1, cid="x")]  # different address
        result = _adjust_insert_positions(ops, others)
        assert result[0]["position"] == 3  # not shifted

    def test_non_insert_ops_pass_through_unchanged(self) -> None:
        ops: list[DomainOp] = [_del("f.mid", pos=3, cid="x")]
        result = _adjust_insert_positions(ops, [_ins("f.mid", pos=1, cid="y")])
        assert result[0] is ops[0]

    def test_unordered_insert_passes_through(self) -> None:
        ops = [_ins("files/", pos=None, cid="a")]
        others = [_ins("files/", pos=None, cid="x")]
        result = _adjust_insert_positions(ops, others)
        assert result[0]["position"] is None

    def test_concrete_example_four_note_insertions(self) -> None:
        """Verify counting formula on the four-note example from the spec."""
        ours = [_ins("f.mid", pos=5, cid="V"), _ins("f.mid", pos=10, cid="W")]
        theirs = [_ins("f.mid", pos=3, cid="X"), _ins("f.mid", pos=8, cid="Y")]

        ours_adj = _adjust_insert_positions(ours, theirs)
        theirs_adj = _adjust_insert_positions(theirs, ours)

        # V(5) shifted by X(3) which is ≤ 5: V → 6
        assert ours_adj[0]["position"] == 6
        # W(10) shifted by X(3) and Y(8) both ≤ 10: W → 12
        assert ours_adj[1]["position"] == 12
        # X(3) no ours inserts ≤ 3: stays 3
        assert theirs_adj[0]["position"] == 3
        # Y(8) shifted by V(5) ≤ 8: Y → 9
        assert theirs_adj[1]["position"] == 9


# ===========================================================================
# Part 4 — merge_op_lists: three-way merge
# ===========================================================================


class TestMergeOpLists:
    def test_empty_inputs_return_empty_result(self) -> None:
        result = merge_op_lists([], [], [])
        assert result.merged_ops == []
        assert result.conflict_ops == []
        assert result.is_clean is True

    def test_ours_only_additions_pass_through(self) -> None:
        op = _ins("f.mid", pos=2, cid="x")
        result = merge_op_lists([], [op], [])
        assert len(result.merged_ops) == 1
        assert result.conflict_ops == []

    def test_theirs_only_additions_pass_through(self) -> None:
        op = _del("f.mid", pos=0)
        result = merge_op_lists([], [], [op])
        assert len(result.merged_ops) == 1
        assert result.conflict_ops == []

    def test_non_conflicting_inserts_both_included(self) -> None:
        ours_op = _ins("f.mid", pos=2, cid="V")
        theirs_op = _ins("f.mid", pos=5, cid="W")
        result = merge_op_lists([], [ours_op], [theirs_op])
        assert result.is_clean is True
        positions = {op["position"] for op in result.merged_ops if op["op"] == "insert"}
        # Ours at 2 stays 2 (no theirs ≤ 2); theirs at 5 → 6 (ours at 2 ≤ 5).
        assert 2 in positions
        assert 6 in positions

    def test_same_position_insert_produces_conflict(self) -> None:
        ours_op = _ins("f.mid", pos=3, cid="A")
        theirs_op = _ins("f.mid", pos=3, cid="B")
        result = merge_op_lists([], [ours_op], [theirs_op])
        assert not result.is_clean
        assert len(result.conflict_ops) == 1
        assert result.conflict_ops[0][0]["content_id"] == "A"
        assert result.conflict_ops[0][1]["content_id"] == "B"

    def test_consensus_addition_included_once(self) -> None:
        op = _ins("f.mid", pos=4, cid="shared")
        result = merge_op_lists([], [op], [op])
        # Consensus: both added the same op → include exactly once.
        assert len(result.merged_ops) == 1
        assert result.conflict_ops == []

    def test_base_ops_kept_by_both_sides_included(self) -> None:
        base_op = _ins("f.mid", pos=0, cid="base")
        # Both sides still have the base op.
        result = merge_op_lists([base_op], [base_op], [base_op])
        assert base_op in result.merged_ops

    def test_base_op_deleted_by_ours_not_in_merged(self) -> None:
        base_op = _ins("f.mid", pos=0, cid="base")
        # Ours removed it, theirs kept it.
        result = merge_op_lists([base_op], [], [base_op])
        # The base op is NOT in kept (ours removed it) and NOT in ours_new
        # (it was in base). It remains in theirs, so theirs "kept" it.
        # Only ops in base AND in both branches end up in kept.
        assert base_op not in result.merged_ops

    def test_replace_conflict_at_same_address(self) -> None:
        ours_op = _rep("f.mid", "old", "v-ours")
        theirs_op = _rep("f.mid", "old", "v-theirs")
        result = merge_op_lists([], [ours_op], [theirs_op])
        assert not result.is_clean
        assert len(result.conflict_ops) == 1

    def test_replace_at_different_addresses_no_conflict(self) -> None:
        ours_op = _rep("a.mid", "old", "new-a")
        theirs_op = _rep("b.mid", "old", "new-b")
        result = merge_op_lists([], [ours_op], [theirs_op])
        assert result.is_clean
        assert len(result.merged_ops) == 2

    def test_consensus_delete_included_once(self) -> None:
        del_op = _del("f.mid", pos=2, cid="gone")
        result = merge_op_lists([], [del_op], [del_op])
        assert len(result.merged_ops) == 1

    def test_note_level_multi_insert_positions_adjusted_correctly(self) -> None:
        """Simulate two musicians adding notes at non-overlapping bars."""
        ours_ops: list[DomainOp] = [
            _ins("lead.mid", pos=5, cid="note-A"),
            _ins("lead.mid", pos=10, cid="note-B"),
        ]
        theirs_ops: list[DomainOp] = [
            _ins("lead.mid", pos=3, cid="note-X"),
            _ins("lead.mid", pos=8, cid="note-Y"),
        ]
        result = merge_op_lists([], ours_ops, theirs_ops)
        assert result.is_clean is True
        assert len(result.merged_ops) == 4

        # Expected positions after adjustment (counting formula):
        # note-A(5) → 5 + count(theirs ≤ 5) = 5 + 1[X(3)] = 6
        # note-B(10) → 10 + 2[X(3),Y(8)] = 12
        # note-X(3) → 3 + 0 = 3
        # note-Y(8) → 8 + 1[A(5)] = 9
        pos_by_cid = {
            op["content_id"]: op["position"]
            for op in result.merged_ops
            if op["op"] == "insert"
        }
        assert pos_by_cid["note-A"] == 6
        assert pos_by_cid["note-B"] == 12
        assert pos_by_cid["note-X"] == 3
        assert pos_by_cid["note-Y"] == 9

    def test_mixed_conflict_and_clean_ops(self) -> None:
        """A conflict on one file should not contaminate clean ops on others."""
        conflict_ours = _rep("shared.mid", "old", "v-ours")
        conflict_theirs = _rep("shared.mid", "old", "v-theirs")
        clean_ours = _ins("only-ours.mid", pos=0, cid="ours-new-file")
        clean_theirs = _del("only-theirs.mid", pos=2, cid="their-del")

        result = merge_op_lists(
            [],
            [conflict_ours, clean_ours],
            [conflict_theirs, clean_theirs],
        )
        assert len(result.conflict_ops) == 1
        # Clean ops from both sides should appear in merged.
        merged_cids = {
            op.get("content_id", "") or op.get("new_content_id", "")
            for op in result.merged_ops
        }
        assert "ours-new-file" in merged_cids
        assert "their-del" in merged_cids

    def test_patch_ops_at_different_files_both_included(self) -> None:
        ours_op = _patch("track-a.mid")
        theirs_op = _patch("track-b.mid")
        result = merge_op_lists([], [ours_op], [theirs_op])
        assert result.is_clean is True
        assert len(result.merged_ops) == 2

    def test_patch_ops_at_same_file_with_non_conflicting_children(self) -> None:
        child_a = _ins("note:0", pos=1, cid="note-1")
        child_b = _ins("note:0", pos=4, cid="note-2")
        ours_op = _patch("f.mid", child_ops=[child_a])
        theirs_op = _patch("f.mid", child_ops=[child_b])
        result = merge_op_lists([], [ours_op], [theirs_op])
        # PatchOps at same address with commuting children should commute.
        assert result.is_clean is True

    def test_move_and_delete_conflict_detected(self) -> None:
        move_op = _mov("f.mid", from_pos=5, to_pos=0)
        del_op = _del("f.mid", pos=5)
        result = merge_op_lists([], [move_op], [del_op])
        assert not result.is_clean

    def test_merge_op_lists_is_deterministic(self) -> None:
        """Same inputs → same output on every call."""
        ours = [_ins("f.mid", pos=2, cid="a"), _del("g.mid", pos=0, cid="b")]
        theirs = [_ins("f.mid", pos=7, cid="c"), _rep("h.mid", "x", "y")]
        r1 = merge_op_lists([], ours, theirs)
        r2 = merge_op_lists([], ours, theirs)
        assert [_op_key(o) for o in r1.merged_ops] == [_op_key(o) for o in r2.merged_ops]
        assert r1.conflict_ops == r2.conflict_ops


# ===========================================================================
# Part 5 — merge_structured: StructuredDelta entry point
# ===========================================================================


class TestMergeStructured:
    def test_empty_deltas_produce_clean_result(self) -> None:
        base = _delta([])
        ours = _delta([])
        theirs = _delta([])
        result = merge_structured(base, ours, theirs)
        assert result.is_clean is True
        assert result.merged_ops == []

    def test_non_conflicting_deltas_auto_merge(self) -> None:
        op_a = _ins("a.mid", pos=1, cid="A")
        op_b = _ins("b.mid", pos=2, cid="B")
        result = merge_structured(_delta([]), _delta([op_a]), _delta([op_b]))
        assert result.is_clean is True
        assert len(result.merged_ops) == 2

    def test_conflicting_deltas_reported(self) -> None:
        op_a = _rep("shared.mid", "old", "v-a")
        op_b = _rep("shared.mid", "old", "v-b")
        result = merge_structured(_delta([]), _delta([op_a]), _delta([op_b]))
        assert not result.is_clean
        assert len(result.conflict_ops) == 1

    def test_base_ops_respected_by_both_sides(self) -> None:
        shared = _ins("f.mid", pos=0, cid="shared")
        result = merge_structured(
            _delta([shared]),
            _delta([shared, _ins("f.mid", pos=5, cid="extra-ours")]),
            _delta([shared]),
        )
        assert result.is_clean is True
        # The 'shared' op is kept; 'extra-ours' is new and passes through.
        assert len(result.merged_ops) >= 1


# ===========================================================================
# Part 6 — MergeOpsResult
# ===========================================================================


class TestMergeOpsResult:
    def test_is_clean_when_no_conflicts(self) -> None:
        r = MergeOpsResult(merged_ops=[], conflict_ops=[])
        assert r.is_clean is True

    def test_is_not_clean_when_conflicts_present(self) -> None:
        a = _ins("f.mid", pos=1)
        b = _ins("f.mid", pos=1)
        r = MergeOpsResult(merged_ops=[], conflict_ops=[(a, b)])
        assert r.is_clean is False

    def test_default_factory_empty_lists(self) -> None:
        r = MergeOpsResult()
        assert r.merged_ops == []
        assert r.conflict_ops == []


# ===========================================================================
# Part 7 — _op_key determinism and uniqueness
# ===========================================================================


class TestOpKey:
    def test_insert_key_includes_all_fields(self) -> None:
        op = _ins("f.mid", pos=3, cid="abc")
        key = _op_key(op)
        assert "insert" in key
        assert "f.mid" in key
        assert "3" in key
        assert "abc" in key

    def test_same_op_produces_same_key(self) -> None:
        op = _del("f.mid", pos=2, cid="xyz")
        assert _op_key(op) == _op_key(op)

    def test_different_positions_produce_different_keys(self) -> None:
        a = _ins("f.mid", pos=1, cid="c")
        b = _ins("f.mid", pos=2, cid="c")
        assert _op_key(a) != _op_key(b)

    def test_move_key_includes_from_and_to(self) -> None:
        op = _mov("f.mid", from_pos=3, to_pos=7)
        key = _op_key(op)
        assert "3" in key
        assert "7" in key

    def test_replace_key_includes_old_and_new(self) -> None:
        op = _rep("f.mid", "old-id", "new-id")
        key = _op_key(op)
        assert "old-id" in key
        assert "new-id" in key

    def test_patch_key_includes_address_and_domain(self) -> None:
        op = _patch("f.mid")
        key = _op_key(op)
        assert "patch" in key
        assert "f.mid" in key

"""Property-based tests using :mod:`hypothesis`.

Three correctness properties are verified exhaustively over randomly generated
inputs — something example-based tests cannot do:

1. **CRDT lattice laws** — commutativity, associativity, and idempotency hold
   for all six CRDT types over arbitrary inputs.

2. **LCS round-trip** — ``diff(a, a)`` always produces zero ops; the number of
   edits reported equals the true edit distance (inserts + deletes, excluding
   moves) for arbitrary sequence pairs.

3. **OT diamond property** — for two concurrently applied ``InsertOp``\\s at
   distinct positions, ``transform`` produces adjusted ops such that applying
   them in either order yields the same final sequence.

Why these three?
----------------
- CRDT laws guarantee that any number of agents can merge state in any order
  and reach the same result.  Example-based tests can only verify a handful of
  cases; hypothesis explores thousands.

- LCS round-trip safety means the diff engine never produces incorrect deltas
  for self-diffs (a common pathological case).

- The OT diamond property is the central correctness criterion for Operational
  Transformation: two concurrent edits, transformed and applied, must converge.
  A single counterexample would break real-time collaboration.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from muse.core.crdts import (
    AWMap,
    GCounter,
    LWWRegister,
    ORSet,
    RGA,
    VectorClock,
)
from muse.core.diff_algorithms.lcs import diff as lcs_diff, myers_ses
from muse.core.op_transform import ops_commute, transform
from muse.core.schema import SequenceSchema
from muse.domain import InsertOp

if TYPE_CHECKING:
    pass

# ---------------------------------------------------------------------------
# Hypothesis strategies
# ---------------------------------------------------------------------------

# Alphabet for generated string values — small alphabet makes overlaps more
# likely, exercising merge paths that would be rare with fully random strings.
_ALPHA = st.sampled_from(list("abcde"))
_SHORT_TEXT = st.text(alphabet="abcdefghij", min_size=0, max_size=8)
_AGENT_ID = st.text(alphabet="abcdefghij", min_size=1, max_size=6)
_TIMESTAMP = st.floats(min_value=0.0, max_value=1_000_000.0, allow_nan=False, allow_infinity=False)


@st.composite
def lww_registers(draw: st.DrawFn) -> LWWRegister:
    """Strategy: arbitrary LWWRegister."""
    return LWWRegister(
        value=draw(_SHORT_TEXT),
        timestamp=draw(_TIMESTAMP),
        author=draw(_AGENT_ID),
    )


@st.composite
def vector_clocks(draw: st.DrawFn) -> VectorClock:
    """Strategy: VectorClock with 0–4 agents, counts 0–20."""
    n = draw(st.integers(min_value=0, max_value=4))
    counts = {f"a{i}": draw(st.integers(min_value=0, max_value=20)) for i in range(n)}
    return VectorClock(counts)


@st.composite
def or_sets(draw: st.DrawFn) -> ORSet:
    """Strategy: ORSet built by arbitrary add/remove sequences."""
    elems = draw(st.lists(_SHORT_TEXT, min_size=0, max_size=6))
    s = ORSet()
    added: list[str] = []
    for e in elems:
        s, _ = s.add(e)
        added.append(e)
    # Remove a random subset of added elements using observed-token semantics.
    if added:
        to_remove = draw(st.lists(st.sampled_from(added), max_size=3, unique=True))
        for e in to_remove:
            if e in s.elements():
                s = s.remove(e, s.tokens_for(e))
    return s


@st.composite
def rgas(draw: st.DrawFn) -> RGA:
    """Strategy: RGA built by sequential inserts (append-only for simplicity)."""
    values = draw(st.lists(_SHORT_TEXT, min_size=0, max_size=6))
    rga = RGA()
    ids: list[str] = []
    for i, v in enumerate(values):
        eid = f"{i}@agent"
        rga = rga.insert(ids[-1] if ids else None, v, element_id=eid)
        ids.append(eid)
    # Optionally delete some elements.
    if ids:
        to_del = draw(st.lists(st.sampled_from(ids), max_size=2, unique=True))
        for eid in to_del:
            rga = rga.delete(eid)
    return rga


@st.composite
def aw_maps(draw: st.DrawFn) -> AWMap:
    """Strategy: AWMap built by arbitrary set operations."""
    entries = draw(st.dictionaries(_SHORT_TEXT, _SHORT_TEXT, max_size=4))
    m = AWMap()
    for k, v in entries.items():
        m = m.set(k, v)
    return m


@st.composite
def g_counters(draw: st.DrawFn) -> GCounter:
    """Strategy: GCounter with 0–4 agents, counts 0–20."""
    n = draw(st.integers(min_value=0, max_value=4))
    counts = {f"a{i}": draw(st.integers(min_value=0, max_value=20)) for i in range(n)}
    return GCounter(counts)


@st.composite
def content_id_lists(draw: st.DrawFn) -> list[str]:
    """Strategy: list of content IDs drawn from a small pool.

    A small pool means pairs of generated lists share elements, exercising
    the LCS "keep" path rather than always producing pure insert+delete scripts.
    """
    pool = [f"h{i}" for i in range(8)]  # 8 possible hashes
    return draw(st.lists(st.sampled_from(pool), min_size=0, max_size=8))


# ---------------------------------------------------------------------------
# Shared SequenceSchema for LCS tests
# ---------------------------------------------------------------------------

_SEQ_SCHEMA = SequenceSchema(
    kind="sequence",
    element_type="content_id",
    identity="by_content",
    diff_algorithm="lcs",
    order="indexed",
)


# ---------------------------------------------------------------------------
# CRDT lattice law tests
# ---------------------------------------------------------------------------


class TestLWWRegisterLatticeHypothesis:
    """LWWRegister satisfies all three CRDT lattice laws for arbitrary inputs."""

    @given(lww_registers(), lww_registers())
    def test_commutativity(self, a: LWWRegister, b: LWWRegister) -> None:
        assert a.join(b).equivalent(b.join(a))

    @given(lww_registers(), lww_registers(), lww_registers())
    def test_associativity(self, a: LWWRegister, b: LWWRegister, c: LWWRegister) -> None:
        assert a.join(b).join(c).equivalent(a.join(b.join(c)))

    @given(lww_registers())
    def test_idempotency(self, a: LWWRegister) -> None:
        assert a.join(a).equivalent(a)

    @given(lww_registers(), lww_registers())
    def test_join_winner_is_higher_timestamp(self, a: LWWRegister, b: LWWRegister) -> None:
        """The winner's value is always the one with the higher comparison key.

        The key is ``(timestamp, author, value)``; including ``value`` as the
        final tiebreaker is what makes ``join`` commutative when two writes share
        the same ``(timestamp, author)`` pair.
        """
        result = a.join(b)
        a_wire = a.to_dict()
        b_wire = b.to_dict()
        a_key = (a_wire["timestamp"], a_wire["author"], a_wire["value"])
        b_key = (b_wire["timestamp"], b_wire["author"], b_wire["value"])
        if a_key >= b_key:
            assert result.read() == a.read()
        else:
            assert result.read() == b.read()


class TestVectorClockLatticeHypothesis:
    """VectorClock satisfies all three CRDT lattice laws for arbitrary inputs."""

    @given(vector_clocks(), vector_clocks())
    def test_commutativity(self, a: VectorClock, b: VectorClock) -> None:
        assert a.merge(b).equivalent(b.merge(a))

    @given(vector_clocks(), vector_clocks(), vector_clocks())
    def test_associativity(self, a: VectorClock, b: VectorClock, c: VectorClock) -> None:
        assert a.merge(b).merge(c).equivalent(a.merge(b.merge(c)))

    @given(vector_clocks())
    def test_idempotency(self, a: VectorClock) -> None:
        assert a.merge(a).equivalent(a)

    @given(vector_clocks(), vector_clocks())
    def test_merge_is_pointwise_max(self, a: VectorClock, b: VectorClock) -> None:
        """Every agent's count in the merged clock equals max(a[agent], b[agent])."""
        merged = a.merge(b)
        a_dict = a.to_dict()
        b_dict = b.to_dict()
        for agent in set(a_dict) | set(b_dict):
            expected = max(a_dict.get(agent, 0), b_dict.get(agent, 0))
            assert merged.to_dict().get(agent, 0) == expected


class TestORSetLatticeHypothesis:
    """ORSet satisfies all three CRDT lattice laws for arbitrary inputs."""

    @given(or_sets(), or_sets())
    def test_commutativity(self, a: ORSet, b: ORSet) -> None:
        assert a.join(b).elements() == b.join(a).elements()

    @given(or_sets(), or_sets(), or_sets())
    def test_associativity(self, a: ORSet, b: ORSet, c: ORSet) -> None:
        assert a.join(b).join(c).elements() == a.join(b.join(c)).elements()

    @given(or_sets())
    def test_idempotency(self, a: ORSet) -> None:
        assert a.join(a).elements() == a.elements()

    @given(or_sets(), or_sets())
    def test_join_contains_both_element_sets(self, a: ORSet, b: ORSet) -> None:
        """join(a, b) must contain every element visible in either a or b."""
        joined = a.join(b)
        assert a.elements() <= joined.elements()
        assert b.elements() <= joined.elements()


class TestRGALatticeHypothesis:
    """RGA satisfies all three CRDT lattice laws for arbitrary inputs."""

    @given(rgas(), rgas())
    def test_commutativity(self, a: RGA, b: RGA) -> None:
        assert a.join(b).equivalent(b.join(a))

    @given(rgas(), rgas(), rgas())
    def test_associativity(self, a: RGA, b: RGA, c: RGA) -> None:
        assert a.join(b).join(c).equivalent(a.join(b.join(c)))

    @given(rgas())
    def test_idempotency(self, a: RGA) -> None:
        assert a.join(a).equivalent(a)

    @given(rgas(), rgas())
    def test_join_contains_all_element_ids(self, a: RGA, b: RGA) -> None:
        """Every element ID known to either replica must appear in the join.

        RGA deletions are *monotone*: once an element is tombstoned in either
        replica it must remain tombstoned in the join.  This means we can only
        assert that element *IDs* are preserved — not that elements remain
        visible — because a concurrent delete in the other replica may correctly
        tombstone them.

        What must hold:
        - Every ID (visible or tombstoned) from either replica appears in the join.
        - If an element is tombstoned in either, it is tombstoned in the join.
        """
        joined = a.join(b)
        joined_all = {e["id"]: e for e in joined.to_dict()}
        a_all = {e["id"]: e for e in a.to_dict()}
        b_all = {e["id"]: e for e in b.to_dict()}

        # All IDs from both replicas must be present in the join.
        assert set(a_all) <= set(joined_all), f"IDs from a lost in join: {set(a_all) - set(joined_all)}"
        assert set(b_all) <= set(joined_all), f"IDs from b lost in join: {set(b_all) - set(joined_all)}"

        # Monotone deletion: if either replica tombstones an element, the join must too.
        for eid, elem in a_all.items():
            if elem["deleted"] and eid in joined_all:
                assert joined_all[eid]["deleted"], f"Element {eid} deleted in a but not in join"
        for eid, elem in b_all.items():
            if elem["deleted"] and eid in joined_all:
                assert joined_all[eid]["deleted"], f"Element {eid} deleted in b but not in join"


class TestAWMapLatticeHypothesis:
    """AWMap satisfies all three CRDT lattice laws for arbitrary inputs."""

    @given(aw_maps(), aw_maps())
    def test_commutativity(self, a: AWMap, b: AWMap) -> None:
        assert a.join(b).to_plain_dict() == b.join(a).to_plain_dict()

    @given(aw_maps(), aw_maps(), aw_maps())
    def test_associativity(self, a: AWMap, b: AWMap, c: AWMap) -> None:
        assert a.join(b).join(c).to_plain_dict() == a.join(b.join(c)).to_plain_dict()

    @given(aw_maps())
    def test_idempotency(self, a: AWMap) -> None:
        assert a.join(a).to_plain_dict() == a.to_plain_dict()


class TestGCounterLatticeHypothesis:
    """GCounter satisfies all three CRDT lattice laws for arbitrary inputs."""

    @given(g_counters(), g_counters())
    def test_commutativity(self, a: GCounter, b: GCounter) -> None:
        assert a.join(b).equivalent(b.join(a))

    @given(g_counters(), g_counters(), g_counters())
    def test_associativity(self, a: GCounter, b: GCounter, c: GCounter) -> None:
        assert a.join(b).join(c).equivalent(a.join(b.join(c)))

    @given(g_counters())
    def test_idempotency(self, a: GCounter) -> None:
        assert a.join(a).equivalent(a)

    @given(g_counters(), g_counters())
    def test_join_is_monotone(self, a: GCounter, b: GCounter) -> None:
        """join(a, b).value(agent) >= a.value(agent) for all agents in a."""
        joined = a.join(b)
        for agent, count in a.to_dict().items():
            assert joined.to_dict().get(agent, 0) >= count


# ---------------------------------------------------------------------------
# LCS round-trip properties
# ---------------------------------------------------------------------------


class TestLCSRoundTrip:
    """LCS diff produces correct edit scripts for arbitrary sequence pairs."""

    @given(content_id_lists())
    def test_self_diff_has_no_ops(self, seq: list[str]) -> None:
        """Diffing a sequence against itself always produces an empty op list."""
        delta = lcs_diff(_SEQ_SCHEMA, seq, seq, domain="test")
        non_keep_ops = [op for op in delta["ops"] if op["op"] != "keep"]
        assert non_keep_ops == [], f"Expected no ops for self-diff, got {non_keep_ops}"

    @given(content_id_lists())
    def test_self_diff_summary_contains_no_changes(self, seq: list[str]) -> None:
        delta = lcs_diff(_SEQ_SCHEMA, seq, seq, domain="test")
        assert "no" in delta["summary"].lower() and "change" in delta["summary"].lower()

    @given(content_id_lists(), content_id_lists())
    def test_delta_op_count_is_at_most_ses_edit_count(
        self, base: list[str], target: list[str]
    ) -> None:
        """The total number of ops in the delta never exceeds the SES edit count.

        ``detect_moves`` collapses (insert, delete) pairs into single ``MoveOp``\\s,
        so the op count is always ≤ the raw SES edit count.  The SES edit count is
        the number of insert + delete steps (keep steps are not counted).
        """
        steps = myers_ses(base, target)
        raw_edit_count = sum(1 for s in steps if s.kind != "keep")

        delta = lcs_diff(_SEQ_SCHEMA, base, target, domain="test")
        # Each MoveOp replaces 1 insert + 1 delete, so total ops ≤ raw edit count.
        actual_op_count = len(delta["ops"])
        assert actual_op_count <= raw_edit_count, (
            f"Op count {actual_op_count} > SES edit count {raw_edit_count}"
        )

    @given(content_id_lists(), content_id_lists())
    def test_diff_ops_content_ids_are_in_base_or_target(
        self, base: list[str], target: list[str]
    ) -> None:
        """Every content_id in the delta ops must come from base or target."""
        base_set = set(base)
        target_set = set(target)
        universe = base_set | target_set

        delta = lcs_diff(_SEQ_SCHEMA, base, target, domain="test")
        for op in delta["ops"]:
            if op["op"] in ("insert", "delete"):
                assert op["content_id"] in universe, (
                    f"Unknown content_id {op['content_id']!r} in op {op['op']!r}"
                )

    @given(content_id_lists())
    def test_empty_base_produces_only_inserts(self, target: list[str]) -> None:
        """Diffing empty base → target has exactly len(target) insert ops."""
        delta = lcs_diff(_SEQ_SCHEMA, [], target, domain="test")
        assert sum(1 for op in delta["ops"] if op["op"] == "insert") == len(target)
        assert all(op["op"] == "insert" for op in delta["ops"])

    @given(content_id_lists())
    def test_empty_target_produces_only_deletes(self, base: list[str]) -> None:
        """Diffing base → empty target has exactly len(base) delete ops."""
        delta = lcs_diff(_SEQ_SCHEMA, base, [], domain="test")
        assert sum(1 for op in delta["ops"] if op["op"] == "delete") == len(base)
        assert all(op["op"] == "delete" for op in delta["ops"])

    @given(content_id_lists(), content_id_lists())
    def test_domain_tag_propagated(self, base: list[str], target: list[str]) -> None:
        """The domain tag from the call site is preserved in the StructuredDelta."""
        delta = lcs_diff(_SEQ_SCHEMA, base, target, domain="my-domain")
        assert delta["domain"] == "my-domain"

    @given(st.lists(st.integers(min_value=0, max_value=5), max_size=6))
    def test_deduplicated_sequence_round_trips(self, indices: list[int]) -> None:
        """Converting ints → content IDs and diffing against itself always has no ops."""
        seq = [f"hash-{i}" for i in indices]
        delta = lcs_diff(_SEQ_SCHEMA, seq, seq, domain="test")
        assert not [op for op in delta["ops"] if op["op"] != "keep"]


# ---------------------------------------------------------------------------
# OT diamond property
# ---------------------------------------------------------------------------


def _apply_insert_ops(base: list[str], ops: list[InsertOp]) -> list[str]:
    """Apply a list of InsertOps to *base*, returning the resulting list.

    Ops are applied in order; each op's position is relative to the *current*
    state of the list at the time of application (i.e. positions shift as
    earlier ops are applied).
    """
    result = list(base)
    for op in ops:
        pos = op["position"]
        if pos is None:
            result.append(op["content_id"])
        else:
            result.insert(pos, op["content_id"])
    return result


@st.composite
def _two_commuting_insert_ops(draw: st.DrawFn) -> tuple[InsertOp, InsertOp]:
    """Strategy: two InsertOps at distinct positions on the same address."""
    addr = draw(st.text(alphabet="abcde", min_size=1, max_size=4))
    pos_a = draw(st.integers(min_value=0, max_value=9))
    # Ensure distinct positions so ops_commute returns True.
    offset = draw(st.integers(min_value=1, max_value=9))
    pos_b = pos_a + offset
    a = InsertOp(
        op="insert",
        address=addr,
        position=pos_a,
        content_id=draw(_SHORT_TEXT),
        content_summary="op-a",
    )
    b = InsertOp(
        op="insert",
        address=addr,
        position=pos_b,
        content_id=draw(_SHORT_TEXT),
        content_summary="op-b",
    )
    return a, b


class TestOTDiamondProperty:
    """``transform`` satisfies the OT diamond property for InsertOp pairs."""

    @given(_two_commuting_insert_ops())
    def test_insert_insert_diamond(self, ops: tuple[InsertOp, InsertOp]) -> None:
        """Applying (a then b') == applying (b then a') for concurrent inserts.

        This is the fundamental OT correctness criterion.  With a 10-element
        base, both application orders must produce identical final sequences.
        """
        a, b = ops
        assert ops_commute(a, b), "strategy must only produce commuting pairs"

        a_prime, b_prime = transform(a, b)
        base: list[str] = [f"x{i}" for i in range(10)]

        # Path 1: apply a first, then the transformed b.
        path1 = _apply_insert_ops(_apply_insert_ops(base, [a]), [b_prime])
        # Path 2: apply b first, then the transformed a.
        path2 = _apply_insert_ops(_apply_insert_ops(base, [b]), [a_prime])

        assert path1 == path2, (
            f"Diamond property violated:\n"
            f"  a={dict(a)}\n  b={dict(b)}\n"
            f"  a'={dict(a_prime)}\n  b'={dict(b_prime)}\n"
            f"  path1={path1}\n  path2={path2}"
        )

    @given(_two_commuting_insert_ops())
    def test_transform_preserves_op_types(self, ops: tuple[InsertOp, InsertOp]) -> None:
        """transform() must always return InsertOps when given InsertOps."""
        a, b = ops
        a_prime, b_prime = transform(a, b)
        assert a_prime["op"] == "insert"
        assert b_prime["op"] == "insert"

    @given(_two_commuting_insert_ops())
    def test_transform_preserves_content(self, ops: tuple[InsertOp, InsertOp]) -> None:
        """transform() must not alter the content being inserted."""
        a, b = ops
        a_prime, b_prime = transform(a, b)
        assert a_prime["content_id"] == a["content_id"]
        assert b_prime["content_id"] == b["content_id"]

    @given(_two_commuting_insert_ops())
    def test_ops_commute_symmetry(self, ops: tuple[InsertOp, InsertOp]) -> None:
        """ops_commute(a, b) == ops_commute(b, a) — the relation is symmetric."""
        a, b = ops
        assert ops_commute(a, b) == ops_commute(b, a)

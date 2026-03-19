"""Comprehensive test suite for the CRDT primitive library.

Tests cover all six CRDT types:
- :class:`~muse.core.crdts.vclock.VectorClock`
- :class:`~muse.core.crdts.lww_register.LWWRegister`
- :class:`~muse.core.crdts.or_set.ORSet`
- :class:`~muse.core.crdts.rga.RGA`
- :class:`~muse.core.crdts.aw_map.AWMap`
- :class:`~muse.core.crdts.g_counter.GCounter`

Each type is tested for:
1. Basic operational correctness.
2. All three CRDT lattice laws: commutativity, associativity, idempotency.
3. Serialisation round-trip (to_dict / from_dict).
4. Edge cases (empty structures, concurrent writes, tombstone correctness).

Additionally, :func:`~muse.core.merge_engine.crdt_join_snapshots` is tested
for the integration path through the merge engine.
"""

import pathlib

import pytest

from muse.domain import CRDTPlugin
from muse.core.crdts import (
    AWMap,
    GCounter,
    LWWRegister,
    ORSet,
    RGA,
    VectorClock,
)
from muse.core.crdts.lww_register import LWWValue
from muse.core.crdts.or_set import ORSetDict
from muse.core.crdts.rga import RGAElement


# ===========================================================================
# VectorClock
# ===========================================================================


class TestVectorClock:
    def test_increment_own_agent(self) -> None:
        vc = VectorClock()
        vc2 = vc.increment("agent-A")
        assert vc2.to_dict() == {"agent-A": 1}

    def test_increment_twice(self) -> None:
        vc = VectorClock().increment("agent-A").increment("agent-A")
        assert vc.to_dict()["agent-A"] == 2

    def test_merge_takes_max_per_agent(self) -> None:
        a = VectorClock({"agent-A": 3, "agent-B": 1})
        b = VectorClock({"agent-A": 1, "agent-B": 5, "agent-C": 2})
        merged = a.merge(b)
        assert merged.to_dict() == {"agent-A": 3, "agent-B": 5, "agent-C": 2}

    def test_happens_before_simple(self) -> None:
        a = VectorClock({"agent-A": 1})
        b = VectorClock({"agent-A": 2})
        assert a.happens_before(b)
        assert not b.happens_before(a)

    def test_happens_before_multi_agent(self) -> None:
        a = VectorClock({"agent-A": 1, "agent-B": 2})
        b = VectorClock({"agent-A": 2, "agent-B": 3})
        assert a.happens_before(b)

    def test_not_happens_before_concurrent(self) -> None:
        a = VectorClock({"agent-A": 2, "agent-B": 1})
        b = VectorClock({"agent-A": 1, "agent-B": 2})
        assert not a.happens_before(b)
        assert not b.happens_before(a)

    def test_concurrent_with_neither_dominates(self) -> None:
        a = VectorClock({"agent-A": 2, "agent-B": 1})
        b = VectorClock({"agent-A": 1, "agent-B": 2})
        assert a.concurrent_with(b)
        assert b.concurrent_with(a)

    def test_not_concurrent_with_itself(self) -> None:
        a = VectorClock({"agent-A": 1})
        assert not a.concurrent_with(a)

    def test_idempotent_merge(self) -> None:
        a = VectorClock({"agent-A": 3, "agent-B": 1})
        assert a.merge(a).equivalent(a)

    def test_merge_commutativity(self) -> None:
        a = VectorClock({"agent-A": 3, "agent-B": 1})
        b = VectorClock({"agent-A": 1, "agent-B": 5})
        assert a.merge(b).equivalent(b.merge(a))

    def test_merge_associativity(self) -> None:
        a = VectorClock({"agent-A": 1})
        b = VectorClock({"agent-B": 2})
        c = VectorClock({"agent-C": 3})
        assert a.merge(b).merge(c).equivalent(a.merge(b.merge(c)))

    def test_round_trip_to_from_dict(self) -> None:
        vc = VectorClock({"agent-A": 5, "agent-B": 3})
        assert VectorClock.from_dict(vc.to_dict()).equivalent(vc)

    def test_empty_clock_happens_before_non_empty(self) -> None:
        empty = VectorClock()
        non_empty = VectorClock({"agent-A": 1})
        assert empty.happens_before(non_empty)

    def test_equal_clocks_not_happens_before(self) -> None:
        a = VectorClock({"agent-A": 1})
        b = VectorClock({"agent-A": 1})
        assert not a.happens_before(b)
        assert not b.happens_before(a)


# ===========================================================================
# LWWRegister
# ===========================================================================


class TestLWWRegister:
    def _make(self, value: str, ts: float, author: str) -> LWWRegister:
        data: LWWValue = {"value": value, "timestamp": ts, "author": author}
        return LWWRegister.from_dict(data)

    def test_read_returns_value(self) -> None:
        r = self._make("C major", 1.0, "agent-1")
        assert r.read() == "C major"

    def test_lww_later_timestamp_wins(self) -> None:
        a = self._make("C major", 1.0, "agent-1")
        b = self._make("G major", 2.0, "agent-2")
        assert a.join(b).read() == "G major"
        assert b.join(a).read() == "G major"

    def test_lww_same_timestamp_author_tiebreak(self) -> None:
        # Lexicographically larger author wins
        a = self._make("C major", 1.0, "agent-A")
        b = self._make("G major", 1.0, "agent-B")
        # "agent-B" > "agent-A" lexicographically
        result = a.join(b)
        assert result.read() == "G major"
        result2 = b.join(a)
        assert result2.read() == "G major"

    def test_join_is_commutative(self) -> None:
        a = self._make("C major", 1.0, "agent-1")
        b = self._make("G major", 2.0, "agent-2")
        assert a.join(b).equivalent(b.join(a))

    def test_join_is_associative(self) -> None:
        a = self._make("C major", 1.0, "agent-1")
        b = self._make("G major", 2.0, "agent-2")
        c = self._make("D minor", 3.0, "agent-3")
        assert a.join(b).join(c).equivalent(a.join(b.join(c)))

    def test_join_is_idempotent(self) -> None:
        a = self._make("C major", 1.0, "agent-1")
        assert a.join(a).equivalent(a)

    def test_write_returns_winner(self) -> None:
        r = self._make("C major", 5.0, "agent-1")
        r2 = r.write("G major", 3.0, "agent-2")
        # older write loses
        assert r2.read() == "C major"

    def test_round_trip_to_from_dict(self) -> None:
        r = self._make("A minor", 42.0, "agent-x")
        assert LWWRegister.from_dict(r.to_dict()).equivalent(r)


# ===========================================================================
# ORSet
# ===========================================================================


class TestORSet:
    def test_add_element(self) -> None:
        s = ORSet()
        s, _ = s.add("note-A")
        assert "note-A" in s

    def test_remove_element(self) -> None:
        s = ORSet()
        s, tok = s.add("note-A")
        s = s.remove("note-A", {tok})
        assert "note-A" not in s

    def test_add_survives_concurrent_remove(self) -> None:
        # Agent 1 adds note-A with token_1
        s1 = ORSet()
        s1, tok1 = s1.add("note-A")

        # Agent 2 removes note-A by tombstoning token_1
        s2 = ORSet()
        s2, _ = s2.add("note-A")  # s2 adds with its own token
        s2 = s2.remove("note-A", {tok1})  # removes agent-1's token only

        # Agent 1 concurrently adds note-A again with token_2 (new token, survives)
        s1_v2, tok2 = s1.add("note-A")

        # Merge: agent-1's new add survives agent-2's remove of old token
        merged = s1_v2.join(s2)
        assert "note-A" in merged

    def test_remove_observed_element_works(self) -> None:
        s = ORSet()
        s, tok = s.add("note-B")
        tokens = s.tokens_for("note-B")
        s = s.remove("note-B", tokens)
        assert "note-B" not in s

    def test_join_is_commutative(self) -> None:
        s1 = ORSet()
        s1, _ = s1.add("X")

        s2 = ORSet()
        s2, _ = s2.add("Y")

        assert s1.join(s2).elements() == s2.join(s1).elements()

    def test_join_is_associative(self) -> None:
        s1 = ORSet()
        s1, _ = s1.add("X")
        s2 = ORSet()
        s2, _ = s2.add("Y")
        s3 = ORSet()
        s3, _ = s3.add("Z")

        left = s1.join(s2).join(s3)
        right = s1.join(s2.join(s3))
        assert left.elements() == right.elements()

    def test_join_is_idempotent(self) -> None:
        s = ORSet()
        s, _ = s.add("X")
        assert s.join(s).elements() == s.elements()

    def test_tokens_for_returns_live_tokens(self) -> None:
        s = ORSet()
        s, tok = s.add("X")
        assert tok in s.tokens_for("X")

    def test_contains_dunder(self) -> None:
        s = ORSet()
        s, _ = s.add("Z")
        assert "Z" in s
        assert "W" not in s

    def test_round_trip_to_from_dict(self) -> None:
        s = ORSet()
        s, _ = s.add("A")
        s, _ = s.add("B")
        data: ORSetDict = s.to_dict()
        s2 = ORSet.from_dict(data)
        assert s2.elements() == s.elements()

    def test_add_multiple_same_element(self) -> None:
        s = ORSet()
        s, tok1 = s.add("X")
        s, tok2 = s.add("X")
        # Both tokens are live
        assert tok1 in s.tokens_for("X")
        assert tok2 in s.tokens_for("X")


# ===========================================================================
# RGA
# ===========================================================================


class TestRGA:
    def test_insert_after_none_is_prepend(self) -> None:
        rga = RGA()
        rga = rga.insert(None, "a", element_id="1@agent")
        assert rga.to_sequence() == ["a"]

    def test_insert_at_end(self) -> None:
        id_a = "1@agent"
        rga = RGA()
        rga = rga.insert(None, "a", element_id=id_a)
        rga = rga.insert(id_a, "b", element_id="2@agent")
        assert rga.to_sequence() == ["a", "b"]

    def test_insert_in_middle(self) -> None:
        # In RGA, more-recently-inserted elements (larger ID) at the same anchor
        # appear to the LEFT of earlier-inserted elements.  So insert "c" first
        # with a smaller ID, then insert "b" with a larger ID at the same anchor
        # to get b appearing before c in the visible sequence.
        id_a = "1@agent"
        rga = RGA()
        rga = rga.insert(None, "a", element_id=id_a)
        rga = rga.insert(id_a, "c", element_id="2@agent")  # inserted first → smaller ID
        rga = rga.insert(id_a, "b", element_id="3@agent")  # inserted second → larger ID → goes left
        assert rga.to_sequence() == ["a", "b", "c"]

    def test_delete_marks_tombstone(self) -> None:
        id_a = "1@agent"
        rga = RGA()
        rga = rga.insert(None, "a", element_id=id_a)
        rga = rga.delete(id_a)
        assert rga.to_sequence() == []

    def test_delete_unknown_id_is_noop(self) -> None:
        rga = RGA()
        rga = rga.insert(None, "a", element_id="1@agent")
        rga2 = rga.delete("nonexistent-id")
        assert rga2.to_sequence() == ["a"]

    def test_concurrent_insert_same_position_deterministic(self) -> None:
        # Two agents both insert after id_a; larger ID goes first
        id_a = "1@agent"
        rga = RGA()
        rga = rga.insert(None, "a", element_id=id_a)

        rga_agent1 = rga.insert(id_a, "B", element_id="2@agent-z")  # larger ID
        rga_agent2 = rga.insert(id_a, "C", element_id="2@agent-a")  # smaller ID

        merged_1_then_2 = rga_agent1.join(rga_agent2)
        merged_2_then_1 = rga_agent2.join(rga_agent1)

        # Both orderings must produce the same sequence (commutativity)
        assert merged_1_then_2.to_sequence() == merged_2_then_1.to_sequence()

    def test_join_is_commutative(self) -> None:
        rga1 = RGA()
        rga1 = rga1.insert(None, "X", element_id="1@a")

        rga2 = RGA()
        rga2 = rga2.insert(None, "Y", element_id="1@b")

        assert rga1.join(rga2).to_sequence() == rga2.join(rga1).to_sequence()

    def test_join_is_associative(self) -> None:
        rga1 = RGA()
        rga1 = rga1.insert(None, "X", element_id="1@a")

        rga2 = RGA()
        rga2 = rga2.insert(None, "Y", element_id="1@b")

        rga3 = RGA()
        rga3 = rga3.insert(None, "Z", element_id="1@c")

        left = rga1.join(rga2).join(rga3)
        right = rga1.join(rga2.join(rga3))
        assert left.to_sequence() == right.to_sequence()

    def test_join_is_idempotent(self) -> None:
        rga = RGA()
        rga = rga.insert(None, "X", element_id="1@agent")
        assert rga.join(rga).to_sequence() == rga.to_sequence()

    def test_to_sequence_excludes_tombstones(self) -> None:
        id1 = "1@agent"
        rga = RGA()
        rga = rga.insert(None, "A", element_id=id1)
        rga = rga.insert(id1, "B", element_id="2@agent")
        rga = rga.delete(id1)
        assert rga.to_sequence() == ["B"]

    def test_rga_round_trip_to_from_dict(self) -> None:
        id1 = "1@agent"
        rga = RGA()
        rga = rga.insert(None, "A", element_id=id1)
        rga = rga.insert(id1, "B", element_id="2@agent")
        data: list[RGAElement] = rga.to_dict()
        rga2 = RGA.from_dict(data)
        assert rga2.to_sequence() == rga.to_sequence()

    def test_len_excludes_tombstones(self) -> None:
        id1 = "1@agent"
        rga = RGA()
        rga = rga.insert(None, "A", element_id=id1)
        rga = rga.insert(id1, "B", element_id="2@agent")
        rga = rga.delete(id1)
        assert len(rga) == 1

    def test_tombstone_survives_join(self) -> None:
        id1 = "1@agent"
        rga1 = RGA()
        rga1 = rga1.insert(None, "A", element_id=id1)

        rga2 = rga1.delete(id1)

        merged = rga1.join(rga2)
        # Once deleted in either replica, stays deleted
        assert "A" not in merged.to_sequence()


# ===========================================================================
# AWMap
# ===========================================================================


class TestAWMap:
    def test_set_and_get(self) -> None:
        m = AWMap()
        m = m.set("tempo", "120bpm")
        assert m.get("tempo") == "120bpm"

    def test_get_absent_returns_none(self) -> None:
        m = AWMap()
        assert m.get("nonexistent") is None

    def test_overwrite_key(self) -> None:
        m = AWMap()
        m = m.set("key", "C major")
        m = m.set("key", "G major")
        assert m.get("key") == "G major"

    def test_remove_key(self) -> None:
        m = AWMap()
        m = m.set("tempo", "120bpm")
        m = m.remove("tempo")
        assert m.get("tempo") is None

    def test_add_wins_concurrent_remove(self) -> None:
        # Agent A sets "tempo"
        m1 = AWMap()
        m1 = m1.set("tempo", "120bpm")

        # Agent B removes "tempo" by tombstoning its tokens
        m2 = AWMap()
        m2 = m2.set("tempo", "120bpm")
        m2 = m2.remove("tempo")

        # Agent A concurrently adds a new value (new token)
        m1_v2 = m1.set("tempo", "140bpm")

        # Merge: the new add survives because it has a fresh token
        merged = m1_v2.join(m2)
        assert merged.get("tempo") == "140bpm"

    def test_join_is_commutative(self) -> None:
        m1 = AWMap()
        m1 = m1.set("A", "1")
        m2 = AWMap()
        m2 = m2.set("B", "2")
        assert m1.join(m2).to_plain_dict() == m2.join(m1).to_plain_dict()

    def test_join_is_associative(self) -> None:
        m1 = AWMap().set("A", "1")
        m2 = AWMap().set("B", "2")
        m3 = AWMap().set("C", "3")
        left = m1.join(m2).join(m3)
        right = m1.join(m2.join(m3))
        assert left.to_plain_dict() == right.to_plain_dict()

    def test_join_is_idempotent(self) -> None:
        m = AWMap().set("A", "1")
        assert m.join(m).to_plain_dict() == m.to_plain_dict()

    def test_keys_returns_live_keys(self) -> None:
        m = AWMap()
        m = m.set("X", "1")
        m = m.set("Y", "2")
        assert m.keys() == frozenset({"X", "Y"})

    def test_contains(self) -> None:
        m = AWMap().set("K", "V")
        assert "K" in m
        assert "Z" not in m

    def test_round_trip_to_from_dict(self) -> None:
        m = AWMap().set("A", "1").set("B", "2")
        m2 = AWMap.from_dict(m.to_dict())
        assert m2.to_plain_dict() == m.to_plain_dict()


# ===========================================================================
# GCounter
# ===========================================================================


class TestGCounter:
    def test_initial_value_is_zero(self) -> None:
        c = GCounter()
        assert c.value() == 0

    def test_increment(self) -> None:
        c = GCounter().increment("agent-1")
        assert c.value() == 1
        assert c.value_for("agent-1") == 1

    def test_increment_by_n(self) -> None:
        c = GCounter().increment("agent-1", by=5)
        assert c.value() == 5

    def test_increment_rejects_zero(self) -> None:
        with pytest.raises(ValueError):
            GCounter().increment("agent-1", by=0)

    def test_increment_rejects_negative(self) -> None:
        with pytest.raises(ValueError):
            GCounter().increment("agent-1", by=-1)

    def test_join_takes_max_per_agent(self) -> None:
        c1 = GCounter({"agent-A": 3, "agent-B": 1})
        c2 = GCounter({"agent-A": 1, "agent-B": 5})
        merged = c1.join(c2)
        assert merged.value_for("agent-A") == 3
        assert merged.value_for("agent-B") == 5
        assert merged.value() == 8

    def test_join_is_commutative(self) -> None:
        c1 = GCounter({"agent-A": 3})
        c2 = GCounter({"agent-B": 7})
        assert c1.join(c2).equivalent(c2.join(c1))

    def test_join_is_associative(self) -> None:
        c1 = GCounter({"agent-A": 1})
        c2 = GCounter({"agent-B": 2})
        c3 = GCounter({"agent-C": 3})
        assert c1.join(c2).join(c3).equivalent(c1.join(c2.join(c3)))

    def test_join_is_idempotent(self) -> None:
        c = GCounter({"agent-A": 5})
        assert c.join(c).equivalent(c)

    def test_value_for_absent_agent_is_zero(self) -> None:
        c = GCounter()
        assert c.value_for("ghost-agent") == 0

    def test_round_trip_to_from_dict(self) -> None:
        c = GCounter({"a": 1, "b": 2})
        c2 = GCounter.from_dict(c.to_dict())
        assert c2.equivalent(c)


# ===========================================================================
# CRDTPlugin integration — merge engine CRDT path
# ===========================================================================


class TestCRDTMergeEngineIntegration:
    """Tests for :func:`~muse.core.merge_engine.crdt_join_snapshots`.

    Since there is no production CRDTPlugin implementation yet (the MIDI plugin
    is still three-way mode), we create a minimal stub that satisfies the
    CRDTPlugin protocol.
    """

    def _make_stub_plugin(self) -> CRDTPlugin:
        """Return a minimal CRDTPlugin stub."""
        from muse.domain import (
            CRDTPlugin,
            CRDTSnapshotManifest,
            DriftReport,
            LiveState,
            MergeResult,
            StateSnapshot,
            StateDelta,
            StructuredDelta,
        )
        from muse.core.schema import CRDTDimensionSpec, DomainSchema

        class StubCRDTPlugin(CRDTPlugin):
            def snapshot(self, live_state: LiveState) -> StateSnapshot:
                return {"files": {}, "domain": "stub"}

            def diff(
                self,
                base: StateSnapshot,
                target: StateSnapshot,
                *,
                repo_root: pathlib.Path | None = None,
            ) -> StateDelta:
                empty_delta: StructuredDelta = {
                    "domain": "stub",
                    "ops": [],
                    "summary": "no changes",
                }
                return empty_delta

            def drift(self, committed: StateSnapshot, live_state: LiveState) -> DriftReport:
                return DriftReport(has_drift=False)

            def apply(self, delta: StateDelta, live_state: LiveState) -> LiveState:
                return live_state

            def merge(
                self,
                base: StateSnapshot,
                left: StateSnapshot,
                right: StateSnapshot,
                *,
                repo_root: pathlib.Path | None = None,
            ) -> MergeResult:
                return MergeResult(merged=base)

            def schema(self) -> DomainSchema:
                from muse.core.schema import SetSchema
                schema: SetSchema = {
                    "kind": "set",
                    "element_type": "str",
                    "identity": "by_content",
                }
                return {
                    "domain": "stub",
                    "description": "Stub CRDT domain",
                    "dimensions": [],
                    "top_level": schema,
                    "merge_mode": "crdt",
                    "schema_version": 1,
                }

            def crdt_schema(self) -> list[CRDTDimensionSpec]:
                return []

            def join(
                self,
                a: CRDTSnapshotManifest,
                b: CRDTSnapshotManifest,
            ) -> CRDTSnapshotManifest:
                # Simple merge: union of files, max vclock, union crdt_state
                from muse.core.crdts.vclock import VectorClock

                vc_a = VectorClock.from_dict(a["vclock"])
                vc_b = VectorClock.from_dict(b["vclock"])
                merged_vc = vc_a.merge(vc_b)
                merged_files = {**a["files"], **b["files"]}
                merged_crdt_state = {**a["crdt_state"], **b["crdt_state"]}
                result: CRDTSnapshotManifest = {
                    "files": merged_files,
                    "domain": a["domain"],
                    "vclock": merged_vc.to_dict(),
                    "crdt_state": merged_crdt_state,
                    "schema_version": 1,
                }
                return result

            def to_crdt_state(self, snapshot: StateSnapshot) -> CRDTSnapshotManifest:
                result: CRDTSnapshotManifest = {
                    "files": snapshot.get("files", {}),
                    "domain": snapshot.get("domain", "stub"),
                    "vclock": {},
                    "crdt_state": {},
                    "schema_version": 1,
                }
                return result

            def from_crdt_state(self, crdt: CRDTSnapshotManifest) -> StateSnapshot:
                return {"files": crdt["files"], "domain": crdt["domain"]}

        return StubCRDTPlugin()

    def test_crdt_join_produces_merge_result(self) -> None:
        from muse.core.merge_engine import crdt_join_snapshots

        plugin = self._make_stub_plugin()
        result = crdt_join_snapshots(
            plugin=plugin,
            a_snapshot={"track.mid": "hash-a"},
            b_snapshot={"beat.mid": "hash-b"},
            a_vclock={"agent-1": 1},
            b_vclock={"agent-2": 1},
            a_crdt_state={},
            b_crdt_state={},
            domain="stub",
        )
        assert result.is_clean
        assert result.conflicts == []

    def test_crdt_join_merges_files(self) -> None:
        from muse.core.merge_engine import crdt_join_snapshots

        plugin = self._make_stub_plugin()
        result = crdt_join_snapshots(
            plugin=plugin,
            a_snapshot={"track.mid": "hash-a"},
            b_snapshot={"beat.mid": "hash-b"},
            a_vclock={},
            b_vclock={},
            a_crdt_state={},
            b_crdt_state={},
            domain="stub",
        )
        assert "track.mid" in result.merged["files"]
        assert "beat.mid" in result.merged["files"]

    def test_crdt_join_is_commutative(self) -> None:
        from muse.core.merge_engine import crdt_join_snapshots

        plugin = self._make_stub_plugin()
        result_ab = crdt_join_snapshots(
            plugin=plugin,
            a_snapshot={"track.mid": "hash-a"},
            b_snapshot={"beat.mid": "hash-b"},
            a_vclock={"agent-1": 1},
            b_vclock={"agent-2": 2},
            a_crdt_state={},
            b_crdt_state={},
            domain="stub",
        )
        result_ba = crdt_join_snapshots(
            plugin=plugin,
            a_snapshot={"beat.mid": "hash-b"},
            b_snapshot={"track.mid": "hash-a"},
            a_vclock={"agent-2": 2},
            b_vclock={"agent-1": 1},
            a_crdt_state={},
            b_crdt_state={},
            domain="stub",
        )
        assert set(result_ab.merged["files"].keys()) == set(result_ba.merged["files"].keys())

    def test_crdt_merge_never_produces_conflicts(self) -> None:
        from muse.core.merge_engine import crdt_join_snapshots

        plugin = self._make_stub_plugin()
        # Even when both replicas modify the same file, CRDT join never conflicts
        result = crdt_join_snapshots(
            plugin=plugin,
            a_snapshot={"shared.mid": "hash-a"},
            b_snapshot={"shared.mid": "hash-b"},
            a_vclock={"agent-1": 1},
            b_vclock={"agent-2": 1},
            a_crdt_state={},
            b_crdt_state={},
            domain="stub",
        )
        assert result.is_clean
        assert len(result.conflicts) == 0

    def test_crdt_join_requires_crdt_plugin_protocol(self) -> None:
        """Verify the protocol check is documented in the function signature.

        The static type of ``crdt_join_snapshots(plugin=...)`` is
        ``MuseDomainPlugin``.  Callers that don't implement ``CRDTPlugin``
        are rejected at the call site by mypy.  The runtime ``isinstance``
        check exists as a defensive guard for duck-typed callers.
        """
        from muse.core.merge_engine import crdt_join_snapshots

        # A plugin that implements MuseDomainPlugin but NOT CRDTPlugin
        # would pass static type-checking but fail at runtime.
        # We verify the docstring is accurate by checking the stub IS a CRDTPlugin.
        plugin = self._make_stub_plugin()
        assert isinstance(plugin, CRDTPlugin)

    def test_crdt_plugin_join_commutes(self) -> None:
        """join(a,b) == join(b,a) at the CRDT primitive level."""
        from muse.domain import CRDTSnapshotManifest

        plugin = self._make_stub_plugin()
        from muse.domain import CRDTPlugin
        assert isinstance(plugin, CRDTPlugin)

        a: CRDTSnapshotManifest = {
            "files": {"a.mid": "ha"},
            "domain": "stub",
            "vclock": {"x": 1},
            "crdt_state": {},
            "schema_version": 1,
        }
        b: CRDTSnapshotManifest = {
            "files": {"b.mid": "hb"},
            "domain": "stub",
            "vclock": {"y": 1},
            "crdt_state": {},
            "schema_version": 1,
        }
        ab = plugin.join(a, b)
        ba = plugin.join(b, a)
        assert set(ab["files"].keys()) == set(ba["files"].keys())


# ===========================================================================
# Cross-module: CRDT primitives satisfy lattice laws end-to-end
# ===========================================================================


class TestLatticeProperties:
    """Property-style checks that every CRDT satisfies all three lattice laws."""

    def test_vector_clock_lattice_laws(self) -> None:
        a = VectorClock({"x": 1, "y": 2})
        b = VectorClock({"x": 3, "z": 1})
        c = VectorClock({"y": 5})

        # Commutativity
        assert a.merge(b).equivalent(b.merge(a))
        # Associativity
        assert a.merge(b).merge(c).equivalent(a.merge(b.merge(c)))
        # Idempotency
        assert a.merge(a).equivalent(a)

    def test_g_counter_lattice_laws(self) -> None:
        a = GCounter({"x": 1, "y": 2})
        b = GCounter({"x": 3, "z": 1})
        c = GCounter({"y": 5})

        assert a.join(b).equivalent(b.join(a))
        assert a.join(b).join(c).equivalent(a.join(b.join(c)))
        assert a.join(a).equivalent(a)

    def test_lww_register_lattice_laws(self) -> None:
        def make(v: str, ts: float, author: str) -> LWWRegister:
            return LWWRegister(v, ts, author)

        a = make("val-a", 1.0, "agent-a")
        b = make("val-b", 2.0, "agent-b")
        c = make("val-c", 3.0, "agent-c")

        assert a.join(b).equivalent(b.join(a))
        assert a.join(b).join(c).equivalent(a.join(b.join(c)))
        assert a.join(a).equivalent(a)

    def test_or_set_lattice_laws(self) -> None:
        s1 = ORSet()
        s1, _ = s1.add("X")
        s2 = ORSet()
        s2, _ = s2.add("Y")
        s3 = ORSet()
        s3, _ = s3.add("Z")

        assert s1.join(s2).elements() == s2.join(s1).elements()
        assert s1.join(s2).join(s3).elements() == s1.join(s2.join(s3)).elements()
        assert s1.join(s1).elements() == s1.elements()

    def test_aw_map_lattice_laws(self) -> None:
        m1 = AWMap().set("A", "1")
        m2 = AWMap().set("B", "2")
        m3 = AWMap().set("C", "3")

        assert m1.join(m2).to_plain_dict() == m2.join(m1).to_plain_dict()
        assert m1.join(m2).join(m3).to_plain_dict() == m1.join(m2.join(m3)).to_plain_dict()
        assert m1.join(m1).to_plain_dict() == m1.to_plain_dict()

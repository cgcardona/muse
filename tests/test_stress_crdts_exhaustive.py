"""Exhaustive CRDT stress tests — lattice laws, concurrent writes, adversarial inputs.

For each CRDT primitive, verifies:
  1. Commutativity:   join(a, b) == join(b, a)
  2. Associativity:   join(join(a, b), c) == join(a, join(b, c))
  3. Idempotency:     join(a, a) == a
  4. Monotonicity:    join never loses information (|join(a,b)| >= |a|)
  5. Serialisation:   from_dict(to_dict(x)) == x (round-trip)

Additional adversarial cases:
  - 100-agent GCounter: concurrent increments from all agents.
  - VectorClock causal ordering under partition / reconnect.
  - ORSet: add → remove semantics, concurrent add wins over remove.
  - RGA: concurrent inserts from N agents produce a deterministic total order.
  - AWMap: nested concurrent updates converge.
  - LWWRegister: last writer wins with tie-broken by agent_id.
"""
from __future__ import annotations

import datetime
import itertools

import pytest

from muse.core.crdts import AWMap, GCounter, LWWRegister, ORSet, RGA, VectorClock
from muse.core.crdts.lww_register import LWWValue
from muse.core.crdts.or_set import ORSetDict
from muse.core.crdts.rga import RGAElement


# ===========================================================================
# VectorClock — exhaustive
# ===========================================================================


class TestVectorClockExhaustive:
    # --- lattice laws ---

    def _clock(self, **kw: int) -> VectorClock:
        return VectorClock(kw)

    def test_commutativity_many_agents(self) -> None:
        for n in range(1, 11):
            a = VectorClock({f"agent-{i}": i for i in range(n)})
            b = VectorClock({f"agent-{i}": n - i for i in range(n)})
            assert a.merge(b).equivalent(b.merge(a))

    def test_associativity_three_replicas(self) -> None:
        a = self._clock(**{"a": 3, "b": 1})
        b = self._clock(**{"a": 1, "b": 5, "c": 2})
        c = self._clock(**{"b": 3, "c": 4, "d": 1})
        left = a.merge(b).merge(c)
        right = a.merge(b.merge(c))
        assert left.equivalent(right)

    def test_idempotency(self) -> None:
        for n in [1, 5, 20]:
            clk = VectorClock({f"a{i}": i * 3 for i in range(n)})
            assert clk.merge(clk).equivalent(clk)

    def test_merge_is_monotone(self) -> None:
        a = VectorClock({"x": 1, "y": 2})
        b = VectorClock({"x": 3, "z": 1})
        merged = a.merge(b)
        # merged must dominate both a and b (or be equal to them in each slot).
        for agent in ["x", "y", "z"]:
            assert merged.to_dict().get(agent, 0) >= a.to_dict().get(agent, 0)
            assert merged.to_dict().get(agent, 0) >= b.to_dict().get(agent, 0)

    # --- causal ordering ---

    def test_happens_before_empty_clocks(self) -> None:
        empty = VectorClock()
        assert not empty.happens_before(empty)

    def test_happens_before_empty_before_non_empty(self) -> None:
        empty = VectorClock()
        non_empty = VectorClock({"a": 1})
        assert empty.happens_before(non_empty)
        assert not non_empty.happens_before(empty)

    def test_transitivity_of_causal_order(self) -> None:
        a = VectorClock({"a": 1})
        b = VectorClock({"a": 2, "b": 1})
        c = VectorClock({"a": 3, "b": 2, "c": 1})
        assert a.happens_before(b)
        assert b.happens_before(c)
        assert a.happens_before(c)

    def test_concurrent_detection_both_ways(self) -> None:
        x = VectorClock({"agent-X": 5, "agent-Y": 1})
        y = VectorClock({"agent-X": 1, "agent-Y": 5})
        assert x.concurrent_with(y)
        assert y.concurrent_with(x)

    def test_not_concurrent_with_itself(self) -> None:
        clk = VectorClock({"a": 3, "b": 2})
        assert not clk.concurrent_with(clk)

    def test_not_concurrent_with_ancestor(self) -> None:
        ancestor = VectorClock({"a": 1})
        descendant = VectorClock({"a": 2})
        assert not ancestor.concurrent_with(descendant)

    def test_partition_and_reconnect_converges(self) -> None:
        """Simulate network partition: A and B increment independently, then merge."""
        shared = VectorClock({"base": 5})
        # Partition: A and B both do 10 events independently.
        a_partition = shared
        b_partition = shared
        for i in range(10):
            a_partition = a_partition.increment("agent-A")
            b_partition = b_partition.increment("agent-B")
        # Reconnect.
        merged = a_partition.merge(b_partition)
        assert merged.to_dict()["base"] == 5
        assert merged.to_dict()["agent-A"] == 10
        assert merged.to_dict()["agent-B"] == 10

    def test_round_trip(self) -> None:
        clk = VectorClock({"x": 7, "y": 3, "z": 0})
        restored = VectorClock.from_dict(clk.to_dict())
        assert restored.equivalent(clk)


# ===========================================================================
# GCounter — exhaustive
# ===========================================================================


class TestGCounterExhaustive:
    def test_commutativity(self) -> None:
        a = GCounter({"a1": 5, "a2": 3})
        b = GCounter({"a1": 2, "a3": 7})
        assert a.join(b).equivalent(b.join(a))

    def test_associativity(self) -> None:
        a = GCounter({"x": 1})
        b = GCounter({"y": 2})
        c = GCounter({"z": 3, "x": 5})
        left = a.join(b).join(c)
        right = a.join(b.join(c))
        assert left.equivalent(right)

    def test_idempotency(self) -> None:
        g = GCounter({"a": 5, "b": 3, "c": 1})
        assert g.join(g).equivalent(g)

    def test_100_agents_concurrent_increment(self) -> None:
        """100 agents each increment once concurrently; merged total must equal 100."""
        counters = [GCounter().increment(f"agent-{i}") for i in range(100)]
        merged = counters[0]
        for c in counters[1:]:
            merged = merged.join(c)
        assert merged.value() == 100

    def test_increment_by_multiple(self) -> None:
        g = GCounter().increment("agent", by=10)
        assert g.value_for("agent") == 10
        g2 = g.increment("agent", by=5)
        assert g2.value_for("agent") == 15

    def test_join_takes_max_per_slot(self) -> None:
        a = GCounter({"x": 3, "y": 1})
        b = GCounter({"x": 1, "y": 5})
        merged = a.join(b)
        assert merged.value_for("x") == 3
        assert merged.value_for("y") == 5

    def test_monotone_join(self) -> None:
        a = GCounter({"a": 10})
        b = GCounter({"a": 20})
        merged = a.join(b)
        assert merged.value() >= a.value()
        assert merged.value() >= b.value()

    def test_round_trip(self) -> None:
        g = GCounter({"x": 7, "y": 3})
        restored = GCounter.from_dict(g.to_dict())
        assert restored.equivalent(g)

    def test_empty_counter_join(self) -> None:
        empty = GCounter()
        g = GCounter({"a": 5})
        assert empty.join(g).equivalent(g)
        assert g.join(empty).equivalent(g)

    def test_value_is_sum_of_slots(self) -> None:
        g = GCounter({"a": 3, "b": 4, "c": 5})
        assert g.value() == 12

    def test_stress_many_increments_single_agent(self) -> None:
        g = GCounter()
        for i in range(1000):
            g = g.increment("solo")
        assert g.value() == 1000


# ===========================================================================
# ORSet — exhaustive
# ===========================================================================


class TestORSetExhaustive:
    def test_commutativity(self) -> None:
        s1 = ORSet()
        s1, t1 = s1.add("apple")
        s2 = ORSet()
        s2, t2 = s2.add("banana")
        assert s1.join(s2).elements() == s2.join(s1).elements()

    def test_associativity(self) -> None:
        s1, _ = ORSet().add("a")
        s2, _ = ORSet().add("b")
        s3, _ = ORSet().add("c")
        left = s1.join(s2).join(s3)
        right = s1.join(s2.join(s3))
        assert left.elements() == right.elements()

    def test_idempotency(self) -> None:
        s, _ = ORSet().add("x")
        s2, _ = s.add("y")
        assert s2.join(s2).elements() == s2.elements()

    def test_add_then_remove(self) -> None:
        s, tok = ORSet().add("element")
        s2 = s.remove("element", tok)
        assert "element" not in s2.elements()

    def test_concurrent_add_wins_over_remove(self) -> None:
        """ORSet semantics: concurrent add always beats remove."""
        # Replica A: add "item" with token t1.
        a, t1 = ORSet().add("item")
        # Replica B: independently add "item" with token t2 and then remove t1.
        b, t2 = ORSet().add("item")
        b_removed = b.remove("item", t1)
        # After join, "item" survives because t2 is still alive in b's replica.
        merged = a.join(b_removed)
        assert "item" in merged.elements()

    def test_remove_nonexistent_is_noop(self) -> None:
        s = ORSet()
        # Removing something that was never added should not crash.
        s2 = s.remove("ghost", "fake-token")
        assert "ghost" not in s2.elements()

    def test_100_elements_all_present(self) -> None:
        s = ORSet()
        for i in range(100):
            s, _ = s.add(f"item-{i}")
        elems = s.elements()
        for i in range(100):
            assert f"item-{i}" in elems

    def test_round_trip_serialisation(self) -> None:
        s = ORSet()
        tokens: list[str] = []
        for label in ["alpha", "beta", "gamma"]:
            s, tok = s.add(label)
            tokens.append(tok)
        d: ORSetDict = s.to_dict()
        restored = ORSet.from_dict(d)
        assert restored.elements() == s.elements()

    def test_join_preserves_all_adds(self) -> None:
        s1, _ = ORSet().add("from-s1")
        s2, _ = ORSet().add("from-s2")
        merged = s1.join(s2)
        assert "from-s1" in merged.elements()
        assert "from-s2" in merged.elements()

    def test_many_add_remove_cycles(self) -> None:
        """Rapidly add and remove the same element; final state must be empty."""
        s = ORSet()
        tok = ""
        for _ in range(10):
            s, tok = s.add("target")
            s = s.remove("target", tok)
        assert "target" not in s.elements()


# ===========================================================================
# LWWRegister — exhaustive
# ===========================================================================


class TestLWWRegisterExhaustive:
    def test_later_timestamp_wins(self) -> None:
        r1 = LWWRegister("old", 1.0, "agent-A")
        r2 = LWWRegister("new", 2.0, "agent-A")
        merged = r1.join(r2)
        assert merged.read() == "new"

    def test_earlier_timestamp_does_not_overwrite(self) -> None:
        current = LWWRegister("current", 5.0, "agent-A")
        stale = LWWRegister("stale", 3.0, "agent-A")
        merged = current.join(stale)
        assert merged.read() == "current"

    def test_concurrent_write_tie_broken_by_agent_id(self) -> None:
        """When timestamps are equal, lexicographically larger author wins."""
        ts = 10.0
        r1 = LWWRegister("value-Z", ts, "agent-Z")
        r2 = LWWRegister("value-A", ts, "agent-A")
        merged = r1.join(r2)
        assert merged.read() == "value-Z"  # "agent-Z" > "agent-A"

    def test_join_commutativity(self) -> None:
        ts = 1.0
        r1 = LWWRegister("alpha", ts, "agent-A")
        r2 = LWWRegister("beta", ts, "agent-B")
        m1 = r1.join(r2)
        m2 = r2.join(r1)
        assert m1.read() == m2.read()

    def test_idempotency(self) -> None:
        r = LWWRegister("val", 1.0, "agent-A")
        assert r.join(r).read() == r.read()

    def test_write_method_updates_register(self) -> None:
        r = LWWRegister("old", 1.0, "agent-A")
        r2 = r.write("new", 2.0, "agent-A")
        assert r2.read() == "new"

    def test_round_trip(self) -> None:
        r = LWWRegister("test-value", 42.0, "agent-X")
        d = r.to_dict()
        restored = LWWRegister.from_dict(d)
        assert restored.read() == "test-value"

    def test_100_concurrent_agents_settle_deterministically(self) -> None:
        """100 agents all write at the same timestamp; result must be deterministic."""
        ts = 0.0
        registers = [LWWRegister(f"val-{i:03d}", ts, f"agent-{i:03d}") for i in range(100)]
        merged = registers[0]
        for r in registers[1:]:
            merged = merged.join(r)
        winner = merged.read()
        assert winner is not None
        # Shuffle and merge again — result must be identical.
        import random
        shuffled = list(registers)
        random.shuffle(shuffled)
        merged2 = shuffled[0]
        for r in shuffled[1:]:
            merged2 = merged2.join(r)
        assert merged2.read() == winner

    def test_higher_timestamp_beats_larger_author(self) -> None:
        """Even if agent-A would lose tiebreaker, newer timestamp wins."""
        r_early_z = LWWRegister("early-z", 1.0, "agent-Z")
        r_late_a = LWWRegister("late-a", 2.0, "agent-A")
        assert r_early_z.join(r_late_a).read() == "late-a"


# ===========================================================================
# RGA — exhaustive
# ===========================================================================


class TestRGAExhaustive:
    def _eid(self, label: str) -> str:
        """Generate a deterministic element_id."""
        return f"1.0@{label}"

    def test_single_insert(self) -> None:
        rga = RGA()
        rga2 = rga.insert(None, "hello", element_id="1.0@agent-A")
        assert rga2.to_sequence() == ["hello"]

    def test_insert_order_preserved(self) -> None:
        rga = RGA()
        rga = rga.insert(None, "a", element_id="1.0@a")
        rga = rga.insert("1.0@a", "b", element_id="2.0@b")
        rga = rga.insert("2.0@b", "c", element_id="3.0@c")
        assert rga.to_sequence() == ["a", "b", "c"]

    def test_delete_removes_element(self) -> None:
        rga = RGA()
        rga = rga.insert(None, "x", element_id="1.0@a")
        rga = rga.delete("1.0@a")
        assert "x" not in rga.to_sequence()

    def test_commutativity(self) -> None:
        rga1 = RGA()
        rga2 = RGA()
        rga1 = rga1.insert(None, "A", element_id="1.0@agent-1")
        rga2 = rga2.insert(None, "B", element_id="1.0@agent-2")
        m1 = rga1.join(rga2)
        m2 = rga2.join(rga1)
        assert m1.to_sequence() == m2.to_sequence()

    def test_idempotency(self) -> None:
        rga = RGA()
        rga = rga.insert(None, "elem", element_id="1.0@agent")
        assert rga.join(rga).to_sequence() == rga.to_sequence()

    def test_concurrent_inserts_deterministic_ordering(self) -> None:
        """5 agents insert concurrently; joined result is always the same total order."""
        agents = [f"agent-{i}" for i in range(5)]
        replicas: list[RGA] = []
        for a in agents:
            r = RGA()
            r = r.insert(None, a, element_id=f"1.0@{a}")
            replicas.append(r)

        merged = replicas[0]
        for r in replicas[1:]:
            merged = merged.join(r)

        result = merged.to_sequence()
        assert sorted(result) == sorted(agents)

        # Merge in reverse order — must give the same deterministic ordering.
        merged2 = replicas[-1]
        for r in reversed(replicas[:-1]):
            merged2 = merged2.join(r)
        assert merged2.to_sequence() == result

    def test_tombstone_not_revived_after_join(self) -> None:
        rga1 = RGA()
        rga1 = rga1.insert(None, "deleted", element_id="1.0@a")
        rga1 = rga1.delete("1.0@a")

        rga2 = RGA()
        rga2 = rga2.insert(None, "other", element_id="1.0@b")

        merged = rga1.join(rga2)
        assert "deleted" not in merged.to_sequence()
        assert "other" in merged.to_sequence()

    def test_large_sequence_sequential_insert(self) -> None:
        rga = RGA()
        prev_id: str | None = None
        for i in range(50):
            eid = f"{i}.0@agent"
            rga = rga.insert(prev_id, str(i), element_id=eid)
            prev_id = eid
        seq = rga.to_sequence()
        assert len(seq) == 50
        assert seq[0] == "0"
        assert seq[-1] == "49"


# ===========================================================================
# AWMap — exhaustive
# ===========================================================================


class TestAWMapExhaustive:
    def test_set_and_get(self) -> None:
        m = AWMap()
        m = m.set("key", "value")
        assert m.get("key") == "value"

    def test_remove_entry(self) -> None:
        m = AWMap()
        m = m.set("key", "value")
        m = m.remove("key")
        assert m.get("key") is None

    def test_concurrent_set_same_key_converges(self) -> None:
        """Concurrent writes to the same key; join must be deterministic."""
        m1 = AWMap().set("k", "from-A")
        m2 = AWMap().set("k", "from-Z")
        merged_ab = m1.join(m2)
        merged_ba = m2.join(m1)
        # Both orderings must produce the same result (commutativity).
        assert merged_ab.get("k") == merged_ba.get("k")
        assert merged_ab.get("k") is not None

    def test_commutativity(self) -> None:
        m1 = AWMap().set("x", "1")
        m2 = AWMap().set("y", "2")
        merged_ab = m1.join(m2)
        merged_ba = m2.join(m1)
        assert merged_ab.get("x") == merged_ba.get("x")
        assert merged_ab.get("y") == merged_ba.get("y")

    def test_idempotency(self) -> None:
        m = AWMap().set("a", "alpha")
        assert m.join(m).get("a") == m.get("a")

    def test_add_wins_over_remove_from_empty(self) -> None:
        """Add-wins: if one replica has the key and the other doesn't, add wins."""
        m1 = AWMap().set("key", "added")
        m2 = AWMap().remove("key")  # remove on empty = no-op
        merged = m1.join(m2)
        # m2 never had the key, so no token to tombstone → add from m1 wins.
        assert merged.get("key") == "added"

    def test_multiple_keys_independent(self) -> None:
        m = AWMap()
        for i in range(20):
            m = m.set(f"key-{i}", f"val-{i}")
        for i in range(20):
            assert m.get(f"key-{i}") == f"val-{i}"

    def test_remove_absent_key_is_noop(self) -> None:
        m = AWMap().remove("ghost")
        assert m.get("ghost") is None

    def test_update_replaces_value(self) -> None:
        m = AWMap().set("k", "v1").set("k", "v2")
        assert m.get("k") == "v2"


# ===========================================================================
# Cross-CRDT: vector-clock-guided merge
# ===========================================================================


class TestCrossTypeConsistency:
    def test_gcounter_and_vclock_agree_on_agent_counts(self) -> None:
        """GCounter and VectorClock track the same 'per-agent count' invariant."""
        gc = GCounter()
        vc = VectorClock()
        agents = ["agent-A", "agent-B", "agent-C"]
        for agent in agents:
            for _ in range(3):
                gc = gc.increment(agent)
                vc = vc.increment(agent)
        for agent in agents:
            assert gc.value_for(agent) == vc.to_dict()[agent]

    def test_lattice_join_never_reduces_information(self) -> None:
        """join(a, b) must always contain at least as much information as a."""
        for _ in range(20):
            import random
            slots = {f"a{i}": random.randint(0, 10) for i in range(5)}
            a = GCounter(slots)
            extra = {f"a{i}": random.randint(0, 5) for i in range(5)}
            b = GCounter(extra)
            merged = a.join(b)
            for agent in slots:
                assert merged.value_for(agent) >= a.value_for(agent)

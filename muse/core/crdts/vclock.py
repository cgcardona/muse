"""Vector clock for causal ordering in distributed multi-agent writes.

A vector clock (Lamport, 1978 / Fidge, 1988) tracks how many events each
agent has observed.  Two clocks can be compared to determine whether one
*causally precedes* the other or whether they are *concurrent* (neither
dominates).

This is the foundational primitive for all CRDT coordination in Muse:

- :class:`LWWRegister` uses vector clock comparison to break same-timestamp
  ties deterministically.
- :class:`RGA` uses ``agent_id`` for deterministic concurrent-insert ordering.
- The ``CRDTPlugin.join()`` protocol uses causal ordering to detect which
  writes truly conflict vs. which are simply out-of-delivery-order.

Public API
----------
- :class:`VClockDict` — ``TypedDict`` wire format ``{agent_id: count}``.
- :class:`VectorClock` — the clock itself, with ``increment``, ``merge``,
  ``happens_before``, ``concurrent_with``, ``to_dict``, ``from_dict``.
"""

from typing import TypedDict


class VClockDict(TypedDict, total=False):
    """Wire format for a vector clock — ``{agent_id: event_count}``.

    ``total=False`` because the presence of a key is meaningful (an absent key
    is equivalent to the value ``0``).  Serialise with :meth:`VectorClock.to_dict`
    and deserialise with :meth:`VectorClock.from_dict`.
    """

class VectorClock:
    """Causal clock for distributed agent writes.

    Stores a mapping from agent identifiers (arbitrary strings) to the number
    of events that agent has performed.  An absent agent is equivalent to
    count ``0``.

    Instances are **immutable** from the outside: every mutating method returns
    a new :class:`VectorClock` rather than modifying ``self``.  This makes
    clocks safe to store as dict values without defensive copying.

    Lattice laws satisfied by :meth:`merge`:
    - **Commutativity**: ``merge(a, b) == merge(b, a)``
    - **Associativity**: ``merge(merge(a, b), c) == merge(a, merge(b, c))``
    - **Idempotency**: ``merge(a, a) == a``
    """

    def __init__(self, counts: dict[str, int] | None = None) -> None:
        """Create a vector clock, optionally pre-populated from *counts*.

        Args:
            counts: Initial ``{agent_id: count}`` mapping.  Copied defensively.
        """
        self._counts: dict[str, int] = dict(counts) if counts else {}

    # ------------------------------------------------------------------
    # Mutation (returns new clock)
    # ------------------------------------------------------------------

    def increment(self, agent_id: str) -> VectorClock:
        """Return a new clock with ``agent_id``'s counter incremented by 1.

        Args:
            agent_id: The agent performing an event.

        Returns:
            A new :class:`VectorClock` with the updated count.
        """
        new_counts = dict(self._counts)
        new_counts[agent_id] = new_counts.get(agent_id, 0) + 1
        return VectorClock(new_counts)

    def merge(self, other: VectorClock) -> VectorClock:
        """Return the least-upper-bound of ``self`` and *other*.

        For each agent, the result holds the *maximum* count seen in either
        clock.  This is the lattice join operation; it satisfies
        commutativity, associativity, and idempotency.

        Args:
            other: The clock to merge with.

        Returns:
            A new :class:`VectorClock` holding per-agent maximums.
        """
        all_agents = set(self._counts) | set(other._counts)
        merged = {
            agent: max(self._counts.get(agent, 0), other._counts.get(agent, 0))
            for agent in all_agents
        }
        return VectorClock(merged)

    # ------------------------------------------------------------------
    # Comparison
    # ------------------------------------------------------------------

    def happens_before(self, other: VectorClock) -> bool:
        """Return ``True`` if ``self`` causally precedes *other*.

        ``a`` happens before ``b`` iff every agent counter in ``a`` is
        ≤ the corresponding counter in ``b``, and at least one counter is
        strictly less (i.e. ``a != b``).

        Args:
            other: The clock to compare against.

        Returns:
            ``True`` when ``self < other`` in causal order.
        """
        all_agents = set(self._counts) | set(other._counts)
        leq = all(
            self._counts.get(agent, 0) <= other._counts.get(agent, 0)
            for agent in all_agents
        )
        return leq and not self.equivalent(other)

    def concurrent_with(self, other: VectorClock) -> bool:
        """Return ``True`` if neither clock causally precedes the other.

        Two clocks are concurrent when each has at least one counter strictly
        greater than the other's corresponding counter.  This is the condition
        that a CRDT ``join`` must handle: there is no causal order between the
        two writes, so neither can be simply discarded.

        Args:
            other: The clock to compare against.

        Returns:
            ``True`` when ``self`` and *other* are incomparable.
        """
        return not self.happens_before(other) and not other.happens_before(self) and not self.equivalent(other)

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, int]:
        """Return a JSON-serialisable ``{agent_id: count}`` mapping.

        Returns:
            A shallow copy of the internal counts dictionary.
        """
        return dict(self._counts)

    @classmethod
    def from_dict(cls, data: dict[str, int]) -> VectorClock:
        """Reconstruct a :class:`VectorClock` from its wire representation.

        Args:
            data: ``{agent_id: count}`` mapping as produced by :meth:`to_dict`.

        Returns:
            A new :class:`VectorClock` with the given counts.
        """
        return cls(data)

    # ------------------------------------------------------------------
    # Python dunder helpers
    # ------------------------------------------------------------------

    def equivalent(self, other: VectorClock) -> bool:
        """Return ``True`` if both clocks represent identical causal state.

        Two clocks are equivalent when every agent's count is the same in both,
        treating absent agents as count 0.  This is a stricter check than
        ``happens_before`` — it requires exact equality, not domination.

        Args:
            other: The vector clock to compare against.

        Returns:
            ``True`` when ``self`` and *other* are causally identical.
        """
        all_agents = set(self._counts) | set(other._counts)
        return all(
            self._counts.get(a, 0) == other._counts.get(a, 0)
            for a in all_agents
        )

    def __repr__(self) -> str:
        return f"VectorClock({self._counts!r})"

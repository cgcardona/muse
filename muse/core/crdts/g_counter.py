"""Grow-only Counter (G-Counter) — CRDT for monotonically increasing counts.

A G-Counter assigns one slot per agent.  Each agent may only increment its own
slot.  The global value is the sum of all slots.  ``join`` takes the per-slot
maximum (matching the Vector Clock pattern), which is commutative, associative,
and idempotent.

Use cases in Muse:
- Commit-count metrics per agent (how many commits has each agent contributed?).
- Replay counters (how many times has this element been touched?).
- Any monotonically increasing quantity across a distributed agent fleet.

**Lattice laws satisfied by** :meth:`join`:
1. Commutativity: ``join(a, b) == join(b, a)``
2. Associativity: ``join(join(a, b), c) == join(a, join(b, c))``
3. Idempotency: ``join(a, a) == a``

Proof: ``join`` is ``max`` per slot and ``value`` is ``sum`` — both operations
on the non-negative integer lattice are trivially lattice-correct.

Public API
----------
- :class:`GCounterDict` — ``TypedDict`` wire format ``{agent_id: count}``.
- :class:`GCounter` — the counter itself.
"""

import logging
from typing import TypedDict

logger = logging.getLogger(__name__)


class GCounterDict(TypedDict, total=False):
    """Wire format for a :class:`GCounter` — ``{agent_id: count}``.

    ``total=False`` because absent keys are equivalent to ``0``.  Serialise
    with :meth:`GCounter.to_dict` and deserialise with :meth:`GCounter.from_dict`.
    """

class GCounter:
    """Grow-only Counter — a CRDT counter that only ever increases.

    Each agent increments its own private slot; the global value is the sum of
    all slots.  Only the owning agent may increment a slot (this is enforced by
    convention — not cryptographically).

    All mutating methods return new :class:`GCounter` instances; ``self`` is
    never modified.

    Example::

        c1 = GCounter().increment("agent-1")
        c2 = GCounter().increment("agent-2").increment("agent-2")
        merged = c1.join(c2)
        assert merged.value() == 3   # 1 from agent-1 + 2 from agent-2
        assert merged.join(c1).value() == 3  # idempotent
    """

    def __init__(self, counts: dict[str, int] | None = None) -> None:
        """Construct a G-Counter, optionally pre-populated.

        Args:
            counts: Initial ``{agent_id: count}`` mapping.  Copied defensively.
                    All values must be non-negative integers.
        """
        self._counts: dict[str, int] = dict(counts) if counts else {}

    # ------------------------------------------------------------------
    # Mutation (returns new GCounter)
    # ------------------------------------------------------------------

    def increment(self, agent_id: str, by: int = 1) -> GCounter:
        """Return a new counter with *agent_id*'s slot incremented by *by*.

        Args:
            agent_id: The agent performing the increment (must be the caller's
                      own agent ID to maintain CRDT invariants).
            by:       Amount to increment; must be a positive integer.

        Returns:
            A new :class:`GCounter` with the updated slot.

        Raises:
            ValueError: If *by* is not a positive integer.
        """
        if by <= 0:
            raise ValueError(f"GCounter.increment: 'by' must be positive, got {by}")
        new_counts = dict(self._counts)
        new_counts[agent_id] = new_counts.get(agent_id, 0) + by
        return GCounter(new_counts)

    # ------------------------------------------------------------------
    # CRDT join
    # ------------------------------------------------------------------

    def join(self, other: GCounter) -> GCounter:
        """Return the lattice join — per-slot maximum of ``self`` and *other*.

        Args:
            other: The counter to merge with.

        Returns:
            A new :class:`GCounter` holding the per-agent maximum counts.
        """
        all_agents = set(self._counts) | set(other._counts)
        merged = {
            agent: max(self._counts.get(agent, 0), other._counts.get(agent, 0))
            for agent in all_agents
        }
        return GCounter(merged)

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def value(self) -> int:
        """Return the global counter value — the sum of all agent slots.

        Returns:
            Non-negative integer.
        """
        return sum(self._counts.values())

    def value_for(self, agent_id: str) -> int:
        """Return the count for a specific agent.

        Args:
            agent_id: The agent to query.

        Returns:
            The agent's slot value, or ``0`` if the agent has not incremented.
        """
        return self._counts.get(agent_id, 0)

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
    def from_dict(cls, data: dict[str, int]) -> GCounter:
        """Reconstruct a :class:`GCounter` from its wire representation.

        Args:
            data: ``{agent_id: count}`` mapping as produced by :meth:`to_dict`.

        Returns:
            A new :class:`GCounter`.
        """
        return cls(data)

    # ------------------------------------------------------------------
    # Python dunder helpers
    # ------------------------------------------------------------------

    def equivalent(self, other: GCounter) -> bool:
        """Return ``True`` if both counters hold identical per-agent counts.

        Args:
            other: The G-Counter to compare against.

        Returns:
            ``True`` when every agent slot has the same value in both counters
            (treating absent agents as count 0).
        """
        all_agents = set(self._counts) | set(other._counts)
        return all(
            self._counts.get(a, 0) == other._counts.get(a, 0)
            for a in all_agents
        )

    def __repr__(self) -> str:
        return f"GCounter(value={self.value()}, slots={self._counts!r})"

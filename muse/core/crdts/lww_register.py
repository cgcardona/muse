"""Last-Write-Wins Register — a CRDT for single scalar values.

A LWW-Register stores one value tagged with a timestamp and an author ID.
On ``join``, the value with the *higher* timestamp wins.  When two writes
carry the same timestamp, the author with the lexicographically greater ID
wins (tiebreaker — deterministic and bias-free).

Use cases in Muse:
- Scalar metadata fields (tempo, key signature, time signature).
- Plugin configuration values that change infrequently.
- Any dimension where "whoever wrote last" is the correct merge policy.

**Correctness guarantee**: ``join`` satisfies the three CRDT lattice laws:

1. **Commutativity**: ``join(a, b) == join(b, a)``
2. **Associativity**: ``join(join(a, b), c) == join(a, join(b, c))``
3. **Idempotency**: ``join(a, a) == a``

Proof sketch: ``join`` computes ``argmax`` on ``(timestamp, author)`` — a
total order — which is trivially commutative, associative, and idempotent.

Public API
----------
- :class:`LWWValue` — ``TypedDict`` wire format.
- :class:`LWWRegister` — the register itself.
"""


from __future__ import annotations

import logging
from typing import TypedDict

logger = logging.getLogger(__name__)


class LWWValue(TypedDict):
    """Wire format for a :class:`LWWRegister`.

    ``value`` is the stored payload (a JSON-serialisable string).
    ``timestamp`` is a monotonically increasing float (Unix seconds or logical
    clock value).  ``author`` is the agent ID used as a lexicographic
    tiebreaker when two writes carry equal timestamps.
    """

    value: str
    timestamp: float
    author: str


class LWWRegister:
    """A register where the last write (by timestamp) always wins on merge.

    Instances are **immutable** from the outside: :meth:`write` and
    :meth:`join` both return new registers.

    The ``author`` field is used solely as a deterministic tiebreaker for
    equal timestamps; it confers no editorial priority.

    Example::

        a = LWWRegister.from_dict({"value": "C major", "timestamp": 1.0, "author": "agent-1"})
        b = LWWRegister.from_dict({"value": "G major", "timestamp": 2.0, "author": "agent-2"})
        assert a.join(b).read() == "G major"   # higher timestamp wins
        assert b.join(a).read() == "G major"   # commutative
    """

    def __init__(self, value: str, timestamp: float, author: str) -> None:
        """Construct a register directly.

        Args:
            value:     The stored payload string.
            timestamp: Write time (Unix seconds or logical clock).
            author:    Agent ID that performed the write.
        """
        self._value = value
        self._timestamp = timestamp
        self._author = author

    # ------------------------------------------------------------------
    # Read / write
    # ------------------------------------------------------------------

    def read(self) -> str:
        """Return the current stored value.

        Returns:
            The payload string of the winning write.
        """
        return self._value

    def write(self, value: str, timestamp: float, author: str) -> LWWRegister:
        """Return a new register with the given write applied.

        The returned register holds *value* if *timestamp* is strictly greater
        than the current timestamp, or equal with a greater author ID.
        Otherwise ``self`` is returned unchanged.

        Args:
            value:     New payload string.
            timestamp: Write time of the new value.
            author:    Agent performing the write.

        Returns:
            A :class:`LWWRegister` holding whichever value wins.
        """
        candidate = LWWRegister(value, timestamp, author)
        return self.join(candidate)

    # ------------------------------------------------------------------
    # CRDT join
    # ------------------------------------------------------------------

    def join(self, other: LWWRegister) -> LWWRegister:
        """Return the lattice join — the value with the higher timestamp.

        Tiebreaks on equal timestamps by taking the lexicographically greater
        ``author`` string.  When both ``timestamp`` and ``author`` are equal
        (rare in practice but possible in tests), the value string itself is
        used as the final tiebreaker, ensuring commutativity is preserved even
        in this degenerate case.

        Args:
            other: The register to merge with.

        Returns:
            A new :class:`LWWRegister` holding the winning value.
        """
        # Include value as the final tiebreaker so that join is commutative even
        # when two writes carry identical (timestamp, author) metadata.
        self_key = (self._timestamp, self._author, self._value)
        other_key = (other._timestamp, other._author, other._value)
        if other_key > self_key:
            return LWWRegister(other._value, other._timestamp, other._author)
        return LWWRegister(self._value, self._timestamp, self._author)

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> LWWValue:
        """Return a JSON-serialisable ``LWWValue`` dict.

        Returns:
            ``{"value": ..., "timestamp": ..., "author": ...}``
        """
        return {"value": self._value, "timestamp": self._timestamp, "author": self._author}

    @classmethod
    def from_dict(cls, data: LWWValue) -> LWWRegister:
        """Reconstruct a :class:`LWWRegister` from its wire representation.

        Args:
            data: Dict as produced by :meth:`to_dict`.

        Returns:
            A new :class:`LWWRegister`.
        """
        return cls(data["value"], data["timestamp"], data["author"])

    # ------------------------------------------------------------------
    # Python dunder helpers
    # ------------------------------------------------------------------

    def equivalent(self, other: LWWRegister) -> bool:
        """Return ``True`` if both registers hold identical state.

        Args:
            other: The register to compare against.

        Returns:
            ``True`` when value, timestamp, and author are all equal.
        """
        return (
            self._value == other._value
            and self._timestamp == other._timestamp
            and self._author == other._author
        )

    def __repr__(self) -> str:
        return (
            f"LWWRegister(value={self._value!r}, "
            f"timestamp={self._timestamp}, author={self._author!r})"
        )

"""Observed-Remove Set (OR-Set) — add-wins CRDT for unordered collections.

An OR-Set is an unordered set where *adds always win over concurrent removes*.
Each element is tagged with a unique token set when it is added.  Removing an
element requires specifying the observed tokens; a concurrent add with a new
token survives the remove.

This gives the "add-wins" property: if agent A adds element X and agent B
concurrently removes X, the merged result still contains X.  The rationale
for Muse: an annotation or a note added by one agent should not be silently
deleted by a concurrent tombstone from another.

**Algorithm (Shapiro et al., 2011 "A Comprehensive Study of CRDTs"):**

- Each element ``e`` maps to a set of *unique tokens* ``{t₁, t₂, …}``.
- ``add(e)`` generates a fresh token and inserts ``(e, token)`` into the
  payload.
- ``remove(e)`` removes every ``(e, token)`` pair currently observed.
  A concurrent ``add(e)`` with a new token survives because its token was not
  yet in the "observed" set at remove time.
- ``join(a, b)`` is set-union on the ``(element, token)`` pairs, then removes
  any pair whose token appears in either replica's *tombstone* set (for
  optimisation — see :attr:`_tombstones`).

We simplify slightly: tombstones are not compacted in this implementation
(correct but not space-optimal).  Compaction is a GC concern, not a
correctness concern.

**Lattice laws satisfied by** :meth:`join`:
1. Commutativity: ``join(a, b) == join(b, a)``
2. Associativity: ``join(join(a, b), c) == join(a, join(b, c))``
3. Idempotency: ``join(a, a) == a``

Public API
----------
- :class:`ORSetEntry` — ``TypedDict`` for a single (element, token) pair.
- :class:`ORSetDict` — ``TypedDict`` wire format for a complete OR-Set.
- :class:`ORSet` — the set itself.
"""


import logging
import uuid
from typing import TypedDict

logger = logging.getLogger(__name__)


class ORSetEntry(TypedDict):
    """A single (element, token) pair in the OR-Set payload.

    ``element`` is the string value being tracked (e.g. a content hash or a
    label).  ``token`` is the unique identifier created when this particular
    *addition* of ``element`` occurred; it distinguishes concurrent adds of
    the same element by different agents.
    """

    element: str
    token: str


class ORSetDict(TypedDict):
    """Wire format for a complete :class:`ORSet`.

    ``entries`` holds all live ``(element, token)`` pairs.
    ``tombstones`` holds all token strings that have been explicitly removed.
    An entry whose token appears in ``tombstones`` is considered deleted.
    """

    entries: list[ORSetEntry]
    tombstones: list[str]


class ORSet:
    """Observed-Remove Set — an unordered add-wins CRDT set.

    Elements are arbitrary strings (content hashes, labels, identifiers).
    The set supports concurrent add and remove from multiple agents with the
    guarantee that adds always win over concurrent removes.

    All mutating methods return new :class:`ORSet` instances; ``self`` is
    never modified.

    Example::

        s1 = ORSet()
        s1, tok = s1.add("note-A")

        s2 = ORSet()
        s2 = s2.remove("note-A", {tok})  # remove the observed token

        # Concurrent add by s1 with a NEW token survives the remove:
        s1_v2, tok2 = s1.add("note-A")  # new token

        merged = s1_v2.join(s2)
        assert "note-A" in merged.elements()  # add-wins
    """

    def __init__(
        self,
        entries: set[tuple[str, str]] | None = None,
        tombstones: set[str] | None = None,
    ) -> None:
        """Initialise an OR-Set, optionally pre-populated.

        Args:
            entries:    Set of ``(element, token)`` pairs (alive entries).
            tombstones: Set of removed tokens.
        """
        self._entries: set[tuple[str, str]] = set(entries) if entries else set()
        self._tombstones: set[str] = set(tombstones) if tombstones else set()

    # ------------------------------------------------------------------
    # Mutations (return new ORSet)
    # ------------------------------------------------------------------

    def add(self, element: str) -> tuple[ORSet, str]:
        """Add *element* to the set with a fresh unique token.

        Args:
            element: The string value to add.

        Returns:
            A ``(new_set, token)`` pair where ``new_set`` contains the
            added element and ``token`` is the unique identifier of this
            particular addition (useful for targeted removal later).
        """
        token = str(uuid.uuid4())
        new_entries = self._entries | {(element, token)}
        return ORSet(new_entries, self._tombstones), token

    def remove(self, element: str, observed_tokens: set[str]) -> ORSet:
        """Remove *element* by tombstoning all currently observed tokens.

        Only the tokens listed in *observed_tokens* are tombstoned.  Any token
        added *after* this remove (i.e. from a concurrent ``add``) is not
        affected and the element will survive in the merged result.

        Args:
            element:         The element to remove.
            observed_tokens: The set of tokens for *element* that were observed
                             at remove time (typically ``self.tokens_for(element)``).

        Returns:
            A new :class:`ORSet` with *element*'s observed tokens tombstoned.
        """
        relevant = {(e, t) for e, t in self._entries if e == element and t in observed_tokens}
        new_tombstones = self._tombstones | {t for _, t in relevant}
        new_entries = self._entries - relevant
        return ORSet(new_entries, new_tombstones)

    # ------------------------------------------------------------------
    # CRDT join
    # ------------------------------------------------------------------

    def join(self, other: ORSet) -> ORSet:
        """Return the lattice join — union of entries minus all tombstones.

        Args:
            other: The OR-Set to merge with.

        Returns:
            A new :class:`ORSet` containing all entries from either replica
            whose tokens have not been tombstoned by either replica.
        """
        all_tombstones = self._tombstones | other._tombstones
        all_entries = (self._entries | other._entries) - {
            (e, t) for e, t in self._entries | other._entries if t in all_tombstones
        }
        return ORSet(all_entries, all_tombstones)

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def elements(self) -> frozenset[str]:
        """Return the set of visible elements (those not tombstoned).

        Returns:
            Frozenset of string elements currently in the set.
        """
        return frozenset(e for e, t in self._entries if t not in self._tombstones)

    def tokens_for(self, element: str) -> set[str]:
        """Return all live tokens for *element*.

        Pass the result to :meth:`remove` to remove *element* without
        accidentally tombstoning tokens added concurrently.

        Args:
            element: The element to look up.

        Returns:
            Set of token strings associated with live copies of *element*.
        """
        return {t for e, t in self._entries if e == element and t not in self._tombstones}

    def __contains__(self, element: str) -> bool:
        return element in self.elements()

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> ORSetDict:
        """Return a JSON-serialisable :class:`ORSetDict`.

        Returns:
            Dict with ``"entries"`` and ``"tombstones"`` lists.
        """
        entries: list[ORSetEntry] = [{"element": e, "token": t} for e, t in sorted(self._entries)]
        return {"entries": entries, "tombstones": sorted(self._tombstones)}

    @classmethod
    def from_dict(cls, data: ORSetDict) -> ORSet:
        """Reconstruct an :class:`ORSet` from its wire representation.

        Args:
            data: Dict as produced by :meth:`to_dict`.

        Returns:
            A new :class:`ORSet`.
        """
        entries = {(entry["element"], entry["token"]) for entry in data["entries"]}
        tombstones = set(data["tombstones"])
        return cls(entries, tombstones)

    # ------------------------------------------------------------------
    # Python dunder helpers
    # ------------------------------------------------------------------

    def equivalent(self, other: ORSet) -> bool:
        """Return ``True`` if both OR-Sets have the same visible elements and tombstones.

        Args:
            other: The OR-Set to compare against.

        Returns:
            ``True`` when visible elements and tombstone sets are identical.
        """
        return self.elements() == other.elements() and self._tombstones == other._tombstones

    def __repr__(self) -> str:
        return f"ORSet(elements={self.elements()!r})"

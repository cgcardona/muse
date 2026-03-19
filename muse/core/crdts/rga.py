"""Replicated Growable Array (RGA) — CRDT for ordered sequences.

The RGA (Roh et al., 2011 "Replicated abstract data types") provides
Google-Docs-style collaborative editing semantics for any ordered sequence
domain.  Every element carries a globally unique, immutable identifier
``f"{timestamp}@{author}"``; this identifier determines insertion order when
two agents concurrently insert at the same position.

**Core invariant**: the visible sequence is the list of elements whose
``deleted`` flag is ``False``, in the order determined by their identifiers.
Deletions are *tombstoned* (``deleted=True``) rather than physically removed
so that identifiers remain stable across replicas.

**Insertion semantics**: ``insert(after_id, element)`` inserts *element*
immediately after the element with ``id == after_id`` (``None`` means
prepend).  Concurrent inserts at the same position are resolved by sorting
the new element's ID lexicographically (descending) — the "bigger" ID wins
and is placed first, giving a deterministic outcome independent of delivery
order.

**Lattice laws satisfied by** :meth:`join`:
1. Commutativity: ``join(a, b) == join(b, a)``
2. Associativity: ``join(join(a, b), c) == join(a, join(b, c))``
3. Idempotency: ``join(a, a) == a``

Public API
----------
- :class:`RGAElement` — ``TypedDict`` for one array element.
- :class:`RGA` — the array itself.
"""


from __future__ import annotations

import logging
from typing import TypedDict

logger = logging.getLogger(__name__)


class RGAElement(TypedDict):
    """A single element in an :class:`RGA`.

    ``id`` is the stable unique identifier ``"{timestamp}@{author}"`` assigned
    at insertion time.  ``value`` is the content hash of the element (it
    references the object store — all binary content lives there).
    ``deleted`` is ``True`` for tombstoned elements that no longer appear in
    the visible sequence.

    ``parent_id`` is the ``id`` of the element this one was inserted after
    (``None`` means it was prepended — inserted at the head).  This is
    required for the commutative join algorithm to correctly place concurrent
    inserts regardless of which replica initiates the join.
    """

    id: str
    value: str
    deleted: bool
    parent_id: str | None


class RGA:
    """Replicated Growable Array — CRDT for ordered sequences.

    Provides ``insert``, ``delete``, ``join``, and ``to_sequence`` operations.
    All mutating methods return new :class:`RGA` instances; ``self`` is
    never modified.

    The internal representation is a list of :class:`RGAElement` dicts in
    insertion order (not visible order — tombstones are kept inline).

    Example::

        rga = RGA()
        rga, id_a = rga.insert(None, "note-hash-A")   # prepend
        rga, id_b = rga.insert(id_a, "note-hash-B")   # insert after A
        rga = rga.delete(id_a)                         # tombstone A
        assert rga.to_sequence() == ["note-hash-B"]
    """

    def __init__(self, elements: list[RGAElement] | None = None) -> None:
        """Construct an RGA, optionally pre-populated.

        Args:
            elements: Ordered list of :class:`RGAElement` dicts (may contain
                      tombstones).  Copied defensively.
        """
        self._elements: list[RGAElement] = list(elements) if elements else []

    # ------------------------------------------------------------------
    # Mutations (return new RGA)
    # ------------------------------------------------------------------

    def insert(self, after_id: str | None, value: str, *, element_id: str) -> RGA:
        """Return a new RGA with *value* inserted after *after_id*.

        Concurrent inserts at the same position are resolved by placing the
        element with the lexicographically *larger* ``element_id`` first.

        Args:
            after_id:   The ``id`` of the element to insert after, or ``None``
                        to prepend (insert before all existing elements).
            value:      The content hash of the new element.
            element_id: The stable unique ID for the new element; callers
                        should use ``f"{timestamp}@{author}"`` to ensure global
                        uniqueness across agents.

        Returns:
            A new :class:`RGA` with the element inserted at the correct position.
        """
        new_elem: RGAElement = {
            "id": element_id,
            "value": value,
            "deleted": False,
            "parent_id": after_id,
        }
        elems = list(self._elements)

        if after_id is None:
            # Prepend: among concurrent prepends (same parent_id=None), larger ID goes first.
            insert_pos = 0
            while (
                insert_pos < len(elems)
                and elems[insert_pos]["parent_id"] is None
                and elems[insert_pos]["id"] > element_id
            ):
                insert_pos += 1
            elems.insert(insert_pos, new_elem)
        else:
            # Find the anchor element.
            anchor_idx = next(
                (i for i, e in enumerate(elems) if e["id"] == after_id), None
            )
            if anchor_idx is None:
                # Unknown anchor — append at end (safe degradation).
                logger.warning("RGA.insert: unknown after_id=%r, appending at end", after_id)
                elems.append(new_elem)
            else:
                # Insert after anchor. Skip any existing elements that also
                # have the same parent_id AND a larger element ID (concurrent
                # inserts at the same position; larger ID wins leftmost slot).
                insert_pos = anchor_idx + 1
                while (
                    insert_pos < len(elems)
                    and elems[insert_pos]["parent_id"] == after_id
                    and elems[insert_pos]["id"] > element_id
                ):
                    insert_pos += 1
                elems.insert(insert_pos, new_elem)

        return RGA(elems)

    def delete(self, element_id: str) -> RGA:
        """Return a new RGA with *element_id* tombstoned.

        Tombstoning is idempotent — deleting an already-deleted or unknown
        element is a no-op.

        Args:
            element_id: The ``id`` of the element to tombstone.

        Returns:
            A new :class:`RGA` with the element marked ``deleted=True``.
        """
        new_elems: list[RGAElement] = []
        for elem in self._elements:
            if elem["id"] == element_id:
                new_elems.append({
                    "id": elem["id"],
                    "value": elem["value"],
                    "deleted": True,
                    "parent_id": elem["parent_id"],
                })
            else:
                new_elems.append({
                    "id": elem["id"],
                    "value": elem["value"],
                    "deleted": elem["deleted"],
                    "parent_id": elem["parent_id"],
                })
        return RGA(new_elems)

    # ------------------------------------------------------------------
    # CRDT join
    # ------------------------------------------------------------------

    def join(self, other: RGA) -> RGA:
        """Return the lattice join — the union of both arrays.

        Elements are keyed by ``id``.  The join:
        1. Takes the union of all element IDs from both replicas.
        2. For each ID, marks the element ``deleted`` if *either* replica has
           it tombstoned (once deleted, always deleted — monotone).
        3. Preserves the insertion-order sequence from ``self``; appends any
           elements from ``other`` not yet seen in ``self``.

        Args:
            other: The RGA to merge with.

        Returns:
            A new :class:`RGA` that is the join of ``self`` and *other*.
        """
        # Build ID → element maps from both replicas.
        self_map: dict[str, RGAElement] = {e["id"]: e for e in self._elements}
        other_map: dict[str, RGAElement] = {e["id"]: e for e in other._elements}

        # Merge deletions monotonically: once deleted in either, always deleted.
        merged_map: dict[str, RGAElement] = {}
        all_ids = set(self_map) | set(other_map)
        for eid in all_ids:
            if eid in self_map and eid in other_map:
                s = self_map[eid]
                o = other_map[eid]
                # In practice the same element_id always carries the same value
                # (because element_id = "{timestamp}@{author}" uniquely identifies
                # a write).  If values differ (only possible in crafted test scenarios),
                # pick the lexicographically larger value for commutativity.
                winning_value = s["value"] if s["value"] >= o["value"] else o["value"]
                merged_map[eid] = {
                    "id": eid,
                    "value": winning_value,
                    "deleted": s["deleted"] or o["deleted"],
                    "parent_id": s["parent_id"],
                }
            elif eid in self_map:
                src = self_map[eid]
                merged_map[eid] = {
                    "id": src["id"],
                    "value": src["value"],
                    "deleted": src["deleted"],
                    "parent_id": src["parent_id"],
                }
            else:
                src = other_map[eid]
                merged_map[eid] = {
                    "id": src["id"],
                    "value": src["value"],
                    "deleted": src["deleted"],
                    "parent_id": src["parent_id"],
                }

        # Rebuild a canonical ordered sequence using parent_id links.
        # Group elements by parent_id.  Within each group, sort by ID
        # descending (larger ID → leftmost, per concurrent-insert tiebreak rule).
        # Traverse recursively: start with children of None (prepended), then
        # recurse on each child's children.
        from collections import defaultdict
        children: dict[str | None, list[str]] = defaultdict(list)
        for eid, elem in merged_map.items():
            children[elem["parent_id"]].append(eid)
        for group in children.values():
            group.sort(reverse=True)  # larger ID first

        ordered: list[RGAElement] = []

        def _traverse(parent: str | None) -> None:
            for eid in children.get(parent, []):
                ordered.append(merged_map[eid])
                _traverse(eid)

        _traverse(None)
        return RGA(ordered)

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def to_sequence(self) -> list[str]:
        """Return the visible element values (excluding tombstones).

        Returns:
            List of ``value`` strings in document order, tombstones excluded.
        """
        return [e["value"] for e in self._elements if not e["deleted"]]

    def __len__(self) -> int:
        return len([e for e in self._elements if not e["deleted"]])

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> list[RGAElement]:
        """Return a JSON-serialisable list of :class:`RGAElement` dicts.

        Returns:
            Ordered list of all elements (including tombstones).
        """
        return [
            {"id": e["id"], "value": e["value"], "deleted": e["deleted"], "parent_id": e["parent_id"]}
            for e in self._elements
        ]

    @classmethod
    def from_dict(cls, data: list[RGAElement]) -> RGA:
        """Reconstruct an :class:`RGA` from its wire representation.

        Args:
            data: List of :class:`RGAElement` dicts as produced by
                  :meth:`to_dict`.

        Returns:
            A new :class:`RGA`.
        """
        return cls(list(data))

    # ------------------------------------------------------------------
    # Python dunder helpers
    # ------------------------------------------------------------------

    def equivalent(self, other: RGA) -> bool:
        """Return ``True`` if both RGAs have identical element lists (including tombstones).

        Note: use :meth:`to_sequence` comparison when only visible content matters.

        Args:
            other: The RGA to compare against.

        Returns:
            ``True`` when the full internal element lists are equal.
        """
        return self._elements == other._elements

    def __repr__(self) -> str:
        return f"RGA(len={len(self)}, elements={self._elements!r})"

"""Add-Wins Map (AW-Map) — CRDT map where adds win over concurrent removes.

An AW-Map is a dictionary where keys map to arbitrary CRDT values (represented
as strings — content hashes referencing the object store).  The "add-wins"
property means that if agent A sets key K and agent B concurrently removes
key K, the merged result still contains K with A's value.

This is built using the same token-tag mechanism as :class:`~muse.core.crdts.or_set.ORSet`:
each key entry carries a set of unique tokens; removal tombstones all observed
tokens for that key.

Use cases in Muse:
- File manifests (path → content hash) where agent A can add a file while
  agent B removes a different file.
- Plugin configuration maps (dimension → value) for independent per-dimension
  settings.
- Annotation maps (element_id → annotation blob hash).

**Lattice laws satisfied by** :meth:`join`:
1. Commutativity: ``join(a, b) == join(b, a)``
2. Associativity: ``join(join(a, b), c) == join(a, join(b, c))``
3. Idempotency: ``join(a, a) == a``

Public API
----------
- :class:`AWMapEntry` — ``TypedDict`` for one map entry (key, value, token).
- :class:`AWMapDict` — ``TypedDict`` wire format for a complete AW-Map.
- :class:`AWMap` — the map itself.
"""

from __future__ import annotations

import logging
import uuid
from typing import TypedDict

logger = logging.getLogger(__name__)


class AWMapEntry(TypedDict):
    """A single (key, value, token) triple in an :class:`AWMap`.

    ``key`` is the map key (e.g. a file path or dimension name).
    ``value`` is the associated value (e.g. a content hash).
    ``token`` is the unique identifier of this specific *setting* of the key;
    it is regenerated on every ``set`` call so concurrent sets of the same key
    by different agents can be distinguished.
    """

    key: str
    value: str
    token: str


class AWMapDict(TypedDict):
    """Wire format for a complete :class:`AWMap`.

    ``entries`` holds all live ``(key, value, token)`` triples.
    ``tombstones`` holds all token strings that have been removed.
    """

    entries: list[AWMapEntry]
    tombstones: list[str]


class AWMap:
    """Add-Wins Map — an unordered map CRDT where adds win over concurrent removes.

    Keys and values are strings.  Each logical key may temporarily have
    multiple (value, token) pairs during concurrent writes; the visible value
    for a key is resolved by taking the entry with the lexicographically
    greatest token among all live entries for that key.  This gives a
    deterministic LWW-like resolution for concurrent writes to the same key
    without requiring wall-clock timestamps.

    All mutating methods return new :class:`AWMap` instances; ``self`` is
    never modified.

    Example::

        m = AWMap()
        m = m.set("tempo", "120bpm")
        m = m.set("key",   "C major")
        assert m.get("tempo") == "120bpm"
        assert m.get("key")   == "C major"
        assert m.remove("tempo").get("tempo") is None
    """

    def __init__(
        self,
        entries: set[tuple[str, str, str]] | None = None,
        tombstones: set[str] | None = None,
    ) -> None:
        """Initialise an AW-Map, optionally pre-populated.

        Args:
            entries:    Set of ``(key, value, token)`` triples (live entries).
            tombstones: Set of removed token strings.
        """
        self._entries: set[tuple[str, str, str]] = set(entries) if entries else set()
        self._tombstones: set[str] = set(tombstones) if tombstones else set()

    # ------------------------------------------------------------------
    # Mutations (return new AWMap)
    # ------------------------------------------------------------------

    def set(self, key: str, value: str) -> AWMap:
        """Set *key* to *value*, replacing all existing live entries for *key*.

        Old tokens for *key* are tombstoned; a new token is generated for the
        new value, giving the add-wins property for concurrent operations.

        Args:
            key:   The map key to set.
            value: The new value to associate with *key*.

        Returns:
            A new :class:`AWMap` with *key* updated to *value*.
        """
        # Tombstone all existing live entries for key
        existing_tokens = {t for k, v, t in self._entries if k == key and t not in self._tombstones}
        new_tombstones = self._tombstones | existing_tokens
        new_entries = {e for e in self._entries if not (e[0] == key and e[2] in existing_tokens)}
        # Add new entry with fresh token
        new_token = str(uuid.uuid4())
        new_entries.add((key, value, new_token))
        return AWMap(new_entries, new_tombstones)

    def remove(self, key: str) -> AWMap:
        """Remove *key* by tombstoning all currently observed tokens for it.

        Concurrent adds with new tokens survive this remove.

        Args:
            key: The map key to remove.

        Returns:
            A new :class:`AWMap` with *key* removed.
        """
        observed_tokens = {t for k, v, t in self._entries if k == key}
        new_tombstones = self._tombstones | observed_tokens
        new_entries = {e for e in self._entries if not (e[0] == key)}
        return AWMap(new_entries, new_tombstones)

    # ------------------------------------------------------------------
    # CRDT join
    # ------------------------------------------------------------------

    def join(self, other: AWMap) -> AWMap:
        """Return the lattice join — union of entries minus all tombstones.

        Args:
            other: The AW-Map to merge with.

        Returns:
            A new :class:`AWMap` that is the join of ``self`` and *other*.
        """
        all_tombstones = self._tombstones | other._tombstones
        all_raw_entries = self._entries | other._entries
        live_entries = {e for e in all_raw_entries if e[2] not in all_tombstones}
        return AWMap(live_entries, all_tombstones)

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def get(self, key: str) -> str | None:
        """Return the current value for *key*, or ``None`` if absent.

        When multiple live entries exist for *key* (due to concurrent un-joined
        writes), the one with the lexicographically greatest token is returned.
        This gives a deterministic, consistent result without wall-clock time.

        Args:
            key: The map key to look up.

        Returns:
            The value string, or ``None`` if *key* has no live entry.
        """
        live = [(v, t) for k, v, t in self._entries if k == key and t not in self._tombstones]
        if not live:
            return None
        return max(live, key=lambda pair: pair[1])[0]

    def keys(self) -> frozenset[str]:
        """Return the set of keys with at least one live entry.

        Returns:
            Frozenset of key strings currently in the map.
        """
        return frozenset(k for k, v, t in self._entries if t not in self._tombstones)

    def to_plain_dict(self) -> dict[str, str]:
        """Return a plain ``{key: value}`` dict of visible entries.

        Concurrent-write conflicts are resolved by lexicographic token order
        (the same rule as :meth:`get`).

        Returns:
            ``{key: resolved_value}`` for all live keys.
        """
        result: dict[str, str] = {}
        for k in self.keys():
            v = self.get(k)
            if v is not None:
                result[k] = v
        return result

    def __contains__(self, key: str) -> bool:
        return key in self.keys()

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> AWMapDict:
        """Return a JSON-serialisable :class:`AWMapDict`.

        Returns:
            Dict with ``"entries"`` and ``"tombstones"`` lists.
        """
        entries: list[AWMapEntry] = [
            {"key": k, "value": v, "token": t}
            for k, v, t in sorted(self._entries)
        ]
        return {"entries": entries, "tombstones": sorted(self._tombstones)}

    @classmethod
    def from_dict(cls, data: AWMapDict) -> AWMap:
        """Reconstruct an :class:`AWMap` from its wire representation.

        Args:
            data: Dict as produced by :meth:`to_dict`.

        Returns:
            A new :class:`AWMap`.
        """
        entries = {(e["key"], e["value"], e["token"]) for e in data["entries"]}
        tombstones = set(data["tombstones"])
        return cls(entries, tombstones)

    # ------------------------------------------------------------------
    # Python dunder helpers
    # ------------------------------------------------------------------

    def equivalent(self, other: AWMap) -> bool:
        """Return ``True`` if both AW-Maps have the same visible key-value pairs and tombstones.

        Args:
            other: The AW-Map to compare against.

        Returns:
            ``True`` when plain dict views and tombstone sets are identical.
        """
        return self.to_plain_dict() == other.to_plain_dict() and self._tombstones == other._tombstones

    def __repr__(self) -> str:
        return f"AWMap(keys={set(self.keys())!r})"

"""CRDT primitive library for Muse's convergent multi-agent write semantics.

This package provides six foundational Conflict-free Replicated Data Types
(CRDTs) that plugin authors can use to give their domains convergent merge
semantics.  With CRDTs, ``join`` always succeeds — no conflict state ever
exists — making them ideal for high-throughput multi-agent scenarios where
finding a merge base is expensive or impossible.

Available primitives
--------------------

+------------------+------------------------------------+-------------------------------+
| Class            | Module                             | Use when…                     |
+==================+====================================+===============================+
| :class:`VectorClock` | :mod:`~muse.core.crdts.vclock` | Causal ordering between agents|
+------------------+------------------------------------+-------------------------------+
| :class:`LWWRegister` | :mod:`~muse.core.crdts.lww_register` | Scalar values; last write wins|
+------------------+------------------------------------+-------------------------------+
| :class:`ORSet`   | :mod:`~muse.core.crdts.or_set`     | Unordered sets; adds win      |
+------------------+------------------------------------+-------------------------------+
| :class:`RGA`     | :mod:`~muse.core.crdts.rga`        | Ordered sequences (collab edit)|
+------------------+------------------------------------+-------------------------------+
| :class:`AWMap`   | :mod:`~muse.core.crdts.aw_map`     | Key-value maps; adds win      |
+------------------+------------------------------------+-------------------------------+
| :class:`GCounter`| :mod:`~muse.core.crdts.g_counter`  | Monotone counters             |
+------------------+------------------------------------+-------------------------------+

All primitives satisfy the three CRDT lattice laws:

1. **Commutativity**: ``join(a, b) == join(b, a)``
2. **Associativity**: ``join(join(a, b), c) == join(a, join(b, c))``
3. **Idempotency**: ``join(a, a) == a``

These three properties guarantee convergence regardless of message order or
delivery count — any two replicas that have received the same set of writes
(in any order) will produce the same state.

Choosing the right primitive
-----------------------------

- Use :class:`LWWRegister` for scalar config values (tempo, key, metadata).
- Use :class:`ORSet` for unordered sets of objects (annotations, tags).
- Use :class:`RGA` for ordered sequences (note lists, event streams).
- Use :class:`AWMap` for mappings (file manifests, dimension configs).
- Use :class:`GCounter` for monotonically increasing totals (commit counts).
- Use :class:`VectorClock` for causal tracking across all of the above.

Plugin integration
------------------

Plugins opt into CRDT semantics by implementing the :class:`CRDTPlugin`
protocol declared in :mod:`muse.domain`.  The core engine detects the protocol
at merge time and calls :meth:`CRDTPlugin.join` instead of the three-way merge
path.  See :mod:`muse.domain` for the full ``CRDTPlugin`` contract.
"""

from __future__ import annotations

from muse.core.crdts.aw_map import AWMap, AWMapDict, AWMapEntry
from muse.core.crdts.g_counter import GCounter, GCounterDict
from muse.core.crdts.lww_register import LWWRegister, LWWValue
from muse.core.crdts.or_set import ORSet, ORSetDict, ORSetEntry
from muse.core.crdts.rga import RGA, RGAElement
from muse.core.crdts.vclock import VClockDict, VectorClock

__all__ = [
    "VectorClock",
    "VClockDict",
    "LWWRegister",
    "LWWValue",
    "ORSet",
    "ORSetEntry",
    "ORSetDict",
    "RGA",
    "RGAElement",
    "AWMap",
    "AWMapEntry",
    "AWMapDict",
    "GCounter",
    "GCounterDict",
]

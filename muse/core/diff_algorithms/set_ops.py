"""Hash-set algebra diff for unordered collections.

Computes the symmetric difference between two ``frozenset[str]`` collections
of content IDs. The result is a ``StructuredDelta`` containing:

- ``InsertOp`` entries for content IDs present in *target* but not *base*.
- ``DeleteOp`` entries for content IDs present in *base* but not *target*.

No ``MoveOp`` or ``ReplaceOp`` is ever produced: unordered sets have no
positional semantics, so every element is either added or removed.

This is the algorithm the MIDI plugin uses for file-level diffing (which set
of POSIX paths changed). Plugins with a ``SetSchema`` in their ``DomainSchema``
get this algorithm for free via :func:`~muse.core.diff_algorithms.diff_by_schema`.

Public API
----------
- :func:`diff` — ``frozenset[str]`` × ``frozenset[str]`` → ``StructuredDelta``.
"""

import logging

from muse.core.schema import SetSchema
from muse.domain import DeleteOp, DomainOp, InsertOp, StructuredDelta

logger = logging.getLogger(__name__)


def diff(
    schema: SetSchema,
    base: frozenset[str],
    target: frozenset[str],
    *,
    domain: str,
    address: str = "",
) -> StructuredDelta:
    """Diff two unordered sets of content IDs under the given ``SetSchema``.

    All insertions and deletions have ``position=None`` because the collection
    is unordered — position has no meaning for set elements.

    Args:
        schema:  The ``SetSchema`` declaring element type and identity.
        base:    Base (ancestor) set of content IDs.
        target:  Target (newer) set of content IDs.
        domain:  Domain tag for the returned ``StructuredDelta``.
        address: Address prefix for generated op entries.

    Returns:
        A ``StructuredDelta`` with ``InsertOp`` and ``DeleteOp`` entries.
    """
    added = sorted(target - base)
    removed = sorted(base - target)
    elem = schema["element_type"]

    ops: list[DomainOp] = []

    for cid in removed:
        ops.append(
            DeleteOp(
                op="delete",
                address=address,
                position=None,
                content_id=cid,
                content_summary=f"{elem} removed: {cid[:12]}",
            )
        )

    for cid in added:
        ops.append(
            InsertOp(
                op="insert",
                address=address,
                position=None,
                content_id=cid,
                content_summary=f"{elem} added: {cid[:12]}",
            )
        )

    n_add = len(added)
    n_del = len(removed)
    parts: list[str] = []
    if n_add:
        parts.append(f"{n_add} {elem}{'s' if n_add != 1 else ''} added")
    if n_del:
        parts.append(f"{n_del} {elem}{'s' if n_del != 1 else ''} removed")
    summary = ", ".join(parts) if parts else f"no {elem} changes"

    logger.debug(
        "set_ops.diff %r: +%d -%d elements",
        address,
        n_add,
        n_del,
    )

    return StructuredDelta(domain=domain, ops=ops, summary=summary)

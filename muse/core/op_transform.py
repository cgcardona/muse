"""Operational transformation for Muse domain operations.

This module implements the commutativity rules and position-adjustment
transforms that allow the merge engine to reason over ``DomainOp`` trees
rather than file-path sets. The result is sub-file auto-merge: two agents
inserting notes at non-overlapping bars never produce a conflict.

Theory
------
Operational Transformation (OT) is the theory behind real-time collaborative
editors (Google Docs, VS Code Live Share). The key insight is that two
*concurrent* operations — generated independently against the same base state
— can be applied in sequence without conflict if and only if they *commute*:
applying them in either order yields the same final state.

For concurrent operations that commute, OT provides a ``transform`` function
that adjusts positions so that the result is identical regardless of which
operation is applied first.

Public API
----------
- :class:`MergeOpsResult`  — structured result of merging two op lists.
- :func:`ops_commute`      — commmutativity oracle for any two ``DomainOp``\\s.
- :func:`transform`        — position-adjusted ``(a', b')`` for commuting ops.
- :func:`merge_op_lists`   — three-way merge at operation granularity.

Commutativity rules (summary)
------------------------------

============================================= =====================================
Op A                      Op B               Commute?
============================================= =====================================
InsertOp(pos=i)           InsertOp(pos=j)    Yes — if i ≠ j or both None (unordered)
InsertOp(pos=i)           InsertOp(pos=i)    **No** — positional conflict
InsertOp(addr=A)          DeleteOp(addr=B)   Yes — if A ≠ B (different containers)
InsertOp(addr=A)          DeleteOp(addr=A)   **No** — same container
DeleteOp(addr=A)          DeleteOp(addr=B)   Yes — always (consensus delete is fine)
ReplaceOp(addr=A)         ReplaceOp(addr=B)  Yes — if A ≠ B
ReplaceOp(addr=A)         ReplaceOp(addr=A)  **No** — concurrent value conflict
MoveOp(from=i)            MoveOp(from=j)     Yes — if i ≠ j
MoveOp(from=i)            DeleteOp(pos=i)    **No** — move-delete conflict
PatchOp(addr=A)           PatchOp(addr=B)    Yes — if A ≠ B; recurse if A == B
============================================= =====================================

Position adjustment
-------------------
When two ``InsertOp``\\s at different positions commute, the later-applied one
must have its position adjusted. Concretely, if *a* inserts at position *i*
and *b* inserts at position *j* with *i < j*:

- Applying *a* first shifts every element at position ≥ i by one; so *b*
  must be adjusted to *j + 1*.
- Applying *b* first does not affect positions < j; so *a* stays at *i*.

For *merge_op_lists*, positions are adjusted via the **counting formula**: for
each InsertOp in one side's exclusive additions, add the count of the other
side's InsertOps that have position ≤ this op's position (on the same
address). This is correct for any number of concurrent insertions and avoids
the cascading adjustment errors that arise from naive sequential pairwise OT.

Synchronous guarantee
---------------------
All functions are synchronous, pure, and allocation-bounded — no I/O, no
async, no external state.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from muse.domain import (
    DeleteOp,
    DomainOp,
    InsertOp,
    MoveOp,
    PatchOp,
    ReplaceOp,
    StructuredDelta,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class MergeOpsResult:
    """Result of a three-way operation-level merge.

    ``merged_ops`` contains the operations from both sides that can be applied
    to the common ancestor to produce the merged state. Positions in any
    ``InsertOp`` entries have been adjusted so that the ops can be applied in
    ascending position order to produce a deterministic result.

    ``conflict_ops`` contains pairs ``(our_op, their_op)`` where the two
    operations cannot be auto-merged. Each pair must be resolved manually
    (or via ``.museattributes`` strategy) before the merge can complete.

    ``is_clean`` is ``True`` when ``conflict_ops`` is empty.
    """

    merged_ops: list[DomainOp] = field(default_factory=list)
    conflict_ops: list[tuple[DomainOp, DomainOp]] = field(default_factory=list)

    @property
    def is_clean(self) -> bool:
        """``True`` when no conflicting operation pairs were found."""
        return len(self.conflict_ops) == 0


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _op_key(op: DomainOp) -> tuple[str, ...]:
    """Return a hashable key uniquely identifying *op* for set membership tests.

    The key captures all semantically significant fields so that two ops with
    identical effect produce the same key. This is used to detect consensus
    operations (both sides added the same op independently).
    """
    if op["op"] == "insert":
        return ("insert", op["address"], str(op["position"]), op["content_id"])
    if op["op"] == "delete":
        return ("delete", op["address"], str(op["position"]), op["content_id"])
    if op["op"] == "move":
        return (
            "move",
            op["address"],
            str(op["from_position"]),
            str(op["to_position"]),
            op["content_id"],
        )
    if op["op"] == "replace":
        return (
            "replace",
            op["address"],
            str(op["position"]),
            op["old_content_id"],
            op["new_content_id"],
        )
    # PatchOp — key on address and child_domain; child_ops are not hashed for
    # performance reasons.  Two patch ops on the same container are treated as
    # the same "slot" for conflict detection purposes.
    return ("patch", op["address"], op["child_domain"])


# ---------------------------------------------------------------------------
# Commutativity oracle
# ---------------------------------------------------------------------------


def ops_commute(a: DomainOp, b: DomainOp) -> bool:
    """Return ``True`` if operations *a* and *b* commute (are auto-mergeable).

    Two operations commute when applying them in either order produces the
    same final state. This function implements the commutativity rules table
    for all 25 op-kind pairs.

    For ``PatchOp`` at the same address, commmutativity is determined
    recursively by checking all child-op pairs.

    Args:
        a: First domain operation.
        b: Second domain operation.

    Returns:
        ``True`` if the two operations can be safely auto-merged.
    """
    # ------------------------------------------------------------------
    # InsertOp + *
    # ------------------------------------------------------------------
    if a["op"] == "insert":
        if b["op"] == "insert":
            # Different containers always commute — they are completely independent.
            if a["address"] != b["address"]:
                return True
            a_pos, b_pos = a["position"], b["position"]
            # Unordered collections (position=None) always commute.
            if a_pos is None or b_pos is None:
                return True
            # Ordered sequences within the same container: conflict only at equal positions.
            return a_pos != b_pos
        if b["op"] == "delete":
            # Conservative: inserts and deletes at the same container conflict.
            return a["address"] != b["address"]
        if b["op"] == "move":
            return a["address"] != b["address"]
        if b["op"] == "replace":
            return a["address"] != b["address"]
        # b is PatchOp (exhaustion of DeleteOp | MoveOp | ReplaceOp | PatchOp)
        return a["address"] != b["address"]

    # ------------------------------------------------------------------
    # DeleteOp + *
    # ------------------------------------------------------------------
    if a["op"] == "delete":
        if b["op"] == "insert":
            return a["address"] != b["address"]
        if b["op"] == "delete":
            # Consensus delete (same or different address) always commutes.
            # Two branches that both removed the same element produce the same
            # result: the element is absent.
            return True
        if b["op"] == "move":
            # Conflict if the delete's position matches the move's source.
            a_pos = a["position"]
            if a_pos is None:
                return True  # unordered collection: no positional conflict
            return a_pos != b["from_position"]
        if b["op"] == "replace":
            return a["address"] != b["address"]
        # b is PatchOp
        return a["address"] != b["address"]

    # ------------------------------------------------------------------
    # MoveOp + *
    # ------------------------------------------------------------------
    if a["op"] == "move":
        if b["op"] == "insert":
            return a["address"] != b["address"]
        if b["op"] == "delete":
            b_pos = b["position"]
            if b_pos is None:
                return True
            return a["from_position"] != b_pos
        if b["op"] == "move":
            # Two moves from different source positions commute.
            return a["from_position"] != b["from_position"]
        if b["op"] == "replace":
            return a["address"] != b["address"]
        # b is PatchOp
        return a["address"] != b["address"]

    # ------------------------------------------------------------------
    # ReplaceOp + *
    # ------------------------------------------------------------------
    if a["op"] == "replace":
        if b["op"] == "insert":
            return a["address"] != b["address"]
        if b["op"] == "delete":
            return a["address"] != b["address"]
        if b["op"] == "move":
            return a["address"] != b["address"]
        if b["op"] == "replace":
            # Two replaces at the same address conflict (concurrent value change).
            return a["address"] != b["address"]
        # b is PatchOp
        return a["address"] != b["address"]

    # ------------------------------------------------------------------
    # PatchOp + *  (a["op"] == "patch" after the four checks above)
    # ------------------------------------------------------------------
    if b["op"] == "insert":
        return a["address"] != b["address"]
    if b["op"] == "delete":
        return a["address"] != b["address"]
    if b["op"] == "move":
        return a["address"] != b["address"]
    if b["op"] == "replace":
        return a["address"] != b["address"]
    # b is PatchOp
    if a["address"] != b["address"]:
        return True
    # Same address: recurse into child ops — all child pairs must commute.
    for child_a in a["child_ops"]:
        for child_b in b["child_ops"]:
            if not ops_commute(child_a, child_b):
                return False
    return True


# ---------------------------------------------------------------------------
# OT transform
# ---------------------------------------------------------------------------


def transform(a: DomainOp, b: DomainOp) -> tuple[DomainOp, DomainOp]:
    """Return ``(a', b')`` such that ``apply(apply(base, a), b') == apply(apply(base, b), a')``.

    This is the core OT transform function. It should only be called when
    :func:`ops_commute` has confirmed that *a* and *b* commute. For all
    commuting pairs except ordered InsertOp+InsertOp, the identity transform
    is returned — the operations do not interfere with each other's positions.

    For the InsertOp+InsertOp case with integer positions (the most common
    case in practice), positions are adjusted so the diamond property holds:
    the same final sequence is produced regardless of application order.

    Args:
        a: First domain operation.
        b: Second domain operation (must commute with *a*).

    Returns:
        A tuple ``(a', b')`` where:

        - *a'* is the version of *a* to apply when *b* has already been applied.
        - *b'* is the version of *b* to apply when *a* has already been applied.
    """
    if a["op"] == "insert" and b["op"] == "insert":
        a_pos, b_pos = a["position"], b["position"]
        if a_pos is not None and b_pos is not None and a_pos != b_pos:
            if a_pos < b_pos:
                # a inserts before b's original position → b shifts up by 1.
                b_prime = InsertOp(
                    op="insert",
                    address=b["address"],
                    position=b_pos + 1,
                    content_id=b["content_id"],
                    content_summary=b["content_summary"],
                )
                return a, b_prime
            else:
                # b inserts before a's original position → a shifts up by 1.
                a_prime = InsertOp(
                    op="insert",
                    address=a["address"],
                    position=a_pos + 1,
                    content_id=a["content_id"],
                    content_summary=a["content_summary"],
                )
                return a_prime, b

    # All other commuting pairs: identity transform.
    return a, b


# ---------------------------------------------------------------------------
# Three-way merge at operation granularity
# ---------------------------------------------------------------------------


def _adjust_insert_positions(
    ops: list[DomainOp],
    other_ops: list[DomainOp],
) -> list[DomainOp]:
    """Adjust ``InsertOp`` positions in *ops* to account for *other_ops*.

    For each ``InsertOp`` with a non-``None`` position in *ops*, the adjusted
    position is ``original_position + count`` where ``count`` is the number of
    ``InsertOp``\\s in *other_ops* that share the same ``address`` and have
    ``position ≤ original_position``.

    This implements the *counting formula* for multi-op position adjustment.
    It is correct for any number of concurrent insertions on each side,
    producing the same final sequence regardless of application order.

    Non-``InsertOp`` entries and unordered inserts (``position=None``) pass
    through unchanged.

    Args:
        ops:       The list of ops whose positions need adjustment.
        other_ops: The concurrent operations from the other branch.

    Returns:
        A new list with adjusted ``InsertOp``\\s; all other entries are copied
        unchanged.
    """
    # Collect other-side InsertOp positions, grouped by address.
    other_by_addr: dict[str, list[int]] = {}
    for op in other_ops:
        if op["op"] == "insert" and op["position"] is not None:
            addr = op["address"]
            if addr not in other_by_addr:
                other_by_addr[addr] = []
            other_by_addr[addr].append(op["position"])

    result: list[DomainOp] = []
    for op in ops:
        if op["op"] == "insert" and op["position"] is not None:
            addr = op["address"]
            pos = op["position"]
            others = other_by_addr.get(addr, [])
            shift = sum(1 for p in others if p <= pos)
            if shift:
                result.append(
                    InsertOp(
                        op="insert",
                        address=addr,
                        position=pos + shift,
                        content_id=op["content_id"],
                        content_summary=op["content_summary"],
                    )
                )
            else:
                result.append(op)
        else:
            result.append(op)

    return result


def merge_op_lists(
    base_ops: list[DomainOp],
    ours_ops: list[DomainOp],
    theirs_ops: list[DomainOp],
) -> MergeOpsResult:
    """Three-way merge at operation granularity.

    Implements the standard three-way merge algorithm applied to typed domain
    operations rather than file-path sets. The inputs represent:

    - *base_ops*:   operations present in the common ancestor.
    - *ours_ops*:   operations present on our branch (superset of base for
      kept ops, plus our new additions).
    - *theirs_ops*: operations present on their branch (same structure).

    Algorithm
    ---------
    1. **Kept from base** — ops in base that both sides retained are included
       unchanged.
    2. **Consensus additions** — ops added independently by both sides (same
       key) are included exactly once (idempotent).
    3. **Exclusive additions** — ops added by only one side enter the
       commmutativity check:

       - Any pair (ours_exclusive, theirs_exclusive) where
         :func:`ops_commute` returns ``False`` is recorded as a conflict.
       - Exclusive additions not involved in any conflict are included in
         ``merged_ops``, with ``InsertOp`` positions adjusted via
         :func:`_adjust_insert_positions`.

    Position adjustment note
    ------------------------
    The adjusted ``InsertOp`` positions in ``merged_ops`` are *absolute
    positions in the final merged sequence* — meaning they already account for
    all insertions from both sides. Callers applying the merged ops to the
    base state should apply ``InsertOp``\\s in ascending position order to
    obtain the correct final sequence.

    Args:
        base_ops:   Operations in the common ancestor delta.
        ours_ops:   Operations on our branch.
        theirs_ops: Operations on their branch.

    Returns:
        A :class:`MergeOpsResult` with merged and conflicting op lists.
    """
    base_key_set = {_op_key(op) for op in base_ops}
    ours_key_set = {_op_key(op) for op in ours_ops}
    theirs_key_set = {_op_key(op) for op in theirs_ops}

    # 1. Ops both sides kept from the base.
    kept: list[DomainOp] = [
        op
        for op in base_ops
        if _op_key(op) in ours_key_set and _op_key(op) in theirs_key_set
    ]

    # 2. New ops — not present in base.
    ours_new = [op for op in ours_ops if _op_key(op) not in base_key_set]
    theirs_new = [op for op in theirs_ops if _op_key(op) not in base_key_set]

    ours_new_keys = {_op_key(op) for op in ours_new}
    theirs_new_keys = {_op_key(op) for op in theirs_new}
    consensus_keys = ours_new_keys & theirs_new_keys

    # Consensus additions: both sides added the same op → include once.
    consensus: list[DomainOp] = [
        op for op in ours_new if _op_key(op) in consensus_keys
    ]

    # 3. Each side's exclusive new additions.
    ours_exclusive = [op for op in ours_new if _op_key(op) not in consensus_keys]
    theirs_exclusive = [op for op in theirs_new if _op_key(op) not in consensus_keys]

    # Conflict detection: any pair from both sides that does not commute.
    conflict_ops: list[tuple[DomainOp, DomainOp]] = []
    conflicting_ours_keys: set[tuple[str, ...]] = set()
    conflicting_theirs_keys: set[tuple[str, ...]] = set()

    for our_op in ours_exclusive:
        for their_op in theirs_exclusive:
            if not ops_commute(our_op, their_op):
                conflict_ops.append((our_op, their_op))
                conflicting_ours_keys.add(_op_key(our_op))
                conflicting_theirs_keys.add(_op_key(their_op))

    # 4. Clean ops: not involved in any conflict.
    clean_ours = [
        op for op in ours_exclusive if _op_key(op) not in conflicting_ours_keys
    ]
    clean_theirs = [
        op for op in theirs_exclusive if _op_key(op) not in conflicting_theirs_keys
    ]

    # 5. Position adjustment using the counting formula.
    clean_ours_adjusted = _adjust_insert_positions(clean_ours, clean_theirs)
    clean_theirs_adjusted = _adjust_insert_positions(clean_theirs, clean_ours)

    merged_ops: list[DomainOp] = (
        list(kept) + list(consensus) + clean_ours_adjusted + clean_theirs_adjusted
    )

    logger.debug(
        "merge_op_lists: kept=%d consensus=%d ours=%d theirs=%d conflicts=%d",
        len(kept),
        len(consensus),
        len(clean_ours_adjusted),
        len(clean_theirs_adjusted),
        len(conflict_ops),
    )

    return MergeOpsResult(merged_ops=merged_ops, conflict_ops=conflict_ops)


def merge_structured(
    base_delta: StructuredDelta,
    ours_delta: StructuredDelta,
    theirs_delta: StructuredDelta,
) -> MergeOpsResult:
    """Merge two structured deltas against a common base delta.

    A convenience wrapper over :func:`merge_op_lists` that accepts
    :class:`~muse.domain.StructuredDelta` objects directly.

    Args:
        base_delta:   Delta representing the common ancestor's operations.
        ours_delta:   Delta produced by our branch.
        theirs_delta: Delta produced by their branch.

    Returns:
        A :class:`MergeOpsResult` describing the merged and conflicting ops.
    """
    return merge_op_lists(
        base_delta["ops"],
        ours_delta["ops"],
        theirs_delta["ops"],
    )

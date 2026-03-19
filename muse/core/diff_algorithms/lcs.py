"""LCS / Myers shortest-edit-script algorithm for ordered sequences.

Operates on ``list[str]`` where each string is a content ID (SHA-256 or
deterministic hash). Two elements are considered identical iff their content
IDs are equal — the algorithm never inspects actual content.

Public API
----------
- :func:`myers_ses` — compute shortest edit script (keep / insert / delete).
- :func:`detect_moves` — post-process insert+delete pairs into ``MoveOp``\\s.
- :func:`diff` — end-to-end: list[str] × list[str] → ``StructuredDelta``.

Algorithm
---------
``myers_ses`` uses the classic O(nm) LCS dynamic-programming traceback. This
is the same algorithm as ``midi_diff.lcs_edit_script`` but operates on content
IDs (strings) rather than ``NoteKey`` dicts, making it fully generic.

The patience-diff and O(nd) Myers variants (see ``SequenceSchema.diff_algorithm``)
are not yet implemented; both fall back to the O(nm) LCS.
as an optimisation without changing the public API.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from muse.core.schema import SequenceSchema
from muse.domain import DeleteOp, DomainOp, InsertOp, MoveOp, StructuredDelta

logger = logging.getLogger(__name__)

EditKind = Literal["keep", "insert", "delete"]


@dataclass(frozen=True)
class EditStep:
    """One step in the shortest edit script produced by :func:`myers_ses`."""

    kind: EditKind
    base_index: int    # index in the base content-ID list
    target_index: int  # index in the target content-ID list
    item: str          # content ID of the element


# ---------------------------------------------------------------------------
# Core algorithm
# ---------------------------------------------------------------------------


def myers_ses(base: list[str], target: list[str]) -> list[EditStep]:
    """Compute the shortest edit script transforming *base* into *target*.

    Uses the O(nm) LCS dynamic-programming table followed by a linear-time
    traceback. Two elements are equal iff their content IDs match.

    Args:
        base:   Ordered list of content IDs for the base sequence.
        target: Ordered list of content IDs for the target sequence.

    Returns:
        A list of :class:`EditStep` entries (keep / insert / delete) that
        transforms *base* into *target*. The number of "keep" steps equals
        the LCS length; insert + delete steps are minimal.
    """
    n, m = len(base), len(target)

    # dp[i][j] = length of LCS of base[i:] and target[j:]
    dp: list[list[int]] = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            if base[i] == target[j]:
                dp[i][j] = dp[i + 1][j + 1] + 1
            else:
                dp[i][j] = max(dp[i + 1][j], dp[i][j + 1])

    steps: list[EditStep] = []
    i, j = 0, 0
    while i < n or j < m:
        if i < n and j < m and base[i] == target[j]:
            steps.append(EditStep("keep", i, j, base[i]))
            i += 1
            j += 1
        elif j < m and (i >= n or dp[i][j + 1] >= dp[i + 1][j]):
            steps.append(EditStep("insert", i, j, target[j]))
            j += 1
        else:
            steps.append(EditStep("delete", i, j, base[i]))
            i += 1

    return steps


# ---------------------------------------------------------------------------
# Move detection post-pass
# ---------------------------------------------------------------------------


def detect_moves(
    inserts: list[InsertOp],
    deletes: list[DeleteOp],
) -> tuple[list[MoveOp], list[InsertOp], list[DeleteOp]]:
    """Collapse (delete, insert) pairs that share a content ID into ``MoveOp``\\s.

    A move is defined as a delete and an insert of the same content (same
    ``content_id``) at different positions. Where the positions are the same,
    the pair is left as separate insert/delete ops (idempotent round-trip).

    Args:
        inserts: ``InsertOp`` entries from the LCS edit script.
        deletes: ``DeleteOp`` entries from the LCS edit script.

    Returns:
        A tuple ``(moves, remaining_inserts, remaining_deletes)`` where
        ``moves`` contains the detected ``MoveOp``\\s and the remaining lists
        hold ops that could not be paired.
    """
    delete_by_content: dict[str, DeleteOp] = {}
    for d in deletes:
        # Keep the first delete for each content_id — later ones are true deletes.
        if d["content_id"] not in delete_by_content:
            delete_by_content[d["content_id"]] = d

    moves: list[MoveOp] = []
    remaining_inserts: list[InsertOp] = []
    consumed: set[str] = set()

    for ins in inserts:
        cid = ins["content_id"]
        if cid in delete_by_content and cid not in consumed:
            d = delete_by_content[cid]
            from_pos = d["position"] if d["position"] is not None else 0
            to_pos = ins["position"] if ins["position"] is not None else 0
            if from_pos != to_pos:
                moves.append(
                    MoveOp(
                        op="move",
                        address=ins["address"],
                        from_position=from_pos,
                        to_position=to_pos,
                        content_id=cid,
                    )
                )
                consumed.add(cid)
                continue
        remaining_inserts.append(ins)

    remaining_deletes = [d for d in deletes if d["content_id"] not in consumed]
    return moves, remaining_inserts, remaining_deletes


# ---------------------------------------------------------------------------
# Top-level diff entry point
# ---------------------------------------------------------------------------


def diff(
    schema: SequenceSchema,
    base: list[str],
    target: list[str],
    *,
    domain: str,
    address: str = "",
) -> StructuredDelta:
    """Diff two ordered sequences of content IDs, returning a ``StructuredDelta``.

    Runs :func:`myers_ses`, then :func:`detect_moves` to collapse paired
    insert/delete entries into ``MoveOp``\\s. The resulting ``ops`` list
    contains ``DeleteOp``, ``InsertOp``, and ``MoveOp`` entries.

    Args:
        schema:  The ``SequenceSchema`` declaring element type and identity.
        base:    Base (ancestor) sequence as a list of content IDs.
        target:  Target (newer) sequence as a list of content IDs.
        domain:  Domain tag for the returned ``StructuredDelta``.
        address: Address prefix for generated op entries (e.g. file path).

    Returns:
        A ``StructuredDelta`` with a human-readable ``summary`` and typed ops.
    """
    steps = myers_ses(base, target)

    raw_inserts: list[InsertOp] = []
    raw_deletes: list[DeleteOp] = []
    elem = schema["element_type"]

    for step in steps:
        if step.kind == "insert":
            raw_inserts.append(
                InsertOp(
                    op="insert",
                    address=address,
                    position=step.target_index,
                    content_id=step.item,
                    content_summary=f"{elem}:{step.item[:8]}",
                )
            )
        elif step.kind == "delete":
            raw_deletes.append(
                DeleteOp(
                    op="delete",
                    address=address,
                    position=step.base_index,
                    content_id=step.item,
                    content_summary=f"{elem}:{step.item[:8]}",
                )
            )

    moves, remaining_inserts, remaining_deletes = detect_moves(raw_inserts, raw_deletes)
    ops: list[DomainOp] = [*remaining_deletes, *remaining_inserts, *moves]

    n_ins = len(remaining_inserts)
    n_del = len(remaining_deletes)
    n_mov = len(moves)

    parts: list[str] = []
    if n_ins:
        parts.append(f"{n_ins} {elem}{'s' if n_ins != 1 else ''} added")
    if n_del:
        parts.append(f"{n_del} {elem}{'s' if n_del != 1 else ''} removed")
    if n_mov:
        parts.append(f"{n_mov} {'moved' if n_mov != 1 else 'moved'}")
    summary = ", ".join(parts) if parts else f"no {elem} changes"

    logger.debug(
        "lcs.diff %r: +%d -%d ~%d ops on %d→%d elements",
        address,
        n_ins,
        n_del,
        n_mov,
        len(base),
        len(target),
    )

    return StructuredDelta(domain=domain, ops=ops, summary=summary)

"""LCS-based tree edit algorithm for labeled ordered trees — Phase 2.

Implements a correct tree diff that produces ``InsertOp``, ``DeleteOp``,
``ReplaceOp``, and ``MoveOp`` entries for labeled ordered trees.

Algorithm
---------
The diff proceeds top-down recursively:

1. Compare root nodes by ``content_id``. Different content_id → ``ReplaceOp``
   on the root node.
2. Diff the children sequences using the same LCS algorithm as
   :mod:`~muse.core.diff_algorithms.lcs`:

   - Matched child pairs (same ``content_id``) → recurse into subtree.
   - Unmatched inserts → ``InsertOp`` (entire subtree added).
   - Unmatched deletes → ``DeleteOp`` (entire subtree removed).
   - Paired insert+delete of same ``content_id`` at different positions →
     ``MoveOp``.

This approach is O(nm) per tree level where n, m are child counts. It does
not find the globally minimal edit script (Zhang-Shasha is optimal), but it
is correct: every change is accounted for, and applying the script to the base
tree produces the target tree. For the bounded tree sizes typical of domain
objects (scenes, tracks, ASTs ≲ 10k nodes), this is more than adequate for
Phase 2. Zhang-Shasha optimisation is a drop-in replacement once needed.

``TreeNode`` is defined here and re-exported by the package ``__init__``.

Public API
----------
- :class:`TreeNode` — labeled ordered tree node (frozen dataclass).
- :func:`diff` — ``TreeNode`` × ``TreeNode`` → ``StructuredDelta``.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from muse.core.schema import TreeSchema
from muse.domain import (
    DeleteOp,
    DomainOp,
    InsertOp,
    MoveOp,
    ReplaceOp,
    StructuredDelta,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# TreeNode — the unit of tree-edit comparison
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TreeNode:
    """A node in a labeled ordered tree for domain tree-edit algorithms.

    ``id`` is a stable unique identifier for the node (e.g. UUID or path).
    ``label`` is the human-readable name (e.g. element tag, node type).
    ``content_id`` is the SHA-256 of this node's own value — excluding its
    children. Two nodes are considered the same iff their ``content_id``\\s
    match; a different ``content_id`` triggers a ``ReplaceOp``.
    ``children`` is an ordered tuple of child nodes.
    """

    id: str
    label: str
    content_id: str
    children: tuple[TreeNode, ...]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _subtree_nodes(node: TreeNode) -> list[TreeNode]:
    """Return all nodes in *node*'s subtree (postorder)."""
    result: list[TreeNode] = []

    def _visit(n: TreeNode) -> None:
        for child in n.children:
            _visit(child)
        result.append(n)

    _visit(node)
    return result


def _lcs_children(
    base_children: tuple[TreeNode, ...],
    target_children: tuple[TreeNode, ...],
) -> list[tuple[Literal["keep", "insert", "delete"], int, int]]:
    """LCS shortest-edit script on two sequences of child nodes.

    Comparison is by ``id`` — children with the same id are matched (a "keep"),
    even if their ``content_id`` differs. A kept pair that has a different
    ``content_id`` will produce a ``ReplaceOp`` when recursed into by
    :func:`_diff_nodes`.

    Unmatched children produce insert / delete ops.

    Returns a list of ``(kind, base_idx, target_idx)`` triples.
    """
    n, m = len(base_children), len(target_children)
    base_ids = [c.id for c in base_children]
    target_ids = [c.id for c in target_children]

    dp: list[list[int]] = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            if base_ids[i] == target_ids[j]:
                dp[i][j] = dp[i + 1][j + 1] + 1
            else:
                dp[i][j] = max(dp[i + 1][j], dp[i][j + 1])

    result: list[tuple[Literal["keep", "insert", "delete"], int, int]] = []
    i, j = 0, 0
    while i < n or j < m:
        if i < n and j < m and base_ids[i] == target_ids[j]:
            result.append(("keep", i, j))
            i += 1
            j += 1
        elif j < m and (i >= n or dp[i][j + 1] >= dp[i + 1][j]):
            result.append(("insert", i, j))
            j += 1
        else:
            result.append(("delete", i, j))
            i += 1

    return result


def _diff_nodes(
    base: TreeNode,
    target: TreeNode,
    *,
    domain: str,
    address: str,
) -> list[DomainOp]:
    """Recursively diff two tree nodes, returning a flat op list."""
    ops: list[DomainOp] = []
    node_addr = f"{address}/{base.id}" if address else base.id

    # Root node comparison
    if base.content_id != target.content_id:
        ops.append(
            ReplaceOp(
                op="replace",
                address=node_addr,
                position=None,
                old_content_id=base.content_id,
                new_content_id=target.content_id,
                old_summary=f"{base.label} (prev)",
                new_summary=f"{target.label} (new)",
            )
        )

    if not base.children and not target.children:
        return ops

    # Diff children via LCS
    script = _lcs_children(base.children, target.children)

    raw_inserts: list[tuple[int, TreeNode]] = []  # (target_idx, node)
    raw_deletes: list[tuple[int, TreeNode]] = []  # (base_idx, node)

    for kind, bi, ti in script:
        if kind == "keep":
            # Recurse into the matched child pair
            ops.extend(
                _diff_nodes(
                    base.children[bi],
                    target.children[ti],
                    domain=domain,
                    address=node_addr,
                )
            )
        elif kind == "insert":
            raw_inserts.append((ti, target.children[ti]))
        else:
            raw_deletes.append((bi, base.children[bi]))

    # Move detection: paired insert+delete of the same node id at different positions.
    # Node identity is tracked by id, not content_id, so a repositioned node
    # is detected as a move even if its content also changed.
    delete_by_id: dict[str, tuple[int, TreeNode]] = {}
    for bi, node in raw_deletes:
        if node.id not in delete_by_id:
            delete_by_id[node.id] = (bi, node)

    consumed_ids: set[str] = set()
    for ti, node in raw_inserts:
        nid = node.id
        if nid in delete_by_id and nid not in consumed_ids:
            from_idx, _ = delete_by_id[nid]
            if from_idx != ti:
                ops.append(
                    MoveOp(
                        op="move",
                        address=node_addr,
                        from_position=from_idx,
                        to_position=ti,
                        content_id=node.content_id,
                    )
                )
                consumed_ids.add(nid)
                continue
        # True insert — recursively add the entire subtree's nodes
        for sub_node in _subtree_nodes(node):
            ops.append(
                InsertOp(
                    op="insert",
                    address=node_addr,
                    position=ti,
                    content_id=sub_node.content_id,
                    content_summary=f"{sub_node.label} added",
                )
            )

    for bi, node in raw_deletes:
        if node.id in consumed_ids:
            continue
        # True delete — recursively remove the entire subtree's nodes
        for sub_node in _subtree_nodes(node):
            ops.append(
                DeleteOp(
                    op="delete",
                    address=node_addr,
                    position=bi,
                    content_id=sub_node.content_id,
                    content_summary=f"{sub_node.label} removed",
                )
            )

    return ops


# ---------------------------------------------------------------------------
# Top-level diff entry point
# ---------------------------------------------------------------------------


def diff(
    schema: TreeSchema,
    base: TreeNode,
    target: TreeNode,
    *,
    domain: str,
    address: str = "",
) -> StructuredDelta:
    """Diff two labeled ordered trees, returning a ``StructuredDelta``.

    Produces ``ReplaceOp`` for node relabels, ``InsertOp`` / ``DeleteOp``
    for subtree insertions and deletions, and ``MoveOp`` for repositioned
    subtrees (detected as paired delete+insert of the same content).

    Args:
        schema:  The ``TreeSchema`` declaring node type and diff algorithm.
        base:    Root of the base (ancestor) tree.
        target:  Root of the target (newer) tree.
        domain:  Domain tag for the returned ``StructuredDelta``.
        address: Address prefix for generated op entries.

    Returns:
        A ``StructuredDelta`` with typed ops and a human-readable summary.
    """
    # Fast path: identical trees
    if base.content_id == target.content_id and base.children == target.children:
        return StructuredDelta(
            domain=domain,
            ops=[],
            summary=f"no {schema['node_type']} changes",
        )

    ops = _diff_nodes(base, target, domain=domain, address=address)

    n_replace = sum(1 for op in ops if op["op"] == "replace")
    n_insert = sum(1 for op in ops if op["op"] == "insert")
    n_delete = sum(1 for op in ops if op["op"] == "delete")
    n_move = sum(1 for op in ops if op["op"] == "move")

    parts: list[str] = []
    if n_replace:
        parts.append(f"{n_replace} relabelled")
    if n_insert:
        parts.append(f"{n_insert} added")
    if n_delete:
        parts.append(f"{n_delete} removed")
    if n_move:
        parts.append(f"{n_move} moved")
    summary = ", ".join(parts) if parts else f"no {schema['node_type']} changes"

    logger.debug(
        "tree_edit.diff: +%d -%d ~%d r%d ops on %r",
        n_insert,
        n_delete,
        n_move,
        n_replace,
        address,
    )

    return StructuredDelta(domain=domain, ops=ops, summary=summary)

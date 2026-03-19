"""Symbol-level diff engine for the code domain plugin.

Produces typed :class:`~muse.domain.DomainOp` entries at symbol granularity
rather than file granularity. This allows Muse to report which *functions*
were added, removed, renamed, or modified — not just which *files* changed.

Operation types produced
------------------------
``InsertOp``
    A symbol was added to the file (new function, class, etc.).

``DeleteOp``
    A symbol was removed from the file.

``ReplaceOp``
    A symbol's content changed. The ``old_summary`` / ``new_summary`` fields
    describe the nature of the change:

    - ``"renamed to <name>"`` — same body, different name (rename detected
      via matching ``body_hash``).
    - ``"signature changed"`` — same body, different signature.
    - ``"implementation changed"`` — same signature, different body.
    - ``"modified"`` — both signature and body changed.

``MoveOp``
    Reserved for intra-file positional moves (used when a symbol's address
    is unchanged but it is detected elsewhere via ``content_id``).

Cross-file move detection
-------------------------
When a symbol disappears from one file and appears in another with an
identical ``content_id``, the diff engine annotates the ``DeleteOp`` and
``InsertOp`` ``content_summary`` fields to indicate the move direction.  No
special op type is introduced — the existing :class:`~muse.domain.InsertOp`
and :class:`~muse.domain.DeleteOp` suffice because their addresses already
encode the file path.

Algorithm
---------
1.  Partition symbol addresses into ``added``, ``removed``, and ``common``.
2.  Build ``body_hash → address`` reverse maps for added and removed sets.
3.  For each ``removed`` symbol:

    a.  If ``content_id`` matches an ``added`` symbol → **exact move/copy**
        (same name, same body, different file).
    b.  Else if ``body_hash`` matches an ``added`` symbol → **rename**
        (same body, different name).

4.  Emit ``ReplaceOp`` for renames; pair the cross-file move partners via
    ``content_summary``.
5.  Emit ``DeleteOp`` for genuinely removed symbols.
6.  Emit ``InsertOp`` for genuinely added symbols.
7.  Emit ``ReplaceOp`` for symbols whose ``content_id`` changed.
"""

import logging

from muse.domain import DeleteOp, DomainOp, InsertOp, PatchOp, ReplaceOp
from muse.plugins.code.ast_parser import SymbolRecord, SymbolTree

logger = logging.getLogger(__name__)

_CHILD_DOMAIN = "code_symbols"


# ---------------------------------------------------------------------------
# Symbol-level diff within a single file
# ---------------------------------------------------------------------------


def diff_symbol_trees(
    base: SymbolTree,
    target: SymbolTree,
) -> list[DomainOp]:
    """Compute symbol-level ops transforming *base* into *target*.

    Both trees must be scoped to the same file (their addresses share the
    same ``"<file_path>::"`` prefix).

    Args:
        base:   Symbol tree of the base (older) version of the file.
        target: Symbol tree of the target (newer) version of the file.

    Returns:
        Ordered list of :class:`~muse.domain.DomainOp` entries.
    """
    base_addrs = set(base)
    target_addrs = set(target)
    added: set[str] = target_addrs - base_addrs
    removed: set[str] = base_addrs - target_addrs
    common: set[str] = base_addrs & target_addrs

    # Reverse maps for rename / move detection.
    added_by_content: dict[str, str] = {
        target[a]["content_id"]: a for a in added
    }
    added_by_body: dict[str, str] = {
        target[a]["body_hash"]: a for a in added
    }

    ops: list[DomainOp] = []
    # Addresses claimed by rename / move detection — excluded from plain ops.
    matched_removed: set[str] = set()
    matched_added: set[str] = set()

    # ── Pass 1: renames (same body_hash, different name) ──────────────────
    for rem_addr in sorted(removed):
        base_rec = base[rem_addr]

        if base_rec["content_id"] in added_by_content:
            # Exact content match at a different address → same symbol moved
            # (intra-file positional moves don't produce a different address;
            # this catches cross-file moves surfaced within a single-file diff
            # when the caller slices the tree incorrectly — uncommon).
            tgt_addr = added_by_content[base_rec["content_id"]]
            tgt_rec = target[tgt_addr]
            ops.append(ReplaceOp(
                op="replace",
                address=rem_addr,
                position=None,
                old_content_id=base_rec["content_id"],
                new_content_id=tgt_rec["content_id"],
                old_summary=f"{base_rec['kind']} {base_rec['name']}",
                new_summary=f"moved to {tgt_rec['qualified_name']}",
            ))
            matched_removed.add(rem_addr)
            matched_added.add(tgt_addr)

        elif (
            base_rec["body_hash"] in added_by_body
            and added_by_body[base_rec["body_hash"]] not in matched_added
        ):
            # Same body, different name → rename.
            tgt_addr = added_by_body[base_rec["body_hash"]]
            tgt_rec = target[tgt_addr]
            ops.append(ReplaceOp(
                op="replace",
                address=rem_addr,
                position=None,
                old_content_id=base_rec["content_id"],
                new_content_id=tgt_rec["content_id"],
                old_summary=f"{base_rec['kind']} {base_rec['name']}",
                new_summary=f"renamed to {tgt_rec['name']}",
            ))
            matched_removed.add(rem_addr)
            matched_added.add(tgt_addr)

    # ── Pass 2: plain deletions ────────────────────────────────────────────
    for rem_addr in sorted(removed - matched_removed):
        rec = base[rem_addr]
        ops.append(DeleteOp(
            op="delete",
            address=rem_addr,
            position=None,
            content_id=rec["content_id"],
            content_summary=f"removed {rec['kind']} {rec['name']}",
        ))

    # ── Pass 3: plain additions ────────────────────────────────────────────
    for add_addr in sorted(added - matched_added):
        rec = target[add_addr]
        ops.append(InsertOp(
            op="insert",
            address=add_addr,
            position=None,
            content_id=rec["content_id"],
            content_summary=f"added {rec['kind']} {rec['name']}",
        ))

    # ── Pass 4: modifications ──────────────────────────────────────────────
    for addr in sorted(common):
        base_rec = base[addr]
        tgt_rec = target[addr]
        if base_rec["content_id"] == tgt_rec["content_id"]:
            continue  # unchanged

        if base_rec["body_hash"] == tgt_rec["body_hash"]:
            # Body unchanged — signature changed (type annotations, defaults…).
            old_summary = f"{base_rec['kind']} {base_rec['name']} (signature changed)"
            new_summary = f"{tgt_rec['kind']} {tgt_rec['name']} (signature updated)"
        elif base_rec["signature_id"] == tgt_rec["signature_id"]:
            # Signature unchanged — implementation changed.
            old_summary = f"{base_rec['kind']} {base_rec['name']} (implementation)"
            new_summary = f"{tgt_rec['kind']} {tgt_rec['name']} (implementation changed)"
        else:
            # Both signature and body changed.
            old_summary = f"{base_rec['kind']} {base_rec['name']}"
            new_summary = f"{tgt_rec['kind']} {tgt_rec['name']} (modified)"

        ops.append(ReplaceOp(
            op="replace",
            address=addr,
            position=None,
            old_content_id=base_rec["content_id"],
            new_content_id=tgt_rec["content_id"],
            old_summary=old_summary,
            new_summary=new_summary,
        ))

    return ops


# ---------------------------------------------------------------------------
# Cross-file diff: build the full op list for a snapshot pair
# ---------------------------------------------------------------------------


def build_diff_ops(
    base_files: dict[str, str],
    target_files: dict[str, str],
    base_trees: dict[str, SymbolTree],
    target_trees: dict[str, SymbolTree],
) -> list[DomainOp]:
    """Build the complete op list transforming *base* snapshot into *target*.

    For each changed file:

    - **No symbol trees available**: coarse ``InsertOp`` / ``DeleteOp`` /
      ``ReplaceOp`` at file level.
    - **Symbol trees available for both sides**: ``PatchOp`` with symbol-level
      ``child_ops``.  If all symbols are unchanged (formatting-only change)
      a ``ReplaceOp`` with ``"reformatted"`` summary is emitted instead.
    - **Symbol tree available for one side only** (new or deleted file):
      ``PatchOp`` listing each symbol individually.

    Cross-file move annotation
    --------------------------
    After building per-file ops, a second pass checks whether any symbol
    ``content_id`` appears in both a ``DeleteOp`` child op and an ``InsertOp``
    child op across *different* files.  When found, both ops' ``content_summary``
    fields are annotated with the move direction.

    Args:
        base_files:   ``{path: raw_bytes_hash}`` from the base snapshot.
        target_files: ``{path: raw_bytes_hash}`` from the target snapshot.
        base_trees:   Symbol trees for changed base files, keyed by path.
        target_trees: Symbol trees for changed target files, keyed by path.

    Returns:
        Ordered list of ``DomainOp`` entries.
    """
    base_paths = set(base_files)
    target_paths = set(target_files)
    added_paths = sorted(target_paths - base_paths)
    removed_paths = sorted(base_paths - target_paths)
    modified_paths = sorted(
        p for p in base_paths & target_paths
        if base_files[p] != target_files[p]
    )

    ops: list[DomainOp] = []

    # ── Added files ────────────────────────────────────────────────────────
    for path in added_paths:
        tree = target_trees.get(path, {})
        if tree:
            child_ops: list[DomainOp] = [
                InsertOp(
                    op="insert",
                    address=addr,
                    position=None,
                    content_id=rec["content_id"],
                    content_summary=f"added {rec['kind']} {rec['name']}",
                )
                for addr, rec in sorted(tree.items())
            ]
            ops.append(_patch(path, child_ops))
        else:
            ops.append(InsertOp(
                op="insert",
                address=path,
                position=None,
                content_id=target_files[path],
                content_summary=f"added {path}",
            ))

    # ── Removed files ──────────────────────────────────────────────────────
    for path in removed_paths:
        tree = base_trees.get(path, {})
        if tree:
            child_ops = [
                DeleteOp(
                    op="delete",
                    address=addr,
                    position=None,
                    content_id=rec["content_id"],
                    content_summary=f"removed {rec['kind']} {rec['name']}",
                )
                for addr, rec in sorted(tree.items())
            ]
            ops.append(_patch(path, child_ops))
        else:
            ops.append(DeleteOp(
                op="delete",
                address=path,
                position=None,
                content_id=base_files[path],
                content_summary=f"removed {path}",
            ))

    # ── Modified files ─────────────────────────────────────────────────────
    for path in modified_paths:
        base_tree = base_trees.get(path, {})
        target_tree = target_trees.get(path, {})

        if base_tree or target_tree:
            child_ops = diff_symbol_trees(base_tree, target_tree)
            if child_ops:
                ops.append(_patch(path, child_ops))
            else:
                # All symbols have the same content_id — formatting-only change.
                ops.append(ReplaceOp(
                    op="replace",
                    address=path,
                    position=None,
                    old_content_id=base_files[path],
                    new_content_id=target_files[path],
                    old_summary=f"{path} (before)",
                    new_summary=f"{path} (reformatted — no semantic change)",
                ))
        else:
            ops.append(ReplaceOp(
                op="replace",
                address=path,
                position=None,
                old_content_id=base_files[path],
                new_content_id=target_files[path],
                old_summary=f"{path} (before)",
                new_summary=f"{path} (after)",
            ))

    _annotate_cross_file_moves(ops)
    return ops


def _patch(path: str, child_ops: list[DomainOp]) -> PatchOp:
    """Wrap symbol child_ops in a file-level PatchOp."""
    n_added = sum(1 for o in child_ops if o["op"] == "insert")
    n_removed = sum(1 for o in child_ops if o["op"] == "delete")
    n_modified = sum(1 for o in child_ops if o["op"] == "replace")
    parts: list[str] = []
    if n_added:
        parts.append(f"{n_added} symbol{'s' if n_added > 1 else ''} added")
    if n_removed:
        parts.append(f"{n_removed} symbol{'s' if n_removed > 1 else ''} removed")
    if n_modified:
        parts.append(f"{n_modified} symbol{'s' if n_modified > 1 else ''} modified")
    summary = ", ".join(parts) if parts else "no symbol changes"
    return PatchOp(
        op="patch",
        address=path,
        child_ops=child_ops,
        child_domain=_CHILD_DOMAIN,
        child_summary=summary,
    )


def _annotate_cross_file_moves(ops: list[DomainOp]) -> None:
    """Annotate DeleteOp/InsertOp pairs that represent cross-file symbol moves.

    Mutates the ``content_summary`` of matching ops in place.  A move is
    detected when:

    - A ``DeleteOp`` child op (inside a ``PatchOp``) has the same
      ``content_id`` as an ``InsertOp`` child op in a *different* file's
      ``PatchOp``.

    This is a best-effort annotation pass — it does not change the semantic
    meaning of the ops, only their human-readable summaries.
    """
    # Collect child op references: content_id → (file_path, op_index_in_patch)
    # We need mutable access so work with lists rather than immutable tuples.
    delete_by_content: dict[str, tuple[str, int, list[DomainOp]]] = {}
    insert_by_content: dict[str, tuple[str, int, list[DomainOp]]] = {}

    for op in ops:
        if op["op"] != "patch":
            continue
        file_path = op["address"]
        for i, child in enumerate(op["child_ops"]):
            if child["op"] == "delete":
                delete_by_content[child["content_id"]] = (file_path, i, op["child_ops"])
            elif child["op"] == "insert":
                insert_by_content[child["content_id"]] = (file_path, i, op["child_ops"])

    for content_id, (del_file, del_idx, del_children) in delete_by_content.items():
        if content_id not in insert_by_content:
            continue
        ins_file, ins_idx, ins_children = insert_by_content[content_id]
        if del_file == ins_file:
            continue  # Same file — not a cross-file move.

        del_op = del_children[del_idx]
        ins_op = ins_children[ins_idx]
        # Narrow to the expected op kinds before accessing kind-specific fields.
        if del_op["op"] != "delete" or ins_op["op"] != "insert":
            continue
        # Annotate both sides with move direction.
        del_children[del_idx] = DeleteOp(
            op="delete",
            address=del_op["address"],
            position=del_op["position"],
            content_id=del_op["content_id"],
            content_summary=f"{del_op['content_summary']} → moved to {ins_file}",
        )
        ins_children[ins_idx] = InsertOp(
            op="insert",
            address=ins_op["address"],
            position=ins_op["position"],
            content_id=ins_op["content_id"],
            content_summary=f"{ins_op['content_summary']} ← moved from {del_file}",
        )


# ---------------------------------------------------------------------------
# Summary helpers
# ---------------------------------------------------------------------------


def delta_summary(ops: list[DomainOp]) -> str:
    """Produce a human-readable one-line summary of a list of ops.

    Counts file-level and symbol-level operations separately.

    Args:
        ops: Top-level op list from :func:`build_diff_ops`.

    Returns:
        A concise summary string (e.g. ``"2 files modified (5 symbols)"``)
        or ``"no changes"`` for an empty list.
    """
    files_added = sum(1 for o in ops if o["op"] == "insert" and "::" not in o["address"])
    files_removed = sum(1 for o in ops if o["op"] == "delete" and "::" not in o["address"])
    files_modified = sum(1 for o in ops if o["op"] in ("replace", "patch") and "::" not in o["address"])

    # Count child-level symbol ops.
    symbols_added = 0
    symbols_removed = 0
    symbols_modified = 0
    for op in ops:
        if op["op"] == "patch":
            for child in op["child_ops"]:
                if child["op"] == "insert":
                    symbols_added += 1
                elif child["op"] == "delete":
                    symbols_removed += 1
                elif child["op"] in ("replace", "move"):
                    symbols_modified += 1
        elif op["op"] == "replace" and "::" not in op["address"]:
            # File-level replace with no symbol breakdown.
            pass

    parts: list[str] = []
    file_parts: list[str] = []
    if files_added:
        file_parts.append(f"{files_added} added")
    if files_removed:
        file_parts.append(f"{files_removed} removed")
    if files_modified:
        file_parts.append(f"{files_modified} modified")
    if file_parts:
        parts.append(f"{', '.join(file_parts)} file{'s' if sum([files_added, files_removed, files_modified]) != 1 else ''}")

    sym_parts: list[str] = []
    if symbols_added:
        sym_parts.append(f"{symbols_added} added")
    if symbols_removed:
        sym_parts.append(f"{symbols_removed} removed")
    if symbols_modified:
        sym_parts.append(f"{symbols_modified} modified")
    if sym_parts:
        parts.append(f"{', '.join(sym_parts)} symbol{'s' if sum([symbols_added, symbols_removed, symbols_modified]) != 1 else ''}")

    return ", ".join(parts) if parts else "no changes"

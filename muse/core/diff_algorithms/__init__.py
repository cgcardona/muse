"""Schema-driven diff algorithm dispatch.

:func:`diff_by_schema` is the single entry point for all diff operations. It
inspects ``schema["kind"]`` and dispatches to the correct algorithm module:

- ``"sequence"`` → :mod:`~muse.core.diff_algorithms.lcs`
- ``"tree"``     → :mod:`~muse.core.diff_algorithms.tree_edit`
- ``"tensor"``   → :mod:`~muse.core.diff_algorithms.numerical`
- ``"set"``      → :mod:`~muse.core.diff_algorithms.set_ops`
- ``"map"``      → built-in key-level diff (insert / delete / replace per key)

This module also defines the typed *input data* container types that pair with
each schema kind. Callers wrap their domain data in the appropriate container
before calling :func:`diff_by_schema`, enabling mypy to verify that schema and
data kinds match at type-check time.

Input data containers
---------------------
Each container is a ``TypedDict`` with a ``kind`` discriminant that mirrors
``ElementSchema.kind``. mypy narrows the union on ``kind`` checks, so type
errors are caught at compile time rather than at runtime.

- :class:`SequenceInput` — wraps ``list[str]`` (content IDs in order).
- :class:`SetInput`      — wraps ``frozenset[str]`` (content IDs, unordered).
- :class:`TensorInput`   — wraps ``list[float]`` (flat 1-D array).
- :class:`MapInput`      — wraps ``dict[str, str]`` (key → content ID).
- :class:`TreeInput`     — wraps :class:`~muse.core.diff_algorithms.tree_edit.TreeNode`.

:class:`TreeNode` is re-exported here for convenience; import it from this
package rather than from the submodule.
"""
from __future__ import annotations

import logging
from typing import Literal, TypedDict

from muse.core.diff_algorithms import lcs as _lcs
from muse.core.diff_algorithms import numerical as _numerical
from muse.core.diff_algorithms import set_ops as _set_ops
from muse.core.diff_algorithms import tree_edit as _tree_edit
from muse.core.diff_algorithms.tree_edit import TreeNode
from muse.core.schema import (
    DomainSchema,
    ElementSchema,
    MapSchema,
    SetSchema,
)
from muse.domain import DeleteOp, DomainOp, InsertOp, ReplaceOp, SnapshotManifest, StructuredDelta

logger = logging.getLogger(__name__)

# Re-export TreeNode so callers can do:
#   from muse.core.diff_algorithms import TreeNode
__all__ = [
    "TreeNode",
    "SequenceInput",
    "SetInput",
    "TensorInput",
    "MapInput",
    "TreeInput",
    "DiffInput",
    "diff_by_schema",
    "snapshot_diff",
]


# ---------------------------------------------------------------------------
# Typed input containers — one per ElementSchema kind
# ---------------------------------------------------------------------------


class SequenceInput(TypedDict):
    """Ordered sequence of content IDs. Pairs with ``SequenceSchema``."""

    kind: Literal["sequence"]
    items: list[str]


class SetInput(TypedDict):
    """Unordered set of content IDs. Pairs with ``SetSchema``."""

    kind: Literal["set"]
    items: frozenset[str]


class TensorInput(TypedDict):
    """Flat 1-D numerical array. Pairs with ``TensorSchema``."""

    kind: Literal["tensor"]
    values: list[float]


class MapInput(TypedDict):
    """Key → content-ID map. Pairs with ``MapSchema``."""

    kind: Literal["map"]
    entries: dict[str, str]


class TreeInput(TypedDict):
    """Labeled ordered tree. Pairs with ``TreeSchema``."""

    kind: Literal["tree"]
    root: TreeNode


#: Union of all input container types — the second and third argument of
#: :func:`diff_by_schema`.
DiffInput = SequenceInput | SetInput | TensorInput | MapInput | TreeInput


# ---------------------------------------------------------------------------
# Schema-driven dispatch
# ---------------------------------------------------------------------------


def diff_by_schema(
    schema: ElementSchema,
    base: DiffInput,
    target: DiffInput,
    *,
    domain: str,
    address: str = "",
) -> StructuredDelta:
    """Select and invoke the correct diff algorithm based on ``schema["kind"]``.

    ``base`` and ``target`` must carry matching ``kind`` tags — a
    ``SequenceInput`` must pair with a ``SequenceSchema``, etc.
    A ``TypeError`` is raised at runtime if the tags do not match.

    Args:
        schema:  An ``ElementSchema`` TypedDict (``SequenceSchema``,
                 ``TreeSchema``, ``TensorSchema``, ``SetSchema``, or
                 ``MapSchema``).
        base:    Input data for the base (ancestor) state.
        target:  Input data for the target (newer) state.
        domain:  Domain tag propagated into the returned ``StructuredDelta``.
        address: Address prefix for generated op entries (e.g. file path).

    Returns:
        A ``StructuredDelta`` produced by the algorithm matching
        ``schema["kind"]``.

    Raises:
        TypeError: When ``base["kind"]`` or ``target["kind"]`` does not match
                   ``schema["kind"]``.
    """
    if schema["kind"] == "sequence":
        if base["kind"] != "sequence":
            raise TypeError(
                f"sequence schema requires SequenceInput, got {base['kind']!r}"
            )
        if target["kind"] != "sequence":
            raise TypeError(
                f"sequence schema requires SequenceInput, got {target['kind']!r}"
            )
        return _lcs.diff(
            schema, base["items"], target["items"], domain=domain, address=address
        )

    if schema["kind"] == "set":
        if base["kind"] != "set":
            raise TypeError(
                f"set schema requires SetInput, got {base['kind']!r}"
            )
        if target["kind"] != "set":
            raise TypeError(
                f"set schema requires SetInput, got {target['kind']!r}"
            )
        return _set_ops.diff(
            schema, base["items"], target["items"], domain=domain, address=address
        )

    if schema["kind"] == "tensor":
        if base["kind"] != "tensor":
            raise TypeError(
                f"tensor schema requires TensorInput, got {base['kind']!r}"
            )
        if target["kind"] != "tensor":
            raise TypeError(
                f"tensor schema requires TensorInput, got {target['kind']!r}"
            )
        return _numerical.diff(
            schema, base["values"], target["values"], domain=domain, address=address
        )

    if schema["kind"] == "tree":
        if base["kind"] != "tree":
            raise TypeError(
                f"tree schema requires TreeInput, got {base['kind']!r}"
            )
        if target["kind"] != "tree":
            raise TypeError(
                f"tree schema requires TreeInput, got {target['kind']!r}"
            )
        return _tree_edit.diff(
            schema, base["root"], target["root"], domain=domain, address=address
        )

    if schema["kind"] == "map":
        if base["kind"] != "map":
            raise TypeError(
                f"map schema requires MapInput, got {base['kind']!r}"
            )
        if target["kind"] != "map":
            raise TypeError(
                f"map schema requires MapInput, got {target['kind']!r}"
            )
        return _diff_map(
            schema, base["entries"], target["entries"], domain=domain, address=address
        )

    # Exhaustiveness guard — schema["kind"] is a closed Literal union.
    raise TypeError(f"Unknown schema kind: {schema['kind']!r}")


# ---------------------------------------------------------------------------
# Map diff (internal — not a separate module because it's simple enough)
# ---------------------------------------------------------------------------


def _diff_map(
    schema: MapSchema,
    base: dict[str, str],
    target: dict[str, str],
    *,
    domain: str,
    address: str,
) -> StructuredDelta:
    """Key-level diff of two ``dict[str, str]`` (key → content ID) maps.

    Produces ``InsertOp`` for new keys, ``DeleteOp`` for removed keys, and
    ``ReplaceOp`` for keys whose content ID changed. The ``value_schema`` on
    the ``MapSchema`` is informational; deep value diffing is a
    future enhancement.
    """
    base_keys = set(base)
    target_keys = set(target)
    key_type = schema["key_type"]

    ops: list[DomainOp] = []

    for key in sorted(target_keys - base_keys):
        key_addr = f"{address}/{key}" if address else key
        ops.append(
            InsertOp(
                op="insert",
                address=key_addr,
                position=None,
                content_id=target[key],
                content_summary=f"{key_type} {key!r} added",
            )
        )

    for key in sorted(base_keys - target_keys):
        key_addr = f"{address}/{key}" if address else key
        ops.append(
            DeleteOp(
                op="delete",
                address=key_addr,
                position=None,
                content_id=base[key],
                content_summary=f"{key_type} {key!r} removed",
            )
        )

    for key in sorted(base_keys & target_keys):
        if base[key] != target[key]:
            key_addr = f"{address}/{key}" if address else key
            ops.append(
                ReplaceOp(
                    op="replace",
                    address=key_addr,
                    position=None,
                    old_content_id=base[key],
                    new_content_id=target[key],
                    old_summary=f"{key_type} {key!r} (prev)",
                    new_summary=f"{key_type} {key!r} (new)",
                )
            )

    n_add = sum(1 for op in ops if op["op"] == "insert")
    n_del = sum(1 for op in ops if op["op"] == "delete")
    n_mod = sum(1 for op in ops if op["op"] == "replace")
    parts: list[str] = []
    if n_add:
        parts.append(f"{n_add} added")
    if n_del:
        parts.append(f"{n_del} removed")
    if n_mod:
        parts.append(f"{n_mod} modified")
    summary = ", ".join(parts) if parts else "no changes"

    return StructuredDelta(domain=domain, ops=ops, summary=summary)


# ---------------------------------------------------------------------------
# Schema-driven snapshot diff — the "auto diff" for new plugin authors
# ---------------------------------------------------------------------------


def snapshot_diff(
    schema: DomainSchema,
    base: SnapshotManifest,
    target: SnapshotManifest,
) -> StructuredDelta:
    """Compute a ``StructuredDelta`` from two snapshots using the declared schema.

    This is the **"free diff"** promised by Phase 2 of the supercharge plan:
    a plugin author who declares a ``DomainSchema`` via ``schema()`` can call
    this function from their ``diff()`` implementation instead of writing
    file-set algebra from scratch.  The core engine dispatches to the correct
    algorithm based on the schema's ``top_level`` kind.

    The function treats the ``SnapshotManifest.files`` dict as a
    ``{path: content_hash}`` map and produces ``InsertOp``, ``DeleteOp``, and
    ``ReplaceOp`` entries per file path.  This gives correct file-level diffs
    for any domain whose state is a collection of files.

    For sub-file granularity (e.g. MIDI note-level diffs), plugins must provide
    their own ``diff()`` implementation — there is no general sub-file algorithm
    because the binary format is domain-specific.  The MIDI plugin uses this
    approach, delegating file-level ops to ``snapshot_diff`` and adding
    ``PatchOp`` entries for changed MIDI files on top.

    Args:
        schema:  The plugin's declared ``DomainSchema`` (from ``plugin.schema()``).
        base:    Snapshot of the earlier state (e.g. HEAD).
        target:  Snapshot of the later state (e.g. working tree).

    Returns:
        A ``StructuredDelta`` with ``InsertOp`` / ``DeleteOp`` / ``ReplaceOp``
        entries describing every file-level change.  The ``domain`` field is
        taken from ``schema["domain"]``.

    Example::

        class MyPlugin:
            def schema(self) -> DomainSchema:
                return DomainSchema(domain="myplugin", ...)

            def diff(self, base, target, *, repo_root=None) -> StateDelta:
                from muse.core.diff_algorithms import snapshot_diff
                return snapshot_diff(self.schema(), base, target)
    """
    domain = schema["domain"]
    # Represent the file collection as a key→content_hash map and dispatch
    # through diff_by_schema using a MapSchema.  _diff_map produces the correct
    # InsertOp / DeleteOp / ReplaceOp per path without needing to know the
    # actual file format.
    map_schema = MapSchema(
        kind="map",
        key_type="file_path",
        value_schema=SetSchema(
            kind="set",
            element_type="content_hash",
            identity="by_content",
        ),
        identity="by_key",
    )
    base_input = MapInput(kind="map", entries=dict(base["files"]))
    target_input = MapInput(kind="map", entries=dict(target["files"]))
    return diff_by_schema(map_schema, base_input, target_input, domain=domain)

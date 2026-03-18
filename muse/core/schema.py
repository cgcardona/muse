"""Domain schema declaration types — Phase 2.

A plugin implements :meth:`~muse.domain.MuseDomainPlugin.schema` returning a
:class:`DomainSchema` to declare the structural shape of its data. The core
engine uses this declaration to:

1. Select the correct diff algorithm for each dimension via
   :func:`~muse.core.diff_algorithms.diff_by_schema`.
2. Provide informed conflict messages (citing dimension names) in Phase 3.
3. Route to CRDT merge when ``merge_mode`` is ``"crdt"`` in Phase 4.

Every schema type is a ``TypedDict`` — JSON-serialisable, zero-``Any``, and
verifiable by mypy in strict mode.

Design note on ``MapSchema.value_schema``
-----------------------------------------
``MapSchema.value_schema`` carries the type ``ElementSchema``, which is
defined *after* ``MapSchema`` in this file. With ``from __future__ import
annotations`` all annotations are evaluated lazily, so this forward reference
is resolved correctly by both the Python runtime and mypy.
"""
from __future__ import annotations

from typing import Literal, TypedDict


# ---------------------------------------------------------------------------
# Element schema types — one per structural primitive
# ---------------------------------------------------------------------------


class SequenceSchema(TypedDict):
    """Ordered sequence of homogeneous elements (LCS-diffable).

    Use for any domain data that is fundamentally a list: note events in a
    MIDI track, nucleotides in a DNA strand, frames in an animation.

    ``diff_algorithm`` selects the variant of LCS:
    - ``"lcs"`` — classic O(nm) LCS, minimal insertions and deletions.
    - ``"myers"`` — O(nd) Myers algorithm, same semantics, faster for low
      edit distance (this is what Git uses).
    - ``"patience"`` — patience-sort variant, produces more human-readable
      diffs for sequences with many repeated elements.
    """

    kind: Literal["sequence"]
    element_type: str
    identity: Literal["by_id", "by_position", "by_content"]
    diff_algorithm: Literal["lcs", "myers", "patience"]
    alphabet: list[str] | None


class TreeSchema(TypedDict):
    """Hierarchical labeled ordered tree (tree-edit-diffable).

    Use for domain data with parent-child relationships: scene graphs, XML /
    AST nodes, track hierarchies in a DAW.

    ``diff_algorithm`` selects the tree edit algorithm:
    - ``"zhang_shasha"`` — Zhang-Shasha 1989 O(n²m) minimum edit distance.
    - ``"gumtree"`` — GumTree heuristic, better for large ASTs.
    """

    kind: Literal["tree"]
    node_type: str
    diff_algorithm: Literal["zhang_shasha", "gumtree"]


class TensorSchema(TypedDict):
    """N-dimensional numerical array (sparse-numerical-diffable).

    Use for simulation state, velocity curves, weight matrices, voxel grids.
    Floating-point drift below ``epsilon`` is *not* considered a change.

    ``diff_mode`` controls the output granularity:
    - ``"sparse"``  — one ``ReplaceOp`` per changed element.
    - ``"block"``   — groups adjacent changes into contiguous range ops.
    - ``"full"``    — one ``ReplaceOp`` for the entire array if anything changed.
    """

    kind: Literal["tensor"]
    dtype: Literal["float32", "float64", "int8", "int16", "int32", "int64"]
    rank: int
    epsilon: float
    diff_mode: Literal["sparse", "block", "full"]


class SetSchema(TypedDict):
    """Unordered collection of unique elements (set-algebra-diffable).

    Use for collections where order is irrelevant: a set of files, a set of
    annotations, a set of material IDs in a 3D scene.

    ``identity`` determines what makes two elements "the same":
    - ``"by_content"`` — SHA-256 of content (structural equality).
    - ``"by_id"``      — stable element ID (e.g. UUID).
    """

    kind: Literal["set"]
    element_type: str
    identity: Literal["by_content", "by_id"]


class MapSchema(TypedDict):
    """Key-value map with known or dynamic keys.

    Use for dictionaries where both key and value structure matter: a map of
    chromosome name → nucleotide sequence, or annotation key → quality scores.

    ``value_schema`` is itself an ``ElementSchema``, allowing recursive
    declarations (e.g. a map of sequences, a map of trees).
    """

    kind: Literal["map"]
    key_type: str
    value_schema: ElementSchema  # forward reference — resolved lazily
    identity: Literal["by_key"]


#: Union of all element schema types.
#: This is the type of ``DimensionSpec.schema`` and ``DomainSchema.top_level``.
ElementSchema = SequenceSchema | TreeSchema | TensorSchema | MapSchema | SetSchema


# ---------------------------------------------------------------------------
# Dimension spec — a named semantic sub-dimension
# ---------------------------------------------------------------------------


class DimensionSpec(TypedDict):
    """A named semantic sub-dimension of the domain's state.

    Domains are multi-dimensional. Music has melodic, harmonic, dynamic, and
    structural dimensions. Genomics has coding regions, regulatory elements,
    and metadata dimensions. 3D spatial design has geometry, materials,
    lighting, and animation dimensions.

    Each dimension can use a different element schema and diff algorithm.
    The merge engine (Phase 3) merges independent dimensions in parallel
    without blocking on each other.

    ``independent_merge`` — when ``True``, a conflict in this dimension does
    not block merging other dimensions. When ``False`` (e.g. structural changes
    in a DAW session), all dimensions must wait for this one to resolve.
    """

    name: str
    description: str
    schema: ElementSchema
    independent_merge: bool


# ---------------------------------------------------------------------------
# Top-level domain schema
# ---------------------------------------------------------------------------


class DomainSchema(TypedDict):
    """Complete structural declaration for a domain plugin.

    Returned by :meth:`~muse.domain.MuseDomainPlugin.schema`. The core engine
    reads this once at plugin registration time.

    ``top_level`` declares the primary collection structure (e.g. a set of
    files for music, a map of chromosome sequences for genomics).

    ``dimensions`` declares the semantic sub-dimensions. The merge engine
    (Phase 3) uses these to determine which changes can be merged independently.

    ``merge_mode`` controls the merge strategy:
    - ``"three_way"`` — standard three-way merge (Phases 1–3).
    - ``"crdt"``      — convergent CRDT join (Phase 4).

    ``schema_version`` tracks the schema format for future migrations.
    It is always ``1`` for Phase 2.
    """

    domain: str
    description: str
    dimensions: list[DimensionSpec]
    top_level: ElementSchema
    merge_mode: Literal["three_way", "crdt"]
    schema_version: Literal[1]

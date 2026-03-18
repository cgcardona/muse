"""MuseDomainPlugin — the five-interface protocol that defines a Muse domain.

Muse provides the DAG engine, content-addressed object store, branching,
lineage walking, topological log graph, and merge base finder. A domain plugin
implements these five interfaces and Muse does the rest.

The music plugin (``muse.plugins.music``) is the reference implementation.
Every other domain — scientific simulation, genomics, 3D spatial design,
spacetime — is a new plugin.

Phase 1 — Typed Delta Algebra
------------------------------
``StateDelta`` is now a ``StructuredDelta`` carrying a typed operation list
rather than the old opaque ``{added, removed, modified}`` path lists. Each
operation knows its kind (insert / delete / move / replace / patch), the
address it touched, and a content-addressed ID for the before/after content.

This replaces ``DeltaManifest`` entirely. Plugins that previously returned
``DeltaManifest`` must now return ``StructuredDelta``.
"""
from __future__ import annotations

import pathlib
from dataclasses import dataclass, field
from typing import Literal, Protocol, TypedDict, runtime_checkable


# ---------------------------------------------------------------------------
# Snapshot types (unchanged from pre-Phase-1)
# ---------------------------------------------------------------------------


class SnapshotManifest(TypedDict):
    """Content-addressed snapshot of domain state.

    ``files`` maps workspace-relative POSIX paths to their SHA-256 content
    digests. ``domain`` identifies which plugin produced this snapshot.
    """

    files: dict[str, str]
    domain: str


# ---------------------------------------------------------------------------
# Typed delta algebra — Phase 1
# ---------------------------------------------------------------------------

#: A domain-specific address identifying a location within the state graph.
#: For file-level ops this is a workspace-relative POSIX path.
#: For sub-file ops this is a domain-specific coordinate (e.g. "note:42").
DomainAddress = str


class InsertOp(TypedDict):
    """An element was inserted into a collection.

    For ordered sequences ``position`` is the integer index at which the
    element was inserted. For unordered sets ``position`` is ``None``.
    ``content_id`` is the SHA-256 of the inserted content — either a blob
    already in the object store (for file-level ops) or a deterministic hash
    of the element's canonical serialisation (for sub-file ops).
    """

    op: Literal["insert"]
    address: DomainAddress
    position: int | None
    content_id: str
    content_summary: str


class DeleteOp(TypedDict):
    """An element was removed from a collection."""

    op: Literal["delete"]
    address: DomainAddress
    position: int | None
    content_id: str
    content_summary: str


class MoveOp(TypedDict):
    """An element was repositioned within an ordered sequence."""

    op: Literal["move"]
    address: DomainAddress
    from_position: int
    to_position: int
    content_id: str


class ReplaceOp(TypedDict):
    """An element's value changed (atomic, leaf-level replacement)."""

    op: Literal["replace"]
    address: DomainAddress
    position: int | None
    old_content_id: str
    new_content_id: str
    old_summary: str
    new_summary: str


#: The four non-recursive (leaf) operation types.
LeafDomainOp = InsertOp | DeleteOp | MoveOp | ReplaceOp


class PatchOp(TypedDict):
    """A container element was internally modified.

    ``address`` names the container (e.g. a file path). ``child_ops`` lists
    the sub-element changes inside that container. In Phase 1 these are always
    leaf ops. Phase 3 will introduce true recursion via a nested
    ``StructuredDelta`` when the operation-level merge engine requires it.

    ``child_domain`` identifies the sub-element domain (e.g. ``"midi_notes"``
    for note-level ops inside a ``.mid`` file). ``child_summary`` is a
    human-readable description of the child changes for ``muse show``.
    """

    op: Literal["patch"]
    address: DomainAddress
    child_ops: list[DomainOp]
    child_domain: str
    child_summary: str


#: Union of all operation types — the atoms of a ``StructuredDelta``.
DomainOp = LeafDomainOp | PatchOp


class StructuredDelta(TypedDict):
    """Rich, composable delta between two domain snapshots.

    ``ops`` is an ordered list of operations that transforms ``base`` into
    ``target`` when applied in sequence. The core engine stores this alongside
    commit records so that ``muse show`` and ``muse diff`` can display it
    without reloading full blobs.

    ``summary`` is a precomputed human-readable string — for example
    ``"3 notes added, 1 note removed"``. Plugins compute it because only they
    understand their domain semantics.
    """

    domain: str
    ops: list[DomainOp]
    summary: str


# ---------------------------------------------------------------------------
# Type aliases used in the protocol signatures
# ---------------------------------------------------------------------------

#: Live state is either an already-snapshotted manifest dict or a workdir path.
#: The music plugin accepts both: a Path (for CLI commit/status) and a
#: SnapshotManifest dict (for in-memory merge and diff operations).
LiveState = SnapshotManifest | pathlib.Path

#: A content-addressed, immutable snapshot of state at a point in time.
StateSnapshot = SnapshotManifest

#: The minimal change between two snapshots — a list of typed domain operations.
StateDelta = StructuredDelta


# ---------------------------------------------------------------------------
# Merge and drift result types
# ---------------------------------------------------------------------------


@dataclass
class MergeResult:
    """Outcome of a three-way merge between two divergent state lines.

    ``merged`` is the reconciled snapshot. ``conflicts`` is a list of
    workspace-relative file paths that could not be auto-merged and require
    manual resolution. An empty ``conflicts`` list means the merge was clean.
    The CLI is responsible for formatting user-facing messages from these paths.

    ``applied_strategies`` maps each path where a ``.museattributes`` rule
    overrode the default conflict behaviour to the strategy that was applied.

    ``dimension_reports`` maps conflicting paths to their per-dimension
    resolution detail.

    ``op_log`` is the ordered list of ``DomainOp`` entries applied to produce
    the merged snapshot. Empty for file-level merges; populated by plugins
    that implement operation-level merge (Phase 3).
    """

    merged: StateSnapshot
    conflicts: list[str] = field(default_factory=list)
    applied_strategies: dict[str, str] = field(default_factory=dict)
    dimension_reports: dict[str, dict[str, str]] = field(default_factory=dict)
    op_log: list[DomainOp] = field(default_factory=list)

    @property
    def is_clean(self) -> bool:
        return len(self.conflicts) == 0


@dataclass
class DriftReport:
    """Gap between committed state and current live state.

    ``has_drift`` is ``True`` when the live state differs from the committed
    snapshot. ``summary`` is a human-readable description of what changed.
    ``delta`` is the machine-readable structured delta for programmatic consumers.
    """

    has_drift: bool
    summary: str = ""
    delta: StateDelta = field(default_factory=lambda: StructuredDelta(
        domain="", ops=[], summary="working tree clean",
    ))


# ---------------------------------------------------------------------------
# The plugin protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class MuseDomainPlugin(Protocol):
    """The five interfaces a domain plugin must implement.

    Muse provides everything else: the DAG, branching, checkout, lineage
    walking, ASCII log graph, and merge base finder. Implement these five
    methods and your domain gets the full Muse VCS for free.

    Music is the reference implementation (``muse.plugins.music``).
    """

    def snapshot(self, live_state: LiveState) -> StateSnapshot:
        """Capture current live state as a serialisable, hashable snapshot.

        The returned ``SnapshotManifest`` must be JSON-serialisable. Muse will
        compute a SHA-256 content address from the canonical JSON form and
        store the snapshot as a blob in ``.muse/objects/``.

        **``.museignore`` contract** — when *live_state* is a
        ``pathlib.Path`` (the ``muse-work/`` directory), domain plugin
        implementations **must** honour ``.museignore`` by calling
        :func:`muse.core.ignore.load_patterns` on the repository root and
        filtering out paths matched by :func:`muse.core.ignore.is_ignored`.
        """
        ...

    def diff(
        self,
        base: StateSnapshot,
        target: StateSnapshot,
        *,
        repo_root: pathlib.Path | None = None,
    ) -> StateDelta:
        """Compute the structured delta between two snapshots.

        Returns a ``StructuredDelta`` where ``ops`` is a minimal list of
        typed operations that transforms ``base`` into ``target``. Plugins
        should:

        1. Compute ops at the finest granularity they can interpret.
        2. Assign meaningful ``content_summary`` strings to each op.
        3. When ``repo_root`` is provided, load sub-file content from the
           object store and produce ``PatchOp`` entries with note/element-level
           ``child_ops`` instead of coarse ``ReplaceOp`` entries.
        4. Compute a human-readable ``summary`` across all ops.

        The core engine stores this delta alongside the commit record so that
        ``muse show`` and ``muse diff`` can display it without reloading blobs.
        """
        ...

    def merge(
        self,
        base: StateSnapshot,
        left: StateSnapshot,
        right: StateSnapshot,
        *,
        repo_root: pathlib.Path | None = None,
    ) -> MergeResult:
        """Three-way merge two divergent state lines against a common base.

        ``base`` is the common ancestor (merge base). ``left`` and ``right``
        are the two divergent snapshots. Returns a ``MergeResult`` with the
        reconciled snapshot and any unresolvable conflicts.

        **``.museattributes`` and multidimensional merge contract** — when
        *repo_root* is provided, domain plugin implementations should:

        1. Load ``.museattributes`` via
           :func:`muse.core.attributes.load_attributes`.
        2. For each conflicting path, call
           :func:`muse.core.attributes.resolve_strategy` with the relevant
           dimension name (or ``"*"`` for file-level resolution).
        3. Apply the returned strategy:

           - ``"ours"`` — take the *left* version; remove from conflict list.
           - ``"theirs"`` — take the *right* version; remove from conflict list.
           - ``"manual"`` — force into conflict list even if the engine would
             auto-resolve.
           - ``"auto"`` / ``"union"`` — defer to the engine's default logic.

        4. For domain formats that support true multidimensional content (e.g.
           MIDI: melodic, rhythmic, harmonic, dynamic, structural), attempt
           sub-file dimension merge before falling back to a file-level conflict.
        """
        ...

    def drift(
        self,
        committed: StateSnapshot,
        live: LiveState,
    ) -> DriftReport:
        """Compare committed state against current live state.

        Used by ``muse status`` to detect uncommitted changes. Returns a
        ``DriftReport`` describing whether the live state has diverged from
        the last committed snapshot and, if so, by how much.
        """
        ...

    def apply(self, delta: StateDelta, live_state: LiveState) -> LiveState:
        """Apply a delta to produce a new live state.

        Used by ``muse checkout`` to reconstruct a historical state. Applies
        ``delta`` on top of ``live_state`` and returns the resulting state.

        For ``InsertOp`` and ``ReplaceOp``, the new content is identified by
        ``content_id`` (a SHA-256 hash). When ``live_state`` is a
        ``pathlib.Path``, the plugin reads the content from the object store.
        When ``live_state`` is a ``SnapshotManifest``, only ``DeleteOp`` and
        ``ReplaceOp`` at the file level can be applied in-memory.
        """
        ...

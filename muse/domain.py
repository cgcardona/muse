"""MuseDomainPlugin — the six-interface protocol that defines a Muse domain.

Muse provides the DAG engine, content-addressed object store, branching,
lineage walking, topological log graph, and merge base finder. A domain plugin
implements these six interfaces and Muse does the rest.

The music plugin (``muse.plugins.music``) is the reference implementation.
Every other domain — scientific simulation, genomics, 3D spatial design,
spacetime — is a new plugin.

Typed Delta Algebra
-------------------
``StateDelta`` is a ``StructuredDelta`` carrying a typed operation list rather
than an opaque path list. Each operation knows its kind (insert / delete /
move / replace / patch), the address it touched, and a content-addressed ID
for the before/after content.

Domain Schema
-------------
``schema()`` is the sixth protocol method. Plugins return a ``DomainSchema``
declaring their data structure. The core engine uses this declaration to drive
diff algorithm selection via :func:`~muse.core.diff_algorithms.diff_by_schema`.

Operational Transformation Merge
---------------------------------
Plugins may optionally implement :class:`StructuredMergePlugin`, a sub-protocol
that adds ``merge_ops()``. When both branches have produced ``StructuredDelta``
from ``diff()``, the merge engine checks
``isinstance(plugin, StructuredMergePlugin)`` and calls ``merge_ops()`` for
fine-grained, operation-level conflict detection. Non-supporting plugins fall
back to the existing file-level ``merge()`` path.

CRDT Convergent Merge
---------------------
Plugins may optionally implement :class:`CRDTPlugin`, a sub-protocol that
replaces ``merge()`` with ``join()``.  ``join`` always succeeds — no conflict
state ever exists.  Given any two :class:`CRDTSnapshotManifest` values,
``join`` produces a deterministic merged result regardless of message delivery
order.

The core engine detects ``CRDTPlugin`` via ``isinstance`` at merge time.
``DomainSchema.merge_mode == "crdt"`` signals that the CRDT path should be
taken.
"""
from __future__ import annotations

import pathlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, Protocol, TypedDict, runtime_checkable

# Public re-exports so callers can do ``from muse.domain import MutateOp`` etc.
__all__ = [
    "SnapshotManifest",
    "DomainAddress",
    "InsertOp",
    "DeleteOp",
    "MoveOp",
    "ReplaceOp",
    "FieldMutation",
    "MutateOp",
    "EntityProvenance",
    "LeafDomainOp",
    "PatchOp",
    "DomainOp",
    "SemVerBump",
    "StructuredDelta",
    "infer_sem_ver_bump",
    "LiveState",
    "StateSnapshot",
    "StateDelta",
    "ConflictRecord",
    "MergeResult",
    "DriftReport",
    "MuseDomainPlugin",
    "StructuredMergePlugin",
    "CRDTSnapshotManifest",
    "CRDTPlugin",
]

if TYPE_CHECKING:
    from muse.core.schema import CRDTDimensionSpec, DomainSchema


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
# Typed delta algebra
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
    """An element was removed from a collection.

    ``position`` is the integer index that was removed for ordered sequences,
    or ``None`` for unordered sets.  ``content_id`` is the SHA-256 of the
    deleted content so that the operation can be applied idempotently (already-
    absent elements can be skipped).  ``content_summary`` is the human-readable
    description of what was removed, for ``muse show``.
    """

    op: Literal["delete"]
    address: DomainAddress
    position: int | None
    content_id: str
    content_summary: str


class MoveOp(TypedDict):
    """An element was repositioned within an ordered sequence.

    ``from_position`` is the source index (in the pre-move sequence) and
    ``to_position`` is the destination index (in the post-move sequence).
    Both are mandatory — moves are only meaningful in ordered collections.
    ``content_id`` identifies the element being moved so that the operation
    can be validated during replay.
    """

    op: Literal["move"]
    address: DomainAddress
    from_position: int
    to_position: int
    content_id: str


class ReplaceOp(TypedDict):
    """An element's value changed (atomic, leaf-level replacement).

    ``old_content_id`` and ``new_content_id`` are SHA-256 hashes of the
    before- and after-content.  They enable three-way merge engines to detect
    concurrent conflicting modifications (both changed from the same
    ``old_content_id`` to different ``new_content_id`` values).
    ``old_summary`` and ``new_summary`` are human-readable strings for display,
    analogous to ``content_summary`` on :class:`InsertOp`.
    ``position`` is the index within the container (``None`` for unordered).
    """

    op: Literal["replace"]
    address: DomainAddress
    position: int | None
    old_content_id: str
    new_content_id: str
    old_summary: str
    new_summary: str


class FieldMutation(TypedDict):
    """The string-serialised before/after of a single field in a :class:`MutateOp`.

    Values are always strings so that typed primitives (int, float, bool) can
    be compared uniformly without carrying domain-specific type information in
    the generic delta algebra.  Plugins format them according to their domain
    conventions (e.g. ``"80"`` for a MIDI velocity, ``"C4"`` for a pitch name).
    """

    old: str
    new: str


class MutateOp(TypedDict):
    """A named entity's specific fields were updated.

    Unlike :class:`ReplaceOp` — which replaces an entire element atomically —
    ``MutateOp`` records *which* specific fields of a domain entity changed.
    This enables mutation tracking for domains that maintain stable entity
    identity separate from content equality.

    Example: a MIDI note's velocity changed from 80 to 100.  Under a pure
    content-hash model that becomes ``DeleteOp + InsertOp`` (two different
    content hashes).  With ``MutateOp`` and a stable ``entity_id`` the diff
    reports "velocity 80→100 on entity C4@bar4" — lineage is preserved.

    ``entity_id``
        Stable identifier for the mutated entity, assigned at first insertion
        and reused across all subsequent mutations (regardless of content
        changes).
    ``fields``
        Mapping from field name (e.g. ``"velocity"``, ``"start_tick"``) to a
        :class:`FieldMutation` recording the serialised old and new values.
    ``old_content_id`` / ``new_content_id``
        SHA-256 of the full element state before and after the mutation,
        enabling three-way merge conflict detection identical to
        :class:`ReplaceOp`.
    ``position``
        Index within the containing ordered sequence (``None`` for unordered).
    """

    op: Literal["mutate"]
    address: DomainAddress
    entity_id: str
    old_content_id: str
    new_content_id: str
    fields: dict[str, FieldMutation]
    old_summary: str
    new_summary: str
    position: int | None


class EntityProvenance(TypedDict, total=False):
    """Causal metadata attached to ops that create or modify tracked entities.

    All fields are optional (``total=False``) because entity tracking is an
    opt-in capability.  Plugins that implement stable entity identity populate
    these fields when constructing :class:`InsertOp`, :class:`MutateOp`, or
    :class:`DeleteOp` entries.  Consumers that do not understand entity
    provenance can safely ignore them.

    ``entity_id``
        Stable domain-specific identifier for the entity (e.g. a UUID assigned
        at the note's first insertion).
    ``origin_op_id``
        The ``op_id`` of the op that first created this entity.
    ``last_modified_op_id``
        The ``op_id`` of the most recent op that touched this entity.
    ``created_at_commit``
        Short-form commit ID where this entity was first introduced.
    ``actor_id``
        The agent or human identity that performed this op.
    """

    entity_id: str
    origin_op_id: str
    last_modified_op_id: str
    created_at_commit: str
    actor_id: str


#: The five non-recursive (leaf) operation types.
LeafDomainOp = InsertOp | DeleteOp | MoveOp | ReplaceOp | MutateOp


class PatchOp(TypedDict):
    """A container element was internally modified.

    ``address`` names the container (e.g. a file path). ``child_ops`` lists
    the sub-element changes inside that container. These are always
    leaf ops in the current implementation; true recursion via a nested
    ``StructuredDelta`` is reserved for a future release.

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
type DomainOp = LeafDomainOp | PatchOp


SemVerBump = Literal["major", "minor", "patch", "none"]
"""Semantic version impact of a delta.

``major``  Breaking change: public symbol deleted, renamed, or signature changed.
``minor``  Additive: new public symbol inserted.
``patch``  Implementation-only change: body changed, signature stable.
``none``   No semantic change (formatting, whitespace, metadata only).
"""


class StructuredDelta(TypedDict, total=False):
    """Rich, composable delta between two domain snapshots.

    ``ops`` is an ordered list of operations that transforms ``base`` into
    ``target`` when applied in sequence. The core engine stores this alongside
    commit records so that ``muse show`` and ``muse diff`` can display it
    without reloading full blobs.

    ``summary`` is a precomputed human-readable string — for example
    ``"3 notes added, 1 note removed"``. Plugins compute it because only they
    understand their domain semantics.

    ``sem_ver_bump`` (v2, optional) is the semantic version impact of this
    delta, computed by :func:`infer_sem_ver_bump`.  Absent for legacy records
    or non-code domains that do not compute it.

    ``breaking_changes`` (v2, optional) lists the symbol addresses whose
    public interface was removed or incompatibly changed.
    """

    domain: str
    ops: list[DomainOp]
    summary: str
    sem_ver_bump: SemVerBump
    breaking_changes: list[str]


# ---------------------------------------------------------------------------
# SemVer inference helper
# ---------------------------------------------------------------------------


def infer_sem_ver_bump(delta: "StructuredDelta") -> tuple[SemVerBump, list[str]]:
    """Infer the semantic version bump and breaking-change list from a delta.

    Reads the ``ops`` list and applies the following rules:

    * Any public symbol (name not starting with ``_``) that is deleted or
      renamed → **major** (breaking: callers will fail).
    * Any public symbol whose ``signature_id`` changed (signature_only or
      full_rewrite with new signature) → **major** (breaking: call-site
      compatibility broken).
    * Any public symbol inserted → **minor** (additive).
    * Any symbol whose only change is the body (``impl_only``) → **patch**.
    * No semantic ops → **none**.

    Returns:
        A ``(bump, breaking_changes)`` tuple where ``breaking_changes`` is a
        sorted list of symbol addresses whose public contract changed.

    This function is domain-agnostic; it relies on the op address format used
    by code plugins (``<file>::<symbol>``) and the ``new_summary`` / ``old_summary``
    conventions from :func:`~muse.plugins.code.symbol_diff.diff_symbol_trees`.
    For non-code domains the heuristics may not apply — plugins should override
    by setting ``sem_ver_bump`` directly when constructing the delta.
    """
    ops = delta.get("ops", [])
    bump: SemVerBump = "none"
    breaking: list[str] = []

    def _is_public(address: str) -> bool:
        """Return True if the innermost symbol name does not start with ``_``."""
        parts = address.split("::")
        name = parts[-1].split(".")[-1] if parts else ""
        return not name.startswith("_")

    def _promote(current: SemVerBump, candidate: SemVerBump) -> SemVerBump:
        order: list[SemVerBump] = ["none", "patch", "minor", "major"]
        return candidate if order.index(candidate) > order.index(current) else current

    for op in ops:
        op_type = op.get("op", "")
        address = str(op.get("address", ""))

        if op_type == "patch":
            # Recurse into child_ops.  We know op is a PatchOp here.
            if op["op"] == "patch":
                child_ops_raw: list[DomainOp] = op["child_ops"]
                sub_delta: StructuredDelta = {"domain": "", "ops": child_ops_raw, "summary": ""}
                sub_bump, sub_breaking = infer_sem_ver_bump(sub_delta)
                bump = _promote(bump, sub_bump)
                breaking.extend(sub_breaking)
            continue

        if not _is_public(address):
            continue

        if op_type == "delete":
            bump = _promote(bump, "major")
            breaking.append(address)

        elif op_type == "insert":
            bump = _promote(bump, "minor")

        elif op_type == "replace":
            new_summary: str = str(op.get("new_summary", ""))
            old_summary: str = str(op.get("old_summary", ""))
            if (
                new_summary.startswith("renamed to ")
                or "signature" in new_summary
                or "signature" in old_summary
            ):
                bump = _promote(bump, "major")
                breaking.append(address)
            elif "implementation" in new_summary or "implementation" in old_summary:
                bump = _promote(bump, "patch")
            else:
                bump = _promote(bump, "major")
                breaking.append(address)

    return bump, sorted(set(breaking))


# ---------------------------------------------------------------------------
# Type aliases used in the protocol signatures
# ---------------------------------------------------------------------------

#: Live state is either an already-snapshotted manifest dict or a workdir path.
#: The music plugin accepts both: a Path (for CLI commit/status) and a
#: SnapshotManifest dict (for in-memory merge and diff operations).
type LiveState = SnapshotManifest | pathlib.Path

#: A content-addressed, immutable snapshot of state at a point in time.
type StateSnapshot = SnapshotManifest

#: The minimal change between two snapshots — a list of typed domain operations.
type StateDelta = StructuredDelta


# ---------------------------------------------------------------------------
# Merge and drift result types
# ---------------------------------------------------------------------------


@dataclass
class ConflictRecord:
    """Structured conflict record in a merge result (v2 taxonomy).

    ``path``           The workspace-relative file path in conflict.
    ``conflict_type``  One of: ``symbol_edit_overlap``, ``rename_edit``,
                       ``move_edit``, ``delete_use``, ``dependency_conflict``,
                       ``file_level`` (legacy, no symbol info).
    ``ours_summary``   Short description of ours-side change.
    ``theirs_summary`` Short description of theirs-side change.
    ``addresses``      Symbol addresses involved (empty for file-level).
    """

    path: str
    conflict_type: str = "file_level"
    ours_summary: str = ""
    theirs_summary: str = ""
    addresses: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, str | list[str]]:
        return {
            "path": self.path,
            "conflict_type": self.conflict_type,
            "ours_summary": self.ours_summary,
            "theirs_summary": self.theirs_summary,
            "addresses": self.addresses,
        }


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
    that implement operation-level OT merge.

    ``conflict_records`` (v2) provides structured conflict metadata with a
    semantic taxonomy per conflicting path.  Populated by plugins that
    implement :class:`StructuredMergePlugin`.  May be empty even when
    ``conflicts`` is non-empty (legacy file-level conflict).
    """

    merged: StateSnapshot
    conflicts: list[str] = field(default_factory=list)
    applied_strategies: dict[str, str] = field(default_factory=dict)
    dimension_reports: dict[str, dict[str, str]] = field(default_factory=dict)
    op_log: list[DomainOp] = field(default_factory=list)
    conflict_records: list[ConflictRecord] = field(default_factory=list)

    @property
    def is_clean(self) -> bool:
        """``True`` when no unresolvable conflicts remain."""
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
    """The six interfaces a domain plugin must implement.

    Muse provides everything else: the DAG, branching, checkout, lineage
    walking, ASCII log graph, and merge base finder. Implement these six
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

    def schema(self) -> DomainSchema:
        """Declare the structural schema of this domain's state.

        The core engine calls this once at plugin registration time. Plugins
        must return a stable, deterministic :class:`~muse.core.schema.DomainSchema`
        describing:

        - ``top_level`` — the primary collection structure (e.g. a set of
          files, a map of chromosome names to sequences).
        - ``dimensions`` — the semantic sub-dimensions of state (e.g. melodic,
          harmonic, dynamic, structural for music).
        - ``merge_mode`` — ``"three_way"`` (OT merge) or ``"crdt"`` (CRDT convergent join).

        The schema drives :func:`~muse.core.diff_algorithms.diff_by_schema`
        algorithm selection and the OT merge engine's conflict detection.

        See :mod:`muse.core.schema` for all available element schema types.
        """
        ...


# ---------------------------------------------------------------------------
# Operational Transformation optional extension — structured (operation-level) merge
# ---------------------------------------------------------------------------


@runtime_checkable
class StructuredMergePlugin(MuseDomainPlugin, Protocol):
    """Optional extension for plugins that support operation-level merging.

    Plugins that implement this sub-protocol gain sub-file auto-merge: two
    agents inserting notes at non-overlapping bars never produce a conflict,
    because the merge engine reasons over ``DomainOp`` trees rather than file
    paths.

    The merge engine detects support at runtime via::

        isinstance(plugin, StructuredMergePlugin)

    Plugins that do not implement ``merge_ops`` fall back to the existing
    file-level ``merge()`` path automatically — no changes required.

    The :class:`~muse.plugins.music.plugin.MusicPlugin` is the reference
    implementation for OT-based merge.
    """

    def merge_ops(
        self,
        base: StateSnapshot,
        ours_snap: StateSnapshot,
        theirs_snap: StateSnapshot,
        ours_ops: list[DomainOp],
        theirs_ops: list[DomainOp],
        *,
        repo_root: pathlib.Path | None = None,
    ) -> MergeResult:
        """Merge two op lists against a common base using domain knowledge.

        The core merge engine calls this when both branches have produced
        ``StructuredDelta`` from ``diff()``. The plugin:

        1. Calls :func:`muse.core.op_transform.merge_op_lists` to detect
           conflicting ``DomainOp`` pairs.
        2. For clean pairs, builds the merged ``SnapshotManifest`` by applying
           the adjusted merged ops to *base*.  The plugin uses *ours_snap* and
           *theirs_snap* to look up the final content IDs for files touched only
           by one side (necessary for ``PatchOp`` entries, which do not carry a
           ``new_content_id`` directly).
        3. For conflicting pairs, consults ``.museattributes`` (when
           *repo_root* is provided) and either auto-resolves via the declared
           strategy or adds the address to ``MergeResult.conflicts``.

        Implementations must be domain-aware: a ``.museattributes`` rule of
        ``merge=ours`` should take this plugin's understanding of "ours" (the
        left branch content), not a raw file-level copy.

        Args:
            base:       Common ancestor snapshot.
            ours_snap:  Final snapshot of our branch.
            theirs_snap: Final snapshot of their branch.
            ours_ops:   Operations from our branch delta (base → ours).
            theirs_ops: Operations from their branch delta (base → theirs).
            repo_root:  Repository root for ``.museattributes`` lookup.

        Returns:
            A :class:`MergeResult` with the reconciled snapshot and any
            remaining unresolvable conflicts.
        """
        ...


# ---------------------------------------------------------------------------
# CRDT convergent merge — snapshot manifest and CRDTPlugin protocol
# ---------------------------------------------------------------------------


class CRDTSnapshotManifest(TypedDict):
    """Extended snapshot manifest for CRDT-mode plugins.

    Carries all the fields of a standard snapshot manifest plus CRDT-specific
    metadata.  The ``files`` mapping has the same semantics as
    :class:`SnapshotManifest` — path → content hash.  The additional fields
    persist CRDT state between commits.

    ``vclock`` records the causal state of the snapshot as a vector clock
    ``{agent_id: event_count}``.  It is used to detect concurrent writes and
    to resolve LWW tiebreaks when two agents write at the same logical time.

    ``crdt_state`` maps per-file-path CRDT state blobs to their SHA-256 hashes
    in the object store.  CRDT metadata (tombstones, RGA element IDs, OR-Set
    tokens) lives here, separate from content hashes, so the content-addressed
    store remains valid.

    ``schema_version`` is always ``1``.
    """

    files: dict[str, str]
    domain: str
    vclock: dict[str, int]
    crdt_state: dict[str, str]
    schema_version: Literal[1]


@runtime_checkable
class CRDTPlugin(MuseDomainPlugin, Protocol):
    """Optional extension for plugins that want convergent CRDT merge semantics.

    Plugins implementing this protocol replace the three-way ``merge()`` with
    a mathematical ``join()`` on a lattice.  ``join`` always succeeds:

    - **No conflict state ever exists.**
    - Any two replicas that have received the same set of writes converge to
      the same state, regardless of delivery order.
    - Millions of agents can write concurrently without coordination.

    The three lattice laws guaranteed by ``join``:

    1. **Commutativity**: ``join(a, b) == join(b, a)``
    2. **Associativity**: ``join(join(a, b), c) == join(a, join(b, c))``
    3. **Idempotency**: ``join(a, a) == a``

    The core engine detects support at runtime via::

        isinstance(plugin, CRDTPlugin)

    and routes to ``join`` when ``DomainSchema.merge_mode == "crdt"``.
    Plugins that do not implement ``CRDTPlugin`` fall back to the existing
    three-way ``merge()`` path.

    Implementation checklist for plugin authors
    -------------------------------------------
    1. Override ``schema()`` to return a :class:`~muse.core.schema.DomainSchema`
       with ``merge_mode="crdt"`` and :class:`~muse.core.schema.CRDTDimensionSpec`
       for each CRDT dimension.
    2. Implement ``crdt_schema()`` to declare which CRDT primitive maps to each
       dimension.
    3. Implement ``join(a, b)`` using the CRDT primitives in
       :mod:`muse.core.crdts`.
    4. Implement ``to_crdt_state(snapshot)`` to lift a plain snapshot into
       CRDT state.
    5. Implement ``from_crdt_state(crdt)`` to materialise a CRDT state back to
       a plain snapshot for ``muse show`` and CLI display.
    """

    def crdt_schema(self) -> list[CRDTDimensionSpec]:
        """Declare the CRDT type used for each dimension.

        Returns a list of :class:`~muse.core.schema.CRDTDimensionSpec` — one
        per dimension that uses CRDT semantics.  Dimensions not listed here
        fall back to three-way merge.

        Returns:
            List of CRDT dimension declarations.
        """
        ...

    def join(
        self,
        a: CRDTSnapshotManifest,
        b: CRDTSnapshotManifest,
    ) -> CRDTSnapshotManifest:
        """Merge two CRDT snapshots by computing their lattice join.

        This operation is:

        - Commutative: ``join(a, b) == join(b, a)``
        - Associative: ``join(join(a, b), c) == join(a, join(b, c))``
        - Idempotent: ``join(a, a) == a``

        These three properties guarantee convergence regardless of message
        order or delivery count.

        The implementation should use the CRDT primitives in
        :mod:`muse.core.crdts` (one primitive per declared CRDT dimension),
        compute the per-dimension joins, then rebuild the ``files`` manifest
        and ``vclock`` from the results.

        Args:
            a: First CRDT snapshot manifest.
            b: Second CRDT snapshot manifest.

        Returns:
            A new :class:`CRDTSnapshotManifest` that is the join of *a* and *b*.
        """
        ...

    def to_crdt_state(self, snapshot: StateSnapshot) -> CRDTSnapshotManifest:
        """Lift a plain snapshot into CRDT state representation.

        Called when importing a snapshot that was created before this plugin
        opted into CRDT mode.  The implementation should initialise fresh CRDT
        primitives from the snapshot content, with an empty vector clock.

        Args:
            snapshot: A plain :class:`StateSnapshot` to lift.

        Returns:
            A :class:`CRDTSnapshotManifest` with the same content and empty
            CRDT metadata (zero vector clock, empty ``crdt_state``).
        """
        ...

    def from_crdt_state(self, crdt: CRDTSnapshotManifest) -> StateSnapshot:
        """Materialise a CRDT state back to a plain snapshot.

        Used by ``muse show``, ``muse status``, and CLI commands that need a
        standard :class:`StateSnapshot` view of a CRDT-mode snapshot.

        Args:
            crdt: A :class:`CRDTSnapshotManifest` to materialise.

        Returns:
            A plain :class:`StateSnapshot` with the visible (non-tombstoned)
            content.
        """
        ...

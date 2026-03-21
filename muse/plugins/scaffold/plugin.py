"""Scaffold domain plugin — copy-paste template for a new Muse domain.

How to use this file
--------------------
1.  Copy this entire ``scaffold/`` directory:
        cp -r muse/plugins/scaffold muse/plugins/<your_domain>

2.  Rename ``ScaffoldPlugin`` to ``<YourDomain>Plugin`` throughout.

3.  Replace every ``raise NotImplementedError(...)`` with real implementation.
    Each method carries a detailed docstring explaining the contract.

4.  Register the plugin in ``muse/plugins/registry.py``:
        from muse.plugins.<your_domain>.plugin import <YourDomain>Plugin
        _REGISTRY["<your_domain>"] = <YourDomain>Plugin()

5.  Run ``muse init --domain <your_domain>`` in a project directory.

6.  All 14 ``muse`` CLI commands work immediately — no core changes needed.

See ``docs/guide/plugin-authoring-guide.md`` for the full walkthrough including
Domain Schema, OT merge, and CRDT convergent merge extensions.

Protocol capabilities implemented here
---------------------------------------
- Core: ``MuseDomainPlugin`` (required — 6 methods including ``schema()``)
- OT merge: ``StructuredMergePlugin`` (optional — remove if not needed)
- CRDT: ``CRDTPlugin`` (optional — remove if not needed)
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import stat as _stat

from muse._version import __version__
from muse.core.crdts import ORSet, VectorClock
from muse.core.diff_algorithms import snapshot_diff
from muse.core.op_transform import merge_op_lists
from muse.core.stat_cache import load_cache
from muse.core.schema import (
    CRDTDimensionSpec,
    DimensionSpec,
    DomainSchema,
    SequenceSchema,
    SetSchema,
)
from muse.domain import (
    CRDTSnapshotManifest,
    DomainOp,
    DriftReport,
    LiveState,
    MergeResult,
    SnapshotManifest,
    StateDelta,
    StateSnapshot,
    StructuredDelta,
)

# ---------------------------------------------------------------------------
# TODO: replace with your domain name and the file extension(s) you version.
# ---------------------------------------------------------------------------
_DOMAIN_NAME = "scaffold"
_FILE_GLOB = "*.scaffold"  # e.g. "*.mid" for music, "*.fasta" for genomics


class ScaffoldPlugin:
    """Scaffold implementation — replace every NotImplementedError with real code.

    This class satisfies all three optional protocol levels (Phases 2–4) via
    structural duck-typing — no explicit inheritance from the Protocol classes
    is needed or desired (see ``MidiPlugin`` for the reference example).

    If your domain only needs Phases 1–2, delete ``merge_ops`` and the four
    CRDT methods.

    See ``docs/guide/plugin-authoring-guide.md`` for detailed guidance.
    """

    # ------------------------------------------------------------------
    # MuseDomainPlugin — required core protocol
    # ------------------------------------------------------------------

    def snapshot(self, live_state: LiveState) -> StateSnapshot:
        """Capture the current working tree as a content-addressed manifest.

        Walk every domain file under ``live_state`` and hash its raw bytes with
        SHA-256.  Paths matched by ``.museignore`` are excluded before hashing.
        Returns a ``SnapshotManifest`` with ``files`` and ``domain``.

        Args:
            live_state: Either a ``pathlib.Path`` pointing to the working tree
                directory, or a ``SnapshotManifest`` dict for in-memory use.

        Returns:
            A ``SnapshotManifest`` mapping workspace-relative POSIX paths to
            their SHA-256 content digests.

        Note:
            ``.museignore`` contract — ``.museignore`` lives in the repository
            root (the working tree root).  Global patterns and patterns
            under ``[domain.<name>]`` matching this plugin's domain are applied.
        """
        if isinstance(live_state, pathlib.Path):
            from muse.core.ignore import is_ignored, load_ignore_config, resolve_patterns

            workdir = live_state
            patterns = resolve_patterns(load_ignore_config(workdir), _DOMAIN_NAME)
            cache = load_cache(workdir)
            files: dict[str, str] = {}
            root_str = str(workdir)
            prefix_len = len(root_str) + 1

            for dirpath, dirnames, filenames in os.walk(root_str, followlinks=False):
                dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
                for fname in sorted(filenames):
                    if fname.startswith("."):
                        continue
                    abs_str = os.path.join(dirpath, fname)
                    try:
                        st = os.lstat(abs_str)
                    except OSError:
                        continue
                    if not _stat.S_ISREG(st.st_mode):
                        continue
                    rel = abs_str[prefix_len:]
                    if os.sep != "/":
                        rel = rel.replace(os.sep, "/")
                    if is_ignored(rel, patterns):
                        continue
                    files[rel] = cache.get_cached(rel, abs_str, st.st_mtime, st.st_size)

            cache.prune(set(files))
            cache.save()
            return SnapshotManifest(files=files, domain=_DOMAIN_NAME)

        # SnapshotManifest dict path — used by merge / diff in memory
        return live_state

    def diff(
        self,
        base: StateSnapshot,
        target: StateSnapshot,
        *,
        repo_root: pathlib.Path | None = None,
    ) -> StateDelta:
        """Compute the typed operation list between two snapshots.

        For a file-level implementation this is set algebra on the ``files``
        dict:  paths in target but not base → ``InsertOp``, paths in base but
        not target → ``DeleteOp``, paths in both with different hashes →
        ``ReplaceOp``.

        For sub-file granularity (Phases 2–3), parse each file and diff its
        internal elements using ``diff_by_schema()`` from
        ``muse.core.diff_algorithms``.

        Args:
            base:   Snapshot of the earlier state (e.g. HEAD).
            target: Snapshot of the later state (e.g. working tree).

        Returns:
            A ``StructuredDelta`` whose ``ops`` list describes every change.
        """
        # snapshot_diff provides the "auto diff" promised by Phase 2: any plugin
        # that declares a DomainSchema can call this instead of writing file-set
        # algebra from scratch.  For sub-file granularity, build PatchOps on top.
        return snapshot_diff(self.schema(), base, target)

    def merge(
        self,
        base: StateSnapshot,
        left: StateSnapshot,
        right: StateSnapshot,
        *,
        repo_root: pathlib.Path | None = None,
    ) -> MergeResult:
        """Three-way merge at file granularity (fallback for cherry-pick etc.).

        Implements standard three-way logic:
        - left and right agree → use the consensus
        - only one side changed → take that side
        - both sides changed differently → conflict

        If you implement OT merge (``merge_ops``), this method is only called
        for ``muse cherry-pick`` and other non-OT operations.

        Args:
            base:      Common ancestor snapshot.
            left:      Snapshot from the current branch (ours).
            right:     Snapshot from the incoming branch (theirs).
            repo_root: Path to the repository root for ``.museattributes``.
                       ``None`` in tests and non-file-system contexts.

        Returns:
            A ``MergeResult`` with ``merged`` snapshot, ``conflicts`` path list,
            ``applied_strategies``, and ``dimension_reports``.
        """
        base_files = base["files"]
        left_files = left["files"]
        right_files = right["files"]

        merged: dict[str, str] = dict(base_files)
        conflicts: list[str] = []

        all_paths = set(base_files) | set(left_files) | set(right_files)
        for path in sorted(all_paths):
            b_val = base_files.get(path)
            l_val = left_files.get(path)
            r_val = right_files.get(path)

            if l_val == r_val:
                # Both sides agree — consensus wins (including both deleted)
                if l_val is None:
                    merged.pop(path, None)
                else:
                    merged[path] = l_val
            elif b_val == l_val:
                # Only right changed
                if r_val is None:
                    merged.pop(path, None)
                else:
                    merged[path] = r_val
            elif b_val == r_val:
                # Only left changed
                if l_val is None:
                    merged.pop(path, None)
                else:
                    merged[path] = l_val
            else:
                # Both changed differently — conflict; keep left as placeholder
                conflicts.append(path)
                merged[path] = l_val or r_val or b_val or ""

        return MergeResult(
            merged=SnapshotManifest(files=merged, domain=_DOMAIN_NAME),
            conflicts=conflicts,
        )

    def drift(self, committed: StateSnapshot, live: LiveState) -> DriftReport:
        """Report how much the working tree has drifted from the last commit.

        Called by ``muse status``. Produces a ``DriftReport`` dataclass with
        ``has_drift``, ``summary``, and ``delta`` fields.

        Args:
            committed: The last committed snapshot.
            live:      Current live state (path or snapshot manifest).

        Returns:
            A ``DriftReport`` describing what has changed since the last commit.
        """
        current = self.snapshot(live)
        delta = self.diff(committed, current)
        has_drift = len(delta["ops"]) > 0
        return DriftReport(
            has_drift=has_drift,
            summary=delta["summary"],
            delta=delta,
        )

    def apply(self, delta: StateDelta, live_state: LiveState) -> LiveState:
        """Apply a delta to the working tree.

        Called by ``muse checkout`` after the core engine has already restored
        file-level objects from the object store. Use this hook for any
        domain-level post-processing (e.g. recompiling derived artefacts,
        updating an index).

        For most domains this is a no-op — the core engine handles file
        restoration and nothing more is needed.

        Args:
            delta:      The typed operation list to apply.
            live_state: Current live state.

        Returns:
            The updated live state.
        """
        # TODO: add domain-level post-processing if needed.
        return live_state

    # ------------------------------------------------------------------
    # Domain schema — required
    # ------------------------------------------------------------------

    def schema(self) -> DomainSchema:
        """Declare the structural shape of this domain's data.

        The schema drives diff algorithm selection, the ``muse domains``
        capability display, and routing between three-way and CRDT merge.

        Returns:
            A ``DomainSchema`` describing the top-level element type, semantic
            dimensions, merge mode, and schema version.
        """
        # TODO: replace with your domain's actual elements and dimensions.
        return DomainSchema(
            domain=_DOMAIN_NAME,
            description=(
                "Scaffold domain — replace this description with your domain's purpose. "
                "TODO: update domain, description, top_level, and dimensions."
            ),
            top_level=SetSchema(
                kind="set",
                element_type="record",   # TODO: rename to your element type
                identity="by_content",
            ),
            dimensions=[
                DimensionSpec(
                    name="primary",
                    description=(
                        "Primary data dimension. "
                        "TODO: rename and describe what this dimension represents."
                    ),
                    schema=SequenceSchema(
                        kind="sequence",
                        element_type="record",  # TODO: rename
                        identity="by_position",
                        diff_algorithm="lcs",
                        alphabet=None,
                    ),
                    independent_merge=True,
                ),
                DimensionSpec(
                    name="metadata",
                    description=(
                        "Metadata / annotation dimension. "
                        "TODO: rename or remove if not applicable."
                    ),
                    schema=SetSchema(
                        kind="set",
                        element_type="label",   # TODO: rename
                        identity="by_content",
                    ),
                    independent_merge=True,
                ),
            ],
            merge_mode="three_way",  # TODO: change to "crdt" if implementing CRDT convergent merge
            schema_version=__version__,
        )

    # ------------------------------------------------------------------
    # StructuredMergePlugin — optional OT merge extension
    # Remove this method and StructuredMergePlugin from the base classes if
    # your domain does not need sub-file OT merge.
    # ------------------------------------------------------------------

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
        """Operation-level three-way merge using Operational Transformation.

        The core engine calls this when both branches have a ``StructuredDelta``.
        ``merge_op_lists`` determines which ops commute (auto-mergeable) and
        which conflict (need human resolution).

        Args:
            base:        Common ancestor snapshot.
            ours_snap:   Our branch's final snapshot.
            theirs_snap: Their branch's final snapshot.
            ours_ops:    Our branch's typed operation list.
            theirs_ops:  Their branch's typed operation list.
            repo_root:   Repository root path for ``.museattributes`` loading.

        Returns:
            A ``MergeResult`` whose ``conflicts`` list is empty if all ops
            commute (can auto-merge) or populated for genuine conflicts.
        """
        result = merge_op_lists(
            base_ops=[],
            ours_ops=ours_ops,
            theirs_ops=theirs_ops,
        )

        conflicts: list[str] = []
        if result.conflict_ops:
            seen: set[str] = set()
            for our_op, _their_op in result.conflict_ops:
                seen.add(our_op["address"])
            conflicts = sorted(seen)

        # TODO: reconstruct the merged snapshot from merged_ops for finer
        # granularity. This fallback re-runs the file-level three-way merge
        # and uses the OT conflict list as the authoritative conflict set.
        fallback = self.merge(base, ours_snap, theirs_snap, repo_root=repo_root)
        return MergeResult(
            merged=fallback.merged,
            conflicts=conflicts if conflicts else fallback.conflicts,
            applied_strategies=fallback.applied_strategies,
            dimension_reports=fallback.dimension_reports,
        )

    # ------------------------------------------------------------------
    # CRDTPlugin — optional convergent merge extension
    # Remove these methods and CRDTPlugin from the base classes if your
    # domain does not need convergent multi-agent join semantics.
    # ------------------------------------------------------------------

    def crdt_schema(self) -> list[CRDTDimensionSpec]:
        """Declare which dimensions use which CRDT primitive.

        Returns:
            One ``CRDTDimensionSpec`` per CRDT-enabled dimension.
        """
        # TODO: replace with your domain's CRDT dimensions.
        return [
            CRDTDimensionSpec(
                name="labels",
                description="Annotation labels — concurrent adds win.",
                crdt_type="or_set",
                independent_merge=True,
            ),
        ]

    def join(
        self,
        a: CRDTSnapshotManifest,
        b: CRDTSnapshotManifest,
    ) -> CRDTSnapshotManifest:
        """Convergent join of two CRDT snapshot manifests.

        ``join`` always succeeds — no conflict state ever exists.

        Args:
            a: First CRDT snapshot manifest.
            b: Second CRDT snapshot manifest.

        Returns:
            The joined manifest (least upper bound of ``a`` and ``b``).
        """
        # TODO: join each CRDT dimension declared in crdt_schema().
        vc_a = VectorClock.from_dict(a["vclock"])
        vc_b = VectorClock.from_dict(b["vclock"])
        merged_vc = vc_a.merge(vc_b)

        # ORSet stores per-label OR-Set state serialised as JSON strings
        labels_a = ORSet.from_dict(json.loads(a["crdt_state"].get("labels", "{}")))
        labels_b = ORSet.from_dict(json.loads(b["crdt_state"].get("labels", "{}")))
        merged_labels = labels_a.join(labels_b)

        return CRDTSnapshotManifest(
            files=a["files"],
            domain=_DOMAIN_NAME,
            vclock=merged_vc.to_dict(),
            crdt_state={"labels": json.dumps(merged_labels.to_dict())},
            schema_version=__version__,
        )

    def to_crdt_state(self, snapshot: StateSnapshot) -> CRDTSnapshotManifest:
        """Lift a plain snapshot into CRDT state.

        Called when merging a snapshot produced before CRDT mode was enabled,
        or when bootstrapping CRDT state for the first time.

        Args:
            snapshot: A plain ``SnapshotManifest``.

        Returns:
            A ``CRDTSnapshotManifest`` with empty CRDT state.
        """
        return CRDTSnapshotManifest(
            files=snapshot["files"],
            domain=_DOMAIN_NAME,
            vclock=VectorClock().to_dict(),
            crdt_state={"labels": json.dumps(ORSet().to_dict())},
            schema_version=__version__,
        )

    def from_crdt_state(self, crdt: CRDTSnapshotManifest) -> StateSnapshot:
        """Materialise a CRDT manifest back into a plain snapshot.

        Called after a CRDT join to produce the snapshot the core engine writes
        to the commit record.

        Args:
            crdt: A ``CRDTSnapshotManifest``.

        Returns:
            A plain ``SnapshotManifest``.
        """
        return SnapshotManifest(files=crdt["files"], domain=_DOMAIN_NAME)

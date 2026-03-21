"""MIDI domain plugin — reference implementation of :class:`MuseDomainPlugin`.

This plugin implements the six Muse domain interfaces for MIDI state:
notes, velocities, controller events (CC), pitch bends, and aftertouch.

It is the domain that proved the abstraction. Every other domain — scientific
simulation, genomics, 3D spatial design — is a new plugin that implements
the same six interfaces.

Live State
----------
For the MIDI domain, ``LiveState`` is either:

1. A ``pathlib.Path`` pointing to the repository root (the working tree) — the
   MIDI files live on disk and are managed by ``muse commit / checkout``.
2. A dict snapshot previously captured by :meth:`snapshot` — used when
   constructing merges and diffs in memory.

Both forms are supported. The plugin detects which form it received by
checking for ``pathlib.Path`` vs ``dict``.

Snapshot Format
---------------
A music snapshot is a JSON-serialisable dict:

.. code-block:: json

    {
        "files": {
            "tracks/drums.mid": "<sha256>",
            "tracks/bass.mid":  "<sha256>"
        },
        "domain": "midi"
    }

The ``files`` key maps POSIX paths (relative to the repository root) to their
SHA-256 content digests.

Delta Format
----------------------
``diff()`` returns a ``StructuredDelta`` with typed ``DomainOp`` entries:

- ``InsertOp`` — a file was added (``content_id`` = its SHA-256 hash).
- ``DeleteOp`` — a file was removed.
- ``ReplaceOp`` — a non-MIDI file's content changed.
- ``PatchOp`` — a ``.mid`` file changed; ``child_ops`` contains note-level
  ``InsertOp`` / ``DeleteOp`` entries from the Myers LCS diff.

When ``repo_root`` is available, MIDI files are loaded from the object store
and diffed at note level. Without it, modified ``.mid`` files fall back to
``ReplaceOp``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import pathlib

from muse._version import __version__
from muse.core.schema import (
    DimensionSpec,
    DomainSchema,
    SequenceSchema,
    SetSchema,
    TensorSchema,
    TreeSchema,
)
from muse.domain import (
    DeleteOp,
    DomainOp,
    DriftReport,
    InsertOp,
    LiveState,
    MergeResult,
    MuseDomainPlugin,
    PatchOp,
    ReplaceOp,
    SnapshotManifest,
    StateDelta,
    StateSnapshot,
    StructuredDelta,
    StructuredMergePlugin,
)
from muse.plugins.midi.midi_diff import NoteKey

logger = logging.getLogger(__name__)

_DOMAIN_TAG = "midi"


class MidiPlugin:
    """MIDI domain plugin for the Muse VCS.

    Implements :class:`~muse.domain.MuseDomainPlugin` (six core interfaces)
    and :class:`~muse.domain.StructuredMergePlugin` (operation-level
    merge) for MIDI state stored as files in the working tree.

    This is the reference implementation. Every other domain plugin implements
    the same six core interfaces; the :class:`~muse.domain.StructuredMergePlugin`
    extension is optional but strongly recommended for domains that produce
    note-level (sub-file) diffs.
    """

    # ------------------------------------------------------------------
    # 1. snapshot — capture live state as a content-addressed dict
    # ------------------------------------------------------------------

    def snapshot(self, live_state: LiveState) -> StateSnapshot:
        """Capture the current working tree as a snapshot dict.

        Args:
            live_state: A ``pathlib.Path`` pointing to the repository root (working tree)
                        or an existing snapshot dict (returned as-is).

        Returns:
            A JSON-serialisable ``{"files": {path: sha256}, "domain": "midi"}``
            dict. The ``files`` mapping is the canonical snapshot manifest used
            by the core VCS engine for commit / checkout / diff.

        Ignore rules
        ------------
        When *live_state* is a ``pathlib.Path``, the plugin reads
        ``.museignore`` from the repository root
        and excludes any matching paths from the snapshot. Dotfiles are always
        excluded regardless of ``.museignore``.
        """
        if isinstance(live_state, pathlib.Path):
            from muse.core.ignore import is_ignored, load_ignore_config, resolve_patterns
            workdir = live_state
            repo_root = workdir
            patterns = resolve_patterns(load_ignore_config(repo_root), _DOMAIN_TAG)
            files: dict[str, str] = {}
            for file_path in sorted(workdir.rglob("*")):
                if not file_path.is_file():
                    continue
                rel_parts = file_path.relative_to(workdir).parts
                if any(part.startswith(".") for part in rel_parts):
                    continue
                rel = file_path.relative_to(workdir).as_posix()
                if is_ignored(rel, patterns):
                    continue
                files[rel] = _hash_file(file_path)
            return SnapshotManifest(files=files, domain=_DOMAIN_TAG)

        return live_state

    # ------------------------------------------------------------------
    # 2. diff — compute the structured delta between two snapshots
    # ------------------------------------------------------------------

    def diff(
        self,
        base: StateSnapshot,
        target: StateSnapshot,
        *,
        repo_root: pathlib.Path | None = None,
    ) -> StateDelta:
        """Compute a ``StructuredDelta`` between two music snapshots.

        File additions and removals produce ``InsertOp`` and ``DeleteOp``
        entries respectively. For modified files:

        - ``.mid`` files: when ``repo_root`` is provided, load the MIDI bytes
          from the object store and produce a ``PatchOp`` with note-level
          ``child_ops`` from the Myers LCS diff. Falls back to ``ReplaceOp``
          when the object store is unavailable or parsing fails.
        - All other files: ``ReplaceOp`` with file-level content IDs.

        Args:
            base:      The ancestor snapshot.
            target:    The later snapshot.
            repo_root: Repository root directory. When provided, MIDI files are
                       loaded from ``.muse/objects/`` for note-level diffing.

        Returns:
            A ``StructuredDelta`` whose ``ops`` list transforms *base* into
            *target* and whose ``summary`` is human-readable.
        """
        base_files = base["files"]
        target_files = target["files"]

        base_paths = set(base_files)
        target_paths = set(target_files)

        ops: list[DomainOp] = []

        # Added files → InsertOp
        for path in sorted(target_paths - base_paths):
            ops.append(
                InsertOp(
                    op="insert",
                    address=path,
                    position=None,
                    content_id=target_files[path],
                    content_summary=f"new file: {path}",
                )
            )

        # Removed files → DeleteOp
        for path in sorted(base_paths - target_paths):
            ops.append(
                DeleteOp(
                    op="delete",
                    address=path,
                    position=None,
                    content_id=base_files[path],
                    content_summary=f"deleted: {path}",
                )
            )

        # Modified files
        for path in sorted(
            p for p in base_paths & target_paths if base_files[p] != target_files[p]
        ):
            op = _diff_modified_file(
                path=path,
                old_hash=base_files[path],
                new_hash=target_files[path],
                repo_root=repo_root,
            )
            ops.append(op)

        summary = _summarise_ops(ops)
        return StructuredDelta(domain=_DOMAIN_TAG, ops=ops, summary=summary)

    # ------------------------------------------------------------------
    # 3. merge — three-way reconciliation
    # ------------------------------------------------------------------

    def merge(
        self,
        base: StateSnapshot,
        left: StateSnapshot,
        right: StateSnapshot,
        *,
        repo_root: pathlib.Path | None = None,
    ) -> MergeResult:
        """Three-way merge two divergent music state lines against a common base.

        A file is auto-merged when only one side changed it.  When both sides
        changed the same file, the merge proceeds in two stages:

        1. **File-level strategy** — if ``.museattributes`` contains an
           ``ours`` or ``theirs`` rule matching the path (dimension ``"*"``),
           the rule is applied and the file is removed from the conflict list.

        2. **Dimension-level merge** — for ``.mid`` files that survive the
           file-level check, the MIDI event stream is split into orthogonal
           dimension slices (notes/melodic/rhythmic, harmonic, dynamic, structural).
           Each dimension is merged independently. Dimension-specific
           ``ours``/``theirs`` rules in ``.museattributes`` are honoured.
           Only dimensions where *both* sides changed AND no resolvable rule
           exists cause a true file-level conflict.

        3. **Manual override** — ``manual`` strategy in ``.museattributes``
           forces a path into the conflict list even when the engine would
           normally auto-resolve it.

        Args:
            base:      Snapshot at the common ancestor commit.
            left:      Snapshot for the *ours* (current) branch.  The distinction
                       between ``left`` and ``right`` only affects the ``applied_strategies``
                       key in the result; the merge is symmetric for clean paths.
            right:     Snapshot for the *theirs* (incoming) branch.
            repo_root: Path to the repository root so ``.museattributes`` and the
                       object store can be located.  ``None`` disables attribute
                       loading and MIDI reconstruction (all conflicts become hard).

        Returns:
            A :class:`~muse.domain.MergeResult` whose ``snapshot`` holds the
            merged manifest (conflict paths absent), ``conflicts`` lists the
            unresolvable paths, and ``applied_strategies`` records which
            ``.museattributes`` rules were used.
        """
        import hashlib as _hashlib

        from muse.core.attributes import load_attributes, resolve_strategy
        from muse.core.object_store import read_object, write_object
        from muse.plugins.midi.midi_merge import merge_midi_dimensions

        base_files = base["files"]
        left_files = left["files"]
        right_files = right["files"]

        attrs = load_attributes(repo_root, domain=_DOMAIN_TAG) if repo_root is not None else []

        left_changed: set[str] = _changed_paths(base_files, left_files)
        right_changed: set[str] = _changed_paths(base_files, right_files)
        all_conflict_paths: set[str] = left_changed & right_changed

        merged: dict[str, str] = dict(base_files)

        # Apply clean single-side changes first.
        for path in left_changed - all_conflict_paths:
            if path in left_files:
                merged[path] = left_files[path]
            else:
                merged.pop(path, None)

        for path in right_changed - all_conflict_paths:
            if path in right_files:
                merged[path] = right_files[path]
            else:
                merged.pop(path, None)

        # Consensus deletions (both sides removed the same file) — not a conflict.
        consensus_deleted = {
            p for p in all_conflict_paths
            if p not in left_files and p not in right_files
        }
        for path in consensus_deleted:
            merged.pop(path, None)

        real_conflicts: set[str] = all_conflict_paths - consensus_deleted

        applied_strategies: dict[str, str] = {}
        dimension_reports: dict[str, dict[str, str]] = {}
        final_conflicts: list[str] = []

        for path in sorted(real_conflicts):
            file_strategy = resolve_strategy(attrs, path, "*")

            if file_strategy == "ours":
                if path in left_files:
                    merged[path] = left_files[path]
                else:
                    merged.pop(path, None)
                applied_strategies[path] = "ours"
                continue

            if file_strategy == "theirs":
                if path in right_files:
                    merged[path] = right_files[path]
                else:
                    merged.pop(path, None)
                applied_strategies[path] = "theirs"
                continue

            if (
                repo_root is not None
                and path.lower().endswith(".mid")
                and path in left_files
                and path in right_files
                and path in base_files
            ):
                base_obj = read_object(repo_root, base_files[path])
                left_obj = read_object(repo_root, left_files[path])
                right_obj = read_object(repo_root, right_files[path])

                if base_obj is not None and left_obj is not None and right_obj is not None:
                    try:
                        dim_result = merge_midi_dimensions(
                            base_obj, left_obj, right_obj,
                            attrs,
                            path,
                        )
                    except ValueError:
                        dim_result = None

                    if dim_result is not None:
                        merged_bytes, dim_report = dim_result
                        new_hash = _hashlib.sha256(merged_bytes).hexdigest()
                        write_object(repo_root, new_hash, merged_bytes)
                        merged[path] = new_hash
                        applied_strategies[path] = "dimension-merge"
                        dimension_reports[path] = dim_report
                        continue

            final_conflicts.append(path)

        for path in sorted((left_changed | right_changed) - real_conflicts):
            if path in consensus_deleted:
                continue
            if resolve_strategy(attrs, path, "*") == "manual":
                final_conflicts.append(path)
                applied_strategies[path] = "manual"
                if path in base_files:
                    merged[path] = base_files[path]
                else:
                    merged.pop(path, None)

        return MergeResult(
            merged=SnapshotManifest(files=merged, domain=_DOMAIN_TAG),
            conflicts=sorted(final_conflicts),
            applied_strategies=applied_strategies,
            dimension_reports=dimension_reports,
        )

    # ------------------------------------------------------------------
    # 4. drift — compare committed state vs live state
    # ------------------------------------------------------------------

    def drift(
        self,
        committed: StateSnapshot,
        live: LiveState,
    ) -> DriftReport:
        """Detect uncommitted changes in the working tree relative to *committed*.

        Args:
            committed: The last committed snapshot.
            live:      Either a ``pathlib.Path`` (repository root) or a snapshot
                       dict representing current live state.

        Returns:
            A :class:`~muse.domain.DriftReport` describing whether and how the
            live state differs from the committed snapshot.
        """
        live_snapshot = self.snapshot(live)
        delta = self.diff(committed, live_snapshot)

        inserts = sum(1 for op in delta["ops"] if op["op"] == "insert")
        deletes = sum(1 for op in delta["ops"] if op["op"] == "delete")
        modified = sum(1 for op in delta["ops"] if op["op"] in ("replace", "patch"))
        has_drift = bool(inserts or deletes or modified)

        parts: list[str] = []
        if inserts:
            parts.append(f"{inserts} added")
        if deletes:
            parts.append(f"{deletes} removed")
        if modified:
            parts.append(f"{modified} modified")

        summary = ", ".join(parts) if parts else "working tree clean"
        return DriftReport(has_drift=has_drift, summary=summary, delta=delta)

    # ------------------------------------------------------------------
    # 5. apply — execute a delta against live state (checkout)
    # ------------------------------------------------------------------

    def apply(self, delta: StateDelta, live_state: LiveState) -> LiveState:
        """Apply a structured delta to produce a new live state.

        When ``live_state`` is a ``pathlib.Path`` the physical files have
        already been updated by the caller (``muse checkout`` restores objects
        from the store before calling this). Rescanning the directory is the
        cheapest correct way to reflect the new state.

        When ``live_state`` is a snapshot dict, only ``DeleteOp`` and
        ``ReplaceOp`` at the file level can be applied in-memory. ``InsertOp``
        at the file level requires the new content to be on disk; callers that
        need those should pass the workdir ``pathlib.Path`` instead.
        ``PatchOp`` entries are skipped in-memory since reconstructing patched
        file content requires both the original bytes and the object store.

        Args:
            delta:      A ``StructuredDelta`` produced by :meth:`diff`.
            live_state: The workdir path (preferred) or a snapshot dict.

        Returns:
            The updated live state as a ``SnapshotManifest``.
        """
        if isinstance(live_state, pathlib.Path):
            return self.snapshot(live_state)

        current_files = dict(live_state["files"])

        for op in delta["ops"]:
            if op["op"] == "delete":
                current_files.pop(op["address"], None)
            elif op["op"] == "replace":
                current_files[op["address"]] = op["new_content_id"]
            elif op["op"] == "insert":
                current_files[op["address"]] = op["content_id"]
            # PatchOp and MoveOp: skip in-memory — caller must use workdir path.

        return SnapshotManifest(files=current_files, domain=_DOMAIN_TAG)

    # ------------------------------------------------------------------
    # 6. schema — declare structural schema for the algorithm library
    # ------------------------------------------------------------------

    def schema(self) -> DomainSchema:
        """Return the full structural schema for the MIDI domain.

        Declares 21 semantic dimensions — one per independent MIDI event class
        — that the core diff algorithm library and OT merge engine use to drive
        per-dimension operations.  This is a significant expansion from the
        original 5 dimensions; the finer granularity means two agents can edit
        completely different aspects of the same MIDI file (e.g. sustain pedal
        and channel volume) without ever creating a merge conflict.

        Top level is a ``SetSchema``: the music workspace is an unordered
        collection of audio/MIDI files, each identified by its SHA-256 content
        hash.

        Independent dimensions (conflicts do not block merging others):
        - **notes** (melodic/rhythmic) — note_on / note_off events
        - **pitch_bend** — pitchwheel controller
        - **channel_pressure** — monophonic aftertouch
        - **poly_pressure** — per-note polyphonic aftertouch
        - **cc_modulation** — CC 1 modulation wheel
        - **cc_volume** — CC 7 channel volume
        - **cc_pan** — CC 10 stereo pan
        - **cc_expression** — CC 11 expression controller
        - **cc_sustain** — CC 64 damper / sustain pedal
        - **cc_portamento** — CC 65 portamento on/off
        - **cc_sostenuto** — CC 66 sostenuto pedal
        - **cc_soft_pedal** — CC 67 soft pedal (una corda)
        - **cc_reverb** — CC 91 reverb send level
        - **cc_chorus** — CC 93 chorus send level
        - **cc_other** — all other numbered CC controllers
        - **program_change** — instrument / patch selection
        - **key_signatures** — key signature meta events
        - **markers** — section markers, cue points, text annotations

        Non-independent dimensions (conflicts block all others):
        - **tempo_map** — set_tempo meta events; tempo changes shift the
          musical meaning of every subsequent tick position, so a bilateral
          tempo conflict requires human resolution before other dimensions
          can be finalised.
        - **time_signatures** — time_signature meta events; bar structure
          changes have the same semantic blocking effect as tempo changes.
        - **track_structure** — track name, instrument name, sysex, and
          unknown meta events affecting routing and session layout.
        """
        seq_schema = SequenceSchema(
            kind="sequence",
            element_type="note_event",
            identity="by_position",
            diff_algorithm="lcs",
            alphabet=None,
        )
        cc_schema = TensorSchema(
            kind="tensor",
            dtype="float32",
            rank=1,
            epsilon=0.5,
            diff_mode="sparse",
        )
        tree_schema = TreeSchema(
            kind="tree",
            node_type="track_node",
            diff_algorithm="zhang_shasha",
        )
        meta_schema = SequenceSchema(
            kind="sequence",
            element_type="meta_event",
            identity="by_position",
            diff_algorithm="lcs",
            alphabet=None,
        )
        return DomainSchema(
            domain=_DOMAIN_TAG,
            description=(
                "MIDI and audio file versioning with note-level diff and "
                "21-dimension independent merge"
            ),
            top_level=SetSchema(
                kind="set",
                element_type="audio_file",
                identity="by_content",
            ),
            dimensions=[
                # --- Expressive note content ---
                DimensionSpec(
                    name="notes",
                    description="Note pitches, durations, and timing (melodic + rhythmic)",
                    schema=seq_schema,
                    independent_merge=True,
                ),
                DimensionSpec(
                    name="pitch_bend",
                    description="Pitchwheel controller — expressive pitch deviation",
                    schema=cc_schema,
                    independent_merge=True,
                ),
                DimensionSpec(
                    name="channel_pressure",
                    description="Monophonic aftertouch — channel-wide pressure",
                    schema=cc_schema,
                    independent_merge=True,
                ),
                DimensionSpec(
                    name="poly_pressure",
                    description="Polyphonic aftertouch — per-note pressure",
                    schema=cc_schema,
                    independent_merge=True,
                ),
                # --- Named CC controllers ---
                DimensionSpec(
                    name="cc_modulation",
                    description="CC 1 — modulation wheel",
                    schema=cc_schema,
                    independent_merge=True,
                ),
                DimensionSpec(
                    name="cc_volume",
                    description="CC 7 — channel volume",
                    schema=cc_schema,
                    independent_merge=True,
                ),
                DimensionSpec(
                    name="cc_pan",
                    description="CC 10 — stereo pan position",
                    schema=cc_schema,
                    independent_merge=True,
                ),
                DimensionSpec(
                    name="cc_expression",
                    description="CC 11 — expression controller",
                    schema=cc_schema,
                    independent_merge=True,
                ),
                DimensionSpec(
                    name="cc_sustain",
                    description="CC 64 — damper / sustain pedal",
                    schema=cc_schema,
                    independent_merge=True,
                ),
                DimensionSpec(
                    name="cc_portamento",
                    description="CC 65 — portamento on/off",
                    schema=cc_schema,
                    independent_merge=True,
                ),
                DimensionSpec(
                    name="cc_sostenuto",
                    description="CC 66 — sostenuto pedal",
                    schema=cc_schema,
                    independent_merge=True,
                ),
                DimensionSpec(
                    name="cc_soft_pedal",
                    description="CC 67 — soft pedal (una corda)",
                    schema=cc_schema,
                    independent_merge=True,
                ),
                DimensionSpec(
                    name="cc_reverb",
                    description="CC 91 — reverb send level",
                    schema=cc_schema,
                    independent_merge=True,
                ),
                DimensionSpec(
                    name="cc_chorus",
                    description="CC 93 — chorus send level",
                    schema=cc_schema,
                    independent_merge=True,
                ),
                DimensionSpec(
                    name="cc_other",
                    description="All other numbered CC controllers",
                    schema=cc_schema,
                    independent_merge=True,
                ),
                # --- Patch / program selection ---
                DimensionSpec(
                    name="program_change",
                    description="Instrument / patch selection events",
                    schema=meta_schema,
                    independent_merge=True,
                ),
                # --- Non-independent timeline metadata ---
                DimensionSpec(
                    name="tempo_map",
                    description=(
                        "Tempo (BPM) changes — non-independent: a conflict "
                        "blocks merging all other dimensions"
                    ),
                    schema=meta_schema,
                    independent_merge=False,
                ),
                DimensionSpec(
                    name="time_signatures",
                    description=(
                        "Time signature changes — non-independent: affects "
                        "bar structure for all other dimensions"
                    ),
                    schema=meta_schema,
                    independent_merge=False,
                ),
                # --- Tonal and annotation metadata ---
                DimensionSpec(
                    name="key_signatures",
                    description="Key signature events",
                    schema=meta_schema,
                    independent_merge=True,
                ),
                DimensionSpec(
                    name="markers",
                    description="Section markers, cue points, text, lyrics, copyright",
                    schema=meta_schema,
                    independent_merge=True,
                ),
                # --- Track structure (non-independent) ---
                DimensionSpec(
                    name="track_structure",
                    description=(
                        "Track name, instrument name, sysex, unknown meta — "
                        "non-independent: routing changes affect all tracks"
                    ),
                    schema=tree_schema,
                    independent_merge=False,
                ),
            ],
            merge_mode="three_way",
            schema_version=__version__,
        )

    # ------------------------------------------------------------------
    # 7. merge_ops — operation-level OT merge (StructuredMergePlugin)
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
        """Operation-level three-way merge using the OT engine.

        Extends the file-level ``merge()`` method with sub-file granularity: two
        changes to non-overlapping notes in the same MIDI file no longer produce
        a conflict.

        Algorithm
        ---------
        1. Run :func:`~muse.core.op_transform.merge_op_lists` on the flat op
           lists to classify each (ours, theirs) pair as commuting or
           conflicting.
        2. Build the merged manifest from *base* by applying all clean merged
           ops.  ``InsertOp`` and ``ReplaceOp`` entries supply a ``content_id``
           / ``new_content_id`` directly.  For ``PatchOp`` entries (sub-file
           note changes), the final file hash is looked up from *ours_snap* or
           *theirs_snap*.  When both sides produced a ``PatchOp`` for the same
           MIDI file and the note-level ops commute, an attempt is made to
           reconstruct the merged MIDI bytes; on failure the file falls back to
           a conflict.
        3. For conflicting pairs, consult ``.museattributes``.  Strategies
           ``"ours"`` and ``"theirs"`` are applied automatically; everything
           else enters ``MergeResult.conflicts``.

        Args:
            base:        Common ancestor snapshot.
            ours_snap:   Final snapshot of our branch.
            theirs_snap: Final snapshot of their branch.
            ours_ops:    Operations from our branch delta (base → ours).
            theirs_ops:  Operations from their branch delta (base → theirs).
            repo_root:   Repository root for object store and attributes.

        Returns:
            A :class:`~muse.domain.MergeResult` with the reconciled snapshot
            and any remaining unresolvable conflicts.
        """
        from muse.core.attributes import load_attributes, resolve_strategy
        from muse.core.op_transform import merge_op_lists

        attrs = load_attributes(repo_root, domain=_DOMAIN_TAG) if repo_root is not None else []

        # OT classification: find commuting and conflicting op pairs.
        ot_result = merge_op_lists([], ours_ops, theirs_ops)

        # Build the merged manifest starting from base.
        merged_files: dict[str, str] = dict(base["files"])
        applied_strategies: dict[str, str] = {}
        final_conflicts: list[str] = []
        op_log: list[DomainOp] = list(ot_result.merged_ops)

        # Group PatchOps by address so we can detect same-file note merges.
        ours_patches: dict[str, PatchOp] = {}
        theirs_patches: dict[str, PatchOp] = {}
        for op in ours_ops:
            if op["op"] == "patch":
                ours_patches[op["address"]] = op
        for op in theirs_ops:
            if op["op"] == "patch":
                theirs_patches[op["address"]] = op

        # Track which addresses are involved in a conflict.
        conflicting_addresses: set[str] = {
            our_op["address"] for our_op, _ in ot_result.conflict_ops
        }

        # --- Apply clean merged ops ---
        for op in ot_result.merged_ops:
            addr = op["address"]
            if addr in conflicting_addresses:
                continue  # handled in conflict resolution below

            if op["op"] == "insert":
                merged_files[addr] = op["content_id"]

            elif op["op"] == "delete":
                merged_files.pop(addr, None)

            elif op["op"] == "replace":
                merged_files[addr] = op["new_content_id"]

            elif op["op"] == "patch":
                # PatchOp: determine which side(s) patched this file.
                has_ours = addr in ours_patches
                has_theirs = addr in theirs_patches

                if has_ours and not has_theirs:
                    # Only our side changed this file — take our version.
                    if addr in ours_snap["files"]:
                        merged_files[addr] = ours_snap["files"][addr]
                    else:
                        merged_files.pop(addr, None)

                elif has_theirs and not has_ours:
                    # Only their side changed this file — take their version.
                    if addr in theirs_snap["files"]:
                        merged_files[addr] = theirs_snap["files"][addr]
                    else:
                        merged_files.pop(addr, None)

                else:
                    # Both sides patched the same file with commuting note ops.
                    # Attempt note-level MIDI reconstruction.
                    merged_content_id = _merge_patch_ops(
                        addr=addr,
                        ours_patch=ours_patches[addr],
                        theirs_patch=theirs_patches[addr],
                        base_files=dict(base["files"]),
                        ours_snap_files=dict(ours_snap["files"]),
                        theirs_snap_files=dict(theirs_snap["files"]),
                        repo_root=repo_root,
                    )
                    if merged_content_id is not None:
                        merged_files[addr] = merged_content_id
                    else:
                        # Reconstruction failed — treat as manual conflict.
                        final_conflicts.append(addr)

        # --- Resolve conflicts ---
        for our_op, their_op in ot_result.conflict_ops:
            addr = our_op["address"]
            strategy = resolve_strategy(attrs, addr, "*")

            if strategy == "ours":
                if addr in ours_snap["files"]:
                    merged_files[addr] = ours_snap["files"][addr]
                else:
                    merged_files.pop(addr, None)
                applied_strategies[addr] = "ours"

            elif strategy == "theirs":
                if addr in theirs_snap["files"]:
                    merged_files[addr] = theirs_snap["files"][addr]
                else:
                    merged_files.pop(addr, None)
                applied_strategies[addr] = "theirs"

            else:
                # Strategy "manual" or "auto" without a clear resolution.
                final_conflicts.append(addr)

        return MergeResult(
            merged=SnapshotManifest(files=merged_files, domain=_DOMAIN_TAG),
            conflicts=sorted(set(final_conflicts)),
            applied_strategies=applied_strategies,
            op_log=op_log,
        )


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _merge_patch_ops(
    *,
    addr: str,
    ours_patch: PatchOp,
    theirs_patch: PatchOp,
    base_files: dict[str, str],
    ours_snap_files: dict[str, str],
    theirs_snap_files: dict[str, str],
    repo_root: pathlib.Path | None,
) -> str | None:
    """Attempt note-level MIDI merge for two ``PatchOp``\\s on the same file.

    Runs OT on the child_ops of each PatchOp.  If the note-level ops all
    commute, reconstructs the merged MIDI by:

    1. Loading base, ours, and theirs MIDI bytes from the object store.
    2. Extracting note sequences from all three versions.
    3. Building ``content_id → NoteKey`` look-ups for the ours and theirs
       sequences (so that InsertOp content IDs can be resolved to real notes).
    4. Applying the merged note ops (deletions then insertions) to the base
       note sequence.
    5. Calling :func:`~muse.plugins.midi.midi_diff.reconstruct_midi` and
       storing the resulting bytes.

    Returns the SHA-256 hash of the reconstructed MIDI (ready to store in the
    object store) on success, or ``None`` when:

    - *repo_root* is ``None`` (cannot access object store).
    - Base or branch bytes are not in the local object store.
    - Note-level OT found conflicts.
    - MIDI reconstruction raised any exception.

    Args:
        addr:              Workspace-relative MIDI file path.
        ours_patch:        Our PatchOp for this file.
        theirs_patch:      Their PatchOp for this file.
        base_files:        Content-ID map for the common ancestor snapshot.
        ours_snap_files:   Content-ID map for our branch's final snapshot.
        theirs_snap_files: Content-ID map for their branch's final snapshot.
        repo_root:         Repository root for object store access.

    Returns:
        Content-ID (SHA-256 hex) of the merged MIDI, or ``None`` on failure.
    """
    if repo_root is None or addr not in base_files:
        return None

    from muse.core.object_store import read_object, write_object
    from muse.core.op_transform import merge_op_lists
    from muse.plugins.midi.midi_diff import NoteKey, extract_notes, reconstruct_midi

    # Run OT on note-level ops to classify conflicts.
    note_result = merge_op_lists([], ours_patch["child_ops"], theirs_patch["child_ops"])
    if not note_result.is_clean:
        logger.debug(
            "⚠️ Note-level conflict in %r: %d pair(s) — falling back to file conflict",
            addr,
            len(note_result.conflict_ops),
        )
        return None

    try:
        base_bytes = read_object(repo_root, base_files[addr])
        if base_bytes is None:
            return None

        ours_hash = ours_snap_files.get(addr)
        theirs_hash = theirs_snap_files.get(addr)
        ours_bytes = read_object(repo_root, ours_hash) if ours_hash else None
        theirs_bytes = read_object(repo_root, theirs_hash) if theirs_hash else None

        base_notes, ticks_per_beat = extract_notes(base_bytes)

        # Build content_id → NoteKey lookups from ours and theirs versions.
        ours_by_id: dict[str, NoteKey] = {}
        if ours_bytes is not None:
            ours_notes, _ = extract_notes(ours_bytes)
            ours_by_id = {_note_content_id(n): n for n in ours_notes}

        theirs_by_id: dict[str, NoteKey] = {}
        if theirs_bytes is not None:
            theirs_notes, _ = extract_notes(theirs_bytes)
            theirs_by_id = {_note_content_id(n): n for n in theirs_notes}

        # Collect content IDs to delete.
        delete_ids: set[str] = {
            op["content_id"] for op in note_result.merged_ops if op["op"] == "delete"
        }

        # Apply deletions to base note list.
        base_note_by_id = {_note_content_id(n): n for n in base_notes}
        surviving: list[NoteKey] = [
            n for n in base_notes if _note_content_id(n) not in delete_ids
        ]

        # Collect insertions: resolve content_id → NoteKey via ours then theirs.
        inserted: list[NoteKey] = []
        for op in note_result.merged_ops:
            if op["op"] == "insert":
                cid = op["content_id"]
                note = ours_by_id.get(cid) or theirs_by_id.get(cid)
                if note is None:
                    # Fallback: base itself shouldn't have it, but check anyway.
                    note = base_note_by_id.get(cid)
                if note is None:
                    logger.debug(
                        "⚠️ Cannot resolve note content_id %s for %r — skipping",
                        cid[:12],
                        addr,
                    )
                    continue
                inserted.append(note)

        merged_notes = surviving + inserted
        merged_bytes = reconstruct_midi(merged_notes, ticks_per_beat=ticks_per_beat)

        merged_hash = hashlib.sha256(merged_bytes).hexdigest()
        write_object(repo_root, merged_hash, merged_bytes)

        logger.info(
            "✅ Note-level MIDI merge for %r: %d ops clean, %d notes in result",
            addr,
            len(note_result.merged_ops),
            len(merged_notes),
        )
        return merged_hash

    except Exception as exc:  # noqa: BLE001  intentional broad catch
        logger.debug("⚠️ MIDI note-level reconstruction failed for %r: %s", addr, exc)
        return None


def _note_content_id(note: NoteKey) -> str:
    """Return the SHA-256 content ID for a :class:`~muse.plugins.midi.midi_diff.NoteKey`.

    Delegates to the same algorithm used in :mod:`muse.plugins.midi.midi_diff`
    so that content IDs computed here are identical to those stored in
    ``InsertOp`` / ``DeleteOp`` entries.
    """
    payload = (
        f"{note['pitch']}:{note['velocity']}:"
        f"{note['start_tick']}:{note['duration_ticks']}:{note['channel']}"
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _diff_modified_file(
    *,
    path: str,
    old_hash: str,
    new_hash: str,
    repo_root: pathlib.Path | None,
) -> DomainOp:
    """Produce the richest available operation for a modified file.

    For ``.mid`` files where both content revisions are readable from the
    object store, performs a full note-level MIDI diff and returns a
    ``PatchOp`` carrying the individual ``InsertOp``/``DeleteOp`` child
    operations.  Falls back to a ``ReplaceOp`` (opaque before/after hash
    pair) when the file is not a MIDI file, ``repo_root`` is ``None``, or
    either content revision cannot be retrieved from the store.

    Args:
        path:      Workspace-relative POSIX path of the modified file.
        old_hash:  SHA-256 of the base content in the object store.
        new_hash:  SHA-256 of the current content in the object store.
        repo_root: Repository root for object store access.  ``None`` forces
                   immediate fallback to ``ReplaceOp``.

    Returns:
        A ``PatchOp`` with note-level child ops when deep diff succeeds,
        otherwise a ``ReplaceOp`` with the opaque before/after content hashes.
    """
    if path.lower().endswith(".mid") and repo_root is not None:
        from muse.core.object_store import read_object
        from muse.plugins.midi.midi_diff import diff_midi_notes

        base_bytes = read_object(repo_root, old_hash)
        target_bytes = read_object(repo_root, new_hash)

        if base_bytes is not None and target_bytes is not None:
            try:
                child_delta = diff_midi_notes(
                    base_bytes, target_bytes, file_path=path
                )
                return PatchOp(
                    op="patch",
                    address=path,
                    child_ops=child_delta["ops"],
                    child_domain=child_delta["domain"],
                    child_summary=child_delta["summary"],
                )
            except (ValueError, Exception) as exc:
                logger.debug("⚠️ MIDI deep diff failed for %r: %s", path, exc)

    return ReplaceOp(
        op="replace",
        address=path,
        position=None,
        old_content_id=old_hash,
        new_content_id=new_hash,
        old_summary=f"{path} (previous)",
        new_summary=f"{path} (updated)",
    )


def _summarise_ops(ops: list[DomainOp]) -> str:
    """Build a human-readable summary string from a list of domain ops."""
    inserts = 0
    deletes = 0
    replaces = 0
    patches = 0

    for op in ops:
        kind = op["op"]
        if kind == "insert":
            inserts += 1
        elif kind == "delete":
            deletes += 1
        elif kind == "replace":
            replaces += 1
        elif kind == "patch":
            patches += 1

    parts: list[str] = []
    if inserts:
        parts.append(f"{inserts} file{'s' if inserts != 1 else ''} added")
    if deletes:
        parts.append(f"{deletes} file{'s' if deletes != 1 else ''} removed")
    if replaces:
        parts.append(f"{replaces} file{'s' if replaces != 1 else ''} modified")
    if patches:
        parts.append(f"{patches} file{'s' if patches != 1 else ''} patched")

    return ", ".join(parts) if parts else "no changes"


def _hash_file(path: pathlib.Path) -> str:
    """Return the SHA-256 hex digest of a file's raw bytes."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _changed_paths(
    base: dict[str, str], other: dict[str, str]
) -> set[str]:
    """Return paths that differ between *base* and *other*."""
    base_p = set(base)
    other_p = set(other)
    added = other_p - base_p
    deleted = base_p - other_p
    common = base_p & other_p
    modified = {p for p in common if base[p] != other[p]}
    return added | deleted | modified


def content_hash(snapshot: StateSnapshot) -> str:
    """Return a stable SHA-256 digest of a snapshot for content-addressing."""
    canonical = json.dumps(snapshot, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


#: Module-level singleton — import and use directly.
plugin = MidiPlugin()

assert isinstance(plugin, MuseDomainPlugin), (
    "MidiPlugin does not satisfy the MuseDomainPlugin protocol"
)
assert isinstance(plugin, StructuredMergePlugin), (
    "MidiPlugin does not satisfy the StructuredMergePlugin protocol"
)

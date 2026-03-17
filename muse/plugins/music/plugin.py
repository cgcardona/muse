"""Music domain plugin — reference implementation of :class:`MuseDomainPlugin`.

This plugin implements the five Muse domain interfaces for MIDI state:
notes, velocities, controller events (CC), pitch bends, and aftertouch.

It is the domain that proved the abstraction. Every other domain — scientific
simulation, genomics, 3D spatial design — is a new plugin that implements
the same five interfaces.

Live State
----------
For the music domain, ``LiveState`` is either:

1. A ``muse-work/`` directory path (``pathlib.Path``) — the CLI path where
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
        "domain": "music"
    }

The ``files`` key maps POSIX paths (relative to ``muse-work/``) to their
SHA-256 content digests. This is the same structure that the core file-based
store uses as a snapshot manifest — the music plugin does not add an
abstraction layer on top of the existing content-addressed object store.

For more sophisticated use cases (DAW-level integration, per-note diffs,
emotion vectors, harmonic analysis), the snapshot can be extended with
additional top-level keys. The core DAG engine only requires that the
snapshot be JSON-serialisable and content-addressable.
"""
from __future__ import annotations

import hashlib
import json
import pathlib

from muse.domain import (
    DeltaManifest,
    DriftReport,
    LiveState,
    MergeResult,
    MuseDomainPlugin,
    SnapshotManifest,
    StateDelta,
    StateSnapshot,
)

_DOMAIN_TAG = "music"


class MusicPlugin:
    """Music domain plugin for the Muse VCS.

    Implements :class:`~muse.domain.MuseDomainPlugin` for MIDI state stored
    as files in ``muse-work/``. Use this plugin when running ``muse`` against
    a directory of MIDI, audio, or other music production files.

    This is the reference implementation. It demonstrates the five-interface
    contract that every other domain plugin must satisfy.
    """

    # ------------------------------------------------------------------
    # 1. snapshot — capture live state as a content-addressed dict
    # ------------------------------------------------------------------

    def snapshot(self, live_state: LiveState) -> StateSnapshot:
        """Capture the current ``muse-work/`` directory as a snapshot dict.

        Args:
            live_state: Either a ``pathlib.Path`` pointing to ``muse-work/``
                        or an existing snapshot dict (returned as-is).

        Returns:
            A JSON-serialisable ``{"files": {path: sha256}, "domain": "music"}``
            dict. The ``files`` mapping is the canonical snapshot manifest used
            by the core VCS engine for commit / checkout / diff.

        Ignore rules
        ------------
        When *live_state* is a ``pathlib.Path``, the plugin reads
        ``.museignore`` from the repository root (the parent of ``muse-work/``)
        and excludes any matching paths from the snapshot.  Dotfiles are always
        excluded regardless of ``.museignore``.  See ``docs/reference/museignore.md``
        for the full format reference.
        """
        if isinstance(live_state, pathlib.Path):
            from muse.core.ignore import is_ignored, load_patterns
            workdir = live_state
            repo_root = workdir.parent
            patterns = load_patterns(repo_root)
            files: dict[str, str] = {}
            for file_path in sorted(workdir.rglob("*")):
                if not file_path.is_file():
                    continue
                if file_path.name.startswith("."):
                    continue
                rel = file_path.relative_to(workdir).as_posix()
                if is_ignored(rel, patterns):
                    continue
                files[rel] = _hash_file(file_path)
            return SnapshotManifest(files=files, domain=_DOMAIN_TAG)

        return live_state

    # ------------------------------------------------------------------
    # 2. diff — compute the minimal delta between two snapshots
    # ------------------------------------------------------------------

    def diff(self, base: StateSnapshot, target: StateSnapshot) -> StateDelta:
        """Compute the file-level delta between two music snapshots.

        Args:
            base:   The ancestor snapshot.
            target: The later snapshot.

        Returns:
            A delta dict with three keys:
            - ``added``:    list of paths present in *target* but not *base*.
            - ``removed``:  list of paths present in *base* but not *target*.
            - ``modified``: list of paths present in both with different digests.
        """
        base_files = base["files"]
        target_files = target["files"]

        base_paths = set(base_files)
        target_paths = set(target_files)

        added = sorted(target_paths - base_paths)
        removed = sorted(base_paths - target_paths)
        common = base_paths & target_paths
        modified = sorted(p for p in common if base_files[p] != target_files[p])

        return DeltaManifest(
            domain=_DOMAIN_TAG,
            added=added,
            removed=removed,
            modified=modified,
        )

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
           dimension slices (notes/melodic, harmonic, dynamic, structural).
           Each dimension is merged independently.  Dimension-specific
           ``ours``/``theirs`` rules in ``.museattributes`` are honoured.
           Only dimensions where *both* sides changed AND no resolvable rule
           exists cause a true file-level conflict.

        3. **Manual override** — ``manual`` strategy in ``.museattributes``
           forces a path into the conflict list even when the engine would
           normally auto-resolve it.

        Args:
            base:      The common ancestor snapshot.
            left:      The current branch snapshot (ours).
            right:     The target branch snapshot (theirs).
            repo_root: Repository root directory.  When provided, ``.museattributes``
                       is loaded and the object store is accessible for MIDI
                       dimension merge.  When ``None``, behaves as before
                       (pure file-level conflict detection, no attributes).

        Returns:
            A :class:`~muse.domain.MergeResult` with the merged snapshot,
            conflict paths, applied strategy overrides, and per-file dimension
            reports.
        """
        import hashlib as _hashlib

        from muse.core.attributes import load_attributes, resolve_strategy
        from muse.core.object_store import read_object, write_object
        from muse.plugins.music.midi_merge import merge_midi_dimensions

        base_files = base["files"]
        left_files = left["files"]
        right_files = right["files"]

        attrs = load_attributes(repo_root) if repo_root is not None else []

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

        # ------------------------------------------------------------------ #
        # Resolution pass: apply .museattributes strategies                   #
        # ------------------------------------------------------------------ #
        applied_strategies: dict[str, str] = {}
        dimension_reports: dict[str, dict[str, str]] = {}
        final_conflicts: list[str] = []

        for path in sorted(real_conflicts):
            file_strategy = resolve_strategy(attrs, path, "*")

            # --- File-level ours/theirs -----------------------------------
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

            # --- MIDI dimension-level merge --------------------------------
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
                            attrs,  # list[AttributeRule]
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

            # --- Remaining true conflicts ----------------------------------
            # Honour "manual" by forcing non-conflict paths into conflicts too.
            final_conflicts.append(path)

        # Force "manual" strategy onto paths that auto-merged cleanly.
        for path in sorted((left_changed | right_changed) - real_conflicts):
            if path in consensus_deleted:
                continue
            if resolve_strategy(attrs, path, "*") == "manual":
                final_conflicts.append(path)
                applied_strategies[path] = "manual"
                # Restore base version as the conflict placeholder.
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
        """Detect uncommitted changes in ``muse-work/`` relative to *committed*.

        Args:
            committed: The last committed snapshot.
            live:      Either a ``pathlib.Path`` (``muse-work/``) or a snapshot
                       dict representing current live state.

        Returns:
            A :class:`~muse.domain.DriftReport` describing whether and how the
            live state differs from the committed snapshot.
        """
        live_snapshot = self.snapshot(live)
        delta = self.diff(committed, live_snapshot)

        added = delta["added"]
        removed = delta["removed"]
        modified = delta["modified"]
        has_drift = bool(added or removed or modified)

        parts: list[str] = []
        if added:
            parts.append(f"{len(added)} added")
        if removed:
            parts.append(f"{len(removed)} removed")
        if modified:
            parts.append(f"{len(modified)} modified")

        summary = ", ".join(parts) if parts else "working tree clean"

        return DriftReport(has_drift=has_drift, summary=summary, delta=delta)

    # ------------------------------------------------------------------
    # 5. apply — execute a delta against live state (checkout)
    # ------------------------------------------------------------------

    def apply(self, delta: StateDelta, live_state: LiveState) -> LiveState:
        """Apply a delta to produce a new live state.

        Called by ``muse checkout`` after it has physically updated
        ``muse-work/`` (removed deleted files, restored added/modified files
        from the object store). This method provides the domain-level
        post-checkout hook and returns the authoritative new live state.

        Two call modes:

        * ``live_state`` is a ``pathlib.Path`` — files in the workdir have
          already been updated by the caller.  Rescanning the directory is the
          cheapest correct way to get the new state; all the heavy lifting was
          done by the object store.

        * ``live_state`` is a snapshot dict — only in-memory state is
          available.  Removals are applied; added/modified paths cannot be
          resolved without the target snapshot's content hashes, so they are
          left to the caller.

        Args:
            delta:      A delta produced by :meth:`diff`.
            live_state: The workdir path (preferred) or a snapshot dict.

        Returns:
            The updated live state as a ``SnapshotManifest``.
        """
        if isinstance(live_state, pathlib.Path):
            # Physical changes are already on disk — rescan gives correct state.
            return self.snapshot(live_state)

        # In-memory path: apply removals.  Added/modified require target-side
        # hashes that are not carried by the delta; callers that need those
        # should pass the workdir Path instead.
        current_files = dict(live_state["files"])
        for path in delta["removed"]:
            current_files.pop(path, None)
        return SnapshotManifest(files=current_files, domain=_DOMAIN_TAG)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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
plugin = MusicPlugin()

assert isinstance(plugin, MuseDomainPlugin), (
    "MusicPlugin does not satisfy the MuseDomainPlugin protocol"
)

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
SHA-256 content digests.

Delta Format (Phase 1)
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
)

logger = logging.getLogger(__name__)

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
        and excludes any matching paths from the snapshot. Dotfiles are always
        excluded regardless of ``.museignore``.
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
           dimension slices (notes/melodic, harmonic, dynamic, structural).
           Each dimension is merged independently. Dimension-specific
           ``ours``/``theirs`` rules in ``.museattributes`` are honoured.
           Only dimensions where *both* sides changed AND no resolvable rule
           exists cause a true file-level conflict.

        3. **Manual override** — ``manual`` strategy in ``.museattributes``
           forces a path into the conflict list even when the engine would
           normally auto-resolve it.
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


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _diff_modified_file(
    *,
    path: str,
    old_hash: str,
    new_hash: str,
    repo_root: pathlib.Path | None,
) -> DomainOp:
    """Produce the best available op for a modified file.

    Tries deep MIDI diff when possible; falls back to ``ReplaceOp``.
    """
    if path.lower().endswith(".mid") and repo_root is not None:
        from muse.core.object_store import read_object
        from muse.plugins.music.midi_diff import diff_midi_notes

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
plugin = MusicPlugin()

assert isinstance(plugin, MuseDomainPlugin), (
    "MusicPlugin does not satisfy the MuseDomainPlugin protocol"
)

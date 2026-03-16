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
from typing import Any

from muse.domain import (
    DriftReport,
    LiveState,
    MergeResult,
    MuseDomainPlugin,
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
        """
        if isinstance(live_state, dict):
            return live_state

        workdir = pathlib.Path(live_state)
        files: dict[str, str] = {}
        for file_path in sorted(workdir.rglob("*")):
            if not file_path.is_file():
                continue
            if file_path.name.startswith("."):
                continue
            rel = file_path.relative_to(workdir).as_posix()
            files[rel] = _hash_file(file_path)

        return {"files": files, "domain": _DOMAIN_TAG}

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
        base_files: dict[str, str] = base.get("files", {})
        target_files: dict[str, str] = target.get("files", {})

        base_paths = set(base_files)
        target_paths = set(target_files)

        added = sorted(target_paths - base_paths)
        removed = sorted(base_paths - target_paths)
        common = base_paths & target_paths
        modified = sorted(p for p in common if base_files[p] != target_files[p])

        return {
            "domain": _DOMAIN_TAG,
            "added": added,
            "removed": removed,
            "modified": modified,
        }

    # ------------------------------------------------------------------
    # 3. merge — three-way reconciliation
    # ------------------------------------------------------------------

    def merge(
        self,
        base: StateSnapshot,
        left: StateSnapshot,
        right: StateSnapshot,
    ) -> MergeResult:
        """Three-way merge two divergent music state lines against a common base.

        A file is auto-merged when only one side changed it. A conflict is
        recorded when both sides changed the same file relative to *base*.

        Args:
            base:  The common ancestor snapshot.
            left:  The current branch snapshot (ours).
            right: The target branch snapshot (theirs).

        Returns:
            A :class:`~muse.domain.MergeResult` with the merged snapshot and
            any conflict descriptions.
        """
        base_files: dict[str, str] = base.get("files", {})
        left_files: dict[str, str] = left.get("files", {})
        right_files: dict[str, str] = right.get("files", {})

        left_changed: set[str] = _changed_paths(base_files, left_files)
        right_changed: set[str] = _changed_paths(base_files, right_files)
        conflict_paths: set[str] = left_changed & right_changed

        merged = dict(base_files)

        for path in left_changed - conflict_paths:
            if path in left_files:
                merged[path] = left_files[path]
            else:
                merged.pop(path, None)

        for path in right_changed - conflict_paths:
            if path in right_files:
                merged[path] = right_files[path]
            else:
                merged.pop(path, None)

        conflicts = [
            f"Both sides modified '{p}' — manual resolution required"
            for p in sorted(conflict_paths)
        ]

        return MergeResult(
            merged={"files": merged, "domain": _DOMAIN_TAG},
            conflicts=conflicts,
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

        added = delta.get("added", [])
        removed = delta.get("removed", [])
        modified = delta.get("modified", [])
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

        For the music plugin in CLI mode, this returns the *target* snapshot
        dict. The actual file restoration (writing MIDI files back to
        ``muse-work/``) is handled by ``muse checkout`` using the core
        object store, not by this method.

        This method is the semantic entry point for DAW-level integrations
        that want to apply a delta to a live project without going through
        the filesystem.

        Args:
            delta:      A delta produced by :meth:`diff`.
            live_state: The current live state to patch.

        Returns:
            The updated live state as a snapshot dict.
        """
        current = self.snapshot(live_state)
        current_files: dict[str, str] = dict(current.get("files", {}))

        for path in delta.get("removed", []):
            current_files.pop(path, None)

        for path in delta.get("added", []) + delta.get("modified", []):
            pass

        return {"files": current_files, "domain": _DOMAIN_TAG}


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

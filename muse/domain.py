"""MuseDomainPlugin — the five-interface protocol that defines a Muse domain.

Muse provides the DAG engine, content-addressed object store, branching,
lineage walking, topological log graph, and merge base finder. A domain plugin
implements these five interfaces and Muse does the rest.

The music plugin (``muse.plugins.music``) is the reference implementation.
Every other domain — scientific simulation, genomics, 3D spatial design,
spacetime — is a new plugin.
"""
from __future__ import annotations

import pathlib
from dataclasses import dataclass, field
from typing import Protocol, TypedDict, runtime_checkable


# ---------------------------------------------------------------------------
# Named snapshot and delta types
# ---------------------------------------------------------------------------


class SnapshotManifest(TypedDict):
    """Content-addressed snapshot of domain state.

    ``files`` maps workspace-relative POSIX paths to their SHA-256 content
    digests. ``domain`` identifies which plugin produced this snapshot.
    """

    files: dict[str, str]
    domain: str


class DeltaManifest(TypedDict):
    """Minimal change description between two snapshots.

    Each list contains workspace-relative POSIX paths. ``domain`` identifies
    the plugin that produced this delta.
    """

    domain: str
    added: list[str]
    removed: list[str]
    modified: list[str]


# ---------------------------------------------------------------------------
# Type aliases used in the protocol signatures
# ---------------------------------------------------------------------------

#: Live state is either an already-snapshotted manifest dict or a workdir path.
#: The music plugin accepts both: a Path (for CLI commit/status) and a
#: SnapshotManifest dict (for in-memory merge and diff operations).
LiveState = SnapshotManifest | pathlib.Path

#: A content-addressed, immutable snapshot of state at a point in time.
StateSnapshot = SnapshotManifest

#: The minimal change between two snapshots — additions, removals, mutations.
StateDelta = DeltaManifest


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
    """

    merged: StateSnapshot
    conflicts: list[str] = field(default_factory=list)

    @property
    def is_clean(self) -> bool:
        return len(self.conflicts) == 0


@dataclass
class DriftReport:
    """Gap between committed state and current live state.

    ``has_drift`` is ``True`` when the live state differs from the committed
    snapshot. ``summary`` is a human-readable description of what changed.
    ``delta`` is the machine-readable diff for programmatic consumers.
    """

    has_drift: bool
    summary: str = ""
    delta: StateDelta = field(default_factory=lambda: DeltaManifest(
        domain="", added=[], removed=[], modified=[],
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
        This ensures that OS artifacts, build outputs, and domain-specific
        scratch files are never committed, regardless of which plugin is active.
        See ``docs/reference/museignore.md`` for the full format reference.
        """
        ...

    def diff(self, base: StateSnapshot, target: StateSnapshot) -> StateDelta:
        """Compute the minimal delta between two snapshots.

        Returns a ``DeltaManifest`` listing which paths were added, removed,
        or modified. Muse stores deltas alongside commits so that ``muse show``
        can display a human-readable summary without reloading full blobs.
        """
        ...

    def merge(
        self,
        base: StateSnapshot,
        left: StateSnapshot,
        right: StateSnapshot,
    ) -> MergeResult:
        """Three-way merge two divergent state lines against a common base.

        ``base`` is the common ancestor (merge base). ``left`` and ``right``
        are the two divergent snapshots. Returns a ``MergeResult`` with the
        reconciled snapshot and any unresolvable conflicts.
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
        """
        ...

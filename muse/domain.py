"""MuseDomainPlugin — the five-interface protocol that defines a Muse domain.

Muse provides the DAG engine, content-addressed object store, branching,
lineage walking, topological log graph, and merge base finder. A domain plugin
implements these five interfaces and Muse does the rest.

The music plugin (``muse.plugins.music``) is the reference implementation.
Every other domain — scientific simulation, genomics, 3D spatial design,
spacetime — is a new plugin.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


# ---------------------------------------------------------------------------
# Shared type primitives
# ---------------------------------------------------------------------------


#: Any serializable, content-addressable representation of live state.
#: Domain plugins define what "state" means for their domain. For music this
#: is a HeadSnapshot (notes, velocities, CC events per region). For genomics
#: it could be a sequence dict. The constraint is: must be JSON-serialisable.
LiveState = Any

#: A content-addressed, immutable snapshot of state at a point in time.
#: Produced by ``snapshot()`` and stored as a blob under ``.muse/objects/``.
StateSnapshot = dict[str, Any]

#: The minimal change between two snapshots — additions, removals, mutations.
#: Domain plugins define the delta structure. The DAG stores these as diffs.
StateDelta = dict[str, Any]


@dataclass
class MergeResult:
    """Outcome of a three-way merge between two divergent state lines.

    ``merged`` is the reconciled snapshot. ``conflicts`` is a list of
    human-readable conflict descriptions that the coordinator must resolve.
    An empty ``conflicts`` list means the merge was clean.
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
    delta: StateDelta = field(default_factory=dict)


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

        The returned dict must be JSON-serialisable. Muse will compute a
        SHA-256 content address from the canonical JSON form and store the
        snapshot as a blob in ``.muse/objects/``.

        For the music plugin, ``live_state`` is a ``HeadSnapshot`` dict
        (notes and CC events per region, track routing). The returned
        ``StateSnapshot`` is the same structure, canonicalised for hashing.
        """
        ...

    def diff(self, base: StateSnapshot, target: StateSnapshot) -> StateDelta:
        """Compute the minimal delta between two snapshots.

        The returned ``StateDelta`` describes what changed between ``base``
        and ``target``. For music: added/removed notes, changed velocities,
        updated CC events. For genomics: CRISPR edits. For simulation:
        changed parameter values.

        Muse stores deltas alongside commits so that ``muse show`` can display
        a human-readable summary of what changed without reloading full blobs.
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

        For music: notes that only one side touched are auto-merged; notes
        that both sides touched on the same beat/pitch are conflicts. The
        Composer agent's cognitive architecture resolves those conflicts.
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

        For music: compares the committed MIDI state against the current DAW
        project state. A note that was added in the DAW but not yet committed
        shows up as drift.
        """
        ...

    def apply(self, delta: StateDelta, live_state: LiveState) -> LiveState:
        """Apply a delta to produce a new live state.

        Used by ``muse checkout`` to reconstruct a historical state. Applies
        ``delta`` on top of ``live_state`` and returns the resulting state.

        For music: replays the note additions and removals from ``delta``
        against the current DAW project, producing the target historical take.
        """
        ...

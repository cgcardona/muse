"""Append-only operation log for Muse live collaboration.

The op log is the bridge between real-time collaborative editing and the
immutable commit DAG.  During a live session, operations are appended to
the log as they occur.  At commit time the log is collapsed into a
:class:`~muse.domain.StructuredDelta` and stored with the commit record.

Design principles
-----------------
- **Append-only** — entries are never modified or deleted; the file grows
  monotonically.  Compaction happens through checkpoints (see below).
- **Lamport-clocked** — every entry carries a logical Lamport timestamp
  that imposes a total order across concurrent actors without wall-clock
  coordination.
- **Causally linked** — ``parent_op_ids`` lets any entry declare the ops it
  depends on, enabling causal replay and CRDT join operations downstream.
- **Domain-neutral** — the log stores :class:`~muse.domain.DomainOp` values
  unchanged; the core engine has no opinion about what those ops mean.
- **Checkpoint / compaction** — when a live session crystallises into a Muse
  commit, a checkpoint record is written that marks the current snapshot.
  Subsequent reads return only ops that arrived after the checkpoint.

Layout::

    .muse/op_log/<session_id>/
        ops.jsonl        — one JSON line per OpEntry (append-only)
        checkpoint.json  — most recent checkpoint (snapshot_id + lamport_ts)

Relationship to the commit DAG
-------------------------------
The op log does **not** replace the commit DAG.  It is a staging area:

    live edits → OpLog.append() → ops.jsonl
    session end → OpLog.checkpoint(snapshot_id) → commit record
    commit record → normal Muse commit DAG

Replaying the log from a checkpoint reproduces the snapshot deterministically,
giving the same guarantee as re-running ``git apply`` from a patch file.

Usage::

    from muse.core.op_log import OpLog, make_op_entry

    log = OpLog(repo_root, session_id="session-abc")
    entry = make_op_entry(
        actor_id="counterpoint-bot",
        domain="midi",
        domain_op=my_insert_op,
        lamport_ts=log.next_lamport_ts(),
    )
    log.append(entry)

    delta = log.to_structured_delta("midi")   # collapse for commit
    ckpt = log.checkpoint(snapshot_id)         # crystallise
"""

from __future__ import annotations

import datetime
import json
import logging
import pathlib
import uuid as _uuid_mod
from typing import TypedDict

from muse.domain import DomainOp, StructuredDelta

logger = logging.getLogger(__name__)

_OP_LOG_DIR = ".muse/op_log"


# ---------------------------------------------------------------------------
# Wire-format TypedDicts
# ---------------------------------------------------------------------------


class OpEntry(TypedDict):
    """A single operation in the append-only op log.

    ``op_id``
        Stable UUID4 for this entry — used by consumers to deduplicate
        on replay and by CRDT join to establish causal identity.
    ``actor_id``
        The agent or human identity that produced this op.
    ``lamport_ts``
        Logical Lamport timestamp.  Monotonically increasing within a
        session; used to establish total ordering when wall-clock times
        are unavailable or unreliable.
    ``parent_op_ids``
        Causal parents — op IDs that this entry depends on.  Empty list
        means this entry has no explicit causal dependency (root entry).
        Used by CRDT merge and causal replay.
    ``domain``
        Domain tag matching the :class:`~muse.domain.MuseDomainPlugin`
        that produced this op (e.g. ``"midi"``, ``"code"``).
    ``domain_op``
        The actual typed domain operation.  Stored verbatim.
    ``created_at``
        ISO 8601 UTC wall-clock timestamp when the entry was appended.
        Informational only — use ``lamport_ts`` for ordering.
    ``intent_id``
        Links this op to a coordination intent (from
        :mod:`muse.core.coordination`).  Empty string if not applicable.
    ``reservation_id``
        Links this op to a coordination reservation.  Empty string if not
        applicable.
    """

    op_id: str
    actor_id: str
    lamport_ts: int
    parent_op_ids: list[str]
    domain: str
    domain_op: DomainOp
    created_at: str
    intent_id: str
    reservation_id: str


class OpLogCheckpoint(TypedDict):
    """A snapshot of the op log state at commit time.

    Written by :meth:`OpLog.checkpoint` when a live session crystallises
    into a Muse commit.  Subsequent :meth:`OpLog.replay_since_checkpoint`
    calls return only ops that arrived after this checkpoint.

    ``session_id``
        The session this checkpoint belongs to.
    ``snapshot_id``
        The commit snapshot ID that this checkpoint materialises.  All ops
        up to and including ``lamport_ts`` are captured by this snapshot.
    ``lamport_ts``
        The Lamport timestamp of the last op included in this checkpoint.
    ``op_count``
        Number of op entries in the log at checkpoint time.
    ``created_at``
        ISO 8601 UTC timestamp.
    """

    session_id: str
    snapshot_id: str
    lamport_ts: int
    op_count: int
    created_at: str


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def make_op_entry(
    actor_id: str,
    domain: str,
    domain_op: DomainOp,
    lamport_ts: int,
    *,
    parent_op_ids: list[str] | None = None,
    intent_id: str = "",
    reservation_id: str = "",
) -> OpEntry:
    """Create a new :class:`OpEntry` with a fresh UUID op_id.

    Args:
        actor_id:        Agent or human identity string.
        domain:          Domain tag (e.g. ``"midi"``).
        domain_op:       The typed domain operation to log.
        lamport_ts:      Logical Lamport timestamp for this entry.
        parent_op_ids:   Causal dependencies.  Defaults to empty list.
        intent_id:       Optional coordination intent linkage.
        reservation_id:  Optional coordination reservation linkage.

    Returns:
        A fully populated :class:`OpEntry`.
    """
    return OpEntry(
        op_id=str(_uuid_mod.uuid4()),
        actor_id=actor_id,
        lamport_ts=lamport_ts,
        parent_op_ids=list(parent_op_ids or []),
        domain=domain,
        domain_op=domain_op,
        created_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        intent_id=intent_id,
        reservation_id=reservation_id,
    )


# ---------------------------------------------------------------------------
# OpLog
# ---------------------------------------------------------------------------


class OpLog:
    """Append-only operation log for a single live collaboration session.

    Each session gets its own directory under ``.muse/op_log/<session_id>/``.
    The log file is JSON-lines: one :class:`OpEntry` per line.  The checkpoint
    file is a single JSON object written atomically when a session is committed.

    Args:
        repo_root:  Repository root (the directory containing ``.muse/``).
        session_id: Stable identifier for this collaboration session.  Use a
                    UUID, a branch name, or any stable string.  The session
                    directory is created on first :meth:`append`.
    """

    def __init__(self, repo_root: pathlib.Path, session_id: str) -> None:
        self._repo_root = repo_root
        self._session_id = session_id
        self._session_dir = repo_root / _OP_LOG_DIR / session_id
        self._ops_path = self._session_dir / "ops.jsonl"
        self._checkpoint_path = self._session_dir / "checkpoint.json"
        self._lamport: int = 0

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ensure_dir(self) -> None:
        self._session_dir.mkdir(parents=True, exist_ok=True)

    def _load_lamport(self) -> int:
        """Return the highest lamport_ts seen in the log so far."""
        if not self._ops_path.exists():
            return 0
        highest = 0
        with self._ops_path.open() as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry: OpEntry = json.loads(line)
                    highest = max(highest, entry.get("lamport_ts", 0))
                except json.JSONDecodeError:
                    continue
        return highest

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def next_lamport_ts(self) -> int:
        """Return the next Lamport timestamp to use, advancing the counter.

        The counter is initialised lazily from the highest value found in the
        log on first call (so that a reopened session continues from where it
        left off).

        Returns:
            Monotonically increasing integer.
        """
        if self._lamport == 0:
            self._lamport = self._load_lamport()
        self._lamport += 1
        return self._lamport

    def append(self, entry: OpEntry) -> None:
        """Append *entry* to the op log.

        The entry is serialised as a single JSON line and flushed to disk.
        This is the only write operation on the log file; entries are never
        modified or deleted.

        Args:
            entry: A fully populated :class:`OpEntry`.
        """
        self._ensure_dir()
        line = json.dumps(entry, separators=(",", ":")) + "\n"
        with self._ops_path.open("a") as fh:
            fh.write(line)
        logger.debug(
            "✅ OpLog append: actor=%r domain=%r ts=%d",
            entry["actor_id"],
            entry["domain"],
            entry["lamport_ts"],
        )

    def read_all(self) -> list[OpEntry]:
        """Return all entries in the log, in append order.

        Returns:
            List of :class:`OpEntry` dicts, oldest first.
        """
        if not self._ops_path.exists():
            return []
        entries: list[OpEntry] = []
        with self._ops_path.open() as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    logger.warning("⚠️ Corrupt op log line in %s: %s", self._ops_path, exc)
        return entries

    def replay_since_checkpoint(self) -> list[OpEntry]:
        """Return entries that arrived after the last checkpoint.

        If no checkpoint exists, returns all entries (equivalent to
        :meth:`read_all`).

        Returns:
            List of :class:`OpEntry` dicts since last checkpoint, oldest first.
        """
        checkpoint = self.read_checkpoint()
        all_entries = self.read_all()
        if checkpoint is None:
            return all_entries
        cutoff = checkpoint["lamport_ts"]
        return [e for e in all_entries if e["lamport_ts"] > cutoff]

    def to_structured_delta(self, domain: str) -> StructuredDelta:
        """Collapse all entries since the last checkpoint into a StructuredDelta.

        Ops are ordered by Lamport timestamp.  Ops from domains other than
        *domain* are filtered out (a session may carry cross-domain ops from
        coordinated agents; each domain collapses its own slice).

        Args:
            domain: Domain tag to filter by (e.g. ``"midi"``).

        Returns:
            A :class:`~muse.domain.StructuredDelta` with the ordered op list
            and a simple count summary.
        """
        entries = self.replay_since_checkpoint()
        entries.sort(key=lambda e: e["lamport_ts"])
        ops = [e["domain_op"] for e in entries if e["domain"] == domain]

        counts: dict[str, int] = {}
        for op in ops:
            kind = op.get("op", "unknown")
            counts[kind] = counts.get(kind, 0) + 1
        parts = [f"{v} {k}" for k, v in sorted(counts.items())]
        summary = ", ".join(parts) if parts else "no ops"

        return StructuredDelta(domain=domain, ops=ops, summary=summary)

    def checkpoint(self, snapshot_id: str) -> OpLogCheckpoint:
        """Write a checkpoint recording that all current ops are in *snapshot_id*.

        After a checkpoint, :meth:`replay_since_checkpoint` will only return
        ops that arrive after this call.  The op log file itself is never
        truncated — the checkpoint is a logical marker.

        Args:
            snapshot_id: The Muse snapshot ID that captured all ops to date.

        Returns:
            The written :class:`OpLogCheckpoint`.
        """
        all_entries = self.read_all()
        highest_ts = max((e["lamport_ts"] for e in all_entries), default=0)
        ckpt = OpLogCheckpoint(
            session_id=self._session_id,
            snapshot_id=snapshot_id,
            lamport_ts=highest_ts,
            op_count=len(all_entries),
            created_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        )
        self._ensure_dir()
        self._checkpoint_path.write_text(
            json.dumps(ckpt, indent=2) + "\n"
        )
        logger.info(
            "✅ OpLog checkpoint: session=%r snapshot=%s ts=%d ops=%d",
            self._session_id,
            snapshot_id[:8],
            highest_ts,
            len(all_entries),
        )
        return ckpt

    def read_checkpoint(self) -> OpLogCheckpoint | None:
        """Load the most recent checkpoint, or ``None`` if none exists."""
        if not self._checkpoint_path.exists():
            return None
        try:
            raw: OpLogCheckpoint = json.loads(self._checkpoint_path.read_text())
            return raw
        except (json.JSONDecodeError, KeyError) as exc:
            logger.warning("⚠️ Corrupt checkpoint file %s: %s", self._checkpoint_path, exc)
            return None

    def session_id(self) -> str:
        """Return the session ID for this log."""
        return self._session_id


# ---------------------------------------------------------------------------
# Session listing
# ---------------------------------------------------------------------------


def list_sessions(repo_root: pathlib.Path) -> list[str]:
    """Return all session IDs that have op log directories under *repo_root*.

    Args:
        repo_root: Repository root.

    Returns:
        Sorted list of session ID strings.
    """
    log_dir = repo_root / _OP_LOG_DIR
    if not log_dir.exists():
        return []
    return sorted(p.name for p in log_dir.iterdir() if p.is_dir())

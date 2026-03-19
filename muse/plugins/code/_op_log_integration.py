"""Op-log integration for the code domain.

When the code plugin computes a structured delta (during ``muse commit``),
the individual symbol-level operations can be recorded in the append-only op
log for real-time replay.  This enables:

- Multiple agents to watch a live op stream as code changes are committed.
- Post-hoc causal ordering of concurrent edits.
- Replay of changes at any point in history with Lamport-clock ordering.

Usage
-----
Call :func:`record_delta_ops` after the code plugin computes its delta::

    from muse.plugins.code._op_log_integration import record_delta_ops

    delta = plugin.diff(base, target, repo_root=root)
    record_delta_ops(root, delta, session_id="my-session", actor_id="agent-x")

The ops are then visible via ``muse.core.op_log.OpLog(root, session_id).read_all()``.

Public API
----------
- :func:`record_delta_ops`  — write a ``StructuredDelta``'s ops to the op log.
- :func:`open_code_session` — open (or create) a named op log session.
"""

from __future__ import annotations

import logging
import pathlib

from muse.core.op_log import OpLog, make_op_entry
from muse.domain import DomainOp, StructuredDelta

logger = logging.getLogger(__name__)

_DOMAIN = "code"


def open_code_session(
    repo_root: pathlib.Path,
    session_id: str,
) -> OpLog:
    """Open (or create) an op log session for code domain recording.

    Args:
        repo_root:  Repository root.
        session_id: Stable session identifier (e.g. branch name or UUID).

    Returns:
        An :class:`~muse.core.op_log.OpLog` instance ready for appending.
    """
    return OpLog(repo_root, session_id)


def record_delta_ops(
    repo_root: pathlib.Path,
    delta: StructuredDelta,
    *,
    session_id: str,
    actor_id: str,
    parent_op_ids: list[str] | None = None,
) -> list[str]:
    """Write a ``StructuredDelta``'s ops into the append-only op log.

    Each top-level op in *delta* becomes one :class:`~muse.core.op_log.OpEntry`.
    Child ops (from ``PatchOp.child_ops``) are also appended, each with the
    parent op's ID as their causal parent.

    Args:
        repo_root:      Repository root.
        delta:          The structured delta to record.
        session_id:     Session to append to (created if it doesn't exist).
        actor_id:       Agent or human who produced the delta.
        parent_op_ids:  Causal parent op IDs (from a prior checkpoint).

    Returns:
        List of op IDs appended to the log (for use as causal parents
        in subsequent calls).
    """
    log = OpLog(repo_root, session_id)
    appended_ids: list[str] = []
    causal_parents = list(parent_op_ids or [])

    # Use the log's current size as the starting Lamport timestamp.
    lamport = len(log.read_all())

    for op in delta.get("ops", []):
        entry = make_op_entry(
            actor_id=actor_id,
            domain=_DOMAIN,
            domain_op=op,
            lamport_ts=lamport,
            parent_op_ids=causal_parents,
        )
        log.append(entry)
        op_id: str = entry["op_id"]
        appended_ids.append(op_id)
        lamport += 1

        # Record child ops (symbol-level changes within a file — PatchOp only).
        child_ops: list[DomainOp] = []
        if op.get("op") == "patch" and op["op"] == "patch":
            child_ops = op["child_ops"]
        for child_op in child_ops:
            child_entry = make_op_entry(
                actor_id=actor_id,
                domain=_DOMAIN,
                domain_op=child_op,
                lamport_ts=lamport,
                parent_op_ids=[op_id],  # causal child of the file op
            )
            log.append(child_entry)
            appended_ids.append(child_entry["op_id"])
            lamport += 1

    return appended_ids

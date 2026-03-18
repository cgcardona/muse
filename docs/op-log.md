# Op Log: Append-Only Operation Log

## Purpose

The op log is the staging area between real-time collaborative edits and
the immutable Muse commit DAG.  During a live session, every operation is
appended to the log as it occurs.  At commit time, the log is collapsed into
a `StructuredDelta` and stored with the commit record.

## Why not just commit frequently?

The commit DAG is optimised for immutability and verifiability, not for
sub-second edit throughput.  A live AI agent producing 100 note edits per
second cannot commit 100 times per second — the overhead of hashing, writing
to the object store, and updating refs would dominate.

The op log is optimised for append throughput: it is a flat JSON-lines file
with no locking beyond OS-level file appends.  A checkpoint converts the
accumulated ops into a single commit.

## Structure

```
.muse/op_log/<session_id>/
    ops.jsonl        — one JSON line per OpEntry (append-only)
    checkpoint.json  — most recent checkpoint record
```

## OpEntry fields

| Field | Description |
|-------|-------------|
| `op_id` | UUID4 — stable identifier for this operation |
| `actor_id` | Agent or human identity |
| `lamport_ts` | Logical Lamport timestamp for causal ordering |
| `parent_op_ids` | Causal dependencies (empty = root entry) |
| `domain` | Domain tag (`"music"`, `"code"`, …) |
| `domain_op` | The typed domain operation |
| `created_at` | ISO 8601 wall-clock timestamp (informational) |
| `intent_id` | Coordination intent linkage (empty if none) |
| `reservation_id` | Coordination reservation linkage (empty if none) |

## Lamport timestamps

Lamport timestamps provide total ordering across concurrent actors without
wall-clock coordination.  Each actor maintains a counter; every new entry
increments it.  When two actors merge their logs, the resulting Lamport
clock continues from `max(a.lamport, b.lamport) + 1`.

The `OpLog` class initialises its counter from the highest value found in
the log file on first access, so that a reopened session continues correctly.

## Checkpoints

A checkpoint marks the point where all ops up to a given Lamport timestamp
have been crystallised into a Muse commit:

```python
ckpt = log.checkpoint(snapshot_id="snap-abc123")
```

After a checkpoint, `replay_since_checkpoint()` returns only ops that arrived
after the checkpoint — enabling incremental application without re-reading the
full log.

The log file itself is never truncated.  Compaction (deleting old log files)
is a separate archival operation outside the scope of this module.

## Lifecycle

```
live edits → OpLog.append()           → ops.jsonl
session end → OpLog.to_structured_delta() → StructuredDelta
commit      → OpLog.checkpoint(snap)  → checkpoint.json
                                      → normal Muse commit DAG
```

## Domain neutrality

The op log stores `DomainOp` values unchanged.  The core engine has no
opinion about what those ops mean.  Each domain plugin collapses its own
slice using `OpLog.to_structured_delta(domain)`.

## Related files

| File | Role |
|------|------|
| `muse/core/op_log.py` | `OpEntry`, `OpLogCheckpoint`, `OpLog`, `list_sessions` |
| `muse/domain.py` | `DomainOp`, `StructuredDelta` |
| `tests/test_op_log.py` | Unit tests |

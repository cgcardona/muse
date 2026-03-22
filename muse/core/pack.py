"""Muse pack format — bundle of commits, snapshots, and blobs for wire transfer.

A :class:`PackBundle` is the unit of exchange between the Muse CLI and a remote
(e.g. MuseHub). It carries everything needed to reconstruct a slice of commit
history locally:

- :class:`CommitDict` records (full metadata)
- :class:`SnapshotDict` records (file manifests)
- :class:`ObjectPayload` entries (raw blob bytes, base64-encoded for JSON)
- ``branch_heads`` mapping (branch name → commit ID, reflecting remote state)

:func:`build_pack` collects all data reachable from a set of commit IDs.
:func:`apply_pack` writes a bundle into a local ``.muse/`` directory.

JSON + base64 encoding trades some efficiency for universal debuggability and
zero external dependencies. A binary-encoded transport can be plugged in later
by swapping the ``HttpTransport`` implementation behind the ``MuseTransport``
Protocol in :mod:`muse.core.transport`.
"""

from __future__ import annotations

import base64
import collections
import logging
import pathlib
from typing import TypedDict

from muse.core.object_store import read_object, write_object
from muse.core.store import (
    CommitDict,
    CommitRecord,
    SnapshotDict,
    SnapshotRecord,
    read_commit,
    read_snapshot,
    write_commit,
    write_snapshot,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Wire-format TypedDicts
# ---------------------------------------------------------------------------


class ObjectPayload(TypedDict):
    """A single content-addressed blob, base64-encoded for JSON transport."""

    object_id: str
    content_b64: str


class PackBundle(TypedDict, total=False):
    """The unit of exchange between the Muse CLI and a remote.

    All fields are optional so that partial bundles (fetch-only, objects-only)
    are valid wire messages. Callers check for presence before consuming.
    """

    commits: list[CommitDict]
    snapshots: list[SnapshotDict]
    objects: list[ObjectPayload]
    #: Remote branch heads at the time the bundle was produced.
    branch_heads: dict[str, str]


class RemoteInfo(TypedDict):
    """Repository metadata returned by ``GET {url}/refs``."""

    repo_id: str
    domain: str
    #: Maps branch name → commit ID for every branch on the remote.
    branch_heads: dict[str, str]
    default_branch: str


class PushResult(TypedDict):
    """Server response after a push attempt."""

    ok: bool
    message: str
    #: Updated branch heads on the remote after the push (if successful).
    branch_heads: dict[str, str]


class FetchRequest(TypedDict, total=False):
    """Body of ``POST {url}/fetch`` — negotiates which commits to transfer.

    ``want`` lists commit IDs the client wants to receive.
    ``have`` lists commit IDs already present locally, allowing the server
    to send only the commits the client lacks (delta negotiation).
    """

    want: list[str]
    have: list[str]


class ApplyResult(TypedDict):
    """Counts returned by :func:`apply_pack` describing what was written.

    ``objects_skipped`` counts blobs already present in the store (not
    rewritten, idempotent).  All other counts reflect *new* writes only.
    """

    commits_written: int
    snapshots_written: int
    objects_written: int
    objects_skipped: int


# ---------------------------------------------------------------------------
# Pack building
# ---------------------------------------------------------------------------


def build_pack(
    repo_root: pathlib.Path,
    commit_ids: list[str],
    *,
    have: list[str] | None = None,
) -> PackBundle:
    """Assemble a :class:`PackBundle` from *commit_ids*, excluding commits in *have*.

    Performs a BFS walk of the commit graph from every ID in *commit_ids*,
    stopping at any commit already in *have*.  Collects all snapshot manifests
    and object blobs reachable from the selected commits.  Object bytes are
    base64-encoded for JSON transport.

    Missing objects or snapshots are logged and skipped — the caller decides
    whether that constitutes an error.

    Args:
        repo_root:  Root of the Muse repository.
        commit_ids: Tip commit IDs to include (e.g. current branch HEAD).
        have:       Commit IDs already known to the receiver.  The BFS stops
                    at these, reducing bundle size.  Pass ``None`` or ``[]``
                    to send the full history.

    Returns:
        A :class:`PackBundle` ready for serialisation and transfer.
    """
    have_set: set[str] = set(have or [])

    # BFS walk from every tip, treating have_set as already-visited.
    commits_to_send: list[CommitRecord] = []
    seen: set[str] = set(have_set)
    queue: collections.deque[str] = collections.deque(
        cid for cid in commit_ids if cid not in seen
    )

    while queue:
        cid = queue.popleft()
        if cid in seen:
            continue
        seen.add(cid)
        commit = read_commit(repo_root, cid)
        if commit is None:
            logger.warning("⚠️ build_pack: commit %s not found — skipping", cid[:8])
            continue
        commits_to_send.append(commit)
        if commit.parent_commit_id and commit.parent_commit_id not in seen:
            queue.append(commit.parent_commit_id)
        if commit.parent2_commit_id and commit.parent2_commit_id not in seen:
            queue.append(commit.parent2_commit_id)

    # Unique snapshot IDs referenced by selected commits.
    snapshot_ids: set[str] = {c.snapshot_id for c in commits_to_send}

    snapshot_dicts: list[SnapshotDict] = []
    all_object_ids: set[str] = set()
    for sid in sorted(snapshot_ids):
        snap = read_snapshot(repo_root, sid)
        if snap is None:
            logger.warning("⚠️ build_pack: snapshot %s not found — skipping", sid[:8])
            continue
        snapshot_dicts.append(snap.to_dict())
        all_object_ids.update(snap.manifest.values())

    object_payloads: list[ObjectPayload] = []
    for oid in sorted(all_object_ids):
        raw = read_object(repo_root, oid)
        if raw is None:
            logger.warning("⚠️ build_pack: blob %s absent from store — skipping", oid[:8])
            continue
        object_payloads.append(
            ObjectPayload(
                object_id=oid,
                content_b64=base64.b64encode(raw).decode("ascii"),
            )
        )

    bundle: PackBundle = {
        "commits": [c.to_dict() for c in commits_to_send],
        "snapshots": snapshot_dicts,
        "objects": object_payloads,
    }
    logger.info(
        "✅ Built pack: %d commits, %d snapshots, %d objects",
        len(commits_to_send),
        len(snapshot_dicts),
        len(object_payloads),
    )
    return bundle


# ---------------------------------------------------------------------------
# Pack applying
# ---------------------------------------------------------------------------


def apply_pack(repo_root: pathlib.Path, bundle: PackBundle) -> ApplyResult:
    """Write the contents of *bundle* into a local ``.muse/`` directory.

    Writes in dependency order: objects first (blobs), then snapshots (which
    reference object IDs), then commits (which reference snapshot IDs).  All
    writes are idempotent — already-present items are silently skipped.

    Args:
        repo_root: Root of the Muse repository to write into.
        bundle:    :class:`PackBundle` received from the remote.

    Returns:
        :class:`ApplyResult` with counts of newly written and skipped items.
    """
    objects_written = 0
    objects_skipped = 0
    snapshots_written = 0
    commits_written = 0

    for obj in bundle.get("objects") or []:
        oid = obj.get("object_id", "")
        b64 = obj.get("content_b64", "")
        if not oid or not b64:
            logger.warning("⚠️ apply_pack: blob entry missing fields — skipped")
            continue
        try:
            raw = base64.b64decode(b64)
        except Exception as exc:
            logger.warning("⚠️ apply_pack: bad base64 for %s: %s", oid[:8], exc)
            continue
        if write_object(repo_root, oid, raw):
            objects_written += 1
        else:
            objects_skipped += 1

    for snap_dict in bundle.get("snapshots") or []:
        try:
            snap = SnapshotRecord.from_dict(snap_dict)
            is_new = read_snapshot(repo_root, snap.snapshot_id) is None
            write_snapshot(repo_root, snap)
            if is_new:
                snapshots_written += 1
        except (KeyError, ValueError) as exc:
            logger.warning("⚠️ apply_pack: malformed snapshot — skipped: %s", exc)

    for commit_dict in bundle.get("commits") or []:
        try:
            commit = CommitRecord.from_dict(commit_dict)
            is_new = read_commit(repo_root, commit.commit_id) is None
            write_commit(repo_root, commit)
            if is_new:
                commits_written += 1
        except (KeyError, ValueError) as exc:
            logger.warning("⚠️ apply_pack: malformed commit — skipped: %s", exc)

    logger.info(
        "✅ Applied pack: %d new blobs, %d new snapshots, %d new commits (%d blobs skipped)",
        objects_written,
        snapshots_written,
        commits_written,
        objects_skipped,
    )
    return ApplyResult(
        commits_written=commits_written,
        snapshots_written=snapshots_written,
        objects_written=objects_written,
        objects_skipped=objects_skipped,
    )

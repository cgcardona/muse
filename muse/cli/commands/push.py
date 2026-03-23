"""muse push — upload local commits, snapshots, and objects to a remote.

MWP push protocol
--------------------

``muse push`` uses the Muse Wire Protocol for all remotes that support it,
falling back transparently to the legacy path for older servers.

**Phase 0 — ref discovery:**
    ``GET {url}/refs`` returns current branch heads.  This cheap call also
    establishes the ``have`` anchors used in commit negotiation.

**Phase 1 — object deduplication (MWP):**
    ``POST {url}/filter-objects`` accepts the full list of object IDs the
    client intends to push.  The server returns only the *missing* subset.
    For incremental pushes this reduces the object payload to near-zero.

**Phase 2 — large-object presign (MWP):**
    Objects above :data:`~muse.core.transport.LARGE_OBJECT_THRESHOLD` (64 KB)
    are uploaded directly to S3/R2 via presigned PUT URLs — they never transit
    the API server.  ``local://`` remotes return all IDs in ``inline`` and
    fall back to the pack body automatically.

**Phase 3 — parallel object upload:**
    Remaining (small) objects are batched into chunks of
    :data:`~muse.core.transport.CHUNK_OBJECTS` and uploaded in parallel using
    ``concurrent.futures.ThreadPoolExecutor`` (4 workers by default).

**Phase 4 — commit push:**
    A single ``POST {url}/push`` carries commits and snapshots with an empty
    ``objects`` list (blobs are already on the remote after Phases 1-3).

Fast-forward check
------------------

By default, ``muse push`` requires the remote branch to be an ancestor of the
local branch (a fast-forward update).  If the remote has diverged, the push is
rejected with exit code 1.  Pass ``--force`` to bypass this check.

Upstream tracking
-----------------

Pass ``-u`` / ``--set-upstream`` on first push to record the tracking
relationship between the local branch and the remote branch so that future
``muse pull`` and ``muse push`` invocations can resolve the remote automatically.
"""

from __future__ import annotations

import argparse
import logging
import pathlib
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

from muse.cli.config import (
    get_auth_token,
    get_remote,
    get_remote_head,
    set_remote_head,
    set_upstream,
)
from muse.core.errors import ExitCode
from muse.core.object_store import read_object
from muse.core.pack import (
    ObjectPayload,
    PackBundle,
    PushResult,
    RemoteInfo,
    build_pack,
    collect_object_ids,
)
from muse.core.repo import require_repo
from muse.core.store import get_head_commit_id, read_current_branch
from muse.core.transport import (
    CHUNK_OBJECTS,
    LARGE_OBJECT_THRESHOLD,
    MuseTransport,
    TransportError,
    make_transport,
)

logger = logging.getLogger(__name__)


def _current_branch(root: pathlib.Path) -> str:
    """Return the current branch name from ``.muse/HEAD``."""
    return read_current_branch(root)


def _upload_chunk(
    transport: MuseTransport,
    url: str,
    token: str | None,
    chunk: list[ObjectPayload],
    chunk_num: int,
    total_chunks: int,
) -> tuple[int, int]:
    """Upload one chunk of objects and return (stored, skipped)."""
    print(
        f"  Uploading objects chunk {chunk_num}/{total_chunks} "
        f"({len(chunk)} object(s)) …"
    )
    resp = transport.push_objects(url, token, chunk)
    return resp["stored"], resp["skipped"]


def _upload_presigned(oid: str, url: str, raw: bytes) -> None:
    """PUT *raw* bytes directly to a presigned S3/R2 URL (MWP Phase 3)."""
    req = urllib.request.Request(url=url, data=raw, method="PUT")
    with urllib.request.urlopen(req, timeout=300) as _resp:
        pass


def _push_objects_parallel(
    transport: MuseTransport,
    url: str,
    token: str | None,
    root: pathlib.Path,
    objects: list[ObjectPayload],
    max_workers: int = 4,
) -> tuple[int, int]:
    """Upload *objects* in parallel chunks (MWP Phase 3 + parallel Phase 4).

    Returns total (stored, skipped) across all chunks.
    """
    if not objects:
        return 0, 0

    chunks: list[list[ObjectPayload]] = [
        objects[i : i + CHUNK_OBJECTS] for i in range(0, len(objects), CHUNK_OBJECTS)
    ]
    total_chunks = len(chunks)
    total_stored = 0
    total_skipped = 0

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(_upload_chunk, transport, url, token, chunk, idx + 1, total_chunks): idx
            for idx, chunk in enumerate(chunks)
        }
        for fut in as_completed(futures):
            stored, skipped = fut.result()
            total_stored += stored
            total_skipped += skipped

    return total_stored, total_skipped


def _push_mwp(
    transport: MuseTransport,
    url: str,
    token: str | None,
    root: pathlib.Path,
    local_head: str,
    have: list[str],
    branch: str,
    force: bool,
) -> tuple[PushResult, int, int]:
    """Full MWP push: dedup negotiation → presign → parallel upload → commit push.

    MWP phases executed here:
      Phase 1 — ``POST /filter-objects``: discover which objects are missing.
      Phase 2 — ``POST /presign``: get presigned PUT URLs for large objects.
      Phase 3 — parallel direct-to-storage upload for large objects.
      Phase 4 — parallel chunked ``POST /push/objects`` for small objects.
      Phase 5 — ``POST /push`` with commits + snapshots, empty objects list.
    """
    # ── Phase 1: object deduplication ────────────────────────────────────────
    all_candidate_ids = collect_object_ids(root, [local_head], have=have)
    try:
        missing_ids_list = transport.filter_objects(url, token, all_candidate_ids)
        missing_ids: set[str] = set(missing_ids_list)
        skipped_count = len(all_candidate_ids) - len(missing_ids)
        if skipped_count:
            print(f"  Object dedup: {skipped_count} already on remote, {len(missing_ids)} to upload.")
    except TransportError:
        # Older server without /filter-objects — send everything.
        logger.debug("filter-objects not supported, falling back to full upload")
        missing_ids = set(all_candidate_ids)

    # ── Phase 2: presigned URLs for large objects ─────────────────────────────
    large_ids: list[str] = []
    small_ids: set[str] = set(missing_ids)

    try:
        large_candidates = [
            oid for oid in missing_ids
            if (raw := read_object(root, oid)) is not None and len(raw) > LARGE_OBJECT_THRESHOLD
        ]
        if large_candidates:
            presign_resp = transport.presign_objects(url, token, large_candidates, "put")
            presigned_map = presign_resp["presigned"]
            inline_ids = set(presign_resp["inline"])

            if presigned_map:
                print(f"  Uploading {len(presigned_map)} large object(s) directly …")
                large_ids = [oid for oid in large_candidates if oid in presigned_map]
                large_id_set = set(large_ids)
                small_ids = missing_ids - large_id_set

                with ThreadPoolExecutor(max_workers=8) as pool:
                    futs = {
                        pool.submit(
                            _upload_presigned,
                            oid,
                            presigned_map[oid],
                            read_object(root, oid) or b"",
                        ): oid
                        for oid in large_ids
                    }
                    for fut in as_completed(futs):
                        fut.result()  # re-raise any upload error
            else:
                # Backend is local:// or presign not supported — include all as small.
                small_ids = missing_ids | inline_ids
    except TransportError:
        logger.debug("presign not supported, all objects go through pack")

    # ── Build pack with only missing small objects ─────────────────────────────
    bundle = build_pack(root, [local_head], have=have, only_objects=small_ids)

    small_objects: list[ObjectPayload] = list(bundle.get("objects") or [])

    # ── Phase 3/4: parallel upload of small objects ───────────────────────────
    if small_objects:
        total_chunks = (len(small_objects) + CHUNK_OBJECTS - 1) // CHUNK_OBJECTS
        stored, skipped = _push_objects_parallel(
            transport, url, token, root, small_objects, max_workers=4
        )
        logger.info(
            "✅ push/objects complete: %d stored, %d skipped across %d chunk(s)",
            stored, skipped, total_chunks,
        )
    else:
        print("  No objects to upload.")

    # ── Phase 5: push commits + snapshots ─────────────────────────────────────
    slim_bundle: PackBundle = {
        "commits": bundle.get("commits") or [],
        "snapshots": bundle.get("snapshots") or [],
        "objects": [],
    }
    branch_heads = bundle.get("branch_heads")
    if branch_heads:
        slim_bundle["branch_heads"] = branch_heads

    commits_count = len(slim_bundle.get("commits") or [])
    print(
        f"  Pushing {commits_count} commit(s) "
        f"and {len(slim_bundle.get('snapshots') or [])} snapshot(s) …"
    )
    result = transport.push_pack(url, token, slim_bundle, branch, force)
    total_objects = len(small_objects) + len(large_ids)
    return result, commits_count, total_objects


def _fetch_remote_info_safe(
    transport: MuseTransport,
    url: str,
    token: str | None,
) -> RemoteInfo | None:
    """Call GET /refs on the remote and return its current branch heads.

    Returns ``None`` on any transport error so callers can fall back
    gracefully instead of aborting the whole push.
    """
    try:
        return transport.fetch_remote_info(url, token)
    except TransportError:
        return None


def _all_known_have_anchors(root: pathlib.Path) -> list[str]:
    """Return every commit ID cached in any remote's tracking refs.

    When pushing a new branch (or to a remote with no local tracking cache),
    these commits are our best guess at what the remote already holds.  Any
    remote the user has previously pushed to shares commit ancestry with other
    remotes — using all cached heads as ``have`` anchors ensures ``build_pack``
    only transmits the delta since the nearest shared ancestor.
    """
    remotes_dir = root / ".muse" / "remotes"
    if not remotes_dir.is_dir():
        return []
    heads: list[str] = []
    for ref_file in remotes_dir.rglob("*"):
        if ref_file.is_file():
            commit_id = ref_file.read_text().strip()
            if commit_id:
                heads.append(commit_id)
    return heads


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the push subcommand."""
    parser = subparsers.add_parser(
        "push",
        help="Upload local commits, snapshots, and objects to a remote.",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("remote", nargs="?", default="origin",
                        help="Remote name to push to (default: origin).")
    parser.add_argument("branch_pos", nargs="?", default=None, metavar="BRANCH",
                        help="Branch to push (default: current branch). Same as --branch.")
    parser.add_argument("--branch", "-b", default=None, dest="branch_flag",
                        help="Branch to push (default: current branch).")
    parser.add_argument("-u", "--set-upstream", action="store_true", dest="set_upstream_flag",
                        help="Record upstream tracking for this branch.")
    parser.add_argument("--force", action="store_true", help="Force push even if the remote has diverged.")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> None:
    """Upload local commits, snapshots, and objects to a remote.

    Requires the remote to be a fast-forward of the local branch unless
    ``--force`` is specified.
    """
    remote: str = args.remote
    branch: str | None = getattr(args, "branch_flag", None) or getattr(args, "branch_pos", None)
    set_upstream_flag: bool = args.set_upstream_flag
    force: bool = args.force

    root = require_repo()

    url = get_remote(remote, root)
    if url is None:
        print(f"❌ Remote '{remote}' is not configured.")
        print(f"  Add it with: muse remote add {remote} <url>")
        raise SystemExit(ExitCode.USER_ERROR)

    token = get_auth_token(root, remote_url=url)
    current_branch = _current_branch(root)
    push_branch = branch or current_branch

    local_head = get_head_commit_id(root, push_branch)
    if local_head is None:
        print(f"❌ Branch '{push_branch}' has no commits to push.")
        raise SystemExit(ExitCode.USER_ERROR)

    transport = make_transport(url)

    # Ask the remote what it already has so we never send redundant objects.
    # This single GET /refs call is cheap and gives us authoritative have-anchors
    # regardless of whether we've cached tracking refs locally.
    remote_info = _fetch_remote_info_safe(transport, url, token)
    remote_branch_heads = remote_info["branch_heads"] if remote_info else {}

    # Collect candidate have-anchors from two sources:
    #   1. Live branch heads from GET /refs (what the remote claims to have)
    #   2. All cached tracking refs across every configured remote (commits we
    #      know are shared ancestry because we've pushed them before)
    # Then filter to only commits that exist in the LOCAL object store —
    # build_pack's BFS can only stop at commits it can walk through locally.
    # Commits from the live remote often don't exist locally (e.g. GitHub
    # merge commits never fetched), so without filtering they become no-ops
    # and build_pack falls back to walking the entire history.
    candidate_have = list(remote_branch_heads.values()) + _all_known_have_anchors(root)
    commits_dir = root / ".muse" / "commits"
    # Exclude local_head itself — if it appears in `have` (e.g. because another
    # remote already has this branch), build_pack stops immediately and sends
    # nothing, even though the target remote doesn't have the branch yet.
    have: list[str] = [
        c for c in candidate_have
        if c != local_head and (commits_dir / f"{c}.json").exists()
    ]

    # Use the live remote head when we have it; only fall back to the locally
    # cached tracking ref when the remote was unreachable (remote_info is None).
    # If we did reach the remote and the branch simply isn't there yet, treat it
    # as a new branch (remote_head = None) so we don't skip the push.
    if remote_info is not None:
        remote_head: str | None = remote_branch_heads.get(push_branch)
    else:
        remote_head = get_remote_head(remote, push_branch, root)

    if remote_head == local_head:
        print(f"Everything up to date. Remote {remote}/{push_branch} is already at {local_head[:8]}.")
        return

    print(f"Pushing {push_branch} → {remote}/{push_branch} …")

    try:
        result, commits_sent, objects_sent = _push_mwp(
            transport, url, token, root, local_head, have, push_branch, force
        )
    except TransportError as exc:
        if exc.status_code == 409:
            print(
                f"❌ Push rejected — remote '{remote}/{push_branch}' has diverged.\n"
                "  Pull first (muse pull) or use --force to override."
            )
        else:
            print(f"❌ Push failed: {exc}")
        raise SystemExit(ExitCode.USER_ERROR)

    if not result["ok"]:
        print(f"❌ Push rejected by remote: {result['message']}")
        raise SystemExit(ExitCode.USER_ERROR)

    # Update local tracking pointer to reflect the new remote state.
    updated_head = result["branch_heads"].get(push_branch, local_head)
    set_remote_head(remote, push_branch, updated_head, root)

    if set_upstream_flag:
        set_upstream(push_branch, remote, root)
        print(f"  Upstream set: {push_branch} → {remote}/{push_branch}")

    print(
        f"✅ Pushed {commits_sent} commit(s), {objects_sent} object(s) "
        f"to {remote}/{push_branch} ({updated_head[:8]})"
    )

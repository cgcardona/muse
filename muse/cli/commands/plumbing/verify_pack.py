"""muse plumbing verify-pack — verify the integrity of a PackBundle.

Reads a PackBundle JSON from stdin (or a file) and performs three levels of
integrity checking:

1. **Object integrity** — every ``objects`` entry is base64-decoded and its
   SHA-256 is recomputed.  The digest must match the declared ``object_id``.

2. **Snapshot consistency** — every snapshot in the bundle references only
   object IDs that are either in the bundle itself or already present in the
   local store.  Orphaned manifest entries are reported as failures.

3. **Commit consistency** — every commit in the bundle references a
   ``snapshot_id`` that is either in the bundle or already in the local store.

Pipe from ``pack-objects`` to validate before sending to a remote::

    muse plumbing pack-objects main | muse plumbing verify-pack

Or verify a saved bundle file::

    muse plumbing verify-pack --file bundle.json

Output (JSON, default)::

    {
      "objects_checked":   42,
      "snapshots_checked": 5,
      "commits_checked":   5,
      "all_ok":            true,
      "failures":          []
    }

With failures::

    {
      "objects_checked":   42,
      "snapshots_checked": 5,
      "commits_checked":   5,
      "all_ok": false,
      "failures": [
        {"kind": "object",   "id": "<sha256>", "error": "hash mismatch"},
        {"kind": "snapshot", "id": "<sha256>", "error": "missing object: <sha256>"}
      ]
    }

Plumbing contract
-----------------

- Exit 0: bundle is fully intact.
- Exit 1: one or more integrity failures; malformed JSON input; missing args.
- Exit 3: I/O error reading stdin or the bundle file.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import sys
from typing import TypedDict

import typer

from muse.core.errors import ExitCode
from muse.core.object_store import has_object
from muse.core.repo import require_repo
from muse.core.store import read_snapshot

logger = logging.getLogger(__name__)

app = typer.Typer()

_FORMAT_CHOICES = ("json", "text")
_CHUNK = 65536  # 64 KiB for streaming hash


class _Failure(TypedDict):
    kind: str
    id: str
    error: str


class _VerifyPackResult(TypedDict):
    objects_checked: int
    snapshots_checked: int
    commits_checked: int
    all_ok: bool
    failures: list[_Failure]


@app.callback(invoke_without_command=True)
def verify_pack(
    ctx: typer.Context,
    bundle_file: str = typer.Option(
        "",
        "--file",
        "-i",
        help="Path to a PackBundle JSON file. Reads from stdin when omitted.",
    ),
    quiet: bool = typer.Option(
        False,
        "--quiet",
        "-q",
        help="No output. Exit 0 if all checks pass, exit 1 otherwise.",
    ),
    skip_local_check: bool = typer.Option(
        False,
        "--no-local",
        "-L",
        help="Skip checking the local store for missing snapshot/commit refs. "
        "Useful when verifying a bundle in isolation.",
    ),
    fmt: str = typer.Option(
        "json", "--format", "-f", help="Output format: json or text."
    ),
) -> None:
    """Verify the integrity of a PackBundle.

    Reads a PackBundle JSON from stdin or ``--file`` and checks:

    - Every object's payload decodes and hashes to its declared ID.
    - Every snapshot's manifest references objects present in the bundle or
      the local store.
    - Every commit's snapshot ID is present in the bundle or the local store.

    Use this before sending a bundle to a remote or after receiving one to
    confirm it was not corrupted in transit.
    """
    if fmt not in _FORMAT_CHOICES:
        typer.echo(
            json.dumps(
                {"error": f"Unknown format {fmt!r}. Valid: {', '.join(_FORMAT_CHOICES)}"}
            )
        )
        raise typer.Exit(code=ExitCode.USER_ERROR)

    # Read input.
    if bundle_file:
        try:
            with open(bundle_file, encoding="utf-8") as fh:
                raw = fh.read()
        except OSError as exc:
            typer.echo(json.dumps({"error": f"Cannot read file: {exc}"}))
            raise typer.Exit(code=ExitCode.INTERNAL_ERROR)
    else:
        try:
            raw = sys.stdin.read()
        except OSError as exc:
            typer.echo(json.dumps({"error": f"Cannot read stdin: {exc}"}))
            raise typer.Exit(code=ExitCode.INTERNAL_ERROR)

    try:
        bundle = json.loads(raw)
    except json.JSONDecodeError as exc:
        typer.echo(json.dumps({"error": f"Invalid JSON: {exc}"}))
        raise typer.Exit(code=ExitCode.USER_ERROR)

    if not isinstance(bundle, dict):
        typer.echo(json.dumps({"error": "PackBundle must be a JSON object."}))
        raise typer.Exit(code=ExitCode.USER_ERROR)

    # We need the repo root for local-store checks (optional).
    root = require_repo() if not skip_local_check else None

    failures: list[_Failure] = []

    # -----------------------------------------------------------------------
    # 1. Object integrity — re-hash each base64 payload.
    # -----------------------------------------------------------------------
    bundle_object_ids: set[str] = set()
    objects_raw = bundle.get("objects", [])
    if not isinstance(objects_raw, list):
        typer.echo(json.dumps({"error": "'objects' field must be a list."}))
        raise typer.Exit(code=ExitCode.USER_ERROR)

    for entry in objects_raw:
        if not isinstance(entry, dict):
            failures.append(
                _Failure(kind="object", id="(unknown)", error="entry is not a dict")
            )
            continue
        oid = entry.get("object_id", "")
        b64 = entry.get("content_b64", "")
        if not isinstance(oid, str) or not isinstance(b64, str):
            failures.append(
                _Failure(
                    kind="object",
                    id=str(oid),
                    error="missing or invalid object_id / content_b64 fields",
                )
            )
            continue

        try:
            raw_bytes = base64.b64decode(b64)
        except Exception as exc:
            failures.append(
                _Failure(kind="object", id=oid, error=f"base64 decode failed: {exc}")
            )
            continue

        actual = hashlib.sha256(raw_bytes).hexdigest()
        if actual != oid:
            failures.append(
                _Failure(
                    kind="object",
                    id=oid,
                    error=f"hash mismatch: declared {oid[:12]}… recomputed {actual[:12]}…",
                )
            )
        else:
            bundle_object_ids.add(oid)

    objects_checked = len(objects_raw)

    # -----------------------------------------------------------------------
    # 2. Snapshot consistency — manifest entries must be present.
    # -----------------------------------------------------------------------
    bundle_snapshot_ids: set[str] = set()
    snapshots_raw = bundle.get("snapshots", [])
    if not isinstance(snapshots_raw, list):
        typer.echo(json.dumps({"error": "'snapshots' field must be a list."}))
        raise typer.Exit(code=ExitCode.USER_ERROR)

    for snap_entry in snapshots_raw:
        if not isinstance(snap_entry, dict):
            failures.append(
                _Failure(
                    kind="snapshot", id="(unknown)", error="snapshot entry is not a dict"
                )
            )
            continue
        snap_id = snap_entry.get("snapshot_id", "")
        if not isinstance(snap_id, str):
            failures.append(
                _Failure(kind="snapshot", id="(unknown)", error="missing snapshot_id")
            )
            continue

        bundle_snapshot_ids.add(snap_id)
        manifest = snap_entry.get("manifest", {})
        if not isinstance(manifest, dict):
            continue

        for path, obj_id in manifest.items():
            if not isinstance(obj_id, str):
                continue
            if obj_id in bundle_object_ids:
                continue
            # Check local store if allowed.
            if root is not None and has_object(root, obj_id):
                continue
            failures.append(
                _Failure(
                    kind="snapshot",
                    id=snap_id,
                    error=f"manifest path {path!r} references missing object {obj_id[:12]}…",
                )
            )

    snapshots_checked = len(snapshots_raw)

    # -----------------------------------------------------------------------
    # 3. Commit consistency — snapshot_id must be resolvable.
    # -----------------------------------------------------------------------
    commits_raw = bundle.get("commits", [])
    if not isinstance(commits_raw, list):
        typer.echo(json.dumps({"error": "'commits' field must be a list."}))
        raise typer.Exit(code=ExitCode.USER_ERROR)

    for commit_entry in commits_raw:
        if not isinstance(commit_entry, dict):
            failures.append(
                _Failure(
                    kind="commit", id="(unknown)", error="commit entry is not a dict"
                )
            )
            continue
        commit_id = commit_entry.get("commit_id", "")
        snap_id = commit_entry.get("snapshot_id", "")
        if not isinstance(commit_id, str) or not isinstance(snap_id, str):
            failures.append(
                _Failure(
                    kind="commit",
                    id=str(commit_id),
                    error="missing commit_id or snapshot_id",
                )
            )
            continue

        if snap_id in bundle_snapshot_ids:
            continue
        if root is not None and read_snapshot(root, snap_id) is not None:
            continue
        if not skip_local_check:
            failures.append(
                _Failure(
                    kind="commit",
                    id=commit_id,
                    error=f"references snapshot {snap_id[:12]}… not in bundle or local store",
                )
            )

    commits_checked = len(commits_raw)
    all_ok = len(failures) == 0

    if quiet:
        raise typer.Exit(code=0 if all_ok else ExitCode.USER_ERROR)

    if fmt == "text":
        typer.echo(
            f"objects={objects_checked}  snapshots={snapshots_checked}  "
            f"commits={commits_checked}  all_ok={all_ok}"
        )
        for f in failures:
            typer.echo(f"  FAIL [{f['kind']}] {f['id'][:16]}…  {f['error']}")
        if not all_ok:
            raise typer.Exit(code=ExitCode.USER_ERROR)
        return

    result: _VerifyPackResult = {
        "objects_checked": objects_checked,
        "snapshots_checked": snapshots_checked,
        "commits_checked": commits_checked,
        "all_ok": all_ok,
        "failures": failures,
    }
    typer.echo(json.dumps(result))
    if not all_ok:
        raise typer.Exit(code=ExitCode.USER_ERROR)

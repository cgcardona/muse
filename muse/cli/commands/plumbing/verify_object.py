"""muse plumbing verify-object — verify the integrity of stored objects.

Reads one or more objects from the content-addressed store and re-hashes each
one to confirm that its on-disk content still matches its claimed SHA-256
identity.  Reports the result per object and exits non-zero if any object
fails verification.

This is the integrity primitive used by backup systems, replication agents,
and CI pipelines to detect silent data corruption without a full fsck.

Output (JSON, default)::

    {
      "results": [
        {"object_id": "<sha256>", "ok": true,  "size_bytes": 4096},
        {"object_id": "<sha256>", "ok": false, "size_bytes": 512,
         "error": "hash mismatch: stored <sha256a> recomputed <sha256b>"},
        {"object_id": "<sha256>", "ok": false, "size_bytes": null,
         "error": "object not found in store"}
      ],
      "all_ok": false,
      "checked": 3,
      "failed":  2
    }

Text output (``--format text``)::

    OK    <sha256>  (4096 bytes)
    FAIL  <sha256>  hash mismatch
    FAIL  <sha256>  object not found in store

With ``--quiet``: no output; exits 0 if all pass, exits 1 otherwise.

Plumbing contract
-----------------

- Exit 0: all objects verified successfully.
- Exit 1: one or more objects failed verification; object not found; bad args.
- Exit 3: unexpected I/O error (e.g. disk read failure).
"""

from __future__ import annotations

import hashlib
import json
import logging
import pathlib
from typing import TypedDict

import typer

from muse.core.errors import ExitCode
from muse.core.object_store import object_path
from muse.core.repo import require_repo
from muse.core.validation import validate_object_id

logger = logging.getLogger(__name__)

app = typer.Typer()

_FORMAT_CHOICES = ("json", "text")
_CHUNK = 65536  # 64 KiB read chunks — keeps the heap clean for large blobs


class _ObjectResult(TypedDict):
    object_id: str
    ok: bool
    size_bytes: int | None
    error: str | None


def _verify_one(root: pathlib.Path, object_id: str) -> _ObjectResult:
    """Integrity-check a single object and return its result record.

    Streams the object in 64 KiB chunks to avoid loading large blobs into
    memory.  Returns an :class:`_ObjectResult` — never raises.
    """
    try:
        validate_object_id(object_id)
    except ValueError as exc:
        return {"object_id": object_id, "ok": False, "size_bytes": None, "error": str(exc)}

    dest = object_path(root, object_id)
    if not dest.exists():
        return {
            "object_id": object_id,
            "ok": False,
            "size_bytes": None,
            "error": "object not found in store",
        }

    try:
        size = dest.stat().st_size
        h = hashlib.sha256()
        with dest.open("rb") as fh:
            for chunk in iter(lambda: fh.read(_CHUNK), b""):
                h.update(chunk)
        actual = h.hexdigest()
    except OSError as exc:
        return {
            "object_id": object_id,
            "ok": False,
            "size_bytes": None,
            "error": f"I/O error: {exc}",
        }

    if actual != object_id:
        return {
            "object_id": object_id,
            "ok": False,
            "size_bytes": size,
            "error": (
                f"hash mismatch: stored {object_id[:12]}… "
                f"recomputed {actual[:12]}…"
            ),
        }

    return {"object_id": object_id, "ok": True, "size_bytes": size, "error": None}


@app.callback(invoke_without_command=True)
def verify_object(
    ctx: typer.Context,
    object_ids: list[str] = typer.Argument(
        ..., help="One or more SHA-256 object IDs to verify."
    ),
    quiet: bool = typer.Option(
        False,
        "--quiet",
        "-q",
        help="No output. Exit 0 if all objects are intact, exit 1 otherwise.",
    ),
    fmt: str = typer.Option(
        "json", "--format", "-f", help="Output format: json or text."
    ),
) -> None:
    """Verify the integrity of one or more objects in the store.

    Re-hashes each object's on-disk content and confirms it matches the SHA-256
    identity used as its filename.  Any mismatch indicates silent data
    corruption and is reported as a failure.

    Objects are streamed in 64 KiB chunks — no full blobs are held in memory —
    so this command is safe to run against very large object stores.

    Exit code is 0 only when *every* supplied object passes verification.
    Use in CI or backup scripts to detect corruption early::

        muse plumbing show-ref -f json \\
          | jq -r '.refs[].commit_id' \\
          | xargs muse plumbing verify-object
    """
    if fmt not in _FORMAT_CHOICES:
        typer.echo(
            json.dumps(
                {"error": f"Unknown format {fmt!r}. Valid: {', '.join(_FORMAT_CHOICES)}"}
            )
        )
        raise typer.Exit(code=ExitCode.USER_ERROR)

    if not object_ids:
        typer.echo(json.dumps({"error": "At least one object ID argument is required."}))
        raise typer.Exit(code=ExitCode.USER_ERROR)

    root = require_repo()

    results: list[_ObjectResult] = [_verify_one(root, oid) for oid in object_ids]
    all_ok = all(r["ok"] for r in results)
    failed_count = sum(1 for r in results if not r["ok"])

    if quiet:
        raise typer.Exit(code=0 if all_ok else ExitCode.USER_ERROR)

    if fmt == "text":
        for r in results:
            status = "OK  " if r["ok"] else "FAIL"
            size_str = f"  ({r['size_bytes']} bytes)" if r["size_bytes"] is not None else ""
            err_str = f"  {r['error']}" if not r["ok"] and r["error"] else ""
            typer.echo(f"{status}  {r['object_id']}{size_str}{err_str}")
        if not all_ok:
            raise typer.Exit(code=ExitCode.USER_ERROR)
        return

    typer.echo(
        json.dumps(
            {
                "results": [dict(r) for r in results],
                "all_ok": all_ok,
                "checked": len(results),
                "failed": failed_count,
            }
        )
    )

    if not all_ok:
        raise typer.Exit(code=ExitCode.USER_ERROR)

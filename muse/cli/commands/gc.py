"""``muse gc`` — garbage-collect unreachable objects.

Content-addressed storage accumulates blobs that no live commit can reach.
These orphaned objects are safe to delete.  ``muse gc`` walks the full commit
graph from every live branch and tag, marks every referenced object as
reachable, then removes the rest.

Usage::

    muse gc                  # remove unreachable objects
    muse gc --dry-run        # show what would be removed, touch nothing
    muse gc --verbose        # print each removed object ID

Exit codes::

    0  — success (even if nothing was collected)
    1  — internal error (e.g. corrupt store)
"""

from __future__ import annotations

import json
import logging
from typing import Annotated

import typer

from muse.core.gc import run_gc
from muse.core.repo import require_repo

logger = logging.getLogger(__name__)
app = typer.Typer(help="Remove unreachable objects from the object store.")


def _fmt_bytes(n: int) -> str:
    """Human-readable byte count."""
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KiB"
    return f"{n / (1024 * 1024):.1f} MiB"


@app.callback(invoke_without_command=True)
def gc(
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", "-n", help="Show what would be removed without removing anything."),
    ] = False,
    verbose: Annotated[
        bool,
        typer.Option("--verbose", "-v", help="Print each collected object ID."),
    ] = False,
    fmt: Annotated[
        str,
        typer.Option("--format", "-f", help="Output format: text or json."),
    ] = "text",
) -> None:
    """Remove unreachable objects from the Muse object store.

    Muse stores every tracked file as a content-addressed blob.  Blobs that are
    no longer referenced by any commit, snapshot, branch, or tag are *garbage*.
    This command identifies and removes them, reclaiming disk space.

    Safety: the reachability walk always runs before any deletion.  Use
    ``--dry-run`` to preview the impact before committing to a sweep.
    Agents should pass ``--format json`` to receive a machine-readable result
    with ``collected_count``, ``collected_bytes``, ``reachable_count``,
    ``elapsed_seconds``, ``dry_run``, and ``collected_ids``.

    Examples::

        muse gc               # safe cleanup
        muse gc --dry-run     # preview only
        muse gc --verbose     # show every removed object
        muse gc --format json # machine-readable
    """
    if fmt not in ("text", "json"):
        typer.echo(f"❌ Unknown --format '{fmt}'. Choose text or json.", err=True)
        raise typer.Exit(code=1)

    repo_root = require_repo()
    result = run_gc(repo_root, dry_run=dry_run)

    if fmt == "json":
        typer.echo(json.dumps({
            "collected_count": result.collected_count,
            "collected_bytes": result.collected_bytes,
            "reachable_count": result.reachable_count,
            "elapsed_seconds": result.elapsed_seconds,
            "dry_run": result.dry_run,
            "collected_ids": sorted(result.collected_ids),
        }))
        return

    prefix = "[dry-run] " if dry_run else ""

    if verbose and result.collected_ids:
        typer.echo(f"{prefix}Unreachable objects:")
        for oid in sorted(result.collected_ids):
            typer.echo(f"  {oid}")

    action = "Would remove" if dry_run else "Removed"
    typer.echo(
        f"{prefix}{action} {result.collected_count} object(s) "
        f"({_fmt_bytes(result.collected_bytes)}) "
        f"in {result.elapsed_seconds:.3f}s  "
        f"[{result.reachable_count} reachable]"
    )

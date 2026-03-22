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

import argparse
import json
import logging
import sys

from muse.core.gc import run_gc
from muse.core.repo import require_repo

logger = logging.getLogger(__name__)


def _fmt_bytes(n: int) -> str:
    """Human-readable byte count."""
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KiB"
    return f"{n / (1024 * 1024):.1f} MiB"


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the gc subcommand."""
    parser = subparsers.add_parser(
        "gc",
        help="Remove unreachable objects from the Muse object store.",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--dry-run", "-n", action="store_true",
                        help="Show what would be removed, touch nothing.")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Print each removed object ID.")
    parser.add_argument("--format", "-f", default="text", dest="fmt",
                        help="Output format: text or json.")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> None:
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
    dry_run: bool = args.dry_run
    verbose: bool = args.verbose
    fmt: str = args.fmt

    if fmt not in ("text", "json"):
        from muse.core.validation import sanitize_display
        print(f"❌ Unknown --format '{sanitize_display(fmt)}'. Choose text or json.", file=sys.stderr)
        raise SystemExit(1)

    repo_root = require_repo()
    result = run_gc(repo_root, dry_run=dry_run)

    if fmt == "json":
        print(json.dumps({
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
        print(f"{prefix}Unreachable objects:")
        for oid in sorted(result.collected_ids):
            print(f"  {oid}")

    action = "Would remove" if dry_run else "Removed"
    print(
        f"{prefix}{action} {result.collected_count} object(s) "
        f"({_fmt_bytes(result.collected_bytes)}) "
        f"in {result.elapsed_seconds:.3f}s  "
        f"[{result.reachable_count} reachable]"
    )

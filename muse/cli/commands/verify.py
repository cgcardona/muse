"""``muse verify`` — whole-repository integrity check.

Walks every reachable commit from every branch ref and performs a three-tier
integrity check:

1. Every branch ref points to an existing commit.
2. Every commit's snapshot exists.
3. Every object referenced by every snapshot exists, and (unless
   ``--no-objects``) its SHA-256 is recomputed to detect silent corruption.

This is Muse's equivalent of ``git fsck``.  Run it periodically on long-lived
agent repositories or after recovering from a storage failure.

Usage::

    muse verify                 # full check — re-hashes all objects
    muse verify --no-objects    # existence check only (faster)
    muse verify --quiet         # no output — exit code only
    muse verify --format json   # machine-readable report

Exit codes::

    0 — all checks passed
    1 — one or more integrity failures detected
    3 — I/O error reading repository files
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.validation import sanitize_display
from muse.core.verify import run_verify

logger = logging.getLogger(__name__)


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the verify subcommand."""
    parser = subparsers.add_parser(
        "verify",
        help="Check repository integrity — commits, snapshots, and objects.",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--quiet", "-q", action="store_true",
                        help="No output — exit code only.")
    parser.add_argument("--no-objects", action="store_true", dest="no_objects",
                        help="Existence check only (faster).")
    parser.add_argument("--format", "-f", default="text", dest="fmt",
                        help="Output format: text or json.")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> None:
    """Check repository integrity — commits, snapshots, and objects.

    Walks every reachable commit from every branch ref.  For each commit,
    verifies that the snapshot exists.  For each snapshot, verifies that every
    object file exists and (by default) re-hashes it to detect bit-rot.

    The exit code is 0 when all checks pass, 1 when any failure is found.
    Use ``--quiet`` in scripts that only care about the exit code.

    Examples::

        muse verify                   # full integrity check
        muse verify --no-objects      # fast existence-only check
        muse verify --quiet && echo "healthy"
        muse verify --format json | jq '.failures'
    """
    quiet: bool = args.quiet
    no_objects: bool = args.no_objects
    fmt: str = args.fmt

    if fmt not in {"text", "json"}:
        print(f"❌ Unknown --format '{sanitize_display(fmt)}'. Choose text or json.", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)

    root = require_repo()

    try:
        result = run_verify(root, check_objects=not no_objects)
    except OSError as exc:
        if not quiet:
            print(f"❌ I/O error during verify: {exc}", file=sys.stderr)
        raise SystemExit(ExitCode.INTERNAL_ERROR) from exc

    if quiet:
        raise SystemExit(0 if result["all_ok"] else ExitCode.USER_ERROR)

    if fmt == "json":
        print(json.dumps(dict(result), indent=2))
    else:
        print(f"Checking refs...        {result['refs_checked']} ref(s)")
        print(f"Checking commits...     {result['commits_checked']} commit(s)")
        print(f"Checking snapshots...   {result['snapshots_checked']} snapshot(s)")
        action = "checked" if not no_objects else "verified (existence only)"
        print(f"Checking objects...     {result['objects_checked']} object(s) {action}")

        if result["all_ok"]:
            print("✅ Repository is healthy.")
        else:
            print(f"\n❌ {len(result['failures'])} integrity failure(s):")
            for f in result["failures"]:
                print(f"  {f['kind']:<10} {f['id'][:24]}  {f['error']}")

    raise SystemExit(0 if result["all_ok"] else ExitCode.USER_ERROR)

"""muse reserve — advisory symbol reservation for parallel agents.

Places an advisory lock on one or more symbol addresses.  This does NOT block
other agents from editing those symbols — it is a coordination signal, not an
enforcement mechanism.  Other agents can check existing reservations via
``muse forecast`` or ``muse reconcile`` before starting work.

Why reservations?
-----------------
When millions of agents operate on a codebase simultaneously, merge conflicts
are inevitable *if* agents don't communicate intent.  Reservations give agents
a low-cost way to say "I'm about to touch this function" before they do it,
so that:

1. Other agents can check with ``muse forecast`` and re-route if needed.
2. ``muse plan-merge`` can predict conflicts with higher accuracy.
3. ``muse reconcile`` can recommend merge ordering.

A reservation expires after ``--ttl`` seconds (default: 1 hour) and is never
enforced — the VCS engine ignores them for correctness.  They are purely
advisory.

Usage::

    muse reserve "src/billing.py::compute_total" --run-id agent-42
    muse reserve "src/auth.py::validate_token" "src/auth.py::refresh_token" \\
        --run-id pipeline-7 --ttl 7200
    muse reserve "src/core.py::hash_content" --op rename --run-id refactor-bot

Output::

    ✅ Reserved 1 address(es) for run-id agent-42
       Expires: 2026-03-18T13:00:00+00:00

    ⚠️  Conflict: src/billing.py::compute_total is already reserved
       by run-id agent-41  (expires 2026-03-18T12:30:00+00:00)

Flags:

``--run-id ID``
    Agent/pipeline identifier (required for conflict detection).

``--ttl N``
    Reservation duration in seconds (default: 3600).

``--op OPERATION``
    Declared operation: rename, move, modify, extract, delete.

``--json``
    Emit reservation details as JSON.
"""

from __future__ import annotations

import argparse
import json
import logging

from muse.core.coordination import active_reservations, create_reservation
from muse.core.repo import require_repo
from muse.core.store import read_current_branch

logger = logging.getLogger(__name__)


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the reserve subcommand."""
    parser = subparsers.add_parser(
        "reserve",
        help="Place advisory reservations on symbol addresses.",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "addresses",
        nargs="+",
        metavar="ADDRESS",
        help='Symbol addresses to reserve, e.g. "src/billing.py::compute_total".',
    )
    parser.add_argument(
        "--run-id",
        dest="run_id",
        default="unknown",
        metavar="ID",
        help="Agent/pipeline identifier.",
    )
    parser.add_argument(
        "--ttl",
        type=int,
        default=3600,
        metavar="SECONDS",
        help="Reservation duration in seconds (default: 3600).",
    )
    parser.add_argument(
        "--op",
        dest="operation",
        default=None,
        metavar="OPERATION",
        help="Declared operation: rename, move, modify, extract, delete.",
    )
    parser.add_argument("--json", dest="as_json", action="store_true", help="Emit reservation details as JSON.")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> None:
    """Place advisory reservations on symbol addresses.

    Reservations are write-once, expiry-based advisory signals.  They do not
    block other agents or affect VCS correctness — they enable conflict
    *prediction* via ``muse forecast`` and ``muse reconcile``.

    Multiple addresses can be reserved in one call.  Active reservations by
    other agents on the same addresses are reported as warnings.
    """
    addresses: list[str] = args.addresses
    run_id: str = args.run_id
    ttl: int = args.ttl
    operation: str | None = args.operation
    as_json: bool = args.as_json

    root = require_repo()

    branch = read_current_branch(root)

    # Check for conflicts with existing active reservations.
    existing = active_reservations(root)
    conflicts: list[str] = []
    for addr in addresses:
        for res in existing:
            if addr in res.addresses and res.run_id != run_id:
                conflicts.append(
                    f"  ⚠️  {addr}\n"
                    f"     already reserved by run-id {res.run_id!r}"
                    f"  (expires {res.expires_at.isoformat()[:19]})"
                )

    res = create_reservation(root, run_id, branch, addresses, ttl, operation)

    if as_json:
        print(json.dumps(
            {
                **res.to_dict(),
                "conflicts": conflicts,
            },
            indent=2,
        ))
        return

    if conflicts:
        for c in conflicts:
            print(c)

    print(
        f"\n✅ Reserved {len(addresses)} address(es) for run-id {run_id!r}\n"
        f"   Reservation ID: {res.reservation_id}\n"
        f"   Expires:        {res.expires_at.isoformat()[:19]}"
    )
    if operation:
        print(f"   Operation:      {operation}")
    if conflicts:
        print(
            f"\n   ⚠️  {len(conflicts)} conflict(s) detected. "
            "Run 'muse forecast' for details."
        )

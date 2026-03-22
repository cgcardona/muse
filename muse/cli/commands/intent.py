"""muse intent — declare a specific operation before executing it.

Records a structured intent extending an existing reservation.  Whereas
``muse reserve`` says "I will touch these symbols", ``muse intent`` says
"I will rename src/billing.py::compute_total to compute_invoice_total".

This additional detail enables:

* ``muse forecast`` to compute more precise conflict predictions
  (a rename conflicts differently with a delete than a modify does).
* ``muse plan-merge`` to classify conflicts by a semantic taxonomy.
* Audit trail of what each agent intended before committing.

Usage::

    muse intent "src/billing.py::compute_total" \\
        --op rename --detail "rename to compute_invoice_total" \\
        --reservation-id <UUID>

    muse intent "src/auth.py::validate_token" \\
        --op extract --detail "extract into src/auth/validators.py" \\
        --run-id agent-42

    muse intent "src/core.py::hash_content" --op delete --run-id refactor-bot

Flags:

``--op OPERATION``
    Required. The operation being declared:
    rename | move | modify | extract | delete | inline | split | merge.

``--detail TEXT``
    Human-readable description of the intended change.

``--reservation-id UUID``
    Link to an existing reservation (optional; creates standalone intent if omitted).

``--run-id ID``
    Agent identifier (used when --reservation-id is not provided).

``--json``
    Emit intent details as JSON.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

from muse.core.coordination import create_intent
from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.store import read_current_branch

logger = logging.getLogger(__name__)

_VALID_OPS = frozenset({
    "rename", "move", "modify", "extract", "delete", "inline", "split", "merge",
})


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the intent subcommand."""
    parser = subparsers.add_parser(
        "intent",
        help="Declare a specific operation before executing it.",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "addresses",
        nargs="+",
        metavar="ADDRESS",
        help="Symbol addresses this intent applies to.",
    )
    parser.add_argument(
        "--op",
        dest="operation",
        required=True,
        metavar="OPERATION",
        help="Operation to declare: rename, move, modify, extract, delete, inline, split, merge.",
    )
    parser.add_argument(
        "--detail",
        default="",
        metavar="TEXT",
        help="Human-readable description of the intended change.",
    )
    parser.add_argument(
        "--reservation-id",
        dest="reservation_id",
        default="",
        metavar="UUID",
        help="Link to an existing reservation.",
    )
    parser.add_argument(
        "--run-id",
        dest="run_id",
        default="unknown",
        metavar="ID",
        help="Agent identifier (used when --reservation-id is not provided).",
    )
    parser.add_argument("--json", dest="as_json", action="store_true", help="Emit intent details as JSON.")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> None:
    """Declare a specific operation before executing it.

    ``muse intent`` extends a reservation with operational detail.  The
    operation type enables ``muse forecast`` to compute more precise conflict
    predictions — a rename conflicts differently from a delete.

    Intents are write-once records stored under ``.muse/coordination/intents/``.
    They are purely advisory and never affect VCS correctness.
    """
    addresses: list[str] = args.addresses
    operation: str = args.operation
    detail: str = args.detail
    reservation_id: str = args.reservation_id
    run_id: str = args.run_id
    as_json: bool = args.as_json

    root = require_repo()

    if operation not in _VALID_OPS:
        print(
            f"❌ Unknown operation '{operation}'. "
            f"Valid: {', '.join(sorted(_VALID_OPS))}",
            file=sys.stderr,
        )
        raise SystemExit(ExitCode.USER_ERROR)

    branch = read_current_branch(root)

    intent_record = create_intent(
        root=root,
        reservation_id=reservation_id,
        run_id=run_id,
        branch=branch,
        addresses=addresses,
        operation=operation,
        detail=detail,
    )

    if as_json:
        print(json.dumps(intent_record.to_dict(), indent=2))
        return

    print(
        f"\n✅ Intent recorded\n"
        f"   Intent ID:      {intent_record.intent_id}\n"
        f"   Operation:      {operation}\n"
        f"   Addresses:      {len(addresses)}\n"
        f"   Run ID:         {intent_record.run_id}"
    )
    if detail:
        print(f"   Detail:         {detail}")
    if reservation_id:
        print(f"   Reservation:    {reservation_id}")
    print("\nRun 'muse forecast' to check for predicted conflicts.")

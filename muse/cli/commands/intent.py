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

import json
import logging

import typer

from muse.core.coordination import create_intent
from muse.core.errors import ExitCode
from muse.core.repo import require_repo

logger = logging.getLogger(__name__)

app = typer.Typer()

_VALID_OPS = frozenset({
    "rename", "move", "modify", "extract", "delete", "inline", "split", "merge",
})


@app.callback(invoke_without_command=True)
def intent(
    ctx: typer.Context,
    addresses: list[str] = typer.Argument(
        ..., metavar="ADDRESS...",
        help='Symbol addresses this intent applies to.',
    ),
    operation: str = typer.Option(
        ..., "--op", metavar="OPERATION",
        help="Operation to declare: rename, move, modify, extract, delete, inline, split, merge.",
    ),
    detail: str = typer.Option(
        "", "--detail", metavar="TEXT",
        help="Human-readable description of the intended change.",
    ),
    reservation_id: str = typer.Option(
        "", "--reservation-id", metavar="UUID",
        help="Link to an existing reservation.",
    ),
    run_id: str = typer.Option(
        "unknown", "--run-id", metavar="ID",
        help="Agent identifier (used when --reservation-id is not provided).",
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit intent details as JSON."),
) -> None:
    """Declare a specific operation before executing it.

    ``muse intent`` extends a reservation with operational detail.  The
    operation type enables ``muse forecast`` to compute more precise conflict
    predictions — a rename conflicts differently from a delete.

    Intents are write-once records stored under ``.muse/coordination/intents/``.
    They are purely advisory and never affect VCS correctness.
    """
    root = require_repo()

    if operation not in _VALID_OPS:
        typer.echo(
            f"❌ Unknown operation '{operation}'. "
            f"Valid: {', '.join(sorted(_VALID_OPS))}",
            err=True,
        )
        raise typer.Exit(code=ExitCode.USER_ERROR)

    head_ref = (root / ".muse" / "HEAD").read_text().strip()
    branch = head_ref.removeprefix("refs/heads/").strip()

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
        typer.echo(json.dumps(intent_record.to_dict(), indent=2))
        return

    typer.echo(
        f"\n✅ Intent recorded\n"
        f"   Intent ID:      {intent_record.intent_id}\n"
        f"   Operation:      {operation}\n"
        f"   Addresses:      {len(addresses)}\n"
        f"   Run ID:         {intent_record.run_id}"
    )
    if detail:
        typer.echo(f"   Detail:         {detail}")
    if reservation_id:
        typer.echo(f"   Reservation:    {reservation_id}")
    typer.echo("\nRun 'muse forecast' to check for predicted conflicts.")

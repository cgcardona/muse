"""muse bitcoin halving — halving epoch, subsidy, and countdown.

Shows the current halving epoch, block subsidy, and the number of blocks
remaining until the next supply halving event.

Usage::

    muse bitcoin halving
    muse bitcoin halving --height 850000
    muse bitcoin halving --json

Output::

    Bitcoin halving — block 850,000

    Epoch:          4  (4th halving era)
    Subsidy:        3.12500000 BTC / block  (312,500,000 sats)
    Next halving:   block 1,050,000
    Blocks left:    200,000  (~2.8 years  ·  ~1,389 days)

    Halving history:
      Epoch 0  block         0  50.00000000 BTC  (genesis)
      Epoch 1  block   210,000  25.00000000 BTC
      Epoch 2  block   420,000  12.50000000 BTC
      Epoch 3  block   630,000   6.25000000 BTC
      Epoch 4  block   840,000   3.12500000 BTC  ◀ current
      Epoch 5  block 1,050,000   1.56250000 BTC  (next)
"""

from __future__ import annotations

import json
import logging

import typer

from muse.core.repo import require_repo
from muse.plugins.bitcoin._loader import (
    load_utxos,
    load_utxos_from_workdir,
    read_current_branch,
    read_repo_id,
)
from muse.plugins.bitcoin._query import (
    _HALVING_INTERVAL,
    _INITIAL_SUBSIDY_SAT,
    _SATS_PER_BTC,
    blocks_until_halving,
    current_subsidy_sat,
    estimated_days_until_halving,
    halving_epoch,
    next_halving_height,
)

logger = logging.getLogger(__name__)
app = typer.Typer()

_MAX_DISPLAY_EPOCHS = 8


@app.callback(invoke_without_command=True)
def halving(
    ctx: typer.Context,
    height: int | None = typer.Option(None, "--height", "-H", metavar="BLOCKS",
        help="Override current block height (default: inferred from latest UTXO)."),
    as_json: bool = typer.Option(False, "--json", help="Emit results as JSON."),
) -> None:
    """Show the current halving epoch, block subsidy, and countdown to the next halving.

    Bitcoin's supply schedule halves every 210 000 blocks (~4 years).  The
    subsidy is the only new BTC entering supply, and each halving reduces it by
    50 %.  Tracking the halving relative to the versioned wallet state lets
    agents reason about the macro supply environment at the time of each
    historical strategy decision.

    The subsidy computation uses Bitcoin Core's exact formula:
    ``50 BTC >> epoch``, which naturally floors to 0 at epoch 33.
    """
    root = require_repo()

    current_height = height
    if current_height is None:
        # Infer from the working tree UTXO set
        utxos = load_utxos_from_workdir(root)
        known = [u["block_height"] for u in utxos if u["block_height"] is not None]
        current_height = max(known) if known else 0

    epoch           = halving_epoch(current_height)
    subsidy_sat     = current_subsidy_sat(current_height)
    subsidy_btc     = subsidy_sat / _SATS_PER_BTC
    blocks_left     = blocks_until_halving(current_height)
    next_height     = next_halving_height(current_height)
    days_left       = estimated_days_until_halving(current_height)
    years_left      = days_left / 365.25

    # Build epoch table (a few before current and a few after)
    history: list[dict[str, int | float | str | bool]] = []
    for ep in range(min(_MAX_DISPLAY_EPOCHS, epoch + 3)):
        ep_height  = ep * _HALVING_INTERVAL
        ep_subsidy = (_INITIAL_SUBSIDY_SAT >> ep) if ep < 33 else 0
        history.append({
            "epoch": ep,
            "start_height": ep_height,
            "subsidy_sat": ep_subsidy,
            "subsidy_btc": round(ep_subsidy / _SATS_PER_BTC, 8),
            "is_current": ep == epoch,
            "is_next": ep == epoch + 1,
        })

    if as_json:
        typer.echo(json.dumps({
            "current_height": current_height,
            "epoch": epoch,
            "subsidy_sat": subsidy_sat,
            "subsidy_btc": round(subsidy_btc, 8),
            "blocks_until_halving": blocks_left,
            "next_halving_height": next_height,
            "estimated_days_until_halving": round(days_left, 1),
            "history": history,
        }, indent=2))
        return

    ordinals = ["", "1st", "2nd", "3rd", "4th", "5th", "6th", "7th", "8th"]
    ord_str  = ordinals[epoch] if epoch < len(ordinals) else f"{epoch}th"

    typer.echo(f"\nBitcoin halving — block {current_height:,}\n")
    typer.echo(f"  Epoch:         {epoch}  ({ord_str} halving era)")
    typer.echo(f"  Subsidy:       {subsidy_btc:.8f} BTC / block  ({subsidy_sat:,} sats)")
    typer.echo(f"  Next halving:  block {next_height:,}")
    typer.echo(f"  Blocks left:   {blocks_left:,}  (~{years_left:.1f} years  ·  ~{days_left:,.0f} days)")
    typer.echo("\n  Halving history:")
    for entry in history:
        ep_n   = int(entry["epoch"])
        ep_h   = int(entry["start_height"])
        ep_btc = float(entry["subsidy_btc"])
        marker = "  ◀ current" if entry["is_current"] else ("  (next)" if entry["is_next"] else "")
        label  = "(genesis)" if ep_n == 0 else ""
        typer.echo(
            f"    Epoch {ep_n}  block {ep_h:>10,}  {ep_btc:>14.8f} BTC  {label}{marker}"
        )

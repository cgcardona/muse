"""muse bitcoin oracle — versioned price and fee oracle history.

Shows the latest oracle price and fee data along with historical trends
from the versioned oracle snapshots.

Usage::

    muse bitcoin oracle
    muse bitcoin oracle --rows 20
    muse bitcoin oracle --commit HEAD~5
    muse bitcoin oracle --json

Output::

    Oracle data — working tree  (last 10 ticks)

    Latest price:  $62,000.00 / BTC  (source: coinbase · block 850,000)
    Latest fees:   1blk: 30 | 6blk: 15 | 144blk: 3 sat/vbyte

    Price history:
      Block   850,000  $62,000.00  coinbase
      Block   849,856  $61,800.00  coinbase
      Block   849,712  $62,100.00  coinbase
      ...

    Fee history:
      Block   850,000  1blk:30 | 6blk:15 | 144blk:3
      ...
"""

from __future__ import annotations

import json
import logging

import typer

from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.store import resolve_commit_ref
from muse.plugins.bitcoin._loader import (
    load_fees,
    load_fees_from_workdir,
    load_prices,
    load_prices_from_workdir,
    read_current_branch,
    read_repo_id,
)
from muse.plugins.bitcoin._query import fee_surface_str, latest_fee_estimate, latest_price

logger = logging.getLogger(__name__)
app = typer.Typer()


@app.callback(invoke_without_command=True)
def oracle(
    ctx: typer.Context,
    ref: str | None = typer.Option(None, "--commit", "-c", metavar="REF",
        help="Read from a historical commit instead of the working tree."),
    rows: int = typer.Option(10, "--rows", "-n", metavar="N",
        help="Number of historical ticks to display."),
    as_json: bool = typer.Option(False, "--json", help="Emit results as JSON."),
) -> None:
    """Show versioned price and fee oracle data with historical trend.

    Oracle data (BTC/USD prices and fee estimates) is a first-class dimension
    of the Bitcoin domain state.  Every agent decision that depends on price
    or fees is made with a specific oracle snapshot in scope — version-
    controlled alongside the UTXO set and strategy.

    This command makes the oracle history auditable: you can see the exact
    price feed that was in effect at each historical commit.
    """
    root = require_repo()
    commit_label = "working tree"

    if ref is not None:
        repo_id = read_repo_id(root)
        branch = read_current_branch(root)
        commit = resolve_commit_ref(root, repo_id, branch, ref)
        if commit is None:
            typer.echo(f"❌ Commit '{ref}' not found.", err=True)
            raise typer.Exit(code=ExitCode.USER_ERROR)
        prices = load_prices(root, commit.commit_id)
        fees   = load_fees(root, commit.commit_id)
        commit_label = commit.commit_id[:8]
    else:
        prices = load_prices_from_workdir(root)
        fees   = load_fees_from_workdir(root)

    latest_p = latest_price(prices)
    latest_f = latest_fee_estimate(fees)

    price_hist = sorted(prices, key=lambda p: p["timestamp"], reverse=True)[:rows]
    fee_hist   = sorted(fees,   key=lambda f: f["timestamp"], reverse=True)[:rows]

    if as_json:
        typer.echo(json.dumps({
            "commit": commit_label,
            "latest_price_usd": latest_p,
            "latest_fee_surface": {
                "1_block": latest_f["target_1_block_sat_vbyte"] if latest_f else None,
                "6_block": latest_f["target_6_block_sat_vbyte"] if latest_f else None,
                "144_block": latest_f["target_144_block_sat_vbyte"] if latest_f else None,
            } if latest_f else None,
            "price_history": [dict(p) for p in price_hist],
            "fee_history": [dict(f) for f in fee_hist],
        }, indent=2))
        return

    typer.echo(f"\nOracle data — {commit_label}  (last {rows} ticks)\n")

    if latest_p:
        src = price_hist[0]["source"] if price_hist else "unknown"
        bh  = price_hist[0]["block_height"] if price_hist else None
        bh_str = f"block {bh:,}" if bh else "unanchored"
        typer.echo(f"  Latest price:  ${latest_p:,.2f} / BTC  (source: {src} · {bh_str})")
    else:
        typer.echo("  Latest price:  (no oracle data)")

    if latest_f:
        typer.echo(f"  Latest fees:   {fee_surface_str(latest_f)}")
    else:
        typer.echo("  Latest fees:   (no oracle data)")

    if price_hist:
        typer.echo(f"\n  Price history ({len(price_hist)} ticks):")
        for p in price_hist:
            bh_str = f"block {p['block_height']:>8,}" if p["block_height"] else "unanchored    "
            typer.echo(f"    {bh_str}  ${p['price_usd']:>12,.2f}  {p['source']}")

    if fee_hist:
        typer.echo(f"\n  Fee history ({len(fee_hist)} ticks):")
        for f in fee_hist:
            bh_str = f"block {f['block_height']:>8,}" if f["block_height"] else "unanchored    "
            typer.echo(f"    {bh_str}  {fee_surface_str(f)}")

"""muse bitcoin pnl — portfolio profit-and-loss between two commits.

Computes the satoshi and USD change in wallet holdings between any two points
in the versioned commit history, anchoring USD values to the oracle price at
each commit.

Usage::

    muse bitcoin pnl --from HEAD~10
    muse bitcoin pnl --from HEAD~30 --to HEAD~5
    muse bitcoin pnl --from abc123 --json

Output::

    Portfolio P&L  abc12345 → HEAD (8 commits)

    Base snapshot   HEAD~10  ·  0.10000000 BTC  ·  $6,200.00 @ $62,000
    Current         HEAD     ·  0.12340000 BTC  ·  $7,650.80 @ $62,000

    Sat delta:       +2,340,000 sats  (+23.40 %)
    USD delta:       +$1,450.80       (+23.40 %)
    Fees paid (est):   50,000 sats
    Net sat delta:   +2,290,000 sats  (gross inflow after fees)
"""

from __future__ import annotations

import json
import logging

import typer

from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.store import get_head_commit_id, resolve_commit_ref
from muse.plugins.bitcoin._loader import (
    load_execution_log,
    load_prices,
    load_utxos,
    read_current_branch,
    read_repo_id,
)
from muse.plugins.bitcoin._query import format_sat
from muse.plugins.bitcoin._analytics import portfolio_pnl, portfolio_snapshot

logger = logging.getLogger(__name__)
app = typer.Typer()

_SATS_PER_BTC = 100_000_000


@app.callback(invoke_without_command=True)
def pnl(
    ctx: typer.Context,
    from_ref: str = typer.Option(..., "--from", "-f", metavar="REF",
        help="Base commit reference (required)."),
    to_ref: str | None = typer.Option(None, "--to", "-t", metavar="REF",
        help="Current commit reference (default: HEAD)."),
    as_json: bool = typer.Option(False, "--json", help="Emit results as JSON."),
) -> None:
    """Compute portfolio profit-and-loss between two commits.

    Reads the UTXO set and oracle price at each commit to produce oracle-
    anchored USD P&L figures.  Fees paid are estimated from the execution log
    stored between the two commits.

    This is the command that makes MUSE fundamentally different from any block
    explorer: you get P&L anchored to the exact price oracle state your agent
    observed when making decisions, not some external API query after the fact.
    Every figure is reproducible and auditable from the commit DAG.
    """
    root = require_repo()
    repo_id = read_repo_id(root)
    branch  = read_current_branch(root)

    base_commit = resolve_commit_ref(root, repo_id, branch, from_ref)
    if base_commit is None:
        typer.echo(f"❌ Base commit '{from_ref}' not found.", err=True)
        raise typer.Exit(code=ExitCode.USER_ERROR)

    if to_ref is not None:
        cur_commit = resolve_commit_ref(root, repo_id, branch, to_ref)
        if cur_commit is None:
            typer.echo(f"❌ Target commit '{to_ref}' not found.", err=True)
            raise typer.Exit(code=ExitCode.USER_ERROR)
        cur_label = to_ref
    else:
        cur_id = get_head_commit_id(root, branch)
        if cur_id is None:
            typer.echo("❌ No commits on current branch.", err=True)
            raise typer.Exit(code=ExitCode.USER_ERROR)
        from muse.core.store import read_commit
        cur_commit = read_commit(root, cur_id)
        cur_label = "HEAD"

    if cur_commit is None:
        typer.echo("❌ Could not resolve target commit.", err=True)
        raise typer.Exit(code=ExitCode.USER_ERROR)

    base_utxos  = load_utxos(root, base_commit.commit_id)
    base_prices = load_prices(root, base_commit.commit_id)
    cur_utxos   = load_utxos(root, cur_commit.commit_id)
    cur_prices  = load_prices(root, cur_commit.commit_id)
    exec_log    = load_execution_log(root, cur_commit.commit_id)

    fees_paid = sum(e["fee_sat"] for e in exec_log if e["fee_sat"] is not None)

    base_snap = portfolio_snapshot(base_utxos, [], base_prices)
    cur_snap  = portfolio_snapshot(cur_utxos,  [], cur_prices)
    result    = portfolio_pnl(base_snap, cur_snap, estimated_fees_paid_sat=fees_paid)

    if as_json:
        typer.echo(json.dumps({
            "base_ref": from_ref,
            "base_commit": base_commit.commit_id[:8],
            "current_ref": cur_label,
            "current_commit": cur_commit.commit_id[:8],
            "base_sat": result["base"]["total_sat"],
            "current_sat": result["current"]["total_sat"],
            "sat_delta": result["sat_delta"],
            "net_sat_delta": result["net_sat_delta"],
            "pct_change": result["pct_change"],
            "base_usd": result["base_usd"],
            "current_usd": result["current_usd"],
            "usd_delta": result["usd_delta"],
            "usd_pct_change": result["usd_pct_change"],
            "estimated_fees_paid_sat": result["estimated_fees_paid_sat"],
        }, indent=2))
        return

    base_price_str = f"@ ${result['base']['price_usd']:,.0f}" if result["base"]["price_usd"] else ""
    cur_price_str  = f"@ ${result['current']['price_usd']:,.0f}" if result["current"]["price_usd"] else ""
    base_usd_str   = f"  ·  ${result['base_usd']:,.2f}" if result["base_usd"] else ""
    cur_usd_str    = f"  ·  ${result['current_usd']:,.2f}" if result["current_usd"] else ""

    typer.echo(f"\nPortfolio P&L  {from_ref} → {cur_label}\n")
    typer.echo(f"  Base snapshot  {from_ref:<10}  "
               f"·  {format_sat(result['base']['total_sat'])}{base_usd_str}  {base_price_str}")
    typer.echo(f"  Current        {cur_label:<10}  "
               f"·  {format_sat(result['current']['total_sat'])}{cur_usd_str}  {cur_price_str}")
    typer.echo("")

    delta_sign  = "+" if result["sat_delta"] >= 0 else ""
    pct_str     = f"  ({delta_sign}{result['pct_change']:.2f} %)" if result["pct_change"] is not None else ""
    typer.echo(f"  Sat delta:       {delta_sign}{format_sat(result['sat_delta'])}{pct_str}")

    if result["usd_delta"] is not None:
        usd_sign = "+" if result["usd_delta"] >= 0 else ""
        usd_pct  = f"  ({usd_sign}{result['usd_pct_change']:.2f} %)" if result["usd_pct_change"] is not None else ""
        typer.echo(f"  USD delta:       {usd_sign}${abs(result['usd_delta']):,.2f}{usd_pct}")

    if fees_paid:
        typer.echo(f"  Fees paid (est): {format_sat(fees_paid)}")
    net_sign = "+" if result["net_sat_delta"] >= 0 else ""
    typer.echo(f"  Net sat delta:   {net_sign}{format_sat(result['net_sat_delta'])}  (gross inflow after fees)")

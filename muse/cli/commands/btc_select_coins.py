"""muse bitcoin select-coins — agent-first coin selection.

Given a target amount and fee rate, selects the optimal UTXO subset using
Branch-and-Bound (or a specified algorithm) before any transaction is broadcast.

Usage::

    muse bitcoin select-coins --target 500000 --fee-rate 10
    muse bitcoin select-coins --target 500000 --fee-rate 10 --algo smallest-first
    muse bitcoin select-coins --target 500000 --fee-rate 10 --json

Output::

    Coin selection — Branch-and-Bound  (target: 500,000 sats · fee rate: 10 sat/vbyte)

    Selected UTXOs (2):
      abc...0:0   p2wpkh   1,000,000 sats
      def...1:0   p2wpkh     200,000 sats
    ──────────────────────────────────────
    Total input:   1,200,000 sats
    Target:          500,000 sats
    Fee (est):         1,221 sats
    Change:          698,779 sats
    Waste score:     698,779 sats  (0 = perfect match)
"""

from __future__ import annotations

import json
import logging

import typer

from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.plugins.bitcoin._loader import (
    load_fees_from_workdir,
    load_utxos_from_workdir,
)
from muse.plugins.bitcoin._query import format_sat, latest_fee_estimate
from muse.plugins.bitcoin._analytics import select_coins
from muse.plugins.bitcoin._types import CoinSelectAlgo

logger = logging.getLogger(__name__)
app = typer.Typer()

_ALGOS: dict[str, CoinSelectAlgo] = {
    "bnb":            "branch_and_bound",
    "largest":        "largest_first",
    "smallest":       "smallest_first",
    "random":         "random",
    "branch_and_bound": "branch_and_bound",
    "largest_first":  "largest_first",
    "smallest_first": "smallest_first",
}


@app.callback(invoke_without_command=True)
def select_coins_cmd(
    ctx: typer.Context,
    target: int = typer.Option(..., "--target", "-t", metavar="SATS",
        help="Amount to send in satoshis (required)."),
    fee_rate: int | None = typer.Option(None, "--fee-rate", "-f", metavar="SAT/VBYTE",
        help="Fee rate in sat/vbyte (default: from oracle)."),
    algo: str = typer.Option("bnb", "--algo", "-a",
        help="Algorithm: bnb | largest | smallest | random."),
    as_json: bool = typer.Option(False, "--json", help="Emit results as JSON."),
) -> None:
    """Select UTXOs to fund a transaction before broadcast.

    Implements four algorithms: Branch-and-Bound (Bitcoin Core's default),
    largest-first, smallest-first (consolidates dust), and random (privacy).
    BnB finds exact-match selections (zero change) when possible, minimising
    the UTXO set growth and long-term fee waste.

    Dust UTXOs (effective value ≤ 0 at the given fee rate) are automatically
    excluded.  The result is agent-actionable: feed ``selected`` UTXOs directly
    into a transaction builder without manual coin control.
    """
    root = require_repo()

    utxo_list = load_utxos_from_workdir(root)
    if not utxo_list:
        typer.echo("❌ No UTXOs in working tree.", err=True)
        raise typer.Exit(code=ExitCode.USER_ERROR)

    if fee_rate is None:
        fees = load_fees_from_workdir(root)
        est = latest_fee_estimate(fees)
        fee_rate = est["target_6_block_sat_vbyte"] if est else 10

    algo_key = _ALGOS.get(algo.lower())
    if algo_key is None:
        typer.echo(f"❌ Unknown algorithm '{algo}'. Use: bnb | largest | smallest | random", err=True)
        raise typer.Exit(code=ExitCode.USER_ERROR)

    result = select_coins(
        utxos=utxo_list,
        target_sat=target,
        fee_rate_sat_vbyte=fee_rate,
        algorithm=algo_key,
    )

    if as_json:
        typer.echo(json.dumps({
            "success": result["success"],
            "algorithm": result["algorithm"],
            "target_sat": result["target_sat"],
            "fee_rate_sat_vbyte": fee_rate,
            "selected_count": len(result["selected"]),
            "total_input_sat": result["total_input_sat"],
            "fee_sat": result["fee_sat"],
            "change_sat": result["change_sat"],
            "waste_score": result["waste_score"],
            "failure_reason": result["failure_reason"],
            "selected": [
                {"key": f"{u['txid']}:{u['vout']}", "amount_sat": u["amount_sat"],
                 "script_type": u["script_type"]}
                for u in result["selected"]
            ],
        }, indent=2))
        return

    if not result["success"]:
        typer.echo(f"❌ Coin selection failed: {result['failure_reason']}", err=True)
        raise typer.Exit(code=ExitCode.USER_ERROR)

    algo_display = result["algorithm"].replace("_", " ").title()
    typer.echo(f"\nCoin selection — {algo_display}  "
               f"(target: {format_sat(target)} · fee rate: {fee_rate} sat/vbyte)\n")
    typer.echo(f"  Selected UTXOs ({len(result['selected'])}):")
    for u in result["selected"]:
        key = f"{u['txid'][:6]}…:{u['vout']}"
        typer.echo(f"    {key:<16}  {u['script_type']:<8}  {format_sat(u['amount_sat'])}")
    typer.echo("  " + "─" * 42)
    typer.echo(f"  Total input:  {format_sat(result['total_input_sat']):>22}")
    typer.echo(f"  Target:       {format_sat(result['target_sat']):>22}")
    typer.echo(f"  Fee (est):    {format_sat(result['fee_sat']):>22}")
    typer.echo(f"  Change:       {format_sat(result['change_sat']):>22}")
    waste = result["waste_score"]
    waste_note = "  (0 = perfect BnB match)" if waste == 0 else ""
    typer.echo(f"  Waste score:  {format_sat(waste):>22}{waste_note}")

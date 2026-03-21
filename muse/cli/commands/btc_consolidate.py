"""muse bitcoin consolidate — UTXO consolidation planner.

Identifies small UTXOs that are worth merging into fewer, larger coins.
Shows the fee cost, expected future savings, and break-even fee rate.

Usage::

    muse bitcoin consolidate
    muse bitcoin consolidate --fee-rate 3 --horizon 20
    muse bitcoin consolidate --json

Output::

    Consolidation plan — working tree  (fee rate: 3 sat/vbyte)

    Recommendation: ✅ Consolidate now

    UTXOs to merge (8):
      a1...0:0  p2wpkh  50,000 sats
      b2...0:0  p2wpkh  48,000 sats
      ...

    Cost:           1,023 sats   (at 3 sat/vbyte)
    Expected saving 8,200 sats   (over 20 future spends)
    Break-even:     at ≥ 2 sat/vbyte future fee rate

    Reason: "Consolidating 8 UTXOs saves ~8,200 sats over 20 transactions."
"""

from __future__ import annotations

import json
import logging

import typer

from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.store import read_current_branch, resolve_commit_ref
from muse.plugins.bitcoin._loader import (
    load_fees,
    load_fees_from_workdir,
    load_utxos,
    load_utxos_from_workdir,
    read_repo_id,
)
from muse.plugins.bitcoin._query import format_sat, latest_fee_estimate
from muse.plugins.bitcoin._analytics import consolidation_plan

logger = logging.getLogger(__name__)
app = typer.Typer()


@app.callback(invoke_without_command=True)
def consolidate(
    ctx: typer.Context,
    ref: str | None = typer.Option(None, "--commit", "-c", metavar="REF",
        help="Read from a historical commit instead of the working tree."),
    fee_rate: int | None = typer.Option(None, "--fee-rate", "-f", metavar="SAT/VBYTE",
        help="Fee rate for the consolidation tx (default: from oracle)."),
    max_inputs: int = typer.Option(50, "--max-inputs", "-m",
        help="Maximum UTXOs to include in one consolidation transaction."),
    horizon: int = typer.Option(10, "--horizon", "-n", metavar="SPENDS",
        help="Number of future spends to amortize savings over."),
    as_json: bool = typer.Option(False, "--json", help="Emit results as JSON."),
) -> None:
    """Plan an optimal UTXO consolidation transaction.

    Selects UTXOs below the median size and computes whether consolidating them
    now — paying a single fee — saves more in future fees than it costs.  The
    break-even fee rate tells you the minimum future fee rate at which today's
    consolidation pays off.

    Agents use this to time consolidations: run during low-fee windows
    (``muse bitcoin fee --target-blocks 144``) to maximise savings.
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
        utxo_list = load_utxos(root, commit.commit_id)
        fees_list = load_fees(root, commit.commit_id)
        commit_label = commit.commit_id[:8]
    else:
        utxo_list = load_utxos_from_workdir(root)
        fees_list = load_fees_from_workdir(root)

    if fee_rate is None:
        est = latest_fee_estimate(fees_list)
        fee_rate = est["target_144_block_sat_vbyte"] if est else 3

    plan = consolidation_plan(
        utxos=utxo_list,
        fee_rate_sat_vbyte=fee_rate,
        max_inputs=max_inputs,
        savings_horizon_spends=horizon,
    )

    if as_json:
        typer.echo(json.dumps({
            "commit": commit_label,
            "fee_rate_sat_vbyte": fee_rate,
            "input_count": plan["input_count"],
            "output_count": plan["output_count"],
            "estimated_fee_sat": plan["estimated_fee_sat"],
            "expected_savings_sat": plan["expected_savings_sat"],
            "savings_horizon_spends": plan["savings_horizon_spends"],
            "break_even_fee_rate": plan["break_even_fee_rate"],
            "recommended": plan["recommended"],
            "reason": plan["reason"],
            "utxos": [
                {"key": f"{u['txid']}:{u['vout']}", "amount_sat": u["amount_sat"]}
                for u in plan["utxos_to_consolidate"]
            ],
        }, indent=2))
        return

    rec_icon = "✅" if plan["recommended"] else "⏳"
    rec_text = "Consolidate now" if plan["recommended"] else "Hold — not worth it yet"

    typer.echo(f"\nConsolidation plan — {commit_label}  (fee rate: {fee_rate} sat/vbyte)\n")
    typer.echo(f"  Recommendation: {rec_icon} {rec_text}\n")

    if plan["input_count"] == 0:
        typer.echo(f"  {plan['reason']}")
        return

    typer.echo(f"  UTXOs to merge ({plan['input_count']}):")
    for u in plan["utxos_to_consolidate"][:10]:
        key = f"{u['txid'][:6]}…:{u['vout']}"
        typer.echo(f"    {key}  {u['script_type']}  {format_sat(u['amount_sat'])}")
    if plan["input_count"] > 10:
        typer.echo(f"    … and {plan['input_count'] - 10} more")

    typer.echo(f"\n  Cost:            {format_sat(plan['estimated_fee_sat'])}   (at {fee_rate} sat/vbyte)")
    typer.echo(f"  Expected saving  {format_sat(plan['expected_savings_sat'])}   (over {horizon} future spends)")
    typer.echo(f"  Break-even:      at ≥ {plan['break_even_fee_rate']} sat/vbyte future fee rate")
    typer.echo(f'\n  Reason: "{plan["reason"]}"')

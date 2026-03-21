"""muse bitcoin dust — dust UTXO analysis and cleanup recommendations.

Identifies UTXOs whose economic value is below the dust threshold at the
current fee rate, shows the total sats locked up as dust, and recommends
whether consolidation or abandonment is appropriate.

Usage::

    muse bitcoin dust
    muse bitcoin dust --fee-rate 20
    muse bitcoin dust --commit HEAD~5
    muse bitcoin dust --json

Output::

    Dust analysis — working tree  (fee rate: 10 sat/vbyte)

    Dust threshold:  1,230 sats  (p2wpkh at 10 sat/vbyte)
    Dust UTXOs:      3  of 9 total  (33 %)
    Dust value:      2,100 sats  (economically locked — unspendable at this fee rate)

    Dust UTXOs:
      abc12345:0  p2wpkh   546 sats   (cost to spend: 410 sats — not worth it)
      def67890:1  p2wpkh   800 sats
      ghi11121:2  p2pkh    754 sats

    Recommendation: consolidate during low-fee window (target 144 blocks)
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
from muse.plugins.bitcoin._query import (
    dust_threshold_sat,
    effective_value_sat,
    estimated_input_vbytes,
    format_sat,
    is_dust,
    latest_fee_estimate,
    total_balance_sat,
    utxo_key,
)

logger = logging.getLogger(__name__)
app = typer.Typer()


@app.callback(invoke_without_command=True)
def dust(
    ctx: typer.Context,
    ref: str | None = typer.Option(None, "--commit", "-c", metavar="REF",
        help="Read from a historical commit instead of the working tree."),
    fee_rate: int | None = typer.Option(None, "--fee-rate", "-f", metavar="SAT/VBYTE",
        help="Fee rate for dust calculation (default: from oracle)."),
    as_json: bool = typer.Option(False, "--json", help="Emit results as JSON."),
) -> None:
    """Find UTXOs that are economically unspendable at the current fee rate.

    A UTXO is dust when its amount is less than three times the fee to spend
    it — Bitcoin Core's definition.  Dust UTXOs accumulate in wallets over
    time (from small change outputs, failed consolidations, etc.) and represent
    sats that are economically locked until fees drop.

    Agents track the dust burden across commits to detect when a strategy is
    generating excessive small change and to schedule cleanup consolidations.
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
        fee_rate = est["target_6_block_sat_vbyte"] if est else 10

    dust_utxos = [u for u in utxo_list if is_dust(u, fee_rate)]
    total_dust  = sum(u["amount_sat"] for u in dust_utxos)
    total_all   = total_balance_sat(utxo_list)
    threshold   = dust_threshold_sat("p2wpkh", fee_rate)

    if as_json:
        typer.echo(json.dumps({
            "commit": commit_label,
            "fee_rate_sat_vbyte": fee_rate,
            "dust_threshold_p2wpkh_sat": threshold,
            "total_utxos": len(utxo_list),
            "dust_count": len(dust_utxos),
            "dust_value_sat": total_dust,
            "dust_utxos": [
                {
                    "key": utxo_key(u),
                    "amount_sat": u["amount_sat"],
                    "script_type": u["script_type"],
                    "effective_value_sat": effective_value_sat(u, fee_rate),
                    "spend_cost_sat": estimated_input_vbytes(u["script_type"]) * fee_rate,
                }
                for u in dust_utxos
            ],
        }, indent=2))
        return

    pct = int(len(dust_utxos) / len(utxo_list) * 100) if utxo_list else 0
    typer.echo(f"\nDust analysis — {commit_label}  (fee rate: {fee_rate} sat/vbyte)\n")
    typer.echo(f"  Dust threshold:  {format_sat(threshold)}  (p2wpkh at {fee_rate} sat/vbyte)")
    typer.echo(f"  Dust UTXOs:      {len(dust_utxos)}  of {len(utxo_list)} total  ({pct} %)")
    typer.echo(f"  Dust value:      {format_sat(total_dust)}  (economically locked at this fee rate)")

    if not dust_utxos:
        typer.echo("\n  ✅ No dust — UTXO set is clean at this fee rate.")
        return

    typer.echo(f"\n  Dust UTXOs:")
    for u in sorted(dust_utxos, key=lambda x: x["amount_sat"]):
        key = utxo_key(u)
        spend_cost = estimated_input_vbytes(u["script_type"]) * fee_rate
        typer.echo(f"    {key:<20}  {u['script_type']:<8}  {format_sat(u['amount_sat']):>16}"
                   f"  (cost to spend: {format_sat(spend_cost)})")

    typer.echo(f"\n  Recommendation: consolidate during low-fee window")
    typer.echo(f"  Run: muse bitcoin consolidate --fee-rate 1 --horizon 20")

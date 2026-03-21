"""muse bitcoin fee — fee-market window analysis and sending recommendation.

Analyses the historical fee-estimate oracle data and gives an actionable
recommendation: send now, wait for lower fees, or bump a stuck transaction.

Usage::

    muse bitcoin fee
    muse bitcoin fee --target-blocks 1
    muse bitcoin fee --commit HEAD~5
    muse bitcoin fee --json

Output::

    Fee window — working tree  (target: 6 blocks)

    Current:  15 sat/vbyte
    History:  min 3  ·  median 12  ·  max 85  sat/vbyte
    Percentile: 42nd  (below median — reasonable conditions)

    Recommendation: ✅ send_now
    "Current rate 15 sat/vbyte is near the historical median. Sending now is reasonable."

    Fee surface (latest):  1blk: 30 | 6blk: 15 | 144blk: 3 sat/vbyte
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
    load_mempool,
    load_mempool_from_workdir,
    read_repo_id,
)
from muse.plugins.bitcoin._query import fee_surface_str, latest_fee_estimate
from muse.plugins.bitcoin._analytics import fee_window

logger = logging.getLogger(__name__)
app = typer.Typer()


@app.callback(invoke_without_command=True)
def fee(
    ctx: typer.Context,
    ref: str | None = typer.Option(None, "--commit", "-c", metavar="REF",
        help="Read from a historical commit instead of the working tree."),
    target_blocks: int = typer.Option(6, "--target-blocks", "-t", metavar="N",
        help="Confirmation target in blocks (1, 6, or 144)."),
    pending_txid: list[str] = typer.Option([], "--pending", "-p", metavar="TXID",
        help="Txid of a stuck pending transaction (triggers RBF recommendation). Repeatable."),
    as_json: bool = typer.Option(False, "--json", help="Emit results as JSON."),
) -> None:
    """Analyse the fee market and recommend whether to send, wait, or RBF.

    Reads the versioned oracle fee history and places the current fee rate in
    its historical context.  If the current rate is in the bottom quartile of
    historical rates, it recommends sending now.  If it is elevated, it
    recommends waiting and estimates how long.  Pass ``--pending`` for any
    stuck txid to trigger an RBF recommendation.

    Agents use this before every transaction to decide timing.  The entire fee
    history is version-controlled: you can replay the agent's fee decision
    at any commit to audit whether it was optimal.
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
        fees_list   = load_fees(root, commit.commit_id)
        mempool_lst = load_mempool(root, commit.commit_id)
        commit_label = commit.commit_id[:8]
    else:
        fees_list   = load_fees_from_workdir(root)
        mempool_lst = load_mempool_from_workdir(root)

    # Merge explicit pending txids with mempool RBF-eligible entries
    mempool_rbf = [t["txid"] for t in mempool_lst if t["rbf_eligible"]]
    all_pending = list(pending_txid) + [t for t in mempool_rbf if t not in pending_txid]

    recommendation = fee_window(fees_list, target_blocks=target_blocks, pending_txids=all_pending or None)
    latest = latest_fee_estimate(fees_list)

    if as_json:
        typer.echo(json.dumps({
            "commit": commit_label,
            "target_blocks": target_blocks,
            **{k: v for k, v in recommendation.items()},
        }, indent=2))
        return

    rec = recommendation["recommendation"]
    rec_icon = {"send_now": "✅", "wait": "⏳", "rbf_now": "🔴", "cpfp_eligible": "🟡"}.get(rec, "")
    pct_int  = int(recommendation["percentile"] * 100)

    typer.echo(f"\nFee window — {commit_label}  (target: {target_blocks} block{'s' if target_blocks != 1 else ''})\n")
    typer.echo(f"  Current:     {recommendation['current_sat_vbyte']} sat/vbyte")
    typer.echo(f"  History:     min {recommendation['historical_min_sat_vbyte']}  "
               f"·  median {recommendation['historical_median_sat_vbyte']}  "
               f"·  max {recommendation['historical_max_sat_vbyte']}  sat/vbyte")
    typer.echo(f"  Percentile:  {pct_int}th")
    typer.echo(f"\n  Recommendation: {rec_icon} {rec}")
    typer.echo(f'  "{recommendation["reason"]}"\n')
    if latest:
        typer.echo(f"  Fee surface (latest):  {fee_surface_str(latest)}")
    if recommendation.get("optimal_wait_blocks"):
        typer.echo(f"  Estimated wait:  {recommendation['optimal_wait_blocks']} blocks")

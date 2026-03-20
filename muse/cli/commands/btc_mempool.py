"""muse bitcoin mempool — local mempool view with RBF analysis.

Shows pending (unconfirmed) transactions from the versioned mempool snapshot,
flagging RBF-eligible transactions and sorting by fee rate.

Usage::

    muse bitcoin mempool
    muse bitcoin mempool --commit HEAD~1
    muse bitcoin mempool --json

Output::

    Mempool — working tree  (3 pending · 0.00150000 BTC · avg 23 sat/vbyte)

    TXID (short)  Amount         Fee rate    RBF   Status
    ─────────────────────────────────────────────────────
    abc12345…   0.00100000 BTC   30 sat/vb  yes   pending
    def67890…   0.00030000 BTC   20 sat/vb  yes   pending
    ghi11121…   0.00020000 BTC   10 sat/vb  no    pending
"""

from __future__ import annotations

import json
import logging

import typer

from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.store import resolve_commit_ref
from muse.plugins.bitcoin._loader import (
    load_mempool,
    load_mempool_from_workdir,
    read_current_branch,
    read_repo_id,
)
from muse.plugins.bitcoin._query import format_sat, mempool_summary_line

logger = logging.getLogger(__name__)
app = typer.Typer()


@app.callback(invoke_without_command=True)
def mempool(
    ctx: typer.Context,
    ref: str | None = typer.Option(None, "--commit", "-c", metavar="REF",
        help="Read from a historical commit instead of the working tree."),
    rbf_only: bool = typer.Option(False, "--rbf", "-r",
        help="Show only RBF-eligible transactions."),
    as_json: bool = typer.Option(False, "--json", help="Emit results as JSON."),
) -> None:
    """Show pending transactions from the versioned mempool snapshot.

    The local mempool is a volatile dimension of Bitcoin state — every agent
    run may observe a different set of pending transactions.  Version-controlling
    it with Muse means you can audit exactly what the mempool looked like when
    a strategy decision was made.

    RBF-eligible transactions can be fee-bumped if they become stuck.  Use
    ``muse bitcoin fee --pending TXID`` to get a bump recommendation.
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
        tx_list = load_mempool(root, commit.commit_id)
        commit_label = commit.commit_id[:8]
    else:
        tx_list = load_mempool_from_workdir(root)

    if rbf_only:
        tx_list = [t for t in tx_list if t["rbf_eligible"]]

    tx_sorted = sorted(tx_list, key=lambda t: t["fee_rate_sat_vbyte"], reverse=True)

    if as_json:
        typer.echo(json.dumps({
            "commit": commit_label,
            "count": len(tx_sorted),
            "transactions": [dict(t) for t in tx_sorted],
        }, indent=2))
        return

    summary = mempool_summary_line(tx_list) if tx_list else "mempool empty"
    typer.echo(f"\nMempool — {commit_label}  ({summary})\n")

    if not tx_sorted:
        typer.echo("  (no pending transactions)")
        return

    typer.echo(f"  {'TXID':<16}  {'Amount':>22}  {'Fee rate':>12}  {'RBF':>4}  Status")
    typer.echo("  " + "─" * 70)
    for tx in tx_sorted:
        txid_short = tx["txid"][:14] + "…"
        rbf_str = "yes" if tx["rbf_eligible"] else "no"
        typer.echo(
            f"  {txid_short:<16}  {format_sat(tx['amount_sat']):>22}"
            f"  {tx['fee_rate_sat_vbyte']:>8} sat/vb  {rbf_str:>4}  pending"
        )

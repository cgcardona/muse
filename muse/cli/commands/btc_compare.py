"""muse bitcoin compare — semantic comparison between two snapshots.

Shows what changed in the Bitcoin state between any two historical commits:
new UTXOs received, UTXOs spent, balance changes, oracle moves, and strategy
configuration changes.

Usage::

    muse bitcoin compare --from HEAD~5
    muse bitcoin compare --from abc1234 --to def5678
    muse bitcoin compare --json

Output::

    Bitcoin compare  abc12345 → def56789

    Balance:   0.10000000 BTC → 0.12340000 BTC  (+0.02340000 BTC)
    UTXOs:     4 → 6  (+2)

    Received (2 new UTXOs):
      new1...0:0  p2wpkh  0.02000000 BTC  (bc1qnew1)
      new2...0:0  p2tr    0.00340000 BTC  (bc1qnew2)

    Spent (0 UTXOs):
      (none)

    Strategy:  (unchanged)
    Oracle:    $61,000 → $62,000  (+$1,000)
"""

from __future__ import annotations

import json
import logging

import typer

from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.store import get_head_commit_id, read_commit, read_current_branch, resolve_commit_ref
from muse.plugins.bitcoin._loader import (
    load_prices,
    load_strategy,
    load_utxos,
    read_repo_id,
)
from muse.plugins.bitcoin._query import format_sat, latest_price, total_balance_sat, utxo_key

logger = logging.getLogger(__name__)
app = typer.Typer()


@app.callback(invoke_without_command=True)
def compare(
    ctx: typer.Context,
    from_ref: str = typer.Option(..., "--from", "-f", metavar="REF",
        help="Base commit reference (required)."),
    to_ref: str | None = typer.Option(None, "--to", "-t", metavar="REF",
        help="Target commit reference (default: HEAD)."),
    as_json: bool = typer.Option(False, "--json", help="Emit results as JSON."),
) -> None:
    """Deep semantic comparison between two Bitcoin state snapshots.

    Shows received UTXOs, spent UTXOs, balance change, oracle price movement,
    and strategy configuration changes between any two points in history.

    Unlike ``git diff`` which shows file-level bytes, ``muse bitcoin compare``
    speaks Bitcoin: it tells you which coins moved, what fees changed, and
    whether the agent's strategy evolved — all from the versioned commit DAG.
    """
    root = require_repo()
    repo_id = read_repo_id(root)
    branch  = read_current_branch(root)

    base_commit = resolve_commit_ref(root, repo_id, branch, from_ref)
    if base_commit is None:
        typer.echo(f"❌ Commit '{from_ref}' not found.", err=True)
        raise typer.Exit(code=ExitCode.USER_ERROR)

    if to_ref is not None:
        cur_commit = resolve_commit_ref(root, repo_id, branch, to_ref)
        cur_label  = to_ref
    else:
        cur_id = get_head_commit_id(root, branch)
        cur_commit = read_commit(root, cur_id) if cur_id else None
        cur_label  = "HEAD"

    if cur_commit is None:
        typer.echo("❌ Could not resolve target commit.", err=True)
        raise typer.Exit(code=ExitCode.USER_ERROR)

    base_utxos   = load_utxos(root, base_commit.commit_id)
    cur_utxos    = load_utxos(root, cur_commit.commit_id)
    base_prices  = load_prices(root, base_commit.commit_id)
    cur_prices   = load_prices(root, cur_commit.commit_id)
    base_strat   = load_strategy(root, base_commit.commit_id)
    cur_strat    = load_strategy(root, cur_commit.commit_id)

    base_keys = {utxo_key(u): u for u in base_utxos}
    cur_keys  = {utxo_key(u): u for u in cur_utxos}
    received  = [cur_keys[k] for k in cur_keys if k not in base_keys]
    spent     = [base_keys[k] for k in base_keys if k not in cur_keys]

    base_total = total_balance_sat(base_utxos)
    cur_total  = total_balance_sat(cur_utxos)
    base_price = latest_price(base_prices)
    cur_price  = latest_price(cur_prices)

    strat_changes = {}
    if base_strat and cur_strat:
        cur_sd  = dict(cur_strat)
        base_sd = dict(base_strat)
        strat_changes = {
            k: {"from": base_sd.get(k), "to": cur_sd.get(k)}
            for k in cur_sd
            if base_sd.get(k) != cur_sd.get(k)
        }

    if as_json:
        typer.echo(json.dumps({
            "base_ref": from_ref,
            "base_commit": base_commit.commit_id[:8],
            "current_ref": cur_label,
            "current_commit": cur_commit.commit_id[:8],
            "base_total_sat": base_total,
            "current_total_sat": cur_total,
            "delta_sat": cur_total - base_total,
            "base_price_usd": base_price,
            "current_price_usd": cur_price,
            "received": [{"key": utxo_key(u), "amount_sat": u["amount_sat"]} for u in received],
            "spent": [{"key": utxo_key(u), "amount_sat": u["amount_sat"]} for u in spent],
            "strategy_changes": strat_changes,
        }, indent=2))
        return

    delta = cur_total - base_total
    sign  = "+" if delta >= 0 else ""

    typer.echo(f"\nBitcoin compare  {from_ref} → {cur_label}\n")
    typer.echo(f"  Balance:  {format_sat(base_total)} → {format_sat(cur_total)}  "
               f"({sign}{format_sat(delta)})")
    typer.echo(f"  UTXOs:    {len(base_utxos)} → {len(cur_utxos)}  "
               f"({'+' if len(cur_utxos) >= len(base_utxos) else ''}{len(cur_utxos) - len(base_utxos)})")

    typer.echo(f"\n  Received ({len(received)} new UTXO{'s' if len(received) != 1 else ''}):")
    if received:
        for u in received:
            short = f"{u['txid'][:6]}…:{u['vout']}"
            typer.echo(f"    {short}  {u['script_type']}  {format_sat(u['amount_sat'])}  ({u['address'][:20]}…)")
    else:
        typer.echo("    (none)")

    typer.echo(f"\n  Spent ({len(spent)} UTXO{'s' if len(spent) != 1 else ''}):")
    if spent:
        for u in spent:
            short = f"{u['txid'][:6]}…:{u['vout']}"
            typer.echo(f"    {short}  {u['script_type']}  {format_sat(u['amount_sat'])}")
    else:
        typer.echo("    (none)")

    if strat_changes:
        typer.echo(f"\n  Strategy ({len(strat_changes)} change{'s' if len(strat_changes) != 1 else ''}):")
        for k, chg in strat_changes.items():
            typer.echo(f"    {k}: {chg['from']!r} → {chg['to']!r}")
    else:
        typer.echo("\n  Strategy:  (unchanged)")

    if base_price and cur_price:
        price_delta = cur_price - base_price
        sign_p = "+" if price_delta >= 0 else ""
        typer.echo(f"\n  Oracle:  ${base_price:,.0f} → ${cur_price:,.0f}  ({sign_p}${price_delta:,.0f})")

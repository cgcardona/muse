"""muse bitcoin balance — on-chain wallet balance breakdown.

Shows confirmed, unconfirmed, and immature coinbase balances; script-type
and label-category breakdowns; and a USD value from the latest oracle price.

Usage::

    muse bitcoin balance
    muse bitcoin balance --commit HEAD~5
    muse bitcoin balance --json

Output::

    Bitcoin balance — working tree

    Confirmed        0.12340000 BTC  ($7,651.80)
    Unconfirmed      0.00050000 BTC
    Immature (cb)    0.00000000 BTC
    Spendable        0.12340000 BTC  ($7,651.80)
    ─────────────────────────────────────────────
    Total portfolio  0.12340000 BTC  ($7,651.80)

    By script type:
      p2wpkh    0.10000000 BTC  81 %
      p2tr      0.02340000 BTC  19 %

    By category:
      cold storage  0.10000000 BTC  81 %
      trading       0.02340000 BTC  19 %

    Oracle: $62,000.00 / BTC  ·  6 UTXOs  ·  0 dust
"""

from __future__ import annotations

import json
import logging
import pathlib

import typer

from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.store import get_head_commit_id, resolve_commit_ref
from muse.plugins.bitcoin._loader import (
    load_fees_from_workdir,
    load_fees,
    load_labels,
    load_labels_from_workdir,
    load_prices,
    load_prices_from_workdir,
    load_utxos,
    load_utxos_from_workdir,
    read_current_branch,
    read_repo_id,
)
from muse.plugins.bitcoin._query import (
    balance_by_category,
    balance_by_script_type,
    confirmed_balance_sat,
    dust_threshold_sat,
    format_sat,
    is_dust,
    latest_fee_estimate,
    latest_price,
    total_balance_sat,
)

logger = logging.getLogger(__name__)
app = typer.Typer()

_SATS_PER_BTC = 100_000_000


def _usd(sats: int, price: float | None) -> str:
    if price is None:
        return ""
    return f"  (${sats / _SATS_PER_BTC * price:,.2f})"


@app.callback(invoke_without_command=True)
def balance(
    ctx: typer.Context,
    ref: str | None = typer.Option(
        None, "--commit", "-c", metavar="REF",
        help="Read from a historical commit instead of the working tree.",
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit results as JSON."),
) -> None:
    """Show the on-chain Bitcoin balance with full breakdown.

    Displays confirmed, unconfirmed, and immature coinbase balances, broken
    down by script type and address label category.  USD value is computed
    from the latest oracle price tick stored in the versioned state.

    Unlike a block explorer, ``muse bitcoin balance`` can show you the wallet
    state at *any* historical commit — letting agents and humans audit exactly
    what was in the wallet when a strategy decision was made.
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
        utxos   = load_utxos(root, commit.commit_id)
        labels  = load_labels(root, commit.commit_id)
        prices  = load_prices(root, commit.commit_id)
        fees    = load_fees(root, commit.commit_id)
        commit_label = commit.commit_id[:8]
    else:
        utxos   = load_utxos_from_workdir(root)
        labels  = load_labels_from_workdir(root)
        prices  = load_prices_from_workdir(root)
        fees    = load_fees_from_workdir(root)

    price = latest_price(prices)
    fee_est = latest_fee_estimate(fees)
    fee_rate = fee_est["target_6_block_sat_vbyte"] if fee_est else 10

    total       = total_balance_sat(utxos)
    confirmed   = confirmed_balance_sat(utxos)
    unconfirmed = total - confirmed
    immature    = sum(
        u["amount_sat"] for u in utxos
        if u["coinbase"] and u["confirmations"] < 100
    )
    spendable   = confirmed - immature
    dust_count  = sum(1 for u in utxos if is_dust(u, fee_rate))

    script_breakdown = balance_by_script_type(utxos)
    cat_breakdown    = balance_by_category(utxos, labels)

    if as_json:
        typer.echo(json.dumps({
            "commit": commit_label,
            "total_sat": total,
            "confirmed_sat": confirmed,
            "unconfirmed_sat": unconfirmed,
            "immature_coinbase_sat": immature,
            "spendable_sat": spendable,
            "price_usd": price,
            "total_usd": (total / _SATS_PER_BTC * price) if price else None,
            "spendable_usd": (spendable / _SATS_PER_BTC * price) if price else None,
            "utxo_count": len(utxos),
            "dust_count": dust_count,
            "script_type_breakdown": script_breakdown,
            "category_breakdown": cat_breakdown,
        }, indent=2))
        return

    typer.echo(f"\nBitcoin balance — {commit_label}\n")
    typer.echo(f"  Confirmed        {format_sat(confirmed):<22}{_usd(confirmed, price)}")
    if unconfirmed:
        typer.echo(f"  Unconfirmed      {format_sat(unconfirmed):<22}")
    if immature:
        typer.echo(f"  Immature (cb)    {format_sat(immature):<22}")
    typer.echo(f"  Spendable        {format_sat(spendable):<22}{_usd(spendable, price)}")
    typer.echo("  " + "─" * 45)
    typer.echo(f"  Total portfolio  {format_sat(total):<22}{_usd(total, price)}")

    if script_breakdown:
        typer.echo("\n  By script type:")
        for stype, sats in sorted(script_breakdown.items(), key=lambda kv: kv[1], reverse=True):
            pct = int(sats / total * 100) if total else 0
            typer.echo(f"    {stype:<10}  {format_sat(sats):<20}  {pct:>3} %")

    if cat_breakdown:
        typer.echo("\n  By category:")
        for cat, sats in sorted(cat_breakdown.items(), key=lambda kv: kv[1], reverse=True):
            pct = int(sats / total * 100) if total else 0
            typer.echo(f"    {cat:<14}  {format_sat(sats):<20}  {pct:>3} %")

    price_str = f"${price:,.2f} / BTC" if price else "no oracle data"
    typer.echo(f"\n  Oracle: {price_str}  ·  {len(utxos)} UTXOs  ·  {dust_count} dust")

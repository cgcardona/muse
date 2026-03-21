"""muse bitcoin moon — price target analysis ("to the moon").

Given the current UTXO stack and oracle price history, computes portfolio
value at multiple target prices.  Shows unrealized gains, the multiple from
today's price, and the USD value at each milestone.

Usage::

    muse bitcoin moon
    muse bitcoin moon --target 250000
    muse bitcoin moon --commit HEAD~20
    muse bitcoin moon --json

Output::

    To the moon 🌕 — working tree

    Stack:    0.12340000 BTC  ·  current price $62,000.00  ·  now worth $7,650.80

    Price target     Portfolio value    Gain (USD)    Multiple
    ────────────────────────────────────────────────────────────
    $    100,000      $12,340.00        +$4,689.20     1.6×
    $    250,000      $30,850.00       +$23,199.20     4.0×
    $    500,000      $61,700.00       +$54,049.20     8.1×
    $  1,000,000     $123,400.00      +$115,749.20    16.1×
    $ 10,000,000   $1,234,000.00    +$1,226,349.20   161.3×
"""

from __future__ import annotations

import json
import logging
from typing import TypedDict

import typer

from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.store import read_current_branch, resolve_commit_ref
from muse.plugins.bitcoin._loader import (
    load_prices,
    load_prices_from_workdir,
    load_utxos,
    load_utxos_from_workdir,
    read_repo_id,
)
from muse.plugins.bitcoin._query import (
    _SATS_PER_BTC,
    format_sat,
    latest_price,
    total_balance_sat,
)

logger = logging.getLogger(__name__)
app = typer.Typer()

_DEFAULT_TARGETS = [100_000, 250_000, 500_000, 1_000_000, 5_000_000, 10_000_000]


class _Projection(TypedDict):
    target_price_usd: int
    portfolio_value_usd: float
    gain_usd: float | None
    multiple: float | None


@app.callback(invoke_without_command=True)
def moon(
    ctx: typer.Context,
    ref: str | None = typer.Option(None, "--commit", "-c", metavar="REF",
        help="Read from a historical commit instead of the working tree."),
    target: int | None = typer.Option(None, "--target", "-t", metavar="USD",
        help="Add a custom price target (USD) to the output."),
    as_json: bool = typer.Option(False, "--json", help="Emit results as JSON."),
) -> None:
    """Project portfolio value at standard and custom BTC price targets.

    Shows what the stack is worth at $100 K, $250 K, $500 K, $1 M, $5 M, and
    $10 M per BTC — plus any custom ``--target`` — relative to today's oracle
    price.  The multiple column shows how many times the current value each
    target represents.

    For an agent running a long-term accumulation mandate, this command
    provides the motivation function: at what price does the portfolio hit the
    target exit value?  Version-controlled oracle history means the projection
    is anchored to the exact price at each commit, not some external API call.
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
        prices    = load_prices(root, commit.commit_id)
        commit_label = commit.commit_id[:8]
    else:
        utxo_list = load_utxos_from_workdir(root)
        prices    = load_prices_from_workdir(root)

    total_sat  = total_balance_sat(utxo_list)
    total_btc  = total_sat / _SATS_PER_BTC
    price      = latest_price(prices)
    now_usd    = total_btc * price if price else None

    targets = list(_DEFAULT_TARGETS)
    if target is not None and target not in targets:
        targets = sorted(targets + [target])

    projections: list[_Projection] = []
    for t in targets:
        value    = total_btc * t
        gain: float | None     = (value - now_usd) if now_usd is not None else None
        multiple: float | None = (t / price) if price else None
        projections.append(_Projection(
            target_price_usd=t,
            portfolio_value_usd=round(value, 2),
            gain_usd=round(gain, 2) if gain is not None else None,
            multiple=round(multiple, 2) if multiple is not None else None,
        ))

    if as_json:
        typer.echo(json.dumps({
            "commit": commit_label,
            "total_sat": total_sat,
            "total_btc": round(total_btc, 8),
            "current_price_usd": price,
            "current_value_usd": round(now_usd, 2) if now_usd is not None else None,
            "projections": projections,
        }, indent=2))
        return

    price_str   = f"${price:,.2f}" if price else "no oracle data"
    now_usd_str = f"${now_usd:,.2f}" if now_usd is not None else "n/a"
    typer.echo(f"\nTo the moon 🌕 — {commit_label}\n")
    typer.echo(f"  Stack:  {format_sat(total_sat)}  ·  current price {price_str}  ·  now worth {now_usd_str}\n")
    typer.echo(f"  {'Price target':>14}  {'Portfolio value':>18}  {'Gain (USD)':>14}  {'Multiple':>8}")
    typer.echo("  " + "─" * 60)
    for p in projections:
        t_str   = f"${int(p['target_price_usd']):>10,}"
        val_str = f"${float(p['portfolio_value_usd']):>14,.2f}"
        gain    = p["gain_usd"]
        gain_str = f"+${float(gain):>12,.2f}" if isinstance(gain, (int, float)) and gain >= 0 else (
            f"-${abs(float(gain or 0)):>12,.2f}" if gain is not None else "          n/a"
        )
        mult    = p["multiple"]
        mult_str = f"{float(mult):.1f}×" if mult is not None else "  n/a"
        typer.echo(f"  {t_str}  {val_str}  {gain_str}  {mult_str:>8}")

"""muse bitcoin whale — whale-tier classification and wealth distribution.

Classifies the wallet's total holdings into the Bitcoin ecosystem's
canonical marine-life tier system and shows where you rank in the food chain.

Usage::

    muse bitcoin whale
    muse bitcoin whale --commit HEAD~50
    muse bitcoin whale --json

Output::

    Whale tier — working tree

    Tier:  🐬 Dolphin  (≥ 1 BTC, < 10 BTC)
    Stack: 0.12340000 BTC  (12,340,000 sats)

    Tier ladder:
      🐳 Whale      ≥ 1 000 BTC    need +987.66 BTC to reach
      🦈 Shark      ≥   100 BTC    need + 99.88 BTC to reach
      🐬 Dolphin    ≥    10 BTC  ◀ YOU ARE HERE
      🐟 Fish       ≥     1 BTC
      🦀 Crab       ≥  0.01 BTC
      🦐 Shrimp     ≥ 0.001 BTC
      🦠 Plankton   <  0.001 BTC
"""

from __future__ import annotations

import json
import logging

import typer

from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.store import resolve_commit_ref
from muse.plugins.bitcoin._loader import (
    load_prices,
    load_prices_from_workdir,
    load_utxos,
    load_utxos_from_workdir,
    read_current_branch,
    read_repo_id,
)
from muse.plugins.bitcoin._query import (
    _SATS_PER_BTC,
    _WHALE_TIERS,
    format_sat,
    latest_price,
    next_tier_threshold_sat,
    total_balance_sat,
    whale_tier,
)

logger = logging.getLogger(__name__)
app = typer.Typer()

_TIER_EMOJI: dict[str, str] = {
    "Humpback": "🐋",
    "Whale":    "🐳",
    "Shark":    "🦈",
    "Dolphin":  "🐬",
    "Fish":     "🐟",
    "Crab":     "🦀",
    "Shrimp":   "🦐",
    "Plankton": "🦠",
}


@app.callback(invoke_without_command=True)
def whale(
    ctx: typer.Context,
    ref: str | None = typer.Option(None, "--commit", "-c", metavar="REF",
        help="Read from a historical commit instead of the working tree."),
    as_json: bool = typer.Option(False, "--json", help="Emit results as JSON."),
) -> None:
    """Classify the wallet's holdings in the Bitcoin ecosystem tier system.

    The tier system is the Bitcoin community's canonical way of describing
    wealth levels, from Plankton (dust holders) to Humpback (100 000+ BTC).
    Knowing your tier — and what it takes to reach the next one — is essential
    context for any long-term accumulation strategy.

    Agents tracking an accumulation mandate use this command to monitor tier
    progression across the commit history and to set intermediate milestones.
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

    total = total_balance_sat(utxo_list)
    tier  = whale_tier(total)
    price = latest_price(prices)
    next_threshold = next_tier_threshold_sat(total)

    tier_data: list[dict[str, str | int | bool]] = []
    for name, threshold in _WHALE_TIERS:
        needed = max(0, threshold - total)
        tier_data.append({
            "name": name,
            "emoji": _TIER_EMOJI.get(name, ""),
            "threshold_sat": threshold,
            "threshold_btc": f"{threshold / _SATS_PER_BTC:.3f}",
            "is_current": name == tier,
            "sats_needed": needed,
        })

    if as_json:
        typer.echo(json.dumps({
            "commit": commit_label,
            "current_tier": tier,
            "total_sat": total,
            "price_usd": price,
            "total_usd": (total / _SATS_PER_BTC * price) if price else None,
            "tiers": tier_data,
        }, indent=2))
        return

    emoji = _TIER_EMOJI.get(tier, "")
    total_usd_str = f"  (${total / _SATS_PER_BTC * price:,.2f})" if price else ""
    typer.echo(f"\nWhale tier — {commit_label}\n")
    typer.echo(f"  Tier:  {emoji} {tier}")
    typer.echo(f"  Stack: {format_sat(total)}{total_usd_str}\n")
    typer.echo("  Tier ladder:")

    for entry in tier_data:
        marker = "  ◀ YOU ARE HERE" if entry["is_current"] else ""
        needed = int(entry["sats_needed"])
        name_str = f"{entry['emoji']} {entry['name']}"
        btc_str  = entry["threshold_btc"]
        needed_str = (f"  need +{format_sat(needed)} to reach" if needed > 0 else "")
        typer.echo(f"    {name_str:<16}  ≥ {btc_str:>12} BTC{marker}{needed_str}")

    if next_threshold is not None:
        gap = next_threshold - total
        typer.echo(f"\n  Next tier in:  {format_sat(gap)}")
        if price:
            gap_usd = gap / _SATS_PER_BTC * price
            typer.echo(f"                 (${gap_usd:,.2f} at current price)")

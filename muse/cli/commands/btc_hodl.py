"""muse bitcoin hodl — HODL analysis and diamond-hands scoring.

Computes the weighted-average coin age of the UTXO set, classifies UTXOs
into short-term (< 1 year) and long-term (≥ 1 year) buckets, and assigns a
HODL grade.  Diamond hands are coins untouched for over one full year.

Usage::

    muse bitcoin hodl
    muse bitcoin hodl --height 850000
    muse bitcoin hodl --commit HEAD~10
    muse bitcoin hodl --json

Output::

    HODL Report — working tree  (block 850,000)

    HODL Score    92,340 blocks  (grade: A — long-term holder)
    Diamond hands 0.10000000 BTC  (81 % of stack)  ≥ 1 year
    Short-term    0.02340000 BTC  (19 % of stack)  < 1 year

    Age distribution:
      > 3 years    0.05000000 BTC  ████████████████  40 %
      1–3 years    0.05000000 BTC  ████████████████  40 %
      6m–1 year    0.02340000 BTC  ███████            19 %
      < 6 months   0.00000000 BTC                      0 %

    Oldest UTXO:   abc...0:0  152,340 blocks  (~2.9 years)
    Newest UTXO:   ghi...2:0    6,170 blocks  (~0.1 years)
"""

from __future__ import annotations

import json
import logging

import typer

from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.store import get_head_commit_id, resolve_commit_ref
from muse.plugins.bitcoin._loader import (
    load_utxos,
    load_utxos_from_workdir,
    read_current_branch,
    read_repo_id,
)
from muse.plugins.bitcoin._query import (
    _BLOCKS_PER_YEAR,
    confirmed_balance_sat,
    diamond_hands_sat,
    format_sat,
    hodl_grade,
    hodl_score,
    short_term_sat,
    total_balance_sat,
)

logger = logging.getLogger(__name__)
app = typer.Typer()


def _bar(fraction: float, width: int = 20) -> str:
    filled = max(0, min(width, int(fraction * width)))
    return "█" * filled


@app.callback(invoke_without_command=True)
def hodl(
    ctx: typer.Context,
    ref: str | None = typer.Option(None, "--commit", "-c", metavar="REF",
        help="Analyse a historical snapshot instead of the working tree."),
    height: int | None = typer.Option(None, "--height", "-H", metavar="BLOCKS",
        help="Current block height for coin-age computation (default: last known)."),
    as_json: bool = typer.Option(False, "--json", help="Emit results as JSON."),
) -> None:
    """Compute HODL score, diamond-hands classification, and age distribution.

    HODL score is the satoshi-weighted average age of all confirmed UTXOs in
    blocks.  Long-held, high-value coins push the score up.

    Diamond hands are UTXOs that have remained unspent for ≥ 52 560 blocks
    (~1 year at 10 min/block).  These coins are in long-term capital-gains
    territory and represent conviction holders.

    Agents use this command to determine whether a proposed spend would
    sacrifice long-term status on a significant portion of the stack, and to
    choose the youngest UTXOs (FIFO) when tax efficiency matters.
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
        commit_label = commit.commit_id[:8]
    else:
        utxo_list = load_utxos_from_workdir(root)

    confirmed = [u for u in utxo_list if u["confirmations"] >= 1]
    if not confirmed:
        typer.echo("  (no confirmed UTXOs — nothing to HODL)")
        return

    # Infer current height from the youngest UTXO if not supplied
    current_height = height
    if current_height is None:
        known_heights = [u["block_height"] for u in confirmed if u["block_height"] is not None]
        current_height = max(known_heights) if known_heights else 0

    score = hodl_score(confirmed, current_height)
    grade = hodl_grade(score)
    diamonds = diamond_hands_sat(confirmed, current_height)
    short_term = short_term_sat(confirmed, current_height)
    total = total_balance_sat(confirmed)

    # Age buckets (in blocks)
    _3yr  = _BLOCKS_PER_YEAR * 3
    _1yr  = _BLOCKS_PER_YEAR
    _6mo  = _BLOCKS_PER_YEAR // 2

    buckets: dict[str, int] = {"> 3 years": 0, "1–3 years": 0, "6m–1 year": 0, "< 6 months": 0}
    oldest_age  = 0
    newest_age  = 999_999_999
    oldest_key  = ""
    newest_key  = ""

    for u in confirmed:
        bh = u["block_height"]
        if bh is None:
            continue
        age = max(0, current_height - bh)
        amt = u["amount_sat"]
        key = f"{u['txid'][:6]}…:{u['vout']}"
        if age >= _3yr:
            buckets["> 3 years"] += amt
        elif age >= _1yr:
            buckets["1–3 years"] += amt
        elif age >= _6mo:
            buckets["6m–1 year"] += amt
        else:
            buckets["< 6 months"] += amt
        if age > oldest_age:
            oldest_age = age
            oldest_key = key
        if age < newest_age:
            newest_age = age
            newest_key = key

    grade_desc = {
        "S": "generational holder", "A": "long-term holder",
        "B": "medium-term holder", "C": "building conviction",
        "D": "new position", "F": "hot wallet / trader",
    }

    if as_json:
        typer.echo(json.dumps({
            "commit": commit_label,
            "current_height": current_height,
            "hodl_score_blocks": round(score, 1),
            "hodl_grade": grade,
            "diamond_hands_sat": diamonds,
            "short_term_sat": short_term,
            "total_confirmed_sat": total,
            "age_buckets_sat": buckets,
            "oldest_utxo": {"key": oldest_key, "age_blocks": oldest_age},
            "newest_utxo": {"key": newest_key, "age_blocks": newest_age},
        }, indent=2))
        return

    diamond_pct = int(diamonds / total * 100) if total else 0
    short_pct   = int(short_term / total * 100) if total else 0

    typer.echo(f"\nHODL Report — {commit_label}  (block {current_height:,})\n")
    typer.echo(f"  HODL Score    {score:,.0f} blocks  (grade: {grade} — {grade_desc.get(grade, '')})")
    typer.echo(f"  Diamond hands {format_sat(diamonds):<22}  ({diamond_pct:>3} % of stack)  ≥ 1 year")
    typer.echo(f"  Short-term    {format_sat(short_term):<22}  ({short_pct:>3} % of stack)  < 1 year")
    typer.echo("\n  Age distribution:")
    for bucket_name, bucket_sats in buckets.items():
        pct = (bucket_sats / total * 100) if total else 0.0
        bar = _bar(pct / 100)
        typer.echo(f"    {bucket_name:<12}  {format_sat(bucket_sats):<22}  {bar:<20}  {pct:>3.0f} %")

    years_oldest = oldest_age / _BLOCKS_PER_YEAR
    years_newest = newest_age / _BLOCKS_PER_YEAR
    typer.echo(f"\n  Oldest UTXO:  {oldest_key:<20}  {oldest_age:>8,} blocks  (~{years_oldest:.1f} years)")
    typer.echo(f"  Newest UTXO:  {newest_key:<20}  {newest_age:>8,} blocks  (~{years_newest:.1f} years)")

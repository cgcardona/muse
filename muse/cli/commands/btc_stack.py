"""muse bitcoin stack — stacking-sats accumulation history.

Walks the commit history to show how the on-chain balance grew over time.
Reveals the accumulation rate (sats per commit interval), DCA discipline,
and whether the stack is growing or shrinking.

Usage::

    muse bitcoin stack
    muse bitcoin stack --limit 20
    muse bitcoin stack --json

Output::

    Stack history — branch main  (last 10 commits)

    Commit    Balance            Delta         Price       Value USD
    ──────────────────────────────────────────────────────────────────
    abc12345  0.12340000 BTC     —             $62,000     $7,650.80   ◀ HEAD
    def67890  0.10000000 BTC   +0.02340000    $61,500     $6,150.00
    ghi11121  0.07500000 BTC   +0.02500000    $60,000     $4,500.00
    ...

    Total accumulated:  +0.12340000 BTC  ($7,650.80 at current price)
    Avg per commit:     +0.01234000 BTC
"""

from __future__ import annotations

import json
import logging
from typing import TypedDict

import typer

from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.store import get_commits_for_branch, get_head_commit_id, read_current_branch
from muse.plugins.bitcoin._loader import (
    load_prices,
    load_utxos,
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


class _StackRow(TypedDict):
    commit_id: str
    total_sat: int
    price_usd: float | None
    value_usd: float | None
    delta_sat: int | None


@app.callback(invoke_without_command=True)
def stack(
    ctx: typer.Context,
    limit: int = typer.Option(15, "--limit", "-n", metavar="N",
        help="Number of recent commits to show (default 15)."),
    as_json: bool = typer.Option(False, "--json", help="Emit results as JSON."),
) -> None:
    """Show UTXO balance progression across the commit history — stacking sats.

    Walks the branch commit DAG, loading the UTXO total at each commit and
    computing the delta from the previous snapshot.  Oracle prices are anchored
    at each commit so you get the USD value at the time each batch of sats was
    accumulated — not today's price applied retroactively.

    This answers the core question for any DCA agent: ``"Am I stacking at the
    right rate?"``  Every row is reproducible from the commit DAG alone.
    """
    root = require_repo()
    repo_id = read_repo_id(root)
    branch  = read_current_branch(root)

    commits = get_commits_for_branch(root, repo_id, branch)
    if not commits:
        typer.echo("❌ No commits found on this branch.", err=True)
        raise typer.Exit(code=ExitCode.USER_ERROR)

    # Most-recent first, then limit
    recent = commits[:limit]

    rows: list[_StackRow] = []
    for commit in recent:
        utxos  = load_utxos(root, commit.commit_id)
        prices = load_prices(root, commit.commit_id)
        total  = total_balance_sat(utxos)
        price  = latest_price(prices)
        usd    = (total / _SATS_PER_BTC * price) if price else None
        rows.append(_StackRow(
            commit_id=commit.commit_id[:8],
            total_sat=total,
            price_usd=price,
            value_usd=round(usd, 2) if usd is not None else None,
            delta_sat=None,
        ))

    # Compute deltas (older commits have higher index)
    for i, row in enumerate(rows):
        if i + 1 < len(rows):
            row["delta_sat"] = row["total_sat"] - rows[i + 1]["total_sat"]

    if as_json:
        typer.echo(json.dumps({"branch": branch, "rows": rows}, indent=2))
        return

    head_id = get_head_commit_id(root, branch)
    typer.echo(f"\nStack history — branch {branch}  (last {len(rows)} commits)\n")
    typer.echo(f"  {'Commit':<10}  {'Balance':>22}  {'Delta':>20}  {'Price':>10}  {'Value USD':>12}")
    typer.echo("  " + "─" * 80)

    for row in rows:
        bal_str   = format_sat(row["total_sat"])
        price_str = f"${row['price_usd']:>9,.0f}" if row["price_usd"] is not None else "         n/a"
        usd_str   = f"${row['value_usd']:>11,.2f}" if row["value_usd"] is not None else "            n/a"
        delta     = row["delta_sat"]
        if delta is not None:
            sign      = "+" if delta >= 0 else ""
            delta_str = f"{sign}{format_sat(delta)}"
        else:
            delta_str = "—"
        head_marker = "  ◀ HEAD" if row["commit_id"] == (head_id or "")[:8] else ""
        typer.echo(
            f"  {row['commit_id']:<10}  {bal_str:>22}  {delta_str:>20}  {price_str}  {usd_str}{head_marker}"
        )

    if rows:
        first_sat = rows[-1]["total_sat"]
        last_sat  = rows[0]["total_sat"]
        total_acc = last_sat - first_sat
        sign = "+" if total_acc >= 0 else ""
        cur_price = rows[0]["price_usd"]
        usd_now = (abs(total_acc) / _SATS_PER_BTC * float(cur_price)) if cur_price else None
        usd_now_str = f"  (${usd_now:,.2f} at current price)" if usd_now else ""
        typer.echo(f"\n  Total accumulated:  {sign}{format_sat(total_acc)}{usd_now_str}")
        if len(rows) > 1:
            avg = total_acc // (len(rows) - 1)
            typer.echo(f"  Avg per commit:     {sign}{format_sat(avg)}")

"""muse bitcoin check — enforce on-chain Bitcoin invariants.

Validates the versioned Bitcoin state against a set of hard invariants:
no spending immature coinbase, fee rate within strategy ceiling, no address
reuse, no negative balances, and strategy sanity checks.

Usage::

    muse bitcoin check
    muse bitcoin check --commit HEAD~5
    muse bitcoin check --json

Output::

    Bitcoin check — working tree

    ✅ No immature coinbase UTXOs being spent
    ✅ Fee rate (10 sat/vbyte) within strategy ceiling (20 sat/vbyte)
    ✅ No address reuse in UTXO set
    ✅ All UTXO amounts positive
    ⚠️  Strategy simulation_mode is active — no real transactions will be sent
    ✅ 6 checks passed  ·  1 warning  ·  0 errors
"""

from __future__ import annotations

import json
import logging
from typing import Literal

import typer

from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.store import read_current_branch, resolve_commit_ref
from muse.plugins.bitcoin._loader import (
    load_fees,
    load_fees_from_workdir,
    load_mempool,
    load_mempool_from_workdir,
    load_strategy,
    load_strategy_from_workdir,
    load_utxos,
    load_utxos_from_workdir,
    read_repo_id,
)
from muse.plugins.bitcoin._query import (
    address_reuse_count,
    latest_fee_estimate,
    total_balance_sat,
)

logger = logging.getLogger(__name__)
app = typer.Typer()

CheckLevel = Literal["ok", "warn", "error"]


def _check(results: list[dict[str, str]], level: CheckLevel, message: str) -> None:
    results.append({"level": level, "message": message})


@app.callback(invoke_without_command=True)
def check(
    ctx: typer.Context,
    ref: str | None = typer.Option(None, "--commit", "-c", metavar="REF",
        help="Read from a historical commit instead of the working tree."),
    as_json: bool = typer.Option(False, "--json", help="Emit results as JSON."),
) -> None:
    """Enforce Bitcoin state invariants against the versioned wallet.

    Runs a suite of sanity checks on the UTXO set, strategy configuration,
    and mempool state.  Exits with code 1 if any check is at error level,
    making it suitable for CI pipelines and pre-commit agent hooks.

    Invariants checked:
    - No immature coinbase UTXOs (< 100 confirmations) in the set
    - Fee rate within strategy ceiling
    - No address reuse in UTXO set
    - All UTXO amounts positive (no zero-value outputs)
    - Strategy simulation mode flag is visible
    - No RBF-eligible mempool transactions that exceed the fee ceiling
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
        utxo_list    = load_utxos(root, commit.commit_id)
        strat        = load_strategy(root, commit.commit_id)
        fees_list    = load_fees(root, commit.commit_id)
        mempool_list = load_mempool(root, commit.commit_id)
        commit_label = commit.commit_id[:8]
    else:
        utxo_list    = load_utxos_from_workdir(root)
        strat        = load_strategy_from_workdir(root)
        fees_list    = load_fees_from_workdir(root)
        mempool_list = load_mempool_from_workdir(root)

    results: list[dict[str, str]] = []

    # Check 1: no immature coinbase
    immature_cb = [u for u in utxo_list if u["coinbase"] and u["confirmations"] < 100]
    if immature_cb:
        _check(results, "error",
               f"{len(immature_cb)} immature coinbase UTXO(s) — not yet spendable (< 100 confirmations)")
    else:
        _check(results, "ok", "No immature coinbase UTXOs")

    # Check 2: all amounts positive
    zero_value = [u for u in utxo_list if u["amount_sat"] <= 0]
    if zero_value:
        _check(results, "error", f"{len(zero_value)} UTXO(s) with zero or negative amount")
    else:
        _check(results, "ok", "All UTXO amounts are positive")

    # Check 3: no address reuse
    reuse = address_reuse_count(utxo_list)
    if reuse > 0:
        _check(results, "warn", f"{reuse} reused address(es) detected — privacy leak")
    else:
        _check(results, "ok", "No address reuse in UTXO set")

    # Check 4: fee rate within strategy ceiling
    fee_est = latest_fee_estimate(fees_list)
    if strat and fee_est:
        cur_rate = fee_est["target_6_block_sat_vbyte"]
        ceiling  = strat["max_fee_rate_sat_vbyte"]
        if cur_rate > ceiling:
            _check(results, "error",
                   f"Current 6-block fee rate {cur_rate} sat/vbyte exceeds strategy ceiling {ceiling}")
        else:
            _check(results, "ok",
                   f"Fee rate {cur_rate} sat/vbyte within strategy ceiling {ceiling} sat/vbyte")

    # Check 5: simulation mode flag
    if strat:
        if strat["simulation_mode"]:
            _check(results, "warn", "Strategy simulation_mode is ACTIVE — no real transactions will be sent")
        else:
            _check(results, "ok", "Strategy is in LIVE mode")
    else:
        _check(results, "warn", "No strategy configured — create strategy/agent.json")

    # Check 6: mempool RBF fee ceiling
    if strat:
        ceiling = strat["max_fee_rate_sat_vbyte"]
        over_ceiling = [t for t in mempool_list if t["fee_rate_sat_vbyte"] > ceiling]
        if over_ceiling:
            _check(results, "warn",
                   f"{len(over_ceiling)} pending tx(s) above strategy fee ceiling ({ceiling} sat/vbyte)")
        else:
            _check(results, "ok", "All mempool transactions within strategy fee ceiling")

    errors   = [r for r in results if r["level"] == "error"]
    warnings = [r for r in results if r["level"] == "warn"]
    oks      = [r for r in results if r["level"] == "ok"]

    if as_json:
        typer.echo(json.dumps({
            "commit": commit_label,
            "total_checks": len(results),
            "errors": len(errors),
            "warnings": len(warnings),
            "passed": len(oks),
            "results": results,
        }, indent=2))
        if errors:
            raise typer.Exit(code=ExitCode.USER_ERROR)
        return

    typer.echo(f"\nBitcoin check — {commit_label}\n")
    for r in results:
        icon = {"ok": "✅", "warn": "⚠️ ", "error": "❌"}.get(r["level"], "")
        typer.echo(f"  {icon} {r['message']}")

    typer.echo(f"\n  {len(oks)} checks passed  ·  {len(warnings)} warning(s)  ·  {len(errors)} error(s)")
    if errors:
        raise typer.Exit(code=ExitCode.USER_ERROR)

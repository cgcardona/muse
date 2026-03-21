"""muse bitcoin strategy — show and diff the active agent strategy.

Displays the current agent strategy configuration and optionally diffs it
against any historical commit to show what changed and when.

Usage::

    muse bitcoin strategy
    muse bitcoin strategy --diff HEAD~5
    muse bitcoin strategy --commit HEAD~3
    muse bitcoin strategy --json

Output::

    Strategy — working tree

    Name:                 conservative
    Mode:                 LIVE
    Coin selection:       branch_and_bound
    Max fee rate:         10 sat/vbyte
    Min confirmations:    6
    DCA amount:           500,000 sats  (every 144 blocks)
    Consolidation:        threshold 20 UTXOs
    Rebalance threshold:  20 %

    ── diff from HEAD~5 ──
    max_fee_rate_sat_vbyte:  8 → 10  (+2)
    simulation_mode:         true → false
"""

from __future__ import annotations

import json
import logging

import typer

from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.store import read_current_branch, resolve_commit_ref
from muse.plugins.bitcoin._loader import (
    load_strategy,
    load_strategy_from_workdir,
    read_repo_id,
)
from muse.plugins.bitcoin._query import format_sat, strategy_summary_line

logger = logging.getLogger(__name__)
app = typer.Typer()


@app.callback(invoke_without_command=True)
def strategy(
    ctx: typer.Context,
    ref: str | None = typer.Option(None, "--commit", "-c", metavar="REF",
        help="Read from a historical commit instead of the working tree."),
    diff_ref: str | None = typer.Option(None, "--diff", "-d", metavar="REF",
        help="Show a diff against this commit's strategy."),
    as_json: bool = typer.Option(False, "--json", help="Emit results as JSON."),
) -> None:
    """Show the active agent strategy configuration.

    The strategy is the agent's operating mandate: which coin-selection
    algorithm to use, what fee ceiling to respect, how aggressively to DCA,
    when to consolidate UTXOs, and whether simulation mode is active.

    Version-controlling the strategy means every decision the agent made is
    auditable: you can see exactly what parameters were in effect at the time
    of each commit.  Use ``--diff`` to see what changed between two strategy
    versions and when the change was committed.
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
        strat = load_strategy(root, commit.commit_id)
        commit_label = commit.commit_id[:8]
    else:
        strat = load_strategy_from_workdir(root)

    if strat is None:
        typer.echo("  (no strategy configured — create strategy/agent.json)")
        return

    diff_strat = None
    if diff_ref is not None:
        repo_id = read_repo_id(root)
        branch = read_current_branch(root)
        diff_commit = resolve_commit_ref(root, repo_id, branch, diff_ref)
        if diff_commit is None:
            typer.echo(f"❌ Diff commit '{diff_ref}' not found.", err=True)
            raise typer.Exit(code=ExitCode.USER_ERROR)
        diff_strat = load_strategy(root, diff_commit.commit_id)

    if as_json:
        if diff_strat is not None:
            cur_d  = dict(strat)
            base_d = dict(diff_strat)
            changes = {
                k: {"from": base_d.get(k), "to": cur_d.get(k)}
                for k in cur_d
                if base_d.get(k) != cur_d.get(k)
            }
            typer.echo(json.dumps({
                "commit": commit_label,
                "strategy": cur_d,
                "diff_ref": diff_ref,
                "changes": changes,
            }, indent=2))
        else:
            typer.echo(json.dumps({
                "commit": commit_label,
                "strategy": dict(strat),
            }, indent=2))
        return

    mode_str = "SIMULATION 🧪" if strat["simulation_mode"] else "LIVE"
    dca_str  = (
        f"{format_sat(strat['dca_amount_sat'])}  (every {strat['dca_interval_blocks']} blocks)"
        if strat["dca_amount_sat"] is not None else "disabled"
    )

    typer.echo(f"\nStrategy — {commit_label}\n")
    typer.echo(f"  Name:                 {strat['name']}")
    typer.echo(f"  Mode:                 {mode_str}")
    typer.echo(f"  Coin selection:       {strat['coin_selection']}")
    typer.echo(f"  Max fee rate:         {strat['max_fee_rate_sat_vbyte']} sat/vbyte")
    typer.echo(f"  Min confirmations:    {strat['min_confirmations']}")
    typer.echo(f"  DCA amount:           {dca_str}")
    typer.echo(f"  Consolidation:        threshold {strat['utxo_consolidation_threshold']} UTXOs")
    typer.echo(f"  Rebalance threshold:  {int(strat['lightning_rebalance_threshold'] * 100)} %")

    if diff_strat is not None:
        typer.echo(f"\n  ── diff from {diff_ref} ──")
        any_change = False
        cur_d2  = dict(strat)
        base_d2 = dict(diff_strat)
        for k in cur_d2:
            old_v = base_d2.get(k)
            new_v = cur_d2.get(k)
            if old_v != new_v:
                typer.echo(f"  {k}:  {old_v!r} → {new_v!r}")
                any_change = True
        if not any_change:
            typer.echo("  (no changes)")

"""muse bitcoin utxos — full UTXO set with lifecycle analysis.

Lists every UTXO in the versioned wallet with rich annotations: age in blocks,
effective value after estimated spend fee, dust flag, label, and category.

Usage::

    muse bitcoin utxos
    muse bitcoin utxos --commit HEAD~3
    muse bitcoin utxos --fee-rate 20 --sort-by effective-value
    muse bitcoin utxos --json

Output::

    UTXOs — working tree  (6 UTXOs · 0.12340000 BTC spendable · fee 10 sat/vbyte)

    UTXO                          Amount        Age(blk) Eff.Value     Label        Category
    ──────────────────────────────────────────────────────────────────────────────────────────
    abc...0:0  p2wpkh  ✓   1.00000000 BTC  12340  0.99999590 BTC  cold wallet  cold storage
    def...1:0  p2tr    ✓   0.12340000 BTC   6170  0.12339942 BTC  DCA stack    income
    ghi...2:0  p2wpkh  ⏳      546 sats      0      96 sats     (none)       unknown
"""

from __future__ import annotations

import json
import logging

import typer

from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.store import resolve_commit_ref
from muse.plugins.bitcoin._loader import (
    load_fees,
    load_fees_from_workdir,
    load_labels,
    load_labels_from_workdir,
    load_utxos,
    load_utxos_from_workdir,
    read_current_branch,
    read_repo_id,
)
from muse.plugins.bitcoin._query import (
    confirmed_balance_sat,
    effective_value_sat,
    format_sat,
    is_dust,
    latest_fee_estimate,
    utxo_key,
)
from muse.plugins.bitcoin._analytics import utxo_lifecycle

logger = logging.getLogger(__name__)
app = typer.Typer()

_SORT_KEYS = ("amount", "age", "effective-value", "confirmations")


@app.callback(invoke_without_command=True)
def utxos(
    ctx: typer.Context,
    ref: str | None = typer.Option(None, "--commit", "-c", metavar="REF",
        help="Read from a historical commit."),
    fee_rate: int = typer.Option(10, "--fee-rate", "-f", metavar="SAT/VBYTE",
        help="Fee rate for effective-value and dust calculation."),
    sort_by: str = typer.Option("amount", "--sort-by", "-s",
        help="Sort key: amount | age | effective-value | confirmations."),
    as_json: bool = typer.Option(False, "--json", help="Emit results as JSON."),
) -> None:
    """List every UTXO with economic and provenance annotations.

    For each UTXO shows: the canonical key (txid:vout), script type, maturity
    status, amount, age in blocks, effective value after estimated miner fee,
    label, and category from the versioned label registry.

    ``--fee-rate`` recalculates effective value and dust status at any hypothetical
    fee rate without touching on-chain state.  Agents use this to pre-screen
    UTXOs before coin selection.
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
        labels    = load_labels(root, commit.commit_id)
        fees_list = load_fees(root, commit.commit_id)
        commit_label = commit.commit_id[:8]
    else:
        utxo_list = load_utxos_from_workdir(root)
        labels    = load_labels_from_workdir(root)
        fees_list = load_fees_from_workdir(root)

    # Use oracle fee rate if not overridden by the caller
    fee_est = latest_fee_estimate(fees_list)
    effective_rate = fee_rate if fee_rate != 10 else (
        fee_est["target_6_block_sat_vbyte"] if fee_est else 10
    )

    lifecycles = [utxo_lifecycle(u, labels, effective_rate) for u in utxo_list]

    # Sorting
    sort_key = sort_by.lower()
    if sort_key == "age":
        lifecycles.sort(key=lambda lc: lc["age_blocks"] or 0, reverse=True)
    elif sort_key == "effective-value":
        lifecycles.sort(key=lambda lc: lc["effective_value_sat"], reverse=True)
    elif sort_key == "confirmations":
        lifecycles.sort(key=lambda lc: lc["confirmations"], reverse=True)
    else:
        lifecycles.sort(key=lambda lc: lc["amount_sat"], reverse=True)

    spendable = confirmed_balance_sat(utxo_list)

    if as_json:
        typer.echo(json.dumps({
            "commit": commit_label,
            "fee_rate_sat_vbyte": effective_rate,
            "utxo_count": len(lifecycles),
            "spendable_sat": spendable,
            "utxos": [dict(lc) for lc in lifecycles],
        }, indent=2))
        return

    typer.echo(f"\nUTXOs — {commit_label}  "
               f"({len(lifecycles)} UTXOs · {format_sat(spendable)} spendable "
               f"· fee {effective_rate} sat/vbyte)\n")

    header = f"  {'UTXO':<20}  {'Type':<8}  {'St':>2}  {'Amount':>22}  {'Age':>8}  {'Eff.Value':>22}  {'Label':<16}  Category"
    typer.echo(header)
    typer.echo("  " + "─" * (len(header) + 2))

    for lc in lifecycles:
        key_short = lc["key"][:16] + "…" if len(lc["key"]) > 16 else lc["key"]
        status = "⏳" if not lc["is_spendable"] else ("💀" if lc["is_dust"] else "✓")
        age_str = str(lc["age_blocks"]) if lc["age_blocks"] is not None else "unconf"
        label_str = (lc["label"] or "(none)")[:16]
        cat_str   = lc["category"][:12]
        typer.echo(
            f"  {key_short:<20}  {lc['script_type']:<8}  {status:>2}  "
            f"{format_sat(lc['amount_sat']):>22}  {age_str:>8}  "
            f"{format_sat(lc['effective_value_sat']):>22}  {label_str:<16}  {cat_str}"
        )

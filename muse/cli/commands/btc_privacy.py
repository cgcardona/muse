"""muse bitcoin privacy — UTXO fingerprint and address-reuse analysis.

Analyses the UTXO set for privacy leaks: address reuse (the single biggest
chain-analysis vector), script type homogeneity, and Taproot adoption.

Usage::

    muse bitcoin privacy
    muse bitcoin privacy --commit HEAD~5
    muse bitcoin privacy --json

Output::

    Privacy analysis — working tree

    Address reuse:     0 reused addresses  ✅ clean
    Script diversity:  1.58 bits entropy  (4 script types)
    Taproot adoption:  19 %  (0.02340000 BTC in P2TR)

    Script type breakdown:
      p2wpkh   5 UTXOs   81 %  of value
      p2tr     1 UTXO    19 %  of value
      p2pkh    0 UTXOs    0 %
      p2sh     0 UTXOs    0 %

    Recommendations:
      ✅ No address reuse detected.
      ⚠️  Consider migrating legacy P2PKH UTXOs to P2TR for better privacy.
      ⚠️  Taproot adoption below 50 % — P2TR outputs are indistinguishable from key-path spends.
"""

from __future__ import annotations

import json
import logging
from collections import Counter

import typer

from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.store import read_current_branch, resolve_commit_ref
from muse.plugins.bitcoin._loader import (
    load_utxos,
    load_utxos_from_workdir,
    read_repo_id,
)
from muse.plugins.bitcoin._query import (
    address_reuse_count,
    format_sat,
    script_type_diversity,
    taproot_adoption_pct,
    total_balance_sat,
)

logger = logging.getLogger(__name__)
app = typer.Typer()


@app.callback(invoke_without_command=True)
def privacy(
    ctx: typer.Context,
    ref: str | None = typer.Option(None, "--commit", "-c", metavar="REF",
        help="Read from a historical commit instead of the working tree."),
    as_json: bool = typer.Option(False, "--json", help="Emit results as JSON."),
) -> None:
    """Analyse the UTXO set for on-chain privacy characteristics.

    Bitcoin transactions are permanently public.  Chain analysis firms exploit
    address reuse, script type homogeneity, and UTXO graph structure to de-
    anonymise wallets.  This command surfaces the most actionable privacy
    metrics so agents can optimise for privacy alongside cost efficiency.

    Key metrics:
    - **Address reuse**: the strongest de-anonymisation signal — same address
      appearing in multiple UTXOs links them to a single entity.
    - **Script type diversity**: a wallet using only one script type is
      trivially identifiable.  Shannon entropy measures diversity.
    - **Taproot adoption**: P2TR outputs are indistinguishable from each other
      in the common key-path case, providing the best privacy available today.
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

    reuse_count = address_reuse_count(utxo_list)
    entropy     = script_type_diversity(utxo_list)
    p2tr_pct    = taproot_adoption_pct(utxo_list)
    total       = total_balance_sat(utxo_list)

    script_counter: Counter[str] = Counter(u["script_type"] for u in utxo_list)
    script_value:   Counter[str] = Counter()
    for u in utxo_list:
        script_value[u["script_type"]] += u["amount_sat"]

    legacy_sat = sum(
        u["amount_sat"] for u in utxo_list
        if u["script_type"] in ("p2pkh", "p2sh")
    )

    recommendations: list[str] = []
    if reuse_count == 0:
        recommendations.append("✅ No address reuse detected.")
    else:
        recommendations.append(f"🔴 {reuse_count} reused address(es) — critical privacy leak. Use fresh addresses.")
    if legacy_sat > 0:
        recommendations.append(f"⚠️  {format_sat(legacy_sat)} in legacy P2PKH/P2SH — consider migrating to P2TR.")
    if p2tr_pct < 50:
        recommendations.append("⚠️  Taproot adoption below 50 % — P2TR offers best on-chain privacy.")
    elif p2tr_pct >= 90:
        recommendations.append("✅ Excellent Taproot adoption.")

    if as_json:
        typer.echo(json.dumps({
            "commit": commit_label,
            "address_reuse_count": reuse_count,
            "script_type_entropy_bits": entropy,
            "taproot_adoption_pct": round(p2tr_pct, 2),
            "legacy_sat": legacy_sat,
            "script_type_counts": dict(script_counter),
            "script_type_sat": dict(script_value),
            "recommendations": recommendations,
        }, indent=2))
        return

    reuse_icon = "✅" if reuse_count == 0 else "🔴"
    typer.echo(f"\nPrivacy analysis — {commit_label}\n")
    typer.echo(f"  Address reuse:     {reuse_count} reused address(es)  {reuse_icon}")
    typer.echo(f"  Script diversity:  {entropy:.2f} bits entropy  ({len(script_counter)} script type(s))")
    typer.echo(f"  Taproot adoption:  {p2tr_pct:.0f} %  ({format_sat(script_value.get('p2tr', 0))} in P2TR)")

    typer.echo("\n  Script type breakdown:")
    for stype in sorted(script_counter.keys()):
        count = script_counter[stype]
        sats  = script_value.get(stype, 0)
        pct   = (sats / total * 100) if total else 0.0
        typer.echo(f"    {stype:<10}  {count:>4} UTXO{'s' if count != 1 else ' '}  "
                   f"{pct:>5.1f} %  of value  ({format_sat(sats)})")

    typer.echo("\n  Recommendations:")
    for rec in recommendations:
        typer.echo(f"    {rec}")

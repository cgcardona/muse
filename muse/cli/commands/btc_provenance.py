"""muse bitcoin provenance — UTXO lineage through the MUSE commit DAG.

Traces a specific UTXO (txid:vout) through every commit on the current branch
to show exactly when it appeared, how long it was held, and (if spent) when
it was consumed.  This is information the Bitcoin blockchain cannot give you.

Usage::

    muse bitcoin provenance abc...64:0
    muse bitcoin provenance abc...64:0 --json

Output::

    UTXO provenance — abc1234...xyz:0

    Key:     abc1234...xyz:0
    Address: bc1qcoldwallet
    Amount:  0.10000000 BTC

    Commit history:
      def56789  APPEARED   age 0 blk    850,000  "DCA buy — oracle $62k"
      ghi89012  PRESENT    age 144 blk  850,144  "fee-bump mempool"
      jkl12345  PRESENT    age 288 blk  850,288  "weekly strategy review"
      mno67890  SPENT      —             850,432  "consolidation tx"

    Held for: 432 blocks (~3 days)
"""

from __future__ import annotations

import json
import logging

import typer

from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.store import get_commits_for_branch, read_current_branch
from muse.plugins.bitcoin._loader import (
    load_utxos,
    read_repo_id,
)
from muse.plugins.bitcoin._query import format_sat, utxo_key

logger = logging.getLogger(__name__)
app = typer.Typer()


@app.callback(invoke_without_command=True)
def provenance(
    ctx: typer.Context,
    utxo_id: str = typer.Argument(..., metavar="TXID:VOUT",
        help="UTXO key in 'txid:vout' format."),
    limit: int = typer.Option(100, "--limit", "-n",
        help="Maximum commits to walk (default 100)."),
    as_json: bool = typer.Option(False, "--json", help="Emit results as JSON."),
) -> None:
    """Trace a UTXO's complete lifecycle through the MUSE commit DAG.

    Unlike a block explorer (which shows transaction history) or a wallet
    (which shows current UTXOs), ``muse bitcoin provenance`` shows the UTXO's
    history through *your versioned agent state*: when the agent first saw it,
    every commit during which it was present, and when the agent spent it.

    This is a uniquely Muse capability: the blockchain records transfers;
    Muse records *decisions*.  Provenance tells you not just what happened,
    but which agent, at which block, under which strategy, made each choice.
    """
    root = require_repo()
    repo_id = read_repo_id(root)
    branch  = read_current_branch(root)

    commits = get_commits_for_branch(root, repo_id, branch)
    if not commits:
        typer.echo("❌ No commits found on this branch.", err=True)
        raise typer.Exit(code=ExitCode.USER_ERROR)

    recent = commits[:limit]
    # Walk oldest → newest to find appearance order
    oldest_first = list(reversed(recent))

    events: list[dict[str, str | int | None]] = []
    was_present = False

    for commit in oldest_first:
        utxos    = load_utxos(root, commit.commit_id)
        keys     = {utxo_key(u): u for u in utxos}
        present  = utxo_id in keys

        if present and not was_present:
            u = keys[utxo_id]
            events.append({
                "commit_id": commit.commit_id[:8],
                "event": "APPEARED",
                "amount_sat": u["amount_sat"],
                "address": u["address"],
                "script_type": u["script_type"],
                "block_height": u["block_height"],
                "confirmations": u["confirmations"],
                "message": commit.message if hasattr(commit, "message") else None,
            })
            was_present = True
        elif not present and was_present:
            events.append({
                "commit_id": commit.commit_id[:8],
                "event": "SPENT",
                "amount_sat": None,
                "address": None,
                "script_type": None,
                "block_height": None,
                "confirmations": None,
                "message": commit.message if hasattr(commit, "message") else None,
            })
            was_present = False
        elif present and was_present:
            events.append({
                "commit_id": commit.commit_id[:8],
                "event": "PRESENT",
                "amount_sat": keys[utxo_id]["amount_sat"],
                "block_height": keys[utxo_id]["block_height"],
                "confirmations": keys[utxo_id]["confirmations"],
                "address": None,
                "script_type": None,
                "message": None,
            })

    if not events:
        typer.echo(f"  UTXO '{utxo_id}' not found in the last {limit} commits.")
        return

    first = next((e for e in events if e["event"] == "APPEARED"), None)
    last_spent = next((e for e in reversed(events) if e["event"] == "SPENT"), None)

    if as_json:
        typer.echo(json.dumps({
            "utxo_key": utxo_id,
            "branch": branch,
            "event_count": len(events),
            "events": events,
        }, indent=2))
        return

    typer.echo(f"\nUTXO provenance — {utxo_id}\n")
    if first:
        typer.echo(f"  Address: {first.get('address', 'n/a')}")
        if first["amount_sat"] is not None:
            typer.echo(f"  Amount:  {format_sat(int(first['amount_sat']))}")
    typer.echo("")
    typer.echo(f"  {'Commit':<10}  {'Event':<10}  {'Block':>10}  {'Confs':>6}  Notes")
    typer.echo("  " + "─" * 60)

    for ev in events:
        bh_str    = str(ev["block_height"]) if ev["block_height"] else "—"
        conf_str  = str(ev["confirmations"]) if ev["confirmations"] else "—"
        msg_short = str(ev["message"] or "")[:30] if ev.get("message") else ""
        event_icon = {"APPEARED": "🟢", "PRESENT": "·", "SPENT": "🔴"}.get(str(ev["event"]), "")
        typer.echo(
            f"  {ev['commit_id']:<10}  {event_icon} {ev['event']:<8}  "
            f"{bh_str:>10}  {conf_str:>6}  {msg_short}"
        )

    present_count = sum(1 for e in events if e["event"] in ("APPEARED", "PRESENT"))
    typer.echo(f"\n  Present in {present_count} commits.")
    if last_spent:
        typer.echo(f"  Status: SPENT (at commit {last_spent['commit_id']})")
    elif was_present:
        typer.echo(f"  Status: UNSPENT (still present in latest scanned commit)")

"""Bitcoin domain query and analysis functions.

This module provides purely functional, read-only analytics over Bitcoin state
TypedDicts. No I/O, no side effects. Functions here are used by ``diff()``,
``merge()``, and external callers (e.g. a ``muse query`` command) to derive
meaning from the raw state.

All balance values are in satoshis throughout — never floating-point BTC —
to avoid rounding errors in financial arithmetic.
"""

from __future__ import annotations

import logging

from muse.plugins.bitcoin._types import (
    AgentStrategyRecord,
    FeeEstimateRecord,
    LightningChannelRecord,
    OraclePriceTickRecord,
    PendingTxRecord,
    UTXORecord,
)

logger = logging.getLogger(__name__)

_SATS_PER_BTC: int = 100_000_000


# ---------------------------------------------------------------------------
# UTXO analytics
# ---------------------------------------------------------------------------


def utxo_key(utxo: UTXORecord) -> str:
    """Return the canonical identity key for a UTXO: ``"{txid}:{vout}"``."""
    return f"{utxo['txid']}:{utxo['vout']}"


def total_balance_sat(utxos: list[UTXORecord]) -> int:
    """Sum all UTXO amounts in satoshis."""
    return sum(u["amount_sat"] for u in utxos)


def confirmed_balance_sat(utxos: list[UTXORecord], min_confirmations: int = 1) -> int:
    """Sum UTXO amounts that have at least *min_confirmations* confirmations.

    Immature coinbase outputs (< 100 confirmations) are excluded unless the
    caller explicitly requests a lower threshold.
    """
    result = 0
    for u in utxos:
        confs = u["confirmations"]
        if confs < min_confirmations:
            continue
        if u["coinbase"] and confs < 100:
            continue
        result += u["amount_sat"]
    return result


def balance_by_script_type(utxos: list[UTXORecord]) -> dict[str, int]:
    """Return ``{script_type: total_sats}`` for all UTXOs."""
    result: dict[str, int] = {}
    for u in utxos:
        st = u["script_type"]
        result[st] = result.get(st, 0) + u["amount_sat"]
    return result


def coin_age_blocks(utxo: UTXORecord, current_height: int) -> int | None:
    """Return how many blocks old this UTXO is, or ``None`` if unconfirmed."""
    bh = utxo["block_height"]
    if bh is None:
        return None
    return max(0, current_height - bh)


def format_sat(amount_sat: int) -> str:
    """Format satoshis as a human-readable string.

    Values below 100 000 sats are displayed as ``"N sats"``.
    Values at or above 100 000 sats are displayed as ``"X.XXXXXXXX BTC"``.
    """
    if abs(amount_sat) < 100_000:
        return f"{amount_sat:,} sats"
    btc = amount_sat / _SATS_PER_BTC
    return f"{btc:.8f} BTC"


def utxo_summary_line(utxos: list[UTXORecord], current_height: int | None = None) -> str:
    """One-line human-readable summary of the UTXO set.

    Example: ``"12 UTXOs | 0.34500000 BTC confirmed | 3 unconfirmed"``
    """
    total = total_balance_sat(utxos)
    confirmed = confirmed_balance_sat(utxos)
    unconfirmed_count = sum(1 for u in utxos if u["confirmations"] == 0)
    parts = [
        f"{len(utxos)} UTXOs",
        f"{format_sat(confirmed)} confirmed",
    ]
    if unconfirmed_count:
        unconf_sat = total - confirmed_balance_sat(utxos, min_confirmations=1)
        parts.append(f"{unconfirmed_count} unconfirmed ({format_sat(unconf_sat)})")
    if current_height is not None:
        oldest = max(
            (coin_age_blocks(u, current_height) or 0 for u in utxos),
            default=0,
        )
        if oldest:
            parts.append(f"oldest {oldest} blocks")
    return " | ".join(parts)


def double_spend_candidates(
    base_utxo_keys: set[str],
    our_spent: set[str],
    their_spent: set[str],
) -> list[str]:
    """Detect UTXOs that both branches attempted to spend concurrently.

    A double-spend candidate is a UTXO that:
    1. Existed in the base state (was a real UTXO at branch point), AND
    2. Was spent (deleted) on BOTH the ours branch AND the theirs branch.

    This signals a strategy-layer double-spend: two agents independently
    decided to spend the same coin. On the real blockchain only one can win;
    MUSE surfaces the conflict before anything touches the mempool.

    Args:
        base_utxo_keys: UTXO keys (``"{txid}:{vout}"``) present in the common
                        ancestor snapshot.
        our_spent:      UTXO keys deleted on our branch since the ancestor.
        their_spent:    UTXO keys deleted on their branch since the ancestor.

    Returns:
        Sorted list of UTXO keys that are double-spend candidates.
    """
    return sorted(base_utxo_keys & our_spent & their_spent)


# ---------------------------------------------------------------------------
# Lightning channel analytics
# ---------------------------------------------------------------------------


def channel_liquidity_totals(
    channels: list[LightningChannelRecord],
) -> tuple[int, int]:
    """Return ``(total_local_sat, total_remote_sat)`` across all channels."""
    local = sum(c["local_balance_sat"] for c in channels)
    remote = sum(c["remote_balance_sat"] for c in channels)
    return local, remote


def channel_utilization(channel: LightningChannelRecord) -> float:
    """Local balance as a fraction of usable channel capacity [0.0, 1.0].

    Usable capacity excludes both reserve amounts.  Returns 0.0 if the
    channel has zero usable capacity (fully reserved).
    """
    usable = (
        channel["capacity_sat"]
        - channel["local_reserve_sat"]
        - channel["remote_reserve_sat"]
    )
    if usable <= 0:
        return 0.0
    return channel["local_balance_sat"] / usable


def channel_summary_line(channels: list[LightningChannelRecord]) -> str:
    """One-line summary of all Lightning channels.

    Example: ``"5 channels | 0.02100000 BTC local | 0.01800000 BTC remote | 3 active"``
    """
    active = sum(1 for c in channels if c["is_active"])
    local, remote = channel_liquidity_totals(channels)
    return (
        f"{len(channels)} channels"
        f" | {format_sat(local)} local"
        f" | {format_sat(remote)} remote"
        f" | {active} active"
    )


# ---------------------------------------------------------------------------
# Fee oracle analytics
# ---------------------------------------------------------------------------


def fee_surface_str(estimate: FeeEstimateRecord) -> str:
    """Format a fee estimate as a compact three-target string.

    Example: ``"1blk: 42 | 6blk: 15 | 144blk: 3 sat/vbyte"``
    """
    return (
        f"1blk: {estimate['target_1_block_sat_vbyte']}"
        f" | 6blk: {estimate['target_6_block_sat_vbyte']}"
        f" | 144blk: {estimate['target_144_block_sat_vbyte']}"
        " sat/vbyte"
    )


def latest_fee_estimate(
    estimates: list[FeeEstimateRecord],
) -> FeeEstimateRecord | None:
    """Return the most recent fee estimate by timestamp, or ``None``."""
    if not estimates:
        return None
    return max(estimates, key=lambda e: e["timestamp"])


# ---------------------------------------------------------------------------
# Price oracle analytics
# ---------------------------------------------------------------------------


def price_at_height(
    prices: list[OraclePriceTickRecord],
    height: int,
) -> float | None:
    """Return the BTC/USD price closest to *height*, or ``None`` if no data."""
    candidates = [p for p in prices if p["block_height"] is not None]
    if not candidates:
        return None
    closest = min(candidates, key=lambda p: abs((p["block_height"] or 0) - height))
    return closest["price_usd"]


def latest_price(prices: list[OraclePriceTickRecord]) -> float | None:
    """Return the most recent BTC/USD price by timestamp, or ``None``."""
    if not prices:
        return None
    return max(prices, key=lambda p: p["timestamp"])["price_usd"]


# ---------------------------------------------------------------------------
# Mempool analytics
# ---------------------------------------------------------------------------


def mempool_summary_line(mempool: list[PendingTxRecord]) -> str:
    """One-line summary of the local mempool view.

    Example: ``"7 pending | 0.00150000 BTC | avg 23 sat/vbyte | 3 RBF"``
    """
    if not mempool:
        return "mempool empty"
    total = sum(t["amount_sat"] for t in mempool)
    avg_rate = sum(t["fee_rate_sat_vbyte"] for t in mempool) / len(mempool)
    rbf = sum(1 for t in mempool if t["rbf_eligible"])
    return (
        f"{len(mempool)} pending"
        f" | {format_sat(total)}"
        f" | avg {avg_rate:.0f} sat/vbyte"
        f" | {rbf} RBF"
    )


# ---------------------------------------------------------------------------
# Strategy analytics
# ---------------------------------------------------------------------------


def strategy_summary_line(strategy: AgentStrategyRecord) -> str:
    """One-line summary of the active agent strategy."""
    parts = [f"strategy={strategy['name']!r}"]
    if strategy["simulation_mode"]:
        parts.append("SIM")
    if strategy["dca_amount_sat"] is not None:
        parts.append(f"DCA={format_sat(strategy['dca_amount_sat'])}")
    parts.append(f"max_fee={strategy['max_fee_rate_sat_vbyte']} sat/vbyte")
    return " | ".join(parts)



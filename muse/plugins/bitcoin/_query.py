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
    AddressLabelRecord,
    AgentStrategyRecord,
    FeeEstimateRecord,
    LightningChannelRecord,
    OraclePriceTickRecord,
    PendingTxRecord,
    ScriptType,
    UTXORecord,
)

logger = logging.getLogger(__name__)

_SATS_PER_BTC: int = 100_000_000

# ---------------------------------------------------------------------------
# Input weight constants (vbytes per UTXO spent, used for fee estimation)
# These are consensus-accurate for the dominant script path of each type.
# P2PKH/P2SH are legacy; P2WPKH/P2WSH are segwit v0; P2TR is taproot key-path.
# ---------------------------------------------------------------------------

_INPUT_VBYTES: dict[ScriptType, int] = {
    "p2pkh":    148,   # legacy — largest inputs
    "p2sh":      91,   # P2SH-P2WPKH wrapped segwit
    "p2wpkh":    41,   # native segwit v0 — most common
    "p2wsh":    105,   # native segwit v0 multisig (avg 2-of-3)
    "p2tr":      58,   # taproot key-path spend
    "op_return": 41,   # unspendable — estimate only
    "unknown":  100,   # conservative fallback
}

# Cost to create + eventually spend a P2WPKH change output (in vbytes).
# Used by coin selection to compute waste score.
_CHANGE_OUTPUT_VBYTES: int = 34   # creating a P2WPKH output
_CHANGE_SPEND_VBYTES: int = 41    # spending a P2WPKH output later
_CHANGE_COST_VBYTES: int = _CHANGE_OUTPUT_VBYTES + _CHANGE_SPEND_VBYTES  # 75


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


def estimated_input_vbytes(script_type: ScriptType) -> int:
    """Return the estimated virtual-byte size of spending a UTXO of *script_type*.

    These values are used for fee estimation, effective-value calculation, and
    dust threshold computation.  They are consensus-accurate for the dominant
    spend path of each script type.

    Args:
        script_type: One of the recognised Bitcoin script types.

    Returns:
        Estimated vbyte size of the input witness stack + scriptSig.
    """
    return _INPUT_VBYTES.get(script_type, _INPUT_VBYTES["unknown"])


def effective_value_sat(utxo: UTXORecord, fee_rate_sat_vbyte: int) -> int:
    """Return the net economic value of spending *utxo* at *fee_rate_sat_vbyte*.

    Effective value = ``amount_sat − (input_vbytes × fee_rate)``.

    A positive result means the UTXO is profitable to spend at this fee rate.
    Zero or negative means it costs more to spend the coin than the coin is
    worth — it is economically dust and should be excluded from coin selection.

    Args:
        utxo:               The UTXO to evaluate.
        fee_rate_sat_vbyte: Current fee rate in satoshis per virtual byte.

    Returns:
        Net effective value in satoshis (may be negative for dust UTXOs).
    """
    spend_fee = estimated_input_vbytes(utxo["script_type"]) * fee_rate_sat_vbyte
    return utxo["amount_sat"] - spend_fee


def dust_threshold_sat(script_type: ScriptType, fee_rate_sat_vbyte: int) -> int:
    """Return the dust threshold for *script_type* at *fee_rate_sat_vbyte*.

    Bitcoin Core defines dust as: ``amount < 3 × fee_to_spend``.
    A UTXO below this threshold would cost more than 33 % of its value in fees
    to spend, making it economically irrational to include in a transaction.

    Args:
        script_type:        Script type of the UTXO to evaluate.
        fee_rate_sat_vbyte: Current fee rate in satoshis per virtual byte.

    Returns:
        Minimum amount in satoshis for a non-dust UTXO of this script type.
    """
    spend_fee = estimated_input_vbytes(script_type) * fee_rate_sat_vbyte
    return 3 * spend_fee


def is_dust(utxo: UTXORecord, fee_rate_sat_vbyte: int) -> bool:
    """Return ``True`` if *utxo* is economically dust at *fee_rate_sat_vbyte*.

    A UTXO is dust when its amount is less than the dust threshold for its
    script type.  Dust UTXOs increase transaction size without contributing
    meaningful value and should be excluded from coin selection.

    Args:
        utxo:               The UTXO to evaluate.
        fee_rate_sat_vbyte: Current fee rate in satoshis per virtual byte.

    Returns:
        ``True`` when the UTXO amount is below the dust threshold.
    """
    return utxo["amount_sat"] < dust_threshold_sat(utxo["script_type"], fee_rate_sat_vbyte)


# ---------------------------------------------------------------------------
# Halving mechanics
# ---------------------------------------------------------------------------

_HALVING_INTERVAL: int = 210_000
_INITIAL_SUBSIDY_SAT: int = 5_000_000_000   # 50 BTC in satoshis
_BLOCKS_PER_YEAR: int = 52_560              # ~6 blocks/hour × 24 × 365
_BLOCKS_PER_10_MIN: int = 1                 # 1 block ≈ 10 minutes


def halving_epoch(height: int) -> int:
    """Return which halving epoch *height* belongs to (0 = genesis era)."""
    return height // _HALVING_INTERVAL


def current_subsidy_sat(height: int) -> int:
    """Return the block subsidy in satoshis at *height*.

    Uses Bitcoin Core's right-shift formula: ``50 BTC >> epoch``.
    Returns 0 once the subsidy rounds down past 1 satoshi (epoch ≥ 33).
    """
    epoch = halving_epoch(height)
    if epoch >= 33:
        return 0
    return _INITIAL_SUBSIDY_SAT >> epoch


def blocks_until_halving(height: int) -> int:
    """Return the number of blocks remaining until the next halving."""
    next_halving_h = (halving_epoch(height) + 1) * _HALVING_INTERVAL
    return max(0, next_halving_h - height)


def next_halving_height(height: int) -> int:
    """Return the block height of the next halving event."""
    return (halving_epoch(height) + 1) * _HALVING_INTERVAL


def estimated_days_until_halving(height: int) -> float:
    """Rough estimate of calendar days until the next halving.

    Assumes one block every 10 minutes on average.
    """
    blocks = blocks_until_halving(height)
    return blocks * 10 / (60 * 24)  # blocks × 10 min / (min/day)


# ---------------------------------------------------------------------------
# Whale tier classification
# ---------------------------------------------------------------------------

# Tiers ordered from largest to smallest — first match wins.
_WHALE_TIERS: list[tuple[str, int]] = [
    ("Humpback", 100_000 * _SATS_PER_BTC),  # ≥ 100 000 BTC
    ("Whale",      1_000 * _SATS_PER_BTC),  # ≥ 1 000 BTC
    ("Shark",        100 * _SATS_PER_BTC),  # ≥ 100 BTC
    ("Dolphin",       10 * _SATS_PER_BTC),  # ≥ 10 BTC
    ("Fish",           1 * _SATS_PER_BTC),  # ≥ 1 BTC
    ("Crab",     1_000_000),                 # ≥ 0.01 BTC
    ("Shrimp",     100_000),                 # ≥ 0.001 BTC
    ("Plankton",         0),                 # < 0.001 BTC
]


def whale_tier(total_sat: int) -> str:
    """Return the ecosystem tier label for a wallet holding *total_sat* satoshis.

    Tiers (ordered largest → smallest):
    Humpback ≥ 100 000 BTC · Whale ≥ 1 000 BTC · Shark ≥ 100 BTC ·
    Dolphin ≥ 10 BTC · Fish ≥ 1 BTC · Crab ≥ 0.01 BTC ·
    Shrimp ≥ 0.001 BTC · Plankton < 0.001 BTC
    """
    for name, threshold in _WHALE_TIERS:
        if total_sat >= threshold:
            return name
    return "Plankton"


def next_tier_threshold_sat(total_sat: int) -> int | None:
    """Return the satoshi amount needed to reach the next whale tier, or ``None`` at Humpback."""
    for name, threshold in _WHALE_TIERS:
        if total_sat < threshold:
            return threshold
    return None  # already Humpback


# ---------------------------------------------------------------------------
# HODL analytics
# ---------------------------------------------------------------------------


def hodl_score(utxos: list[UTXORecord], current_height: int) -> float:
    """Weighted-average coin age in blocks — the canonical HODL metric.

    Coins weighted by their satoshi value: a 1 BTC coin held for 1 000 blocks
    contributes more to the score than a 1 000 sat coin held for the same time.
    Higher score = stronger HODLer.

    Args:
        utxos:          UTXO set to analyse.
        current_height: Current chain tip height.

    Returns:
        Weighted-average coin age in blocks.  ``0.0`` if no confirmed UTXOs.
    """
    total_weighted_age = 0.0
    total_sats = 0
    for u in utxos:
        bh = u["block_height"]
        if bh is not None and u["confirmations"] >= 1:
            age = max(0, current_height - bh)
            total_weighted_age += age * u["amount_sat"]
            total_sats += u["amount_sat"]
    if total_sats == 0:
        return 0.0
    return total_weighted_age / total_sats


def diamond_hands_sat(utxos: list[UTXORecord], current_height: int) -> int:
    """Return satoshis held for more than one year (~52 560 blocks).

    These are the "diamond hands" — coins that have never been moved through
    a complete market cycle.  Long-term capital gains territory.

    Args:
        utxos:          UTXO set to analyse.
        current_height: Current chain tip height.

    Returns:
        Total satoshis in UTXOs older than one year.
    """
    return sum(
        u["amount_sat"]
        for u in utxos
        if u["block_height"] is not None
        and (current_height - u["block_height"]) >= _BLOCKS_PER_YEAR
    )


def short_term_sat(utxos: list[UTXORecord], current_height: int) -> int:
    """Return satoshis in UTXOs younger than one year (short-term capital gains territory)."""
    return sum(
        u["amount_sat"]
        for u in utxos
        if u["block_height"] is not None
        and (current_height - u["block_height"]) < _BLOCKS_PER_YEAR
        and u["confirmations"] >= 1
    )


def hodl_grade(score: float) -> str:
    """Return a letter grade for a HODL score (weighted-average coin age in blocks).

    Grade thresholds (1 year ≈ 52 560 blocks):
    S ≥ 3 years · A ≥ 1 year · B ≥ 6 months · C ≥ 3 months · D ≥ 1 month · F < 1 month
    """
    if score >= _BLOCKS_PER_YEAR * 3:
        return "S"
    if score >= _BLOCKS_PER_YEAR:
        return "A"
    if score >= _BLOCKS_PER_YEAR // 2:
        return "B"
    if score >= _BLOCKS_PER_YEAR // 4:
        return "C"
    if score >= _BLOCKS_PER_YEAR // 12:
        return "D"
    return "F"


# ---------------------------------------------------------------------------
# Privacy analytics
# ---------------------------------------------------------------------------


def address_reuse_count(utxos: list[UTXORecord]) -> int:
    """Return the number of addresses that appear more than once in *utxos*.

    Address reuse is the single biggest Bitcoin privacy leak — it links
    multiple transactions to the same owner.  Zero reuse is the target.
    """
    from collections import Counter
    addr_count: Counter[str] = Counter(u["address"] for u in utxos)
    return sum(1 for count in addr_count.values() if count > 1)


def script_type_diversity(utxos: list[UTXORecord]) -> float:
    """Return the Shannon entropy of the script type distribution.

    A wallet using only one script type is trivially fingerprinted by chain
    analysis.  Higher entropy (more type diversity) = harder to fingerprint.
    Maximum entropy for N types = log2(N).

    Returns:
        Shannon entropy in bits.  0.0 for a single-type or empty wallet.
    """
    import math
    from collections import Counter
    total = len(utxos)
    if total == 0:
        return 0.0
    counts: Counter[str] = Counter(u["script_type"] for u in utxos)
    entropy = 0.0
    for count in counts.values():
        p = count / total
        entropy -= p * math.log2(p)
    return round(entropy, 4)


def taproot_adoption_pct(utxos: list[UTXORecord]) -> float:
    """Return the fraction of UTXO value (by sats) held in P2TR outputs.

    Taproot (P2TR) provides the best privacy and script flexibility.
    100 % adoption means all coins are in taproot outputs.
    """
    total = total_balance_sat(utxos)
    if total == 0:
        return 0.0
    p2tr = sum(u["amount_sat"] for u in utxos if u["script_type"] == "p2tr")
    return (p2tr / total) * 100.0


def balance_by_category(
    utxos: list[UTXORecord],
    labels: list[AddressLabelRecord],
) -> dict[str, int]:
    """Return ``{category: total_sats}`` using address label annotations.

    UTXOs whose address has no label are bucketed under ``"unknown"``.

    Args:
        utxos:  UTXO set to categorize.
        labels: Address label records providing category annotations.

    Returns:
        Dict mapping category strings to total satoshis in that category.
    """
    addr_to_cat: dict[str, str] = {lbl["address"]: lbl["category"] for lbl in labels}
    result: dict[str, int] = {}
    for u in utxos:
        cat = addr_to_cat.get(u["address"], "unknown")
        result[cat] = result.get(cat, 0) + u["amount_sat"]
    return result


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



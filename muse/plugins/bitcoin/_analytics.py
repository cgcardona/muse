"""Bitcoin domain semantic porcelain — the analytics engine.

This module is the god-tier layer that sits above the plumbing (snapshot /
diff / merge / CRDT) and above the raw query functions (_query.py).  Every
function here is purely functional, side-effect-free, and fully typed.

Why this belongs in MUSE
------------------------
The Bitcoin blockchain records *what* happened.  MUSE records *why* it
happened, *who* decided it, *which strategy* was active, and *what the
alternatives were*.  The analytics engine makes that versioned intent
actionable: it answers questions that no blockchain explorer can answer.

Function catalogue
------------------
**Coin selection**
- :func:`select_coins` — Branch-and-Bound (BnB) with four fallback algorithms.
  Bitcoin Core's own algorithm.  Agents call this before broadcasting.

**Wallet health**
- :func:`wallet_summary` — Complete, oracle-enriched snapshot of wallet state.
- :func:`utxo_lifecycle` — UTXO-level provenance: age, maturity, dust status,
  effective value, category annotation.

**Portfolio analytics**
- :func:`portfolio_snapshot` — Time-anchored portfolio value in sats and USD.
- :func:`portfolio_pnl` — P&L between two portfolio snapshots.

**Lightning analytics**
- :func:`channel_health_report` — Composite health score for the Lightning node.
- :func:`rebalance_candidates` — Channels that need rebalancing and how much.

**Fee analytics**
- :func:`fee_window` — Historical fee context + actionable recommendation.

**UTXO management**
- :func:`consolidation_plan` — When and how to merge small UTXOs, with
  break-even analysis.

All functions accept only TypedDicts from :mod:`~muse.plugins.bitcoin._types`
and primitives — no I/O, no network, no object store access.
"""

from __future__ import annotations

import logging
import statistics
from typing import Literal, TypedDict

from muse.plugins.bitcoin._query import (
    balance_by_category,
    balance_by_script_type,
    channel_liquidity_totals,
    channel_utilization,
    coin_age_blocks,
    confirmed_balance_sat,
    dust_threshold_sat,
    effective_value_sat,
    estimated_input_vbytes,
    format_sat,
    is_dust,
    latest_fee_estimate,
    latest_price,
    total_balance_sat,
    utxo_key,
)
from muse.plugins.bitcoin._types import (
    AddressLabelRecord,
    AgentStrategyRecord,
    CoinSelectAlgo,
    FeeEstimateRecord,
    LightningChannelRecord,
    OraclePriceTickRecord,
    PendingTxRecord,
    RoutingPolicyRecord,
    UTXORecord,
)

logger = logging.getLogger(__name__)

_SATS_PER_BTC: int = 100_000_000

# ---------------------------------------------------------------------------
# Analytics TypedDicts — structured results returned by every public function
# ---------------------------------------------------------------------------


class CoinSelectionResult(TypedDict):
    """Result of a coin selection algorithm run.

    ``selected`` is the list of UTXOs chosen to fund the transaction.
    ``total_input_sat`` is the sum of their amounts.
    ``change_sat`` is the amount returned to the wallet (0 for an exact match).
    ``fee_sat`` is the estimated miner fee for this input set.
    ``waste_score`` is BnB's waste metric: change_sat (lower is better).
    ``algorithm`` records which algorithm produced this result.
    ``success`` is ``False`` if no valid selection exists (insufficient funds
    or all UTXOs are dust at this fee rate).
    ``failure_reason`` provides a human-readable explanation on failure.
    """

    selected: list[UTXORecord]
    total_input_sat: int
    target_sat: int
    change_sat: int
    fee_sat: int
    waste_score: int
    algorithm: CoinSelectAlgo
    success: bool
    failure_reason: str | None


class UTXOLifecycle(TypedDict):
    """Rich provenance snapshot of a single UTXO.

    Combines raw UTXO data with computed fields: economic viability at the
    current fee rate, coin maturity, label-annotated category, and age.
    """

    key: str
    amount_sat: int
    address: str
    script_type: str
    received_at_height: int | None
    age_blocks: int | None
    confirmations: int
    is_coinbase: bool
    is_mature: bool
    is_spendable: bool
    is_dust: bool
    effective_value_sat: int
    estimated_spend_fee_sat: int
    label: str | None
    category: str


class WalletSummary(TypedDict):
    """Complete oracle-enriched wallet snapshot for display or agent decision-making.

    Aggregates on-chain and Lightning state into a single view.  USD values
    are ``None`` when no oracle price data is available.
    ``script_type_breakdown`` and ``category_breakdown`` are dynamic maps
    because the set of script types / categories in any wallet varies.
    """

    block_height: int | None
    total_sat: int
    confirmed_sat: int
    unconfirmed_sat: int
    immature_coinbase_sat: int
    spendable_sat: int
    in_lightning_sat: int
    total_portfolio_sat: int
    total_usd: float | None
    spendable_usd: float | None
    utxo_count: int
    dust_utxo_count: int
    script_type_breakdown: dict[str, int]
    category_breakdown: dict[str, int]
    channel_count: int
    active_channel_count: int
    pending_tx_count: int
    strategy_name: str
    simulation_mode: bool


class PortfolioSnapshot(TypedDict):
    """Time-anchored portfolio value at a single point.

    ``block_height`` and ``timestamp`` anchor the snapshot to a specific
    moment so that two snapshots can be compared for P&L computation.
    """

    block_height: int | None
    timestamp: int | None
    on_chain_sat: int
    lightning_sat: int
    total_sat: int
    price_usd: float | None
    total_usd: float | None


class PortfolioPnL(TypedDict):
    """Profit-and-loss comparison between two portfolio snapshots.

    ``sat_delta`` is the raw change in satoshi holdings.
    ``net_sat_delta`` subtracts estimated fees paid to give the true economic gain.
    USD deltas are ``None`` when either snapshot lacks oracle price data.
    ``pct_change`` is the percentage change in total satoshi holdings.
    """

    base: PortfolioSnapshot
    current: PortfolioSnapshot
    sat_delta: int
    net_sat_delta: int
    estimated_fees_paid_sat: int
    pct_change: float | None
    base_usd: float | None
    current_usd: float | None
    usd_delta: float | None
    usd_pct_change: float | None


class RebalanceCandidate(TypedDict):
    """A Lightning channel that needs liquidity rebalancing.

    ``direction`` indicates whether local balance is too low (``"pull_in"``)
    or too high (``"push_out"``).  ``suggested_amount_sat`` is the amount to
    route in a circular payment to reach the target utilization midpoint.
    ``urgency`` is determined by how far the channel deviates from the target.
    """

    channel_id: str
    peer_alias: str | None
    capacity_sat: int
    local_balance_sat: int
    remote_balance_sat: int
    utilization: float
    target_utilization: float
    direction: Literal["push_out", "pull_in"]
    suggested_amount_sat: int
    urgency: Literal["critical", "high", "medium", "low"]


class ChannelHealthReport(TypedDict):
    """Composite Lightning node health assessment.

    ``health_score`` is a [0.0, 1.0] composite: 1.0 is perfect (all channels
    active, all balanced within threshold).  ``assessment`` provides a
    human-readable verdict.  ``rebalance_candidates`` is the list of channels
    needing attention, sorted by urgency.
    """

    total_channels: int
    active_channels: int
    inactive_channels: int
    total_capacity_sat: int
    local_balance_sat: int
    remote_balance_sat: int
    avg_local_utilization: float
    imbalanced_count: int
    htlc_stuck_count: int
    rebalance_candidates: list[RebalanceCandidate]
    health_score: float
    assessment: str


class ConsolidationPlan(TypedDict):
    """Plan to consolidate small UTXOs into fewer, larger ones.

    Consolidation is worthwhile when the fee savings from fewer future inputs
    exceeds the consolidation transaction fee.  ``recommended`` is ``True``
    when the break-even point is within a reasonable planning horizon.
    ``expected_savings_sat`` is the total fee saved over the next
    ``savings_horizon_spends`` independent spends.
    ``break_even_fee_rate`` is the fee rate at which consolidation is neutral.
    """

    utxos_to_consolidate: list[UTXORecord]
    input_count: int
    output_count: int
    estimated_fee_sat: int
    expected_savings_sat: int
    savings_horizon_spends: int
    break_even_fee_rate: int
    recommended: bool
    reason: str


class FeeRecommendation(TypedDict):
    """Historical fee context and actionable sending recommendation.

    ``percentile`` is where the current rate sits in the historical
    distribution [0.0 = historical low, 1.0 = historical high].
    ``recommendation`` is one of:
    - ``"send_now"`` — current rate is at or near historical low.
    - ``"wait"`` — current rate is elevated; saving is likely.
    - ``"rbf_now"`` — in-flight transaction is stuck; bump it.
    - ``"cpfp_eligible"`` — a child transaction can boost a stuck parent.
    ``optimal_wait_blocks`` is a rough estimate when ``recommendation`` is
    ``"wait"``; ``None`` when prediction is not possible.
    """

    target_blocks: int
    current_sat_vbyte: int
    historical_min_sat_vbyte: int
    historical_max_sat_vbyte: int
    historical_median_sat_vbyte: int
    percentile: float
    recommendation: Literal["send_now", "wait", "rbf_now", "cpfp_eligible"]
    reason: str
    optimal_wait_blocks: int | None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _sat_to_usd(amount_sat: int, price_usd: float) -> float:
    """Convert satoshis to USD at the given price per BTC."""
    return (amount_sat / _SATS_PER_BTC) * price_usd


def _pct(a: int, b: int) -> float | None:
    """Return (a - b) / b as a percentage, or ``None`` if b is zero."""
    if b == 0:
        return None
    return ((a - b) / b) * 100.0


# ---------------------------------------------------------------------------
# Coin selection — Branch-and-Bound + fallbacks
# ---------------------------------------------------------------------------


def _bnb(
    candidates: list[tuple[int, UTXORecord]],
    target: int,
    change_cost: int,
    max_iters: int,
) -> list[UTXORecord] | None:
    """Branch-and-Bound search for an exact or near-exact coin selection.

    Finds the subset of *candidates* (sorted descending by effective value)
    whose total effective value satisfies::

        target ≤ total ≤ target + change_cost

    This is the "no-change" target: the selection exactly funds the
    transaction without creating a change output, which maximises privacy
    and minimises long-term waste.

    Args:
        candidates:  ``(effective_value_sat, UTXORecord)`` pairs, sorted
                     descending by effective value.
        target:      Minimum total effective value required.
        change_cost: Maximum overshoot allowed (creating a change output
                     costs this many satoshis in the long run).
        max_iters:   Maximum BnB iterations before aborting.

    Returns:
        The best set of UTXOs found, or ``None`` if BnB fails within the
        iteration budget.
    """
    upper = target + change_cost
    n = len(candidates)
    suffix_sum = [0] * (n + 1)
    for i in range(n - 1, -1, -1):
        suffix_sum[i] = suffix_sum[i + 1] + candidates[i][0]

    best: list[UTXORecord] | None = None
    best_waste = upper + 1

    stack: list[tuple[int, int, list[UTXORecord]]] = [(0, 0, [])]
    iters = 0

    while stack and iters < max_iters:
        iters += 1
        depth, current, selection = stack.pop()

        if current >= target:
            waste = current - target
            if waste < best_waste:
                best_waste = waste
                best = list(selection)
            if waste == 0:
                break
            continue

        if depth == n or current + suffix_sum[depth] < target:
            continue

        ev, utxo = candidates[depth]

        # Branch: exclude this coin
        stack.append((depth + 1, current, selection))

        # Branch: include this coin (push last — explored first)
        stack.append((depth + 1, current + ev, selection + [utxo]))

    return best


def select_coins(
    utxos: list[UTXORecord],
    target_sat: int,
    fee_rate_sat_vbyte: int,
    algorithm: CoinSelectAlgo = "branch_and_bound",
    tx_overhead_vbytes: int = 11,
    change_output_vbytes: int = 34,
    bnb_max_iters: int = 100_000,
) -> CoinSelectionResult:
    """Select UTXOs to fund a transaction of *target_sat* satoshis.

    Implements four algorithms, selectable via *algorithm*:

    ``"branch_and_bound"`` (default, recommended)
        Bitcoin Core's algorithm.  Searches for a subset whose effective value
        exactly equals *target_sat* (zero waste).  Falls back to
        ``"largest_first"`` when BnB fails within the iteration budget.

    ``"largest_first"``
        Greedy selection: sort by amount descending, take until the target
        is met.  Simple and fast but often creates change.

    ``"smallest_first"``
        Greedy selection: sort ascending.  Consolidates dust at the cost of
        higher fee.  Use when fees are low and UTXO consolidation is desired.

    ``"random"``
        Privacy-preserving selection: shuffle, then take until target is met.
        Hides wallet fingerprint at the cost of sub-optimal fee efficiency.

    Dust UTXOs (effective value ≤ 0 at *fee_rate_sat_vbyte*) are always
    excluded before selection begins.

    Args:
        utxos:                 The available UTXO set.
        target_sat:            Amount to send in satoshis (excluding fee).
        fee_rate_sat_vbyte:    Current fee rate in sat/vbyte.
        algorithm:             Coin selection algorithm to use.
        tx_overhead_vbytes:    Fixed transaction overhead (version, locktime,
                               input/output count) — default 11 vbytes.
        change_output_vbytes:  Size of the change output (default 34 for
                               P2WPKH).  Used to compute the fee with change.
        bnb_max_iters:         Maximum BnB search iterations.

    Returns:
        A :class:`CoinSelectionResult` with ``success=True`` when a valid
        selection exists, or ``success=False`` with a ``failure_reason``.
    """
    if target_sat <= 0:
        return CoinSelectionResult(
            selected=[],
            total_input_sat=0,
            target_sat=target_sat,
            change_sat=0,
            fee_sat=0,
            waste_score=0,
            algorithm=algorithm,
            success=False,
            failure_reason="target_sat must be positive",
        )

    # Filter dust
    spendable = [u for u in utxos if not is_dust(u, fee_rate_sat_vbyte)]

    if not spendable:
        return CoinSelectionResult(
            selected=[],
            total_input_sat=0,
            target_sat=target_sat,
            change_sat=0,
            fee_sat=0,
            waste_score=0,
            algorithm=algorithm,
            success=False,
            failure_reason="all UTXOs are dust at this fee rate",
        )

    def _fee_for(inputs: list[UTXORecord], with_change: bool) -> int:
        input_vbytes = sum(estimated_input_vbytes(u["script_type"]) for u in inputs)
        output_vbytes = change_output_vbytes if with_change else 0
        total_vbytes = tx_overhead_vbytes + input_vbytes + output_vbytes
        return total_vbytes * fee_rate_sat_vbyte

    def _build_result(
        selected: list[UTXORecord],
        algo: CoinSelectAlgo,
    ) -> CoinSelectionResult:
        total_in = sum(u["amount_sat"] for u in selected)
        fee_no_change = _fee_for(selected, with_change=False)
        fee_with_change = _fee_for(selected, with_change=True)
        if total_in >= target_sat + fee_with_change:
            change = total_in - target_sat - fee_with_change
            fee = fee_with_change
        else:
            change = 0
            fee = fee_no_change
        return CoinSelectionResult(
            selected=selected,
            total_input_sat=total_in,
            target_sat=target_sat,
            change_sat=max(0, change),
            fee_sat=fee,
            waste_score=max(0, change),
            algorithm=algo,
            success=True,
            failure_reason=None,
        )

    def _largest_first(pool: list[UTXORecord]) -> list[UTXORecord] | None:
        pool_sorted = sorted(pool, key=lambda u: u["amount_sat"], reverse=True)
        selected: list[UTXORecord] = []
        total = 0
        for u in pool_sorted:
            selected.append(u)
            total += u["amount_sat"]
            fee = _fee_for(selected, with_change=(total > target_sat + _fee_for(selected, False)))
            if total >= target_sat + fee:
                return selected
        return None

    def _smallest_first(pool: list[UTXORecord]) -> list[UTXORecord] | None:
        pool_sorted = sorted(pool, key=lambda u: u["amount_sat"])
        selected: list[UTXORecord] = []
        total = 0
        for u in pool_sorted:
            selected.append(u)
            total += u["amount_sat"]
            fee = _fee_for(selected, with_change=False)
            if total >= target_sat + fee:
                return selected
        return None

    def _random_select(pool: list[UTXORecord]) -> list[UTXORecord] | None:
        import random
        shuffled = list(pool)
        random.shuffle(shuffled)
        selected: list[UTXORecord] = []
        total = 0
        for u in shuffled:
            selected.append(u)
            total += u["amount_sat"]
            fee = _fee_for(selected, with_change=False)
            if total >= target_sat + fee:
                return selected
        return None

    # Branch-and-Bound
    if algorithm == "branch_and_bound":
        candidates = sorted(
            [(effective_value_sat(u, fee_rate_sat_vbyte), u) for u in spendable],
            reverse=True,
        )
        # Limit BnB to 20 UTXOs (Bitcoin Core's pool size limit)
        bnb_pool = candidates[:20]
        change_cost = (change_output_vbytes + estimated_input_vbytes("p2wpkh")) * fee_rate_sat_vbyte
        result = _bnb(bnb_pool, target_sat, change_cost, bnb_max_iters)
        if result:
            return _build_result(result, "branch_and_bound")
        # BnB failed — fall through to largest_first
        fallback = _largest_first(spendable)
        if fallback:
            return _build_result(fallback, "branch_and_bound")

    elif algorithm == "largest_first":
        result_lf = _largest_first(spendable)
        if result_lf:
            return _build_result(result_lf, "largest_first")

    elif algorithm == "smallest_first":
        result_sf = _smallest_first(spendable)
        if result_sf:
            return _build_result(result_sf, "smallest_first")

    elif algorithm == "random":
        result_r = _random_select(spendable)
        if result_r:
            return _build_result(result_r, "random")

    total_available = sum(u["amount_sat"] for u in spendable)
    return CoinSelectionResult(
        selected=[],
        total_input_sat=0,
        target_sat=target_sat,
        change_sat=0,
        fee_sat=0,
        waste_score=0,
        algorithm=algorithm,
        success=False,
        failure_reason=(
            f"insufficient funds: {format_sat(total_available)} available, "
            f"{format_sat(target_sat)} required"
        ),
    )


# ---------------------------------------------------------------------------
# UTXO lifecycle
# ---------------------------------------------------------------------------


def utxo_lifecycle(
    utxo: UTXORecord,
    labels: list[AddressLabelRecord],
    fee_rate_sat_vbyte: int,
    current_height: int | None = None,
) -> UTXOLifecycle:
    """Produce a complete provenance-annotated lifecycle record for *utxo*.

    Combines raw UTXO fields with computed economics (effective value, dust
    status, estimated spend fee), maturity status (for coinbase outputs), and
    label annotations.

    Args:
        utxo:               The UTXO to analyse.
        labels:             Address label records for annotation lookup.
        fee_rate_sat_vbyte: Current fee rate for effective-value computation.
        current_height:     Current chain tip height; ``None`` for age computation.

    Returns:
        A :class:`UTXOLifecycle` with every computed and annotated field.
    """
    addr_labels = {lbl["address"]: lbl for lbl in labels}
    label_rec = addr_labels.get(utxo["address"])
    label = label_rec["label"] if label_rec else None
    category = label_rec["category"] if label_rec else "unknown"

    age = coin_age_blocks(utxo, current_height) if current_height is not None else None
    is_mature = (not utxo["coinbase"]) or (utxo["confirmations"] >= 100)
    is_spendable = utxo["confirmations"] >= 1 and is_mature
    ev = effective_value_sat(utxo, fee_rate_sat_vbyte)
    spend_fee = estimated_input_vbytes(utxo["script_type"]) * fee_rate_sat_vbyte

    return UTXOLifecycle(
        key=utxo_key(utxo),
        amount_sat=utxo["amount_sat"],
        address=utxo["address"],
        script_type=utxo["script_type"],
        received_at_height=utxo["block_height"],
        age_blocks=age,
        confirmations=utxo["confirmations"],
        is_coinbase=utxo["coinbase"],
        is_mature=is_mature,
        is_spendable=is_spendable,
        is_dust=is_dust(utxo, fee_rate_sat_vbyte),
        effective_value_sat=ev,
        estimated_spend_fee_sat=spend_fee,
        label=label,
        category=category,
    )


# ---------------------------------------------------------------------------
# Wallet summary
# ---------------------------------------------------------------------------


def wallet_summary(
    utxos: list[UTXORecord],
    labels: list[AddressLabelRecord],
    channels: list[LightningChannelRecord],
    strategy: AgentStrategyRecord,
    prices: list[OraclePriceTickRecord],
    mempool: list[PendingTxRecord],
    fee_rate_sat_vbyte: int = 10,
    current_height: int | None = None,
) -> WalletSummary:
    """Produce a complete oracle-enriched wallet state summary.

    Aggregates every dimension of the versioned Bitcoin state into a single
    structured record suitable for display (``muse btc status``) or agent
    decision-making.

    Args:
        utxos:              The confirmed UTXO set.
        labels:             Address annotations for category breakdown.
        channels:           Lightning channel states.
        strategy:           Active agent strategy configuration.
        prices:             Oracle price feed for USD conversion.
        mempool:            Local mempool (pending transactions).
        fee_rate_sat_vbyte: Fee rate for dust detection.
        current_height:     Chain tip for age computation.

    Returns:
        A :class:`WalletSummary` with every computed field.
    """
    price = latest_price(prices)

    # On-chain balance components
    total = total_balance_sat(utxos)
    # Confirmed = any UTXO with ≥ 1 confirmation (includes immature coinbase)
    confirmed = sum(u["amount_sat"] for u in utxos if u["confirmations"] >= 1)
    unconfirmed = total - confirmed

    # Immature coinbase: confirmed (≥ 1 conf) but not yet spendable (< 100 conf)
    immature = sum(
        u["amount_sat"]
        for u in utxos
        if u["coinbase"] and u["confirmations"] < 100
    )
    # Spendable = confirmed minus immature (immature not excluded by confirmed count above)
    spendable = confirmed - immature

    # Lightning
    local_ln, _ = channel_liquidity_totals(channels)
    portfolio_total = spendable + local_ln

    # Dust
    dust_count = sum(1 for u in utxos if is_dust(u, fee_rate_sat_vbyte))

    # Script type and category breakdowns
    script_breakdown = balance_by_script_type(utxos)
    cat_breakdown = balance_by_category(utxos, labels)

    # Active channels
    active = sum(1 for c in channels if c["is_active"])

    total_usd = _sat_to_usd(portfolio_total, price) if price else None
    spendable_usd = _sat_to_usd(spendable, price) if price else None

    return WalletSummary(
        block_height=current_height,
        total_sat=total,
        confirmed_sat=confirmed,
        unconfirmed_sat=unconfirmed,
        immature_coinbase_sat=immature,
        spendable_sat=spendable,
        in_lightning_sat=local_ln,
        total_portfolio_sat=portfolio_total,
        total_usd=total_usd,
        spendable_usd=spendable_usd,
        utxo_count=len(utxos),
        dust_utxo_count=dust_count,
        script_type_breakdown=script_breakdown,
        category_breakdown=cat_breakdown,
        channel_count=len(channels),
        active_channel_count=active,
        pending_tx_count=len(mempool),
        strategy_name=strategy["name"],
        simulation_mode=strategy["simulation_mode"],
    )


# ---------------------------------------------------------------------------
# Portfolio analytics
# ---------------------------------------------------------------------------


def portfolio_snapshot(
    utxos: list[UTXORecord],
    channels: list[LightningChannelRecord],
    prices: list[OraclePriceTickRecord],
    current_height: int | None = None,
    timestamp: int | None = None,
) -> PortfolioSnapshot:
    """Capture a time-anchored portfolio value snapshot.

    Args:
        utxos:          On-chain UTXO set.
        channels:       Lightning channel states.
        prices:         Oracle price feed.
        current_height: Chain tip height for this snapshot.
        timestamp:      Unix timestamp for this snapshot (from oracle or system).

    Returns:
        A :class:`PortfolioSnapshot` with on-chain, Lightning, and USD totals.
    """
    on_chain = total_balance_sat(utxos)
    local_ln, _ = channel_liquidity_totals(channels)
    total = on_chain + local_ln
    price = latest_price(prices)
    total_usd = _sat_to_usd(total, price) if price else None

    return PortfolioSnapshot(
        block_height=current_height,
        timestamp=timestamp,
        on_chain_sat=on_chain,
        lightning_sat=local_ln,
        total_sat=total,
        price_usd=price,
        total_usd=total_usd,
    )


def portfolio_pnl(
    base: PortfolioSnapshot,
    current: PortfolioSnapshot,
    estimated_fees_paid_sat: int = 0,
) -> PortfolioPnL:
    """Compute P&L between two portfolio snapshots.

    ``sat_delta`` is the gross change in satoshi holdings.
    ``net_sat_delta`` is the gross change minus fees paid — the true economic gain.
    USD figures use each snapshot's own oracle price, so they represent the
    real USD value at each point in time (not a single exchange rate applied
    to the difference).

    Args:
        base:                    The earlier portfolio snapshot.
        current:                 The later portfolio snapshot.
        estimated_fees_paid_sat: Total on-chain fees paid between the two
                                 snapshots (from the execution log).

    Returns:
        A :class:`PortfolioPnL` with gross and net P&L in both sats and USD.
    """
    sat_delta = current["total_sat"] - base["total_sat"]
    net_sat_delta = sat_delta + estimated_fees_paid_sat  # fees are a cost, so adding them back gives gross inflow

    base_usd = base["total_usd"]
    current_usd = current["total_usd"]
    usd_delta = (current_usd - base_usd) if (base_usd is not None and current_usd is not None) else None
    usd_pct = _pct(int(current_usd * 100), int(base_usd * 100)) if (base_usd and current_usd) else None
    sat_pct = _pct(current["total_sat"], base["total_sat"])

    return PortfolioPnL(
        base=base,
        current=current,
        sat_delta=sat_delta,
        net_sat_delta=net_sat_delta,
        estimated_fees_paid_sat=estimated_fees_paid_sat,
        pct_change=sat_pct,
        base_usd=base_usd,
        current_usd=current_usd,
        usd_delta=usd_delta,
        usd_pct_change=usd_pct,
    )


# ---------------------------------------------------------------------------
# Lightning analytics
# ---------------------------------------------------------------------------


def rebalance_candidates(
    channels: list[LightningChannelRecord],
    strategy: AgentStrategyRecord,
) -> list[RebalanceCandidate]:
    """Find Lightning channels that need rebalancing.

    A channel needs rebalancing when its local utilization falls below or above
    the strategy's ``lightning_rebalance_threshold``.  Below the threshold →
    ``"pull_in"`` (route a circular payment inward).  Above
    ``1 - threshold`` → ``"push_out"`` (route outward).

    Urgency is determined by the distance from the threshold:
    - ``"critical"``: utilization < 0.1 or > 0.9
    - ``"high"``:     utilization < threshold or > (1 - threshold)
    - ``"medium"``:   within 5 % of the threshold
    - ``"low"``:      approaching but not yet crossing the threshold

    Args:
        channels: Lightning channel states.
        strategy: Active agent strategy with ``lightning_rebalance_threshold``.

    Returns:
        List of :class:`RebalanceCandidate` records, sorted by urgency.
    """
    threshold = strategy["lightning_rebalance_threshold"]
    upper_threshold = 1.0 - threshold
    candidates: list[RebalanceCandidate] = []

    for ch in channels:
        if not ch["is_active"]:
            continue
        util = channel_utilization(ch)
        target_util = 0.5

        if util < threshold:
            direction: Literal["push_out", "pull_in"] = "pull_in"
            deviation = threshold - util
        elif util > upper_threshold:
            direction = "push_out"
            deviation = util - upper_threshold
        else:
            continue

        # Urgency from deviation magnitude
        if deviation >= 0.4 or util < 0.1 or util > 0.9:
            urgency: Literal["critical", "high", "medium", "low"] = "critical"
        elif deviation >= 0.2:
            urgency = "high"
        elif deviation >= 0.1:
            urgency = "medium"
        else:
            urgency = "low"

        usable = ch["capacity_sat"] - ch["local_reserve_sat"] - ch["remote_reserve_sat"]
        target_local = int(usable * target_util)
        suggested = abs(ch["local_balance_sat"] - target_local)

        candidates.append(
            RebalanceCandidate(
                channel_id=ch["channel_id"],
                peer_alias=ch["peer_alias"],
                capacity_sat=ch["capacity_sat"],
                local_balance_sat=ch["local_balance_sat"],
                remote_balance_sat=ch["remote_balance_sat"],
                utilization=util,
                target_utilization=target_util,
                direction=direction,
                suggested_amount_sat=suggested,
                urgency=urgency,
            )
        )

    urgency_order: dict[str, int] = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    candidates.sort(key=lambda c: urgency_order.get(c["urgency"], 99))
    return candidates


def channel_health_report(
    channels: list[LightningChannelRecord],
    routing: list[RoutingPolicyRecord],
    strategy: AgentStrategyRecord,
) -> ChannelHealthReport:
    """Produce a composite Lightning node health assessment.

    The health score weights three factors:
    - **Availability** (40 %): fraction of channels that are active.
    - **Balance** (40 %): fraction of channels within the rebalance threshold.
    - **HTLC health** (20 %): fraction of channels with zero stuck HTLCs.

    Args:
        channels: Lightning channel states.
        routing:  Routing policies (used for future routing revenue analysis).
        strategy: Active strategy with the rebalance threshold.

    Returns:
        A :class:`ChannelHealthReport` with composite score and candidates.
    """
    if not channels:
        return ChannelHealthReport(
            total_channels=0,
            active_channels=0,
            inactive_channels=0,
            total_capacity_sat=0,
            local_balance_sat=0,
            remote_balance_sat=0,
            avg_local_utilization=0.0,
            imbalanced_count=0,
            htlc_stuck_count=0,
            rebalance_candidates=[],
            health_score=1.0,
            assessment="No channels configured.",
        )

    total = len(channels)
    active_chs = [c for c in channels if c["is_active"]]
    inactive_chs = [c for c in channels if not c["is_active"]]
    active_count = len(active_chs)
    inactive_count = len(inactive_chs)

    total_cap = sum(c["capacity_sat"] for c in channels)
    local, remote = channel_liquidity_totals(channels)
    utils = [channel_utilization(c) for c in active_chs]
    avg_util = sum(utils) / len(utils) if utils else 0.0

    r_candidates = rebalance_candidates(channels, strategy)
    imbalanced = len(r_candidates)
    stuck_htlcs = sum(c["htlc_count"] for c in active_chs if c["htlc_count"] > 0)

    availability_score = active_count / total
    balance_score = max(0.0, 1.0 - (imbalanced / max(active_count, 1)))
    htlc_score = 1.0 if stuck_htlcs == 0 else max(0.0, 1.0 - (stuck_htlcs / (active_count * 5)))

    health = (0.4 * availability_score) + (0.4 * balance_score) + (0.2 * htlc_score)

    if health >= 0.9:
        assessment = "Excellent — all channels active and balanced."
    elif health >= 0.7:
        assessment = f"Good — {imbalanced} channel(s) need rebalancing."
    elif health >= 0.5:
        assessment = f"Fair — {inactive_count} inactive, {imbalanced} imbalanced."
    else:
        assessment = f"Poor — {inactive_count} channels inactive; immediate attention required."

    return ChannelHealthReport(
        total_channels=total,
        active_channels=active_count,
        inactive_channels=inactive_count,
        total_capacity_sat=total_cap,
        local_balance_sat=local,
        remote_balance_sat=remote,
        avg_local_utilization=avg_util,
        imbalanced_count=imbalanced,
        htlc_stuck_count=stuck_htlcs,
        rebalance_candidates=r_candidates,
        health_score=round(health, 3),
        assessment=assessment,
    )


# ---------------------------------------------------------------------------
# Fee analytics
# ---------------------------------------------------------------------------


def fee_window(
    fee_history: list[FeeEstimateRecord],
    target_blocks: int = 6,
    pending_txids: list[str] | None = None,
) -> FeeRecommendation:
    """Analyse historical fee data and produce a sending recommendation.

    Uses the full fee history from oracle snapshots to compute a statistical
    context for the current fee rate.  ``percentile`` places the current rate
    in its historical distribution so the agent can decide whether to send now
    or wait for lower fees.

    Args:
        fee_history:   All historical fee estimates (from ``oracles/fees.json``).
        target_blocks: Block target for confirmation (1, 6, or 144).
        pending_txids: Txids of stuck pending transactions (triggers RBF/CPFP
                       recommendations).

    Returns:
        A :class:`FeeRecommendation` with context, percentile, and verdict.
    """
    if not fee_history:
        return FeeRecommendation(
            target_blocks=target_blocks,
            current_sat_vbyte=1,
            historical_min_sat_vbyte=1,
            historical_max_sat_vbyte=1,
            historical_median_sat_vbyte=1,
            percentile=0.5,
            recommendation="send_now",
            reason="No fee history available — using minimum fee.",
            optimal_wait_blocks=None,
        )

    latest = max(fee_history, key=lambda e: e["timestamp"])

    # Extract the rate matching the target
    def _rate(e: FeeEstimateRecord) -> int:
        if target_blocks <= 1:
            return e["target_1_block_sat_vbyte"]
        if target_blocks <= 6:
            return e["target_6_block_sat_vbyte"]
        return e["target_144_block_sat_vbyte"]

    historical_rates = sorted(_rate(e) for e in fee_history)
    current_rate = _rate(latest)

    hist_min = historical_rates[0]
    hist_max = historical_rates[-1]
    hist_median = int(statistics.median(historical_rates))

    # Percentile: fraction of historical rates at or below current rate
    at_or_below = sum(1 for r in historical_rates if r <= current_rate)
    percentile = at_or_below / len(historical_rates)

    # Stuck transaction detection
    has_stuck = bool(pending_txids)
    if has_stuck:
        recommendation: Literal["send_now", "wait", "rbf_now", "cpfp_eligible"] = "rbf_now"
        reason = (
            f"{len(pending_txids or [])} pending transaction(s) may be stuck. "
            f"Current rate {current_rate} sat/vbyte — consider RBF fee bump."
        )
        return FeeRecommendation(
            target_blocks=target_blocks,
            current_sat_vbyte=current_rate,
            historical_min_sat_vbyte=hist_min,
            historical_max_sat_vbyte=hist_max,
            historical_median_sat_vbyte=hist_median,
            percentile=percentile,
            recommendation=recommendation,
            reason=reason,
            optimal_wait_blocks=None,
        )

    # Fee recommendation heuristics
    if percentile <= 0.25:
        recommendation = "send_now"
        reason = (
            f"Current rate {current_rate} sat/vbyte is in the bottom 25th percentile "
            f"of historical rates (min {hist_min}, median {hist_median}). Send now."
        )
        optimal_wait: int | None = None
    elif percentile >= 0.75:
        recommendation = "wait"
        # Estimate how long to wait: find the gap between recent rates
        recent_rates = sorted(_rate(e) for e in fee_history[-10:])
        low_recent = recent_rates[0] if recent_rates else hist_min
        # Rough heuristic: 1 block per 2 sat/vbyte above the local low
        wait_est = max(1, (current_rate - low_recent) * 2)
        optimal_wait = min(wait_est, 144)
        reason = (
            f"Current rate {current_rate} sat/vbyte is in the top 25th percentile. "
            f"Estimated {optimal_wait} blocks until rates drop near {low_recent} sat/vbyte."
        )
    else:
        recommendation = "send_now"
        optimal_wait = None
        reason = (
            f"Current rate {current_rate} sat/vbyte is near the historical median "
            f"({hist_median} sat/vbyte). Neutral conditions — sending now is reasonable."
        )

    return FeeRecommendation(
        target_blocks=target_blocks,
        current_sat_vbyte=current_rate,
        historical_min_sat_vbyte=hist_min,
        historical_max_sat_vbyte=hist_max,
        historical_median_sat_vbyte=hist_median,
        percentile=percentile,
        recommendation=recommendation,
        reason=reason,
        optimal_wait_blocks=optimal_wait,
    )


# ---------------------------------------------------------------------------
# UTXO consolidation planner
# ---------------------------------------------------------------------------


def consolidation_plan(
    utxos: list[UTXORecord],
    fee_rate_sat_vbyte: int,
    target_fee_rate_sat_vbyte: int | None = None,
    max_inputs: int = 50,
    savings_horizon_spends: int = 10,
) -> ConsolidationPlan:
    """Plan an optimal UTXO consolidation transaction.

    Consolidation merges multiple small UTXOs into one large UTXO in a
    low-fee environment.  The future benefit is that every subsequent
    transaction uses fewer (and smaller) inputs, paying less in fees.

    The planner selects UTXOs that are:
    1. Spendable (confirmed, not immature coinbase).
    2. Below the median UTXO size (small relative to the set).
    3. Limit to *max_inputs* inputs.

    Break-even analysis: at what fee rate does the consolidation fee equal the
    total savings from *savings_horizon_spends* transactions that avoid
    spending each UTXO individually?

    Args:
        utxos:                    Full confirmed UTXO set.
        fee_rate_sat_vbyte:       Current fee rate for consolidation cost.
        target_fee_rate_sat_vbyte: Expected future fee rate for savings
                                  computation.  Defaults to *fee_rate_sat_vbyte*.
        max_inputs:               Maximum UTXOs to consolidate in one tx.
        savings_horizon_spends:   Number of future transactions to amortize
                                  the consolidation benefit over.

    Returns:
        A :class:`ConsolidationPlan` with feasibility and break-even analysis.
    """
    future_rate = target_fee_rate_sat_vbyte or fee_rate_sat_vbyte

    # Only consolidate spendable, non-dust UTXOs
    spendable = [
        u for u in utxos
        if u["confirmations"] >= 1
        and not (u["coinbase"] and u["confirmations"] < 100)
        and not is_dust(u, fee_rate_sat_vbyte)
    ]

    if len(spendable) <= 1:
        return ConsolidationPlan(
            utxos_to_consolidate=[],
            input_count=0,
            output_count=0,
            estimated_fee_sat=0,
            expected_savings_sat=0,
            savings_horizon_spends=savings_horizon_spends,
            break_even_fee_rate=0,
            recommended=False,
            reason="Not enough spendable UTXOs to consolidate.",
        )

    # Target: UTXOs below the median size
    amounts = sorted(u["amount_sat"] for u in spendable)
    median_amount = amounts[len(amounts) // 2]
    candidates = [u for u in spendable if u["amount_sat"] <= median_amount]
    candidates = candidates[:max_inputs]

    if not candidates:
        return ConsolidationPlan(
            utxos_to_consolidate=[],
            input_count=0,
            output_count=0,
            estimated_fee_sat=0,
            expected_savings_sat=0,
            savings_horizon_spends=savings_horizon_spends,
            break_even_fee_rate=0,
            recommended=False,
            reason="No small UTXOs found below the median size.",
        )

    # Consolidation transaction cost
    input_vbytes = sum(estimated_input_vbytes(u["script_type"]) for u in candidates)
    tx_vbytes = 11 + input_vbytes + 31  # overhead + inputs + 1 P2WPKH output
    consolidation_fee = tx_vbytes * fee_rate_sat_vbyte

    # Savings: instead of spending N UTXOs individually N times each,
    # after consolidation we spend 1 UTXO. The saving per future tx
    # is: (N inputs at future_rate) − (1 input at future_rate)
    n = len(candidates)
    per_tx_saving = (input_vbytes - estimated_input_vbytes("p2wpkh")) * future_rate
    total_savings = per_tx_saving * savings_horizon_spends

    # Break-even fee rate: at what future_rate does savings == consolidation_fee?
    # consolidation_fee = tx_vbytes * consolidation_rate  (fixed)
    # savings = per_input_saving_vbytes * be_rate * spends
    # be_rate = consolidation_fee / (per_input_saving_vbytes * spends)
    saving_vbytes = input_vbytes - estimated_input_vbytes("p2wpkh")
    if saving_vbytes > 0 and savings_horizon_spends > 0:
        be_rate = int(consolidation_fee / (saving_vbytes * savings_horizon_spends))
    else:
        be_rate = 0

    recommended = total_savings > consolidation_fee

    if recommended:
        reason = (
            f"Consolidating {n} UTXOs saves ~{format_sat(total_savings)} in fees "
            f"over {savings_horizon_spends} future transactions. "
            f"Consolidation costs {format_sat(consolidation_fee)} at {fee_rate_sat_vbyte} sat/vbyte."
        )
    else:
        reason = (
            f"Consolidation costs {format_sat(consolidation_fee)} but only saves "
            f"~{format_sat(total_savings)} over {savings_horizon_spends} transactions. "
            f"Wait for lower fees or more UTXOs."
        )

    return ConsolidationPlan(
        utxos_to_consolidate=candidates,
        input_count=n,
        output_count=1,
        estimated_fee_sat=consolidation_fee,
        expected_savings_sat=max(0, total_savings),
        savings_horizon_spends=savings_horizon_spends,
        break_even_fee_rate=be_rate,
        recommended=recommended,
        reason=reason,
    )

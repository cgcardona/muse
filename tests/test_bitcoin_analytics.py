"""Comprehensive tests for the Bitcoin domain semantic porcelain layer.

Covers every public function in ``_analytics.py`` and the new query helpers
added to ``_query.py``.  Tests are organized by capability and assert exact
behaviour with realistic Bitcoin fixtures.
"""

from __future__ import annotations

import pytest

from muse.plugins.bitcoin._analytics import (
    ChannelHealthReport,
    CoinSelectionResult,
    ConsolidationPlan,
    FeeRecommendation,
    PortfolioPnL,
    PortfolioSnapshot,
    RebalanceCandidate,
    UTXOLifecycle,
    WalletSummary,
    channel_health_report,
    consolidation_plan,
    fee_window,
    portfolio_pnl,
    portfolio_snapshot,
    rebalance_candidates,
    select_coins,
    utxo_lifecycle,
    wallet_summary,
)
from muse.plugins.bitcoin._query import (
    balance_by_category,
    dust_threshold_sat,
    effective_value_sat,
    estimated_input_vbytes,
    is_dust,
)
from muse.plugins.bitcoin._types import (
    AddressLabelRecord,
    AgentStrategyRecord,
    CoinCategory,
    CoinSelectAlgo,
    FeeEstimateRecord,
    LightningChannelRecord,
    OraclePriceTickRecord,
    PendingTxRecord,
    RoutingPolicyRecord,
    ScriptType,
    UTXORecord,
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


def _utxo(
    txid: str = "a" * 64,
    vout: int = 0,
    amount_sat: int = 1_000_000,
    script_type: ScriptType = "p2wpkh",
    address: str = "bc1qtest",
    confirmations: int = 6,
    block_height: int | None = 850_000,
    coinbase: bool = False,
    label: str | None = None,
) -> UTXORecord:
    return UTXORecord(
        txid=txid,
        vout=vout,
        amount_sat=amount_sat,
        script_type=script_type,
        address=address,
        confirmations=confirmations,
        block_height=block_height,
        coinbase=coinbase,
        label=label,
    )


def _channel(
    channel_id: str = "850000x1x0",
    capacity_sat: int = 2_000_000,
    local_balance_sat: int = 1_000_000,
    remote_balance_sat: int = 900_000,
    is_active: bool = True,
    local_reserve_sat: int = 20_000,
    remote_reserve_sat: int = 20_000,
    htlc_count: int = 0,
    peer_alias: str | None = "ACINQ",
) -> LightningChannelRecord:
    return LightningChannelRecord(
        channel_id=channel_id,
        peer_pubkey="0279" + "aa" * 32,
        peer_alias=peer_alias,
        capacity_sat=capacity_sat,
        local_balance_sat=local_balance_sat,
        remote_balance_sat=remote_balance_sat,
        is_active=is_active,
        is_public=True,
        local_reserve_sat=local_reserve_sat,
        remote_reserve_sat=remote_reserve_sat,
        unsettled_balance_sat=0,
        htlc_count=htlc_count,
    )


def _label(
    address: str = "bc1qtest",
    label: str = "cold storage",
    category: CoinCategory = "income",
) -> AddressLabelRecord:
    return AddressLabelRecord(
        address=address,
        label=label,
        category=category,
        created_at=1_700_000_000,
    )


def _strategy(
    name: str = "conservative",
    max_fee: int = 10,
    rebalance_threshold: float = 0.2,
    simulation_mode: bool = False,
    dca_amount_sat: int | None = 500_000,
) -> AgentStrategyRecord:
    return AgentStrategyRecord(
        name=name,
        max_fee_rate_sat_vbyte=max_fee,
        min_confirmations=6,
        utxo_consolidation_threshold=20,
        dca_amount_sat=dca_amount_sat,
        dca_interval_blocks=144,
        lightning_rebalance_threshold=rebalance_threshold,
        coin_selection="branch_and_bound",
        simulation_mode=simulation_mode,
    )


def _price(usd: float = 62_000.0, ts: int = 1_700_000_000) -> OraclePriceTickRecord:
    return OraclePriceTickRecord(
        timestamp=ts,
        block_height=850_000,
        price_usd=usd,
        source="coinbase",
    )


def _fee(t1: int = 30, t6: int = 15, t144: int = 3, ts: int = 1_700_000_000) -> FeeEstimateRecord:
    return FeeEstimateRecord(
        timestamp=ts,
        block_height=850_000,
        target_1_block_sat_vbyte=t1,
        target_6_block_sat_vbyte=t6,
        target_144_block_sat_vbyte=t144,
    )


def _routing() -> RoutingPolicyRecord:
    return RoutingPolicyRecord(
        channel_id="850000x1x0",
        base_fee_msat=1_000,
        fee_rate_ppm=500,
        min_htlc_msat=1_000,
        max_htlc_msat=1_000_000_000,
        time_lock_delta=40,
    )


# ---------------------------------------------------------------------------
# _query.py: new economics helpers
# ---------------------------------------------------------------------------


class TestEstimatedInputVbytes:
    def test_p2wpkh_is_41(self) -> None:
        assert estimated_input_vbytes("p2wpkh") == 41

    def test_p2pkh_is_148(self) -> None:
        assert estimated_input_vbytes("p2pkh") == 148

    def test_p2tr_is_58(self) -> None:
        assert estimated_input_vbytes("p2tr") == 58

    def test_unknown_uses_conservative_fallback(self) -> None:
        assert estimated_input_vbytes("unknown") == 100

    def test_p2sh_is_91(self) -> None:
        assert estimated_input_vbytes("p2sh") == 91


class TestEffectiveValueSat:
    def test_positive_for_large_utxo(self) -> None:
        u = _utxo(amount_sat=1_000_000, script_type="p2wpkh")
        # effective = 1_000_000 − 41 × 10 = 999_590
        assert effective_value_sat(u, 10) == 1_000_000 - 41 * 10

    def test_negative_for_tiny_utxo_at_high_fee(self) -> None:
        u = _utxo(amount_sat=100, script_type="p2wpkh")
        assert effective_value_sat(u, 10) < 0

    def test_zero_fee_rate_returns_full_amount(self) -> None:
        u = _utxo(amount_sat=50_000)
        assert effective_value_sat(u, 0) == 50_000

    def test_legacy_input_costs_more(self) -> None:
        segwit = _utxo(amount_sat=100_000, script_type="p2wpkh")
        legacy = _utxo(amount_sat=100_000, script_type="p2pkh")
        assert effective_value_sat(segwit, 10) > effective_value_sat(legacy, 10)


class TestDustThreshold:
    def test_p2wpkh_at_10_sat_vbyte(self) -> None:
        # 3 × 41 × 10 = 1_230
        assert dust_threshold_sat("p2wpkh", 10) == 1_230

    def test_p2pkh_is_larger(self) -> None:
        assert dust_threshold_sat("p2pkh", 10) > dust_threshold_sat("p2wpkh", 10)

    def test_higher_fee_raises_threshold(self) -> None:
        assert dust_threshold_sat("p2wpkh", 50) > dust_threshold_sat("p2wpkh", 10)


class TestIsDust:
    def test_tiny_utxo_is_dust(self) -> None:
        u = _utxo(amount_sat=200, script_type="p2wpkh")
        assert is_dust(u, 10)

    def test_large_utxo_is_not_dust(self) -> None:
        u = _utxo(amount_sat=1_000_000, script_type="p2wpkh")
        assert not is_dust(u, 10)

    def test_zero_fee_rate_never_dust(self) -> None:
        u = _utxo(amount_sat=1)
        assert not is_dust(u, 0)


class TestBalanceByCategory:
    def test_groups_by_label_category(self) -> None:
        u1 = _utxo(amount_sat=500_000, address="addr1")
        u2 = _utxo(amount_sat=300_000, address="addr2")
        u3 = _utxo(amount_sat=200_000, address="addr3")
        labels = [
            _label(address="addr1", category="income"),
            _label(address="addr2", category="exchange"),
        ]
        result = balance_by_category([u1, u2, u3], labels)
        assert result["income"] == 500_000
        assert result["exchange"] == 300_000
        assert result["unknown"] == 200_000

    def test_empty_labels_all_unknown(self) -> None:
        utxos = [_utxo(amount_sat=100_000)]
        result = balance_by_category(utxos, [])
        assert result == {"unknown": 100_000}

    def test_empty_utxos_returns_empty(self) -> None:
        assert balance_by_category([], [_label()]) == {}


# ---------------------------------------------------------------------------
# Coin selection
# ---------------------------------------------------------------------------


class TestSelectCoins:
    def test_largest_first_selects_minimum_inputs(self) -> None:
        utxos = [
            _utxo(txid="a" * 64, vout=0, amount_sat=2_000_000),
            _utxo(txid="b" * 64, vout=0, amount_sat=500_000),
            _utxo(txid="c" * 64, vout=0, amount_sat=100_000),
        ]
        result = select_coins(utxos, 400_000, fee_rate_sat_vbyte=1, algorithm="largest_first")
        assert result["success"] is True
        # Should only need the 2M UTXO
        assert len(result["selected"]) == 1
        assert result["selected"][0]["amount_sat"] == 2_000_000

    def test_smallest_first_uses_small_utxos(self) -> None:
        utxos = [
            _utxo(txid="a" * 64, vout=0, amount_sat=2_000_000),
            _utxo(txid="b" * 64, vout=0, amount_sat=500_000),
            _utxo(txid="c" * 64, vout=0, amount_sat=200_000),
        ]
        result = select_coins(utxos, 400_000, fee_rate_sat_vbyte=1, algorithm="smallest_first")
        assert result["success"] is True
        # Should start with the 200k then the 500k
        selected_amounts = {u["amount_sat"] for u in result["selected"]}
        assert 200_000 in selected_amounts

    def test_insufficient_funds_returns_failure(self) -> None:
        u = _utxo(amount_sat=10_000)
        result = select_coins([u], target_sat=1_000_000, fee_rate_sat_vbyte=1)
        assert result["success"] is False
        assert result["failure_reason"] is not None
        assert "insufficient" in result["failure_reason"].lower()

    def test_all_dust_returns_failure(self) -> None:
        u = _utxo(amount_sat=100, script_type="p2wpkh")
        result = select_coins([u], target_sat=50, fee_rate_sat_vbyte=10)
        assert result["success"] is False
        assert "dust" in (result["failure_reason"] or "")

    def test_zero_target_returns_failure(self) -> None:
        u = _utxo(amount_sat=1_000_000)
        result = select_coins([u], target_sat=0, fee_rate_sat_vbyte=1)
        assert result["success"] is False

    def test_bnb_finds_exact_match(self) -> None:
        # Craft UTXOs so that one exactly equals the target
        # effective_value at 1 sat/vbyte: 1_000_000 − 41 = 999_959
        # target_sat = 999_959 → exact match
        u = _utxo(amount_sat=1_000_000, script_type="p2wpkh")
        ev = effective_value_sat(u, 1)
        result = select_coins([u], target_sat=ev, fee_rate_sat_vbyte=1, algorithm="branch_and_bound")
        assert result["success"] is True
        # Change should be zero or very small (exact BnB match)
        assert result["change_sat"] == 0 or result["waste_score"] == 0

    def test_bnb_falls_back_to_largest_when_no_exact_match(self) -> None:
        utxos = [
            _utxo(txid="a" * 64, vout=0, amount_sat=300_000),
            _utxo(txid="b" * 64, vout=0, amount_sat=400_000),
        ]
        result = select_coins(utxos, target_sat=500_000, fee_rate_sat_vbyte=1, algorithm="branch_and_bound")
        assert result["success"] is True

    def test_random_returns_valid_selection(self) -> None:
        utxos = [
            _utxo(txid=c * 64, vout=0, amount_sat=500_000)
            for c in "abcde"
        ]
        result = select_coins(utxos, target_sat=300_000, fee_rate_sat_vbyte=1, algorithm="random")
        assert result["success"] is True
        assert result["total_input_sat"] >= result["target_sat"]

    def test_result_covers_target_plus_fee(self) -> None:
        utxos = [_utxo(txid=c * 64, vout=i, amount_sat=200_000) for i, c in enumerate("abcde")]
        result = select_coins(utxos, target_sat=350_000, fee_rate_sat_vbyte=10, algorithm="largest_first")
        assert result["success"] is True
        # total_input_sat must cover target + fee
        assert result["total_input_sat"] >= result["target_sat"] + result["fee_sat"]

    def test_dust_utxos_excluded_from_selection(self) -> None:
        dust = _utxo(txid="a" * 64, vout=0, amount_sat=100)
        large = _utxo(txid="b" * 64, vout=0, amount_sat=1_000_000)
        result = select_coins([dust, large], target_sat=50_000, fee_rate_sat_vbyte=10)
        assert result["success"] is True
        keys = {u["txid"] for u in result["selected"]}
        assert "a" * 64 not in keys  # dust excluded

    def test_fee_sat_is_positive(self) -> None:
        u = _utxo(amount_sat=1_000_000)
        result = select_coins([u], target_sat=500_000, fee_rate_sat_vbyte=10)
        assert result["success"] is True
        assert result["fee_sat"] > 0

    def test_algorithm_recorded_in_result(self) -> None:
        u = _utxo(amount_sat=1_000_000)
        result = select_coins([u], target_sat=100_000, fee_rate_sat_vbyte=1, algorithm="largest_first")
        assert result["algorithm"] == "largest_first"


# ---------------------------------------------------------------------------
# UTXO lifecycle
# ---------------------------------------------------------------------------


class TestUTXOLifecycle:
    def test_confirmed_mature_utxo(self) -> None:
        u = _utxo(amount_sat=500_000, confirmations=100, block_height=849_000, coinbase=False)
        lc = utxo_lifecycle(u, labels=[], fee_rate_sat_vbyte=10, current_height=850_000)
        assert lc["is_mature"] is True
        assert lc["is_spendable"] is True
        assert lc["is_coinbase"] is False
        assert lc["age_blocks"] == 1_000

    def test_immature_coinbase_not_spendable(self) -> None:
        u = _utxo(amount_sat=625_000_000, confirmations=50, coinbase=True)
        lc = utxo_lifecycle(u, labels=[], fee_rate_sat_vbyte=10)
        assert lc["is_coinbase"] is True
        assert lc["is_mature"] is False
        assert lc["is_spendable"] is False

    def test_mature_coinbase_is_spendable(self) -> None:
        u = _utxo(amount_sat=625_000_000, confirmations=101, coinbase=True)
        lc = utxo_lifecycle(u, labels=[], fee_rate_sat_vbyte=10)
        assert lc["is_mature"] is True
        assert lc["is_spendable"] is True

    def test_dust_detection(self) -> None:
        u = _utxo(amount_sat=200, script_type="p2wpkh")
        lc = utxo_lifecycle(u, labels=[], fee_rate_sat_vbyte=10)
        assert lc["is_dust"] is True
        assert lc["effective_value_sat"] < 0

    def test_label_annotation(self) -> None:
        u = _utxo(address="bc1qcold")
        labels = [_label(address="bc1qcold", label="treasury", category="income")]
        lc = utxo_lifecycle(u, labels=labels, fee_rate_sat_vbyte=10)
        assert lc["label"] == "treasury"
        assert lc["category"] == "income"

    def test_unlabelled_utxo_category_is_unknown(self) -> None:
        u = _utxo(address="bc1qunknown")
        lc = utxo_lifecycle(u, labels=[], fee_rate_sat_vbyte=10)
        assert lc["category"] == "unknown"
        assert lc["label"] is None

    def test_unconfirmed_not_spendable(self) -> None:
        u = _utxo(confirmations=0, block_height=None)
        lc = utxo_lifecycle(u, labels=[], fee_rate_sat_vbyte=10)
        assert lc["is_spendable"] is False

    def test_no_current_height_age_is_none(self) -> None:
        u = _utxo(block_height=840_000)
        lc = utxo_lifecycle(u, labels=[], fee_rate_sat_vbyte=10, current_height=None)
        assert lc["age_blocks"] is None

    def test_key_format(self) -> None:
        u = _utxo(txid="b" * 64, vout=3)
        lc = utxo_lifecycle(u, labels=[], fee_rate_sat_vbyte=10)
        assert lc["key"] == "b" * 64 + ":3"

    def test_estimated_spend_fee_is_positive(self) -> None:
        u = _utxo(amount_sat=1_000_000)
        lc = utxo_lifecycle(u, labels=[], fee_rate_sat_vbyte=10)
        assert lc["estimated_spend_fee_sat"] > 0


# ---------------------------------------------------------------------------
# Wallet summary
# ---------------------------------------------------------------------------


class TestWalletSummary:
    def _make_default(self) -> WalletSummary:
        utxos = [
            _utxo(txid="a" * 64, amount_sat=1_000_000, confirmations=6, address="bc1q1"),
            _utxo(txid="b" * 64, amount_sat=500_000, confirmations=0, address="bc1q2"),  # unconfirmed
            _utxo(txid="c" * 64, amount_sat=625_000_000, confirmations=50, coinbase=True, address="bc1q3"),  # immature
        ]
        labels = [_label(address="bc1q1", category="income")]
        channels = [_channel()]
        return wallet_summary(
            utxos=utxos,
            labels=labels,
            channels=channels,
            strategy=_strategy(),
            prices=[_price(usd=60_000.0)],
            mempool=[],
            fee_rate_sat_vbyte=10,
            current_height=850_000,
        )

    def test_total_sat_includes_all_utxos(self) -> None:
        s = self._make_default()
        assert s["total_sat"] == 1_000_000 + 500_000 + 625_000_000

    def test_unconfirmed_separated(self) -> None:
        s = self._make_default()
        assert s["unconfirmed_sat"] == 500_000

    def test_immature_coinbase_tracked(self) -> None:
        s = self._make_default()
        assert s["immature_coinbase_sat"] == 625_000_000

    def test_spendable_excludes_immature_and_unconfirmed(self) -> None:
        s = self._make_default()
        assert s["spendable_sat"] == 1_000_000  # only the confirmed, non-coinbase UTXO

    def test_lightning_balance_included(self) -> None:
        s = self._make_default()
        assert s["in_lightning_sat"] == 1_000_000

    def test_usd_value_computed(self) -> None:
        s = self._make_default()
        assert s["total_usd"] is not None
        assert s["total_usd"] > 0

    def test_no_price_data_gives_none_usd(self) -> None:
        s = wallet_summary(
            utxos=[_utxo(amount_sat=1_000_000)],
            labels=[],
            channels=[],
            strategy=_strategy(),
            prices=[],
            mempool=[],
        )
        assert s["total_usd"] is None

    def test_dust_count(self) -> None:
        dust = _utxo(txid="d" * 64, amount_sat=200, script_type="p2wpkh")
        large = _utxo(txid="e" * 64, amount_sat=1_000_000)
        s = wallet_summary(
            utxos=[dust, large],
            labels=[],
            channels=[],
            strategy=_strategy(),
            prices=[],
            mempool=[],
            fee_rate_sat_vbyte=10,
        )
        assert s["dust_utxo_count"] == 1

    def test_channel_counts(self) -> None:
        s = self._make_default()
        assert s["channel_count"] == 1
        assert s["active_channel_count"] == 1

    def test_simulation_mode_propagated(self) -> None:
        s = wallet_summary(
            utxos=[],
            labels=[],
            channels=[],
            strategy=_strategy(simulation_mode=True),
            prices=[],
            mempool=[],
        )
        assert s["simulation_mode"] is True

    def test_script_type_breakdown_present(self) -> None:
        s = self._make_default()
        assert "p2wpkh" in s["script_type_breakdown"]

    def test_category_breakdown_present(self) -> None:
        s = self._make_default()
        assert "income" in s["category_breakdown"]


# ---------------------------------------------------------------------------
# Portfolio analytics
# ---------------------------------------------------------------------------


class TestPortfolioSnapshot:
    def test_totals_correct(self) -> None:
        utxos = [_utxo(amount_sat=1_000_000)]
        channels = [_channel(local_balance_sat=500_000)]
        snap = portfolio_snapshot(utxos, channels, [_price(usd=60_000.0)])
        assert snap["on_chain_sat"] == 1_000_000
        assert snap["lightning_sat"] == 500_000
        assert snap["total_sat"] == 1_500_000

    def test_usd_computed(self) -> None:
        utxos = [_utxo(amount_sat=_SATS_PER_BTC)]
        snap = portfolio_snapshot(utxos, [], [_price(usd=60_000.0)])
        assert snap["total_usd"] is not None
        assert abs((snap["total_usd"] or 0) - 60_000.0) < 1.0

    def test_no_price_gives_none_usd(self) -> None:
        snap = portfolio_snapshot([_utxo()], [], [])
        assert snap["total_usd"] is None

    def test_height_and_timestamp_stored(self) -> None:
        snap = portfolio_snapshot([], [], [], current_height=850_000, timestamp=1_700_000_000)
        assert snap["block_height"] == 850_000
        assert snap["timestamp"] == 1_700_000_000


_SATS_PER_BTC = 100_000_000


class TestPortfolioPnL:
    def _snap(self, total: int, price: float | None = None) -> PortfolioSnapshot:
        usd = ((total / _SATS_PER_BTC) * price) if price else None
        return PortfolioSnapshot(
            block_height=850_000,
            timestamp=1_700_000_000,
            on_chain_sat=total,
            lightning_sat=0,
            total_sat=total,
            price_usd=price,
            total_usd=usd,
        )

    def test_positive_pnl(self) -> None:
        base = self._snap(1_000_000)
        current = self._snap(1_500_000)
        pnl = portfolio_pnl(base, current)
        assert pnl["sat_delta"] == 500_000
        assert pnl["pct_change"] is not None
        assert pnl["pct_change"] > 0

    def test_negative_pnl(self) -> None:
        base = self._snap(2_000_000)
        current = self._snap(1_500_000)
        pnl = portfolio_pnl(base, current)
        assert pnl["sat_delta"] == -500_000

    def test_fees_paid_affects_net_delta(self) -> None:
        base = self._snap(1_000_000)
        current = self._snap(900_000)
        pnl = portfolio_pnl(base, current, estimated_fees_paid_sat=50_000)
        # net = -100_000 + 50_000 = -50_000 (fees were a cost, adding them back)
        assert pnl["net_sat_delta"] == -100_000 + 50_000

    def test_usd_delta_when_prices_available(self) -> None:
        base = self._snap(1_000_000, 60_000.0)
        current = self._snap(1_000_000, 70_000.0)
        pnl = portfolio_pnl(base, current)
        assert pnl["usd_delta"] is not None
        assert (pnl["usd_delta"] or 0) > 0  # USD went up even with same sats

    def test_usd_delta_none_without_prices(self) -> None:
        base = self._snap(1_000_000, None)
        current = self._snap(1_500_000, None)
        pnl = portfolio_pnl(base, current)
        assert pnl["usd_delta"] is None

    def test_zero_change(self) -> None:
        snap = self._snap(1_000_000)
        pnl = portfolio_pnl(snap, snap)
        assert pnl["sat_delta"] == 0
        assert pnl["pct_change"] == 0.0


# ---------------------------------------------------------------------------
# Lightning analytics
# ---------------------------------------------------------------------------


class TestRebalanceCandidates:
    def test_balanced_channel_not_a_candidate(self) -> None:
        ch = _channel(local_balance_sat=960_000, remote_balance_sat=960_000, capacity_sat=2_000_000)
        result = rebalance_candidates([ch], _strategy(rebalance_threshold=0.2))
        assert result == []

    def test_low_local_balance_pull_in(self) -> None:
        # local/(capacity-reserves) < 0.2 → pull_in
        ch = _channel(
            local_balance_sat=10_000,
            remote_balance_sat=1_950_000,
            capacity_sat=2_000_000,
            local_reserve_sat=20_000,
            remote_reserve_sat=20_000,
        )
        result = rebalance_candidates([ch], _strategy(rebalance_threshold=0.2))
        assert len(result) == 1
        assert result[0]["direction"] == "pull_in"

    def test_high_local_balance_push_out(self) -> None:
        # local/(capacity-reserves) > 0.8 → push_out
        ch = _channel(
            local_balance_sat=1_900_000,
            remote_balance_sat=50_000,
            capacity_sat=2_000_000,
            local_reserve_sat=20_000,
            remote_reserve_sat=20_000,
        )
        result = rebalance_candidates([ch], _strategy(rebalance_threshold=0.2))
        assert len(result) == 1
        assert result[0]["direction"] == "push_out"

    def test_inactive_channel_excluded(self) -> None:
        ch = _channel(local_balance_sat=0, is_active=False)
        result = rebalance_candidates([ch], _strategy())
        assert result == []

    def test_critical_urgency_for_extreme_imbalance(self) -> None:
        ch = _channel(
            local_balance_sat=1_000,
            remote_balance_sat=1_960_000,
            capacity_sat=2_000_000,
            local_reserve_sat=20_000,
            remote_reserve_sat=20_000,
        )
        result = rebalance_candidates([ch], _strategy(rebalance_threshold=0.2))
        assert result[0]["urgency"] == "critical"

    def test_multiple_channels_sorted_by_urgency(self) -> None:
        # Slightly imbalanced channel
        ch_medium = _channel(
            channel_id="1",
            local_balance_sat=200_000,
            remote_balance_sat=1_750_000,
            capacity_sat=2_000_000,
            local_reserve_sat=20_000,
            remote_reserve_sat=20_000,
        )
        # Severely imbalanced channel
        ch_critical = _channel(
            channel_id="2",
            local_balance_sat=1_000,
            remote_balance_sat=1_960_000,
            capacity_sat=2_000_000,
            local_reserve_sat=20_000,
            remote_reserve_sat=20_000,
        )
        result = rebalance_candidates([ch_medium, ch_critical], _strategy(rebalance_threshold=0.2))
        assert result[0]["channel_id"] == "2"  # critical first

    def test_suggested_amount_is_positive(self) -> None:
        ch = _channel(
            local_balance_sat=10_000,
            remote_balance_sat=1_950_000,
            capacity_sat=2_000_000,
            local_reserve_sat=20_000,
            remote_reserve_sat=20_000,
        )
        result = rebalance_candidates([ch], _strategy(rebalance_threshold=0.2))
        assert result[0]["suggested_amount_sat"] > 0


class TestChannelHealthReport:
    def test_empty_channels_perfect_score(self) -> None:
        report = channel_health_report([], [], _strategy())
        assert report["health_score"] == 1.0
        assert report["total_channels"] == 0

    def test_all_balanced_active_channels(self) -> None:
        channels = [
            _channel(channel_id="1", local_balance_sat=960_000, remote_balance_sat=960_000),
            _channel(channel_id="2", local_balance_sat=960_000, remote_balance_sat=960_000),
        ]
        report = channel_health_report(channels, [_routing()], _strategy())
        assert report["health_score"] >= 0.9
        assert "Excellent" in report["assessment"]

    def test_inactive_channel_lowers_score(self) -> None:
        active = _channel(channel_id="1")
        inactive = _channel(channel_id="2", is_active=False)
        report = channel_health_report([active, inactive], [], _strategy())
        assert report["inactive_channels"] == 1
        assert report["health_score"] < 1.0

    def test_imbalanced_channels_lower_score(self) -> None:
        imbalanced = _channel(
            local_balance_sat=10_000,
            remote_balance_sat=1_950_000,
            capacity_sat=2_000_000,
            local_reserve_sat=20_000,
            remote_reserve_sat=20_000,
        )
        report = channel_health_report([imbalanced], [], _strategy())
        assert report["imbalanced_count"] >= 1
        assert report["health_score"] < 1.0

    def test_rebalance_candidates_in_report(self) -> None:
        ch = _channel(
            local_balance_sat=1_000,
            remote_balance_sat=1_960_000,
            capacity_sat=2_000_000,
            local_reserve_sat=20_000,
            remote_reserve_sat=20_000,
        )
        report = channel_health_report([ch], [], _strategy())
        assert len(report["rebalance_candidates"]) >= 1

    def test_capacity_totals(self) -> None:
        ch1 = _channel(channel_id="1", capacity_sat=1_000_000)
        ch2 = _channel(channel_id="2", capacity_sat=2_000_000)
        report = channel_health_report([ch1, ch2], [], _strategy())
        assert report["total_capacity_sat"] == 3_000_000

    def test_htlc_count_tracked(self) -> None:
        ch = _channel(htlc_count=3)
        report = channel_health_report([ch], [], _strategy())
        assert report["htlc_stuck_count"] == 3


# ---------------------------------------------------------------------------
# Fee window analytics
# ---------------------------------------------------------------------------


class TestFeeWindow:
    def test_empty_history_defaults_to_send_now(self) -> None:
        result = fee_window([], target_blocks=6)
        assert result["recommendation"] == "send_now"
        assert result["current_sat_vbyte"] == 1

    def test_low_fee_sends_now(self) -> None:
        # Create history with mostly high fees, current is low
        history = [_fee(t6=100, ts=1_000_000 + i) for i in range(20)]
        history.append(_fee(t6=5, ts=2_000_000))  # current: very low
        result = fee_window(history, target_blocks=6)
        assert result["recommendation"] == "send_now"
        assert result["percentile"] <= 0.25

    def test_high_fee_recommends_wait(self) -> None:
        # Create history with mostly low fees, current is high
        history = [_fee(t6=5, ts=1_000_000 + i) for i in range(20)]
        history.append(_fee(t6=100, ts=2_000_000))  # current: very high
        result = fee_window(history, target_blocks=6)
        assert result["recommendation"] == "wait"
        assert result["percentile"] >= 0.75

    def test_stuck_tx_recommends_rbf(self) -> None:
        result = fee_window(
            [_fee()],
            target_blocks=1,
            pending_txids=["deadbeef"],
        )
        assert result["recommendation"] == "rbf_now"

    def test_historical_stats_present(self) -> None:
        history = [
            _fee(t6=10, ts=1_000),
            _fee(t6=20, ts=2_000),
            _fee(t6=30, ts=3_000),
        ]
        result = fee_window(history, target_blocks=6)
        assert result["historical_min_sat_vbyte"] == 10
        assert result["historical_max_sat_vbyte"] == 30
        assert result["historical_median_sat_vbyte"] == 20

    def test_target_blocks_1_uses_1_block_rate(self) -> None:
        result = fee_window([_fee(t1=50, t6=20)], target_blocks=1)
        assert result["current_sat_vbyte"] == 50

    def test_target_blocks_144_uses_144_block_rate(self) -> None:
        result = fee_window([_fee(t6=20, t144=3)], target_blocks=144)
        assert result["current_sat_vbyte"] == 3

    def test_optimal_wait_blocks_set_when_waiting(self) -> None:
        history = [_fee(t6=5, ts=1_000_000 + i) for i in range(20)]
        history.append(_fee(t6=100, ts=2_000_000))
        result = fee_window(history, target_blocks=6)
        if result["recommendation"] == "wait":
            assert result["optimal_wait_blocks"] is not None
            assert result["optimal_wait_blocks"] > 0

    def test_send_now_has_none_wait_blocks(self) -> None:
        history = [_fee(t6=100, ts=1_000_000 + i) for i in range(20)]
        history.append(_fee(t6=5, ts=2_000_000))  # current is low
        result = fee_window(history, target_blocks=6)
        if result["recommendation"] == "send_now":
            assert result["optimal_wait_blocks"] is None


# ---------------------------------------------------------------------------
# Consolidation planner
# ---------------------------------------------------------------------------


class TestConsolidationPlan:
    def test_single_utxo_not_recommended(self) -> None:
        plan = consolidation_plan([_utxo(amount_sat=1_000_000)], fee_rate_sat_vbyte=10)
        assert plan["recommended"] is False
        assert plan["input_count"] == 0

    def test_many_small_utxos_recommended_at_low_fee(self) -> None:
        # Many small UTXOs at low fee rate
        utxos = [
            _utxo(txid=c * 64, vout=i, amount_sat=50_000, confirmations=6)
            for i, c in enumerate("abcdefghij")
        ]
        plan = consolidation_plan(utxos, fee_rate_sat_vbyte=1, savings_horizon_spends=20)
        assert plan["recommended"] is True
        assert plan["input_count"] > 0
        assert plan["estimated_fee_sat"] > 0

    def test_dust_utxos_excluded(self) -> None:
        dust = _utxo(txid="a" * 64, vout=0, amount_sat=200, confirmations=6)
        large = _utxo(txid="b" * 64, vout=0, amount_sat=1_000_000, confirmations=6)
        plan = consolidation_plan([dust, large], fee_rate_sat_vbyte=10)
        selected_keys = {u["txid"] for u in plan["utxos_to_consolidate"]}
        assert "a" * 64 not in selected_keys

    def test_immature_coinbase_excluded(self) -> None:
        immature = _utxo(txid="a" * 64, vout=0, amount_sat=625_000_000, coinbase=True, confirmations=50)
        mature = _utxo(txid="b" * 64, vout=0, amount_sat=50_000, confirmations=6)
        plan = consolidation_plan([immature, mature], fee_rate_sat_vbyte=1)
        selected_keys = {u["txid"] for u in plan["utxos_to_consolidate"]}
        assert "a" * 64 not in selected_keys

    def test_output_count_is_one(self) -> None:
        utxos = [_utxo(txid=c * 64, vout=i, amount_sat=50_000, confirmations=6) for i, c in enumerate("abcde")]
        plan = consolidation_plan(utxos, fee_rate_sat_vbyte=1)
        if plan["input_count"] > 0:
            assert plan["output_count"] == 1

    def test_break_even_fee_rate_positive_when_savings_possible(self) -> None:
        utxos = [_utxo(txid=c * 64, vout=i, amount_sat=50_000, confirmations=6) for i, c in enumerate("abcde")]
        plan = consolidation_plan(utxos, fee_rate_sat_vbyte=1, savings_horizon_spends=10)
        if plan["input_count"] > 0:
            assert plan["break_even_fee_rate"] >= 0

    def test_reason_string_present(self) -> None:
        utxos = [_utxo(txid=c * 64, vout=i, amount_sat=50_000, confirmations=6) for i, c in enumerate("abcde")]
        plan = consolidation_plan(utxos, fee_rate_sat_vbyte=1)
        assert len(plan["reason"]) > 0

    def test_max_inputs_respected(self) -> None:
        utxos = [
            _utxo(txid=f"{'a' * 63}{i}", vout=0, amount_sat=10_000, confirmations=6)
            for i in range(30)
        ]
        plan = consolidation_plan(utxos, fee_rate_sat_vbyte=1, max_inputs=10)
        assert plan["input_count"] <= 10

    def test_high_fee_rate_may_not_recommend(self) -> None:
        # At very high fees, consolidation might not be worth it
        utxos = [
            _utxo(txid=c * 64, vout=i, amount_sat=50_000, confirmations=6)
            for i, c in enumerate("ab")  # only 2 UTXOs
        ]
        plan = consolidation_plan(utxos, fee_rate_sat_vbyte=200, savings_horizon_spends=2)
        # With very high fee and few future spends, it may not be recommended
        # Just verify it produces a valid result without errors
        assert isinstance(plan["recommended"], bool)

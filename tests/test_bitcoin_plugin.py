"""Comprehensive test suite for the Bitcoin domain plugin.

Tests cover all three protocol levels:
- Core MuseDomainPlugin (snapshot, diff, merge, drift, apply, schema)
- StructuredMergePlugin (OT merge with double-spend detection)
- CRDTPlugin (convergent join — commutativity, associativity, idempotency)

Tests are organized by capability, each asserting exact behaviour
with realistic Bitcoin fixtures.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import tempfile

import pytest

from muse._version import __version__
from muse.domain import (
    CRDTPlugin,
    DeleteOp,
    DomainOp,
    InsertOp,
    MuseDomainPlugin,
    MutateOp,
    PatchOp,
    ReplaceOp,
    SnapshotManifest,
    StructuredMergePlugin,
)
from muse.plugins.bitcoin._query import (
    balance_by_script_type,
    channel_liquidity_totals,
    channel_utilization,
    coin_age_blocks,
    confirmed_balance_sat,
    double_spend_candidates,
    fee_surface_str,
    format_sat,
    latest_fee_estimate,
    latest_price,
    mempool_summary_line,
    strategy_summary_line,
    total_balance_sat,
    utxo_key,
    utxo_summary_line,
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
from muse.plugins.bitcoin.plugin import (
    BitcoinPlugin,
    _diff_channels,
    _diff_labels,
    _diff_strategy,
    _diff_time_series,
    _diff_transactions,
    _diff_utxos,
    _handler_for_path,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_utxo(
    txid: str = "abc" * 21 + "ab",
    vout: int = 0,
    amount_sat: int = 100_000,
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


def _make_channel(
    channel_id: str = "850000x1x0",
    peer_pubkey: str = "0279" + "aa" * 32,
    peer_alias: str | None = "ACINQ",
    capacity_sat: int = 2_000_000,
    local_balance_sat: int = 1_000_000,
    remote_balance_sat: int = 900_000,
    is_active: bool = True,
    is_public: bool = True,
    local_reserve_sat: int = 20_000,
    remote_reserve_sat: int = 20_000,
    unsettled_balance_sat: int = 0,
    htlc_count: int = 0,
) -> LightningChannelRecord:
    return LightningChannelRecord(
        channel_id=channel_id,
        peer_pubkey=peer_pubkey,
        peer_alias=peer_alias,
        capacity_sat=capacity_sat,
        local_balance_sat=local_balance_sat,
        remote_balance_sat=remote_balance_sat,
        is_active=is_active,
        is_public=is_public,
        local_reserve_sat=local_reserve_sat,
        remote_reserve_sat=remote_reserve_sat,
        unsettled_balance_sat=unsettled_balance_sat,
        htlc_count=htlc_count,
    )


def _make_label(
    address: str = "bc1qtest",
    label: str = "cold storage",
    category: CoinCategory = "income",
    created_at: int = 1_700_000_000,
) -> AddressLabelRecord:
    return AddressLabelRecord(
        address=address,
        label=label,
        category=category,
        created_at=created_at,
    )


def _make_strategy(
    name: str = "conservative",
    max_fee_rate_sat_vbyte: int = 10,
    min_confirmations: int = 6,
    utxo_consolidation_threshold: int = 20,
    dca_amount_sat: int | None = 500_000,
    dca_interval_blocks: int | None = 144,
    lightning_rebalance_threshold: float = 0.2,
    coin_selection: CoinSelectAlgo = "branch_and_bound",
    simulation_mode: bool = False,
) -> AgentStrategyRecord:
    return AgentStrategyRecord(
        name=name,
        max_fee_rate_sat_vbyte=max_fee_rate_sat_vbyte,
        min_confirmations=min_confirmations,
        utxo_consolidation_threshold=utxo_consolidation_threshold,
        dca_amount_sat=dca_amount_sat,
        dca_interval_blocks=dca_interval_blocks,
        lightning_rebalance_threshold=lightning_rebalance_threshold,
        coin_selection=coin_selection,
        simulation_mode=simulation_mode,
    )


def _make_price(
    timestamp: int = 1_700_000_000,
    block_height: int | None = 850_000,
    price_usd: float = 62_000.0,
    source: str = "coinbase",
) -> OraclePriceTickRecord:
    return OraclePriceTickRecord(
        timestamp=timestamp,
        block_height=block_height,
        price_usd=price_usd,
        source=source,
    )


def _make_fee(
    timestamp: int = 1_700_000_000,
    block_height: int | None = 850_000,
    t1: int = 30,
    t6: int = 15,
    t144: int = 3,
) -> FeeEstimateRecord:
    return FeeEstimateRecord(
        timestamp=timestamp,
        block_height=block_height,
        target_1_block_sat_vbyte=t1,
        target_6_block_sat_vbyte=t6,
        target_144_block_sat_vbyte=t144,
    )


_AnyBitcoinRecord = (
    UTXORecord
    | LightningChannelRecord
    | AddressLabelRecord
    | AgentStrategyRecord
    | FeeEstimateRecord
    | RoutingPolicyRecord
    | PendingTxRecord
    | OraclePriceTickRecord
)


def _json_bytes(obj: _AnyBitcoinRecord | list[_AnyBitcoinRecord]) -> bytes:
    return json.dumps(obj, sort_keys=True).encode()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _make_manifest(files: dict[str, bytes]) -> SnapshotManifest:
    return SnapshotManifest(
        files={path: _sha256(data) for path, data in files.items()},
        domain="bitcoin",
    )


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


class TestProtocolConformance:
    def test_satisfies_muse_domain_plugin(self) -> None:
        assert isinstance(BitcoinPlugin(), MuseDomainPlugin)

    def test_satisfies_structured_merge_plugin(self) -> None:
        assert isinstance(BitcoinPlugin(), StructuredMergePlugin)

    def test_satisfies_crdt_plugin(self) -> None:
        assert isinstance(BitcoinPlugin(), CRDTPlugin)


# ---------------------------------------------------------------------------
# snapshot
# ---------------------------------------------------------------------------


class TestSnapshot:
    def test_snapshot_from_path_hashes_all_json_files(self) -> None:
        plugin = BitcoinPlugin()
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "wallet").mkdir()
            (root / "wallet" / "utxos.json").write_bytes(b"[]")
            (root / "wallet" / "labels.json").write_bytes(b"[]")

            snap = plugin.snapshot(root)

        assert snap["domain"] == "bitcoin"
        assert "wallet/utxos.json" in snap["files"]
        assert "wallet/labels.json" in snap["files"]

    def test_snapshot_excludes_hidden_files(self) -> None:
        plugin = BitcoinPlugin()
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "wallet").mkdir()
            (root / "wallet" / "utxos.json").write_bytes(b"[]")
            (root / ".secret").write_bytes(b"private key would be here")

            snap = plugin.snapshot(root)

        assert ".secret" not in snap["files"]
        assert "wallet/utxos.json" in snap["files"]

    def test_snapshot_passthrough_for_existing_manifest(self) -> None:
        plugin = BitcoinPlugin()
        manifest = SnapshotManifest(
            files={"wallet/utxos.json": "abc" * 21 + "ab"},
            domain="bitcoin",
        )
        result = plugin.snapshot(manifest)
        assert result is manifest

    def test_snapshot_content_hash_is_sha256(self) -> None:
        plugin = BitcoinPlugin()
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "wallet").mkdir()
            content = b"[]\n"
            (root / "wallet" / "utxos.json").write_bytes(content)

            snap = plugin.snapshot(root)

        expected_hash = _sha256(content)
        assert snap["files"]["wallet/utxos.json"] == expected_hash

    def test_snapshot_deterministic(self) -> None:
        plugin = BitcoinPlugin()
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "wallet").mkdir()
            (root / "wallet" / "utxos.json").write_bytes(b"[]")

            snap1 = plugin.snapshot(root)
            snap2 = plugin.snapshot(root)

        assert snap1["files"] == snap2["files"]


# ---------------------------------------------------------------------------
# diff — file-level
# ---------------------------------------------------------------------------


class TestDiffFileLevel:
    def setup_method(self) -> None:
        self.plugin = BitcoinPlugin()

    def test_added_file_produces_insert_op(self) -> None:
        base = SnapshotManifest(files={}, domain="bitcoin")
        target = SnapshotManifest(
            files={"wallet/utxos.json": "a" * 64},
            domain="bitcoin",
        )
        delta = self.plugin.diff(base, target)
        assert len(delta["ops"]) == 1
        assert delta["ops"][0]["op"] == "insert"
        assert delta["ops"][0]["address"] == "wallet/utxos.json"

    def test_removed_file_produces_delete_op(self) -> None:
        base = SnapshotManifest(
            files={"wallet/utxos.json": "a" * 64},
            domain="bitcoin",
        )
        target = SnapshotManifest(files={}, domain="bitcoin")
        delta = self.plugin.diff(base, target)
        assert delta["ops"][0]["op"] == "delete"

    def test_unchanged_file_produces_no_ops(self) -> None:
        files = {"wallet/utxos.json": "a" * 64}
        base = SnapshotManifest(files=files, domain="bitcoin")
        target = SnapshotManifest(files=files, domain="bitcoin")
        delta = self.plugin.diff(base, target)
        assert len(delta["ops"]) == 0
        assert "clean" in delta["summary"]

    def test_modified_file_without_repo_root_produces_replace_op(self) -> None:
        base = SnapshotManifest(
            files={"wallet/utxos.json": "a" * 64},
            domain="bitcoin",
        )
        target = SnapshotManifest(
            files={"wallet/utxos.json": "b" * 64},
            domain="bitcoin",
        )
        delta = self.plugin.diff(base, target)
        assert delta["ops"][0]["op"] == "replace"

    def test_summary_reflects_op_counts(self) -> None:
        base = SnapshotManifest(
            files={"wallet/utxos.json": "a" * 64, "wallet/labels.json": "b" * 64},
            domain="bitcoin",
        )
        target = SnapshotManifest(
            files={"wallet/utxos.json": "a" * 64, "strategy/agent.json": "c" * 64},
            domain="bitcoin",
        )
        delta = self.plugin.diff(base, target)
        assert "added" in delta["summary"]
        assert "removed" in delta["summary"]


# ---------------------------------------------------------------------------
# diff — semantic UTXO level
# ---------------------------------------------------------------------------


class TestDiffUTXOs:
    def test_new_utxo_produces_insert_op(self) -> None:
        u = _make_utxo(txid="a" * 64, vout=0, amount_sat=500_000)
        old = _json_bytes([])
        new = _json_bytes([u])
        ops = _diff_utxos("wallet/utxos.json", old, new)
        assert len(ops) == 1
        assert ops[0]["op"] == "insert"
        assert "a" * 64 + ":0" in ops[0]["address"]
        assert "0.00500000 BTC" in ops[0]["content_summary"]

    def test_spent_utxo_produces_delete_op(self) -> None:
        u = _make_utxo(txid="b" * 64, vout=1, amount_sat=200_000)
        old = _json_bytes([u])
        new = _json_bytes([])
        ops = _diff_utxos("wallet/utxos.json", old, new)
        assert len(ops) == 1
        assert ops[0]["op"] == "delete"
        assert "spent" in ops[0]["content_summary"]

    def test_confirmation_update_produces_mutate_op(self) -> None:
        txid = "c" * 64
        u_old = _make_utxo(txid=txid, vout=0, confirmations=1)
        u_new = _make_utxo(txid=txid, vout=0, confirmations=6)
        old = _json_bytes([u_old])
        new = _json_bytes([u_new])
        ops = _diff_utxos("wallet/utxos.json", old, new)
        assert len(ops) == 1
        assert ops[0]["op"] == "mutate"
        assert ops[0]["fields"]["confirmations"]["old"] == "1"
        assert ops[0]["fields"]["confirmations"]["new"] == "6"

    def test_unchanged_utxos_produce_no_ops(self) -> None:
        u = _make_utxo()
        data = _json_bytes([u])
        ops = _diff_utxos("wallet/utxos.json", data, data)
        assert ops == []

    def test_multiple_utxos_diff(self) -> None:
        u1 = _make_utxo(txid="d" * 64, vout=0, amount_sat=100_000)
        u2 = _make_utxo(txid="e" * 64, vout=0, amount_sat=200_000)
        u3 = _make_utxo(txid="f" * 64, vout=0, amount_sat=300_000)
        old = _json_bytes([u1, u2])
        new = _json_bytes([u2, u3])
        ops = _diff_utxos("wallet/utxos.json", old, new)
        op_types = {op["op"] for op in ops}
        assert "insert" in op_types
        assert "delete" in op_types


# ---------------------------------------------------------------------------
# diff — semantic channel level
# ---------------------------------------------------------------------------


class TestDiffChannels:
    def test_new_channel_produces_insert_op(self) -> None:
        ch = _make_channel()
        ops = _diff_channels("channels/channels.json", _json_bytes([]), _json_bytes([ch]))
        assert len(ops) == 1
        assert ops[0]["op"] == "insert"
        assert "opened" in ops[0]["content_summary"]

    def test_closed_channel_produces_delete_op(self) -> None:
        ch = _make_channel()
        ops = _diff_channels("channels/channels.json", _json_bytes([ch]), _json_bytes([]))
        assert len(ops) == 1
        assert ops[0]["op"] == "delete"

    def test_balance_change_produces_mutate_op(self) -> None:
        ch_old = _make_channel(local_balance_sat=1_000_000, remote_balance_sat=900_000)
        ch_new = _make_channel(local_balance_sat=800_000, remote_balance_sat=1_100_000)
        ops = _diff_channels(
            "channels/channels.json",
            _json_bytes([ch_old]),
            _json_bytes([ch_new]),
        )
        assert len(ops) == 1
        assert ops[0]["op"] == "mutate"
        fields = ops[0]["fields"]
        assert "local_balance_sat" in fields
        assert "remote_balance_sat" in fields


# ---------------------------------------------------------------------------
# diff — strategy field level
# ---------------------------------------------------------------------------


class TestDiffStrategy:
    def test_fee_rate_change_produces_mutate_op(self) -> None:
        old_strat = _make_strategy(max_fee_rate_sat_vbyte=10)
        new_strat = _make_strategy(max_fee_rate_sat_vbyte=50)
        ops = _diff_strategy("strategy/agent.json", _json_bytes(old_strat), _json_bytes(new_strat))
        assert len(ops) == 1
        assert ops[0]["op"] == "mutate"
        assert "max_fee_rate_sat_vbyte" in ops[0]["fields"]

    def test_simulation_mode_toggle_detected(self) -> None:
        old_s = _make_strategy(simulation_mode=False)
        new_s = _make_strategy(simulation_mode=True)
        ops = _diff_strategy("strategy/agent.json", _json_bytes(old_s), _json_bytes(new_s))
        assert ops[0]["op"] == "mutate"
        assert ops[0]["fields"]["simulation_mode"]["old"] == "False"
        assert ops[0]["fields"]["simulation_mode"]["new"] == "True"

    def test_identical_strategy_no_ops(self) -> None:
        s = _make_strategy()
        data = _json_bytes(s)
        ops = _diff_strategy("strategy/agent.json", data, data)
        assert ops == []


# ---------------------------------------------------------------------------
# diff — time series (prices, fees)
# ---------------------------------------------------------------------------


class TestDiffTimeSeries:
    def test_new_price_tick_produces_insert(self) -> None:
        tick = _make_price(timestamp=1_000, price_usd=60_000.0)
        ops = _diff_time_series(
            "oracles/prices.json", _json_bytes([]), _json_bytes([tick]), "prices"
        )
        assert len(ops) == 1
        assert ops[0]["op"] == "insert"
        assert "$60,000.00" in ops[0]["content_summary"]

    def test_new_fee_estimate_produces_insert(self) -> None:
        fee = _make_fee(timestamp=2_000, t1=25, t6=12, t144=2)
        ops = _diff_time_series(
            "oracles/fees.json", _json_bytes([]), _json_bytes([fee]), "fees"
        )
        assert len(ops) == 1
        assert ops[0]["op"] == "insert"
        assert "25" in ops[0]["content_summary"]

    def test_existing_tick_not_duplicated(self) -> None:
        tick = _make_price(timestamp=3_000)
        data = _json_bytes([tick])
        ops = _diff_time_series("oracles/prices.json", data, data, "prices")
        assert ops == []


# ---------------------------------------------------------------------------
# diff with repo_root — produces PatchOp
# ---------------------------------------------------------------------------


class TestDiffWithRepoRoot:
    def test_modified_utxos_json_produces_patch_op(self) -> None:
        plugin = BitcoinPlugin()
        u_old = _make_utxo(txid="a" * 64, vout=0, amount_sat=100_000)
        u_new = _make_utxo(txid="b" * 64, vout=0, amount_sat=200_000)
        old_bytes = _json_bytes([u_old])
        new_bytes = _json_bytes([u_new])
        old_hash = _sha256(old_bytes)
        new_hash = _sha256(new_bytes)

        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            muse_dir = root / ".muse" / "objects"
            muse_dir.mkdir(parents=True)
            # Write blobs in sharded layout
            (muse_dir / old_hash[:2]).mkdir(exist_ok=True)
            (muse_dir / old_hash[:2] / old_hash[2:]).write_bytes(old_bytes)
            (muse_dir / new_hash[:2]).mkdir(exist_ok=True)
            (muse_dir / new_hash[:2] / new_hash[2:]).write_bytes(new_bytes)

            base = SnapshotManifest(
                files={"wallet/utxos.json": old_hash}, domain="bitcoin"
            )
            target = SnapshotManifest(
                files={"wallet/utxos.json": new_hash}, domain="bitcoin"
            )
            delta = plugin.diff(base, target, repo_root=root)

        assert delta["ops"][0]["op"] == "patch"
        patch = delta["ops"][0]
        assert patch["child_domain"] == "bitcoin.utxos"
        child_ops = patch["child_ops"]
        op_types = {op["op"] for op in child_ops}
        assert "insert" in op_types
        assert "delete" in op_types

    def test_fallback_to_replace_when_object_missing(self) -> None:
        plugin = BitcoinPlugin()
        base = SnapshotManifest(
            files={"wallet/utxos.json": "a" * 64}, domain="bitcoin"
        )
        target = SnapshotManifest(
            files={"wallet/utxos.json": "b" * 64}, domain="bitcoin"
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / ".muse" / "objects").mkdir(parents=True)
            delta = plugin.diff(base, target, repo_root=root)

        assert delta["ops"][0]["op"] == "replace"


# ---------------------------------------------------------------------------
# merge
# ---------------------------------------------------------------------------


class TestMerge:
    def setup_method(self) -> None:
        self.plugin = BitcoinPlugin()

    def test_clean_merge_no_conflicts(self) -> None:
        base = SnapshotManifest(files={}, domain="bitcoin")
        left = SnapshotManifest(
            files={"wallet/utxos.json": "a" * 64}, domain="bitcoin"
        )
        right = SnapshotManifest(
            files={"channels/channels.json": "b" * 64}, domain="bitcoin"
        )
        result = self.plugin.merge(base, left, right)
        assert result.is_clean
        assert "wallet/utxos.json" in result.merged["files"]
        assert "channels/channels.json" in result.merged["files"]

    def test_both_sides_unchanged_no_conflict(self) -> None:
        files = {"wallet/utxos.json": "a" * 64}
        base = SnapshotManifest(files=files, domain="bitcoin")
        left = SnapshotManifest(files=files, domain="bitcoin")
        right = SnapshotManifest(files=files, domain="bitcoin")
        result = self.plugin.merge(base, left, right)
        assert result.is_clean

    def test_only_left_changed_takes_left(self) -> None:
        base = SnapshotManifest(files={"f": "a" * 64}, domain="bitcoin")
        left = SnapshotManifest(files={"f": "b" * 64}, domain="bitcoin")
        right = SnapshotManifest(files={"f": "a" * 64}, domain="bitcoin")
        result = self.plugin.merge(base, left, right)
        assert result.is_clean
        assert result.merged["files"]["f"] == "b" * 64

    def test_only_right_changed_takes_right(self) -> None:
        base = SnapshotManifest(files={"f": "a" * 64}, domain="bitcoin")
        left = SnapshotManifest(files={"f": "a" * 64}, domain="bitcoin")
        right = SnapshotManifest(files={"f": "c" * 64}, domain="bitcoin")
        result = self.plugin.merge(base, left, right)
        assert result.is_clean
        assert result.merged["files"]["f"] == "c" * 64

    def test_both_changed_differently_produces_conflict(self) -> None:
        base = SnapshotManifest(files={"wallet/utxos.json": "a" * 64}, domain="bitcoin")
        left = SnapshotManifest(files={"wallet/utxos.json": "b" * 64}, domain="bitcoin")
        right = SnapshotManifest(files={"wallet/utxos.json": "c" * 64}, domain="bitcoin")
        result = self.plugin.merge(base, left, right)
        assert not result.is_clean
        assert "wallet/utxos.json" in result.conflicts

    def test_double_spend_detection_in_merge(self) -> None:
        u_base = _make_utxo(txid="d" * 64, vout=0, amount_sat=1_000_000)
        # Left spent the base UTXO and got change at a different address
        u_left_change = _make_utxo(txid="e" * 64, vout=0, amount_sat=900_000, address="bc1qleft")
        # Right spent the SAME base UTXO in a different tx → double-spend
        u_right_change = _make_utxo(txid="f" * 64, vout=0, amount_sat=850_000, address="bc1qright")
        base_bytes = _json_bytes([u_base])
        left_bytes = _json_bytes([u_left_change])   # left spent base UTXO, got change
        right_bytes = _json_bytes([u_right_change])  # right also spent it → double-spend!

        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            muse_dir = root / ".muse" / "objects"
            muse_dir.mkdir(parents=True)

            for data in (base_bytes, left_bytes, right_bytes):
                h = _sha256(data)
                shard = muse_dir / h[:2]
                shard.mkdir(exist_ok=True)
                (shard / h[2:]).write_bytes(data)

            base = SnapshotManifest(
                files={"wallet/utxos.json": _sha256(base_bytes)}, domain="bitcoin"
            )
            left = SnapshotManifest(
                files={"wallet/utxos.json": _sha256(left_bytes)}, domain="bitcoin"
            )
            right = SnapshotManifest(
                files={"wallet/utxos.json": _sha256(right_bytes)}, domain="bitcoin"
            )
            result = self.plugin.merge(base, left, right, repo_root=root)

        # Both sides deleted the same UTXO from the same base → double-spend
        double_spend_records = [
            cr for cr in result.conflict_records
            if cr.conflict_type == "double_spend"
        ]
        assert len(double_spend_records) >= 1

    def test_both_deleted_same_file_is_clean(self) -> None:
        base = SnapshotManifest(files={"f": "a" * 64}, domain="bitcoin")
        left = SnapshotManifest(files={}, domain="bitcoin")
        right = SnapshotManifest(files={}, domain="bitcoin")
        result = self.plugin.merge(base, left, right)
        assert result.is_clean
        assert "f" not in result.merged["files"]


# ---------------------------------------------------------------------------
# drift
# ---------------------------------------------------------------------------


class TestDrift:
    def test_no_drift_when_identical(self) -> None:
        plugin = BitcoinPlugin()
        snap = SnapshotManifest(
            files={"wallet/utxos.json": "a" * 64}, domain="bitcoin"
        )
        report = plugin.drift(snap, snap)
        assert not report.has_drift

    def test_drift_detected_when_file_added(self) -> None:
        plugin = BitcoinPlugin()
        committed = SnapshotManifest(files={}, domain="bitcoin")
        live = SnapshotManifest(
            files={"wallet/utxos.json": "a" * 64}, domain="bitcoin"
        )
        report = plugin.drift(committed, live)
        assert report.has_drift
        assert "added" in report.summary

    def test_drift_from_disk_detected(self) -> None:
        plugin = BitcoinPlugin()
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "wallet").mkdir()
            (root / "wallet" / "utxos.json").write_bytes(b"[]")

            committed = SnapshotManifest(files={}, domain="bitcoin")
            report = plugin.drift(committed, root)

        assert report.has_drift


# ---------------------------------------------------------------------------
# apply
# ---------------------------------------------------------------------------


class TestApply:
    def test_apply_is_passthrough(self) -> None:
        plugin = BitcoinPlugin()
        delta = plugin.diff(
            SnapshotManifest(files={}, domain="bitcoin"),
            SnapshotManifest(files={}, domain="bitcoin"),
        )
        snap = SnapshotManifest(files={}, domain="bitcoin")
        result = plugin.apply(delta, snap)
        assert result is snap


# ---------------------------------------------------------------------------
# schema
# ---------------------------------------------------------------------------


class TestSchema:
    def test_schema_returns_domain_schema(self) -> None:
        from muse.core.schema import DomainSchema
        s = BitcoinPlugin().schema()
        assert isinstance(s, dict)
        assert s["domain"] == "bitcoin"
        assert s["schema_version"] == __version__

    def test_schema_has_ten_dimensions(self) -> None:
        s = BitcoinPlugin().schema()
        assert len(s["dimensions"]) == 10

    def test_schema_merge_mode_is_crdt(self) -> None:
        s = BitcoinPlugin().schema()
        assert s["merge_mode"] == "crdt"

    def test_dimension_names(self) -> None:
        s = BitcoinPlugin().schema()
        names = {d["name"] for d in s["dimensions"]}
        expected = {
            "utxos", "transactions", "labels", "descriptors",
            "channels", "strategy", "oracle_prices", "oracle_fees",
            "network", "execution",
        }
        assert names == expected

    def test_strategy_dimension_is_not_independent(self) -> None:
        s = BitcoinPlugin().schema()
        strat = next(d for d in s["dimensions"] if d["name"] == "strategy")
        assert strat["independent_merge"] is False


# ---------------------------------------------------------------------------
# OT merge — StructuredMergePlugin
# ---------------------------------------------------------------------------


class TestMergeOps:
    def setup_method(self) -> None:
        self.plugin = BitcoinPlugin()
        self.base = SnapshotManifest(files={}, domain="bitcoin")
        self.snap = SnapshotManifest(files={}, domain="bitcoin")

    def test_non_conflicting_ops_clean_merge(self) -> None:
        ours_ops: list[DomainOp] = [
            InsertOp(
                op="insert",
                address="wallet/utxos.json::aaa:0",
                position=None,
                content_id="a" * 64,
                content_summary="received UTXO aaa:0",
            )
        ]
        theirs_ops: list[DomainOp] = [
            InsertOp(
                op="insert",
                address="wallet/utxos.json::bbb:0",
                position=None,
                content_id="b" * 64,
                content_summary="received UTXO bbb:0",
            )
        ]
        result = self.plugin.merge_ops(
            self.base, self.snap, self.snap, ours_ops, theirs_ops
        )
        # Different addresses → no double-spend, clean merge
        assert len(result.conflict_records) == 0

    def test_double_spend_detected_in_merge_ops(self) -> None:
        utxo_addr = "wallet/utxos.json::deadbeef:0"
        ours_ops: list[DomainOp] = [
            DeleteOp(
                op="delete",
                address=utxo_addr,
                position=None,
                content_id="a" * 64,
                content_summary="spent UTXO deadbeef:0",
            )
        ]
        theirs_ops: list[DomainOp] = [
            DeleteOp(
                op="delete",
                address=utxo_addr,
                position=None,
                content_id="a" * 64,
                content_summary="spent UTXO deadbeef:0",
            )
        ]
        result = self.plugin.merge_ops(
            self.base, self.snap, self.snap, ours_ops, theirs_ops
        )
        double_spends = [
            cr for cr in result.conflict_records
            if cr.conflict_type == "double_spend"
        ]
        assert len(double_spends) == 1
        assert "deadbeef:0" in double_spends[0].addresses[0]

    def test_non_utxo_delete_not_flagged_as_double_spend(self) -> None:
        addr = "channels/channels.json::850000x1x0"
        ours_ops: list[DomainOp] = [
            DeleteOp(op="delete", address=addr, position=None,
                     content_id="a" * 64, content_summary="channel closed")
        ]
        theirs_ops: list[DomainOp] = [
            DeleteOp(op="delete", address=addr, position=None,
                     content_id="a" * 64, content_summary="channel closed")
        ]
        result = self.plugin.merge_ops(
            self.base, self.snap, self.snap, ours_ops, theirs_ops
        )
        double_spends = [
            cr for cr in result.conflict_records
            if cr.conflict_type == "double_spend"
        ]
        assert len(double_spends) == 0


# ---------------------------------------------------------------------------
# CRDTPlugin
# ---------------------------------------------------------------------------


class TestCRDT:
    def setup_method(self) -> None:
        self.plugin = BitcoinPlugin()
        self.base_snap = SnapshotManifest(
            files={"wallet/utxos.json": "a" * 64},
            domain="bitcoin",
        )

    def test_to_crdt_state_preserves_files(self) -> None:
        crdt = self.plugin.to_crdt_state(self.base_snap)
        assert crdt["domain"] == "bitcoin"
        assert crdt["schema_version"] == __version__
        assert "wallet/utxos.json" in crdt["files"]

    def test_from_crdt_state_returns_plain_snapshot(self) -> None:
        crdt = self.plugin.to_crdt_state(self.base_snap)
        snap = self.plugin.from_crdt_state(crdt)
        assert snap["domain"] == "bitcoin"
        assert "wallet/utxos.json" in snap["files"]

    def test_join_idempotent(self) -> None:
        crdt = self.plugin.to_crdt_state(self.base_snap)
        joined = self.plugin.join(crdt, crdt)
        result = self.plugin.from_crdt_state(joined)
        original = self.plugin.from_crdt_state(crdt)
        assert result["files"] == original["files"]

    def test_join_commutative(self) -> None:
        snap_a = SnapshotManifest(
            files={"wallet/utxos.json": "a" * 64}, domain="bitcoin"
        )
        snap_b = SnapshotManifest(
            files={"channels/channels.json": "b" * 64}, domain="bitcoin"
        )
        a = self.plugin.to_crdt_state(snap_a)
        b = self.plugin.to_crdt_state(snap_b)
        ab = self.plugin.from_crdt_state(self.plugin.join(a, b))
        ba = self.plugin.from_crdt_state(self.plugin.join(b, a))
        assert ab["files"] == ba["files"]

    def test_join_associative(self) -> None:
        snap_a = SnapshotManifest(files={"f1": "a" * 64}, domain="bitcoin")
        snap_b = SnapshotManifest(files={"f2": "b" * 64}, domain="bitcoin")
        snap_c = SnapshotManifest(files={"f3": "c" * 64}, domain="bitcoin")
        a = self.plugin.to_crdt_state(snap_a)
        b = self.plugin.to_crdt_state(snap_b)
        c = self.plugin.to_crdt_state(snap_c)
        ab_c = self.plugin.from_crdt_state(
            self.plugin.join(self.plugin.join(a, b), c)
        )
        a_bc = self.plugin.from_crdt_state(
            self.plugin.join(a, self.plugin.join(b, c))
        )
        assert ab_c["files"] == a_bc["files"]

    def test_join_preserves_both_agents_files(self) -> None:
        snap_a = SnapshotManifest(
            files={"wallet/utxos.json": "a" * 64}, domain="bitcoin"
        )
        snap_b = SnapshotManifest(
            files={"channels/channels.json": "b" * 64}, domain="bitcoin"
        )
        a = self.plugin.to_crdt_state(snap_a)
        b = self.plugin.to_crdt_state(snap_b)
        joined = self.plugin.from_crdt_state(self.plugin.join(a, b))
        assert "wallet/utxos.json" in joined["files"]
        assert "channels/channels.json" in joined["files"]

    def test_crdt_schema_has_seven_dimensions(self) -> None:
        dims = self.plugin.crdt_schema()
        assert len(dims) == 7

    def test_crdt_schema_dimension_types(self) -> None:
        dims = self.plugin.crdt_schema()
        types = {d["crdt_type"] for d in dims}
        assert "aw_map" in types
        assert "or_set" in types

    def test_vector_clock_advances_on_join(self) -> None:
        snap_a = SnapshotManifest(files={"f": "a" * 64}, domain="bitcoin")
        snap_b = SnapshotManifest(files={"f": "b" * 64}, domain="bitcoin")
        a = self.plugin.to_crdt_state(snap_a)
        b = self.plugin.to_crdt_state(snap_b)
        joined = self.plugin.join(a, b)
        assert isinstance(joined["vclock"], dict)


# ---------------------------------------------------------------------------
# Query analytics
# ---------------------------------------------------------------------------


class TestQueryAnalytics:
    def test_total_balance_sat(self) -> None:
        utxos = [
            _make_utxo(amount_sat=100_000),
            _make_utxo(txid="b" * 64, amount_sat=200_000),
        ]
        assert total_balance_sat(utxos) == 300_000

    def test_confirmed_balance_excludes_unconfirmed(self) -> None:
        utxos = [
            _make_utxo(amount_sat=100_000, confirmations=6),
            _make_utxo(txid="b" * 64, amount_sat=50_000, confirmations=0),
        ]
        assert confirmed_balance_sat(utxos) == 100_000

    def test_confirmed_balance_excludes_immature_coinbase(self) -> None:
        utxos = [
            _make_utxo(amount_sat=625_000_000, confirmations=50, coinbase=True),
            _make_utxo(txid="c" * 64, amount_sat=100_000, confirmations=1),
        ]
        assert confirmed_balance_sat(utxos) == 100_000

    def test_balance_by_script_type(self) -> None:
        utxos = [
            _make_utxo(amount_sat=100_000, script_type="p2wpkh"),
            _make_utxo(txid="b" * 64, amount_sat=200_000, script_type="p2tr"),
            _make_utxo(txid="c" * 64, amount_sat=50_000, script_type="p2wpkh"),
        ]
        breakdown = balance_by_script_type(utxos)
        assert breakdown["p2wpkh"] == 150_000
        assert breakdown["p2tr"] == 200_000

    def test_coin_age_blocks(self) -> None:
        u = _make_utxo(block_height=800_000)
        assert coin_age_blocks(u, 850_000) == 50_000

    def test_coin_age_blocks_unconfirmed(self) -> None:
        u = _make_utxo(block_height=None)
        assert coin_age_blocks(u, 850_000) is None

    def test_format_sat_small(self) -> None:
        assert format_sat(1_000) == "1,000 sats"

    def test_format_sat_large(self) -> None:
        result = format_sat(100_000_000)
        assert "1.00000000 BTC" in result

    def test_utxo_key(self) -> None:
        u = _make_utxo(txid="abc", vout=3)
        assert utxo_key(u) == "abc:3"

    def test_double_spend_candidates_detects_concurrent_spends(self) -> None:
        base = {"aaa:0", "bbb:1", "ccc:2"}
        our_spent = {"aaa:0", "bbb:1"}
        their_spent = {"aaa:0", "ccc:2"}
        candidates = double_spend_candidates(base, our_spent, their_spent)
        assert candidates == ["aaa:0"]

    def test_double_spend_candidates_no_overlap(self) -> None:
        base = {"aaa:0", "bbb:1"}
        our_spent = {"aaa:0"}
        their_spent = {"bbb:1"}
        assert double_spend_candidates(base, our_spent, their_spent) == []

    def test_channel_liquidity_totals(self) -> None:
        ch1 = _make_channel(local_balance_sat=1_000_000, remote_balance_sat=500_000)
        ch2 = _make_channel(
            channel_id="x", local_balance_sat=200_000, remote_balance_sat=800_000
        )
        local, remote = channel_liquidity_totals([ch1, ch2])
        assert local == 1_200_000
        assert remote == 1_300_000

    def test_channel_utilization(self) -> None:
        ch = _make_channel(
            capacity_sat=2_000_000,
            local_balance_sat=800_000,
            local_reserve_sat=20_000,
            remote_reserve_sat=20_000,
        )
        util = channel_utilization(ch)
        assert 0.0 <= util <= 1.0

    def test_fee_surface_str(self) -> None:
        est = _make_fee(t1=42, t6=15, t144=3)
        s = fee_surface_str(est)
        assert "42" in s
        assert "15" in s
        assert "3" in s
        assert "sat/vbyte" in s

    def test_latest_fee_estimate(self) -> None:
        old = _make_fee(timestamp=1_000)
        new = _make_fee(timestamp=2_000, t1=50)
        result = latest_fee_estimate([old, new])
        assert result is not None
        assert result["timestamp"] == 2_000

    def test_latest_fee_estimate_empty(self) -> None:
        assert latest_fee_estimate([]) is None

    def test_latest_price(self) -> None:
        p1 = _make_price(timestamp=1_000, price_usd=50_000.0)
        p2 = _make_price(timestamp=2_000, price_usd=70_000.0)
        assert latest_price([p1, p2]) == 70_000.0

    def test_strategy_summary_line_simulation_mode(self) -> None:
        s = _make_strategy(simulation_mode=True, name="aggressive")
        line = strategy_summary_line(s)
        assert "SIM" in line
        assert "aggressive" in line

    def test_utxo_summary_line(self) -> None:
        utxos = [
            _make_utxo(amount_sat=100_000, confirmations=6),
            _make_utxo(txid="b" * 64, amount_sat=50_000, confirmations=0),
        ]
        line = utxo_summary_line(utxos)
        assert "2 UTXOs" in line

    def test_mempool_summary_empty(self) -> None:
        line = mempool_summary_line([])
        assert "empty" in line


# ---------------------------------------------------------------------------
# Handler routing
# ---------------------------------------------------------------------------


class TestHandlerRouting:
    def test_utxos_json_routed(self) -> None:
        assert _handler_for_path("wallet/utxos.json") == "utxos"

    def test_channels_json_routed(self) -> None:
        assert _handler_for_path("channels/channels.json") == "channels"

    def test_agent_json_routed(self) -> None:
        assert _handler_for_path("strategy/agent.json") == "strategy"

    def test_prices_json_routed(self) -> None:
        assert _handler_for_path("oracles/prices.json") == "prices"

    def test_unknown_file_returns_none(self) -> None:
        assert _handler_for_path("README.md") is None
        assert _handler_for_path("custom/data.bin") is None


# ---------------------------------------------------------------------------
# Registry registration
# ---------------------------------------------------------------------------


class TestRegistry:
    def test_bitcoin_registered_in_plugin_registry(self) -> None:
        from muse.plugins.registry import registered_domains
        assert "bitcoin" in registered_domains()

    def test_resolve_bitcoin_plugin(self) -> None:
        import json as _json
        from muse.plugins.registry import resolve_plugin

        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / ".muse").mkdir()
            (root / ".muse" / "repo.json").write_text(
                _json.dumps({"domain": "bitcoin"})
            )
            plugin = resolve_plugin(root)

        assert isinstance(plugin, BitcoinPlugin)

"""Bitcoin domain plugin — multidimensional version control for Bitcoin state.

This plugin implements the full :class:`~muse.domain.MuseDomainPlugin` protocol
plus both optional extensions:

- :class:`~muse.domain.StructuredMergePlugin` — operation-level OT merge with
  **double-spend detection**: when two agents concurrently mark the same UTXO
  as spent, MUSE surfaces the conflict before any transaction hits the mempool.

- :class:`~muse.domain.CRDTPlugin` — convergent CRDT join for high-throughput
  multi-agent scenarios where millions of agents operate per second. ``join``
  always succeeds; no conflict state exists in CRDT mode.

What is versioned
-----------------
The plugin versions your *relationship* with Bitcoin — never private keys.
All descriptors are watch-only xpubs. Signing happens outside MUSE; MUSE
tracks the intent, the strategy, and the outcomes.

+--------------------------+-----------------------------------------------+
| Workdir path             | What it tracks                                |
+==========================+===============================================+
| wallet/utxos.json        | Unspent transaction outputs (the coin set)    |
| wallet/transactions.json | Confirmed transaction history                 |
| wallet/labels.json       | Address semantic annotations                  |
| wallet/descriptors.json  | Watch-only wallet descriptors (xpubs)         |
| channels/channels.json   | Lightning payment channel states              |
| channels/routing.json    | Lightning routing policies                    |
| strategy/agent.json      | Agent DCA / fee / rebalancing configuration   |
| strategy/execution.json  | Agent decision event log (append-only)        |
| oracles/prices.json      | BTC/USD price feed                            |
| oracles/fees.json        | Mempool fee surface snapshots                 |
| network/peers.json       | Known P2P network peers                       |
| network/mempool.json     | Local mempool view (pending transactions)     |
+--------------------------+-----------------------------------------------+

Branch semantics
----------------
``main`` — real wallet state: UTXOs, confirmed transactions, active channels.
``feat/<name>`` — strategy experiment: ``simulation_mode: true`` in
``strategy/agent.json`` means no real transactions are broadcast.

MUSE merge checks whether the strategy branch and the main branch have
incompatible UTXO spends — the OT merge engine's double-spend detector runs
before any signing happens.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import pathlib
import stat as stat_mod
from typing import Literal

from muse.core.crdts import AWMap, ORSet, VectorClock
from muse.core.crdts.aw_map import AWMapDict
from muse.core.crdts.or_set import ORSetDict
from muse.core.object_store import object_path
from muse.core.op_transform import merge_op_lists
from muse.core.schema import (
    CRDTDimensionSpec,
    DimensionSpec,
    DomainSchema,
    MapSchema,
    SequenceSchema,
    SetSchema,
)
from muse.domain import (
    CRDTSnapshotManifest,
    ConflictRecord,
    DeleteOp,
    DomainOp,
    DriftReport,
    FieldMutation,
    InsertOp,
    LiveState,
    MergeResult,
    MutateOp,
    PatchOp,
    ReplaceOp,
    SnapshotManifest,
    StateDelta,
    StateSnapshot,
    StructuredDelta,
)
from muse.plugins.bitcoin._query import (
    channel_summary_line,
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
    DescriptorRecord,
    ExecutionEventRecord,
    FeeEstimateRecord,
    LightningChannelRecord,
    NetworkPeerRecord,
    OraclePriceTickRecord,
    PendingTxRecord,
    RoutingPolicyRecord,
    TransactionRecord,
    UTXORecord,
)

logger = logging.getLogger(__name__)

_DOMAIN_NAME = "bitcoin"

# Recognized semantic file suffixes → diff handler key
_SEMANTIC_SUFFIXES: dict[str, str] = {
    "utxos.json": "utxos",
    "transactions.json": "transactions",
    "labels.json": "labels",
    "descriptors.json": "descriptors",
    "channels.json": "channels",
    "routing.json": "routing",
    "agent.json": "strategy",
    "execution.json": "execution",
    "prices.json": "prices",
    "fees.json": "fees",
    "peers.json": "peers",
    "mempool.json": "mempool",
}


# ---------------------------------------------------------------------------
# Internal helpers — JSON loading (typed)
# ---------------------------------------------------------------------------


def _load_utxos(data: bytes) -> list[UTXORecord]:
    result: list[UTXORecord] = json.loads(data)
    return result


def _load_transactions(data: bytes) -> list[TransactionRecord]:
    result: list[TransactionRecord] = json.loads(data)
    return result


def _load_labels(data: bytes) -> list[AddressLabelRecord]:
    result: list[AddressLabelRecord] = json.loads(data)
    return result


def _load_descriptors(data: bytes) -> list[DescriptorRecord]:
    result: list[DescriptorRecord] = json.loads(data)
    return result


def _load_channels(data: bytes) -> list[LightningChannelRecord]:
    result: list[LightningChannelRecord] = json.loads(data)
    return result


def _load_routing(data: bytes) -> list[RoutingPolicyRecord]:
    result: list[RoutingPolicyRecord] = json.loads(data)
    return result


def _load_strategy(data: bytes) -> AgentStrategyRecord:
    result: AgentStrategyRecord = json.loads(data)
    return result


def _load_execution(data: bytes) -> list[ExecutionEventRecord]:
    result: list[ExecutionEventRecord] = json.loads(data)
    return result


def _load_prices(data: bytes) -> list[OraclePriceTickRecord]:
    result: list[OraclePriceTickRecord] = json.loads(data)
    return result


def _load_fees(data: bytes) -> list[FeeEstimateRecord]:
    result: list[FeeEstimateRecord] = json.loads(data)
    return result


def _load_peers(data: bytes) -> list[NetworkPeerRecord]:
    result: list[NetworkPeerRecord] = json.loads(data)
    return result


def _load_mempool(data: bytes) -> list[PendingTxRecord]:
    result: list[PendingTxRecord] = json.loads(data)
    return result


def _content_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _hash_file(path: pathlib.Path) -> str:
    return _content_hash(path.read_bytes())


def _jhash(json_str: str) -> str:
    """SHA-256 of a pre-serialized canonical JSON string."""
    return hashlib.sha256(json_str.encode()).hexdigest()




# ---------------------------------------------------------------------------
# Semantic diff helpers — one per recognized file type
# ---------------------------------------------------------------------------


def _diff_utxos(
    path: str,
    old_data: bytes,
    new_data: bytes,
) -> list[DomainOp]:
    """Diff two UTXO sets at the UTXO level (txid:vout identity)."""
    base_list = _load_utxos(old_data)
    target_list = _load_utxos(new_data)

    base_map: dict[str, UTXORecord] = {utxo_key(u): u for u in base_list}
    target_map: dict[str, UTXORecord] = {utxo_key(u): u for u in target_list}

    ops: list[DomainOp] = []

    for key in sorted(set(target_map) - set(base_map)):
        utxo = target_map[key]
        ops.append(
            InsertOp(
                op="insert",
                address=f"{path}::{key}",
                position=None,
                content_id=_jhash(json.dumps(utxo, sort_keys=True, separators=(",", ":"))),
                content_summary=(
                    f"received +{format_sat(utxo['amount_sat'])}"
                    f" at {utxo['address'][:16]}…"
                    f" ({utxo['script_type']})"
                ),
            )
        )

    for key in sorted(set(base_map) - set(target_map)):
        utxo = base_map[key]
        ops.append(
            DeleteOp(
                op="delete",
                address=f"{path}::{key}",
                position=None,
                content_id=_jhash(json.dumps(utxo, sort_keys=True, separators=(",", ":"))),
                content_summary=(
                    f"spent -{format_sat(utxo['amount_sat'])}"
                    f" from {utxo['address'][:16]}…"
                ),
            )
        )

    for key in sorted(set(base_map) & set(target_map)):
        b = base_map[key]
        t = target_map[key]
        b_id = _jhash(json.dumps(b, sort_keys=True, separators=(",", ":")))
        t_id = _jhash(json.dumps(t, sort_keys=True, separators=(",", ":")))
        if b_id == t_id:
            continue
        fields: dict[str, FieldMutation] = {}
        if b["confirmations"] != t["confirmations"]:
            fields["confirmations"] = FieldMutation(
                old=str(b["confirmations"]),
                new=str(t["confirmations"]),
            )
        if b["label"] != t["label"]:
            fields["label"] = FieldMutation(
                old=str(b["label"] or ""),
                new=str(t["label"] or ""),
            )
        if fields:
            ops.append(
                MutateOp(
                    op="mutate",
                    address=f"{path}::{key}",
                    entity_id=key,
                    old_content_id=b_id,
                    new_content_id=t_id,
                    fields=fields,
                    old_summary=f"UTXO {key} (prev)",
                    new_summary=f"UTXO {key} (updated)",
                    position=None,
                )
            )
        else:
            ops.append(
                ReplaceOp(
                    op="replace",
                    address=f"{path}::{key}",
                    position=None,
                    old_content_id=b_id,
                    new_content_id=t_id,
                    old_summary=f"UTXO {key} (prev)",
                    new_summary=f"UTXO {key} (updated)",
                )
            )

    return ops


def _diff_transactions(
    path: str,
    old_data: bytes,
    new_data: bytes,
) -> list[DomainOp]:
    """Diff confirmed transaction histories (append-only by txid)."""
    base_txids: set[str] = {t["txid"] for t in _load_transactions(old_data)}
    target_map: dict[str, TransactionRecord] = {
        t["txid"]: t for t in _load_transactions(new_data)
    }

    ops: list[DomainOp] = []
    for txid in sorted(set(target_map) - base_txids):
        tx = target_map[txid]
        ops.append(
            InsertOp(
                op="insert",
                address=f"{path}::{txid}",
                position=None,
                content_id=_jhash(json.dumps(tx, sort_keys=True, separators=(",", ":"))),
                content_summary=(
                    f"tx {txid[:12]}…"
                    f" fee={format_sat(tx['fee_sat'])}"
                    f" {'confirmed' if tx['confirmed'] else 'unconfirmed'}"
                ),
            )
        )
    return ops


def _diff_labels(
    path: str,
    old_data: bytes,
    new_data: bytes,
) -> list[DomainOp]:
    """Diff address label sets (by address identity)."""
    base_map: dict[str, AddressLabelRecord] = {
        lbl["address"]: lbl for lbl in _load_labels(old_data)
    }
    target_map: dict[str, AddressLabelRecord] = {
        lbl["address"]: lbl for lbl in _load_labels(new_data)
    }

    ops: list[DomainOp] = []
    for addr in sorted(set(target_map) - set(base_map)):
        lbl = target_map[addr]
        ops.append(
            InsertOp(
                op="insert",
                address=f"{path}::{addr}",
                position=None,
                content_id=_jhash(json.dumps(lbl, sort_keys=True, separators=(",", ":"))),
                content_summary=f"label {lbl['label']!r} → {addr[:16]}…",
            )
        )
    for addr in sorted(set(base_map) - set(target_map)):
        lbl = base_map[addr]
        ops.append(
            DeleteOp(
                op="delete",
                address=f"{path}::{addr}",
                position=None,
                content_id=_jhash(json.dumps(lbl, sort_keys=True, separators=(",", ":"))),
                content_summary=f"removed label from {addr[:16]}…",
            )
        )
    for addr in sorted(set(base_map) & set(target_map)):
        b, t = base_map[addr], target_map[addr]
        b_id = _jhash(json.dumps(b, sort_keys=True, separators=(",", ":")))
        t_id = _jhash(json.dumps(t, sort_keys=True, separators=(",", ":")))
        if b_id != t_id:
            ops.append(
                MutateOp(
                    op="mutate",
                    address=f"{path}::{addr}",
                    entity_id=addr,
                    old_content_id=b_id,
                    new_content_id=t_id,
                    fields={"label": FieldMutation(old=b["label"], new=t["label"])},
                    old_summary=f"label {b['label']!r}",
                    new_summary=f"label {t['label']!r}",
                    position=None,
                )
            )
    return ops


def _diff_channels(
    path: str,
    old_data: bytes,
    new_data: bytes,
) -> list[DomainOp]:
    """Diff Lightning channel states (by channel_id identity)."""
    base_map: dict[str, LightningChannelRecord] = {
        c["channel_id"]: c for c in _load_channels(old_data)
    }
    target_map: dict[str, LightningChannelRecord] = {
        c["channel_id"]: c for c in _load_channels(new_data)
    }

    ops: list[DomainOp] = []
    for cid in sorted(set(target_map) - set(base_map)):
        ch = target_map[cid]
        ops.append(
            InsertOp(
                op="insert",
                address=f"{path}::{cid}",
                position=None,
                content_id=_jhash(json.dumps(ch, sort_keys=True, separators=(",", ":"))),
                content_summary=(
                    f"channel opened with {(ch['peer_alias'] or ch['peer_pubkey'][:16] + '…')}"
                    f" capacity={format_sat(ch['capacity_sat'])}"
                ),
            )
        )
    for cid in sorted(set(base_map) - set(target_map)):
        ch = base_map[cid]
        ops.append(
            DeleteOp(
                op="delete",
                address=f"{path}::{cid}",
                position=None,
                content_id=_jhash(json.dumps(ch, sort_keys=True, separators=(",", ":"))),
                content_summary=(
                    f"channel closed with {(ch['peer_alias'] or ch['peer_pubkey'][:16] + '…')}"
                ),
            )
        )
    for cid in sorted(set(base_map) & set(target_map)):
        b, t = base_map[cid], target_map[cid]
        b_id = _jhash(json.dumps(b, sort_keys=True, separators=(",", ":")))
        t_id = _jhash(json.dumps(t, sort_keys=True, separators=(",", ":")))
        if b_id == t_id:
            continue
        ch_fields: dict[str, FieldMutation] = {}
        if b["local_balance_sat"] != t["local_balance_sat"]:
            ch_fields["local_balance_sat"] = FieldMutation(
                old=str(b["local_balance_sat"]), new=str(t["local_balance_sat"])
            )
        if b["remote_balance_sat"] != t["remote_balance_sat"]:
            ch_fields["remote_balance_sat"] = FieldMutation(
                old=str(b["remote_balance_sat"]), new=str(t["remote_balance_sat"])
            )
        if b["is_active"] != t["is_active"]:
            ch_fields["is_active"] = FieldMutation(
                old=str(b["is_active"]), new=str(t["is_active"])
            )
        if b["htlc_count"] != t["htlc_count"]:
            ch_fields["htlc_count"] = FieldMutation(
                old=str(b["htlc_count"]), new=str(t["htlc_count"])
            )
        if not ch_fields:
            ch_fields["state"] = FieldMutation(old="prev", new="updated")
        ops.append(
            MutateOp(
                op="mutate",
                address=f"{path}::{cid}",
                entity_id=cid,
                old_content_id=b_id,
                new_content_id=t_id,
                fields=ch_fields,
                old_summary=f"channel {cid} (prev)",
                new_summary=f"channel {cid} (updated)",
                position=None,
            )
        )
    return ops


def _diff_strategy(
    path: str,
    old_data: bytes,
    new_data: bytes,
) -> list[DomainOp]:
    """Diff agent strategy at field level using MutateOp per changed field."""
    base_strat = _load_strategy(old_data)
    target_strat = _load_strategy(new_data)
    b_id = _jhash(json.dumps(base_strat, sort_keys=True, separators=(",", ":")))
    t_id = _jhash(json.dumps(target_strat, sort_keys=True, separators=(",", ":")))
    if b_id == t_id:
        return []
    strat_fields: dict[str, FieldMutation] = {}
    if base_strat["name"] != target_strat["name"]:
        strat_fields["name"] = FieldMutation(old=base_strat["name"], new=target_strat["name"])
    if base_strat["max_fee_rate_sat_vbyte"] != target_strat["max_fee_rate_sat_vbyte"]:
        strat_fields["max_fee_rate_sat_vbyte"] = FieldMutation(
            old=str(base_strat["max_fee_rate_sat_vbyte"]),
            new=str(target_strat["max_fee_rate_sat_vbyte"]),
        )
    if base_strat["min_confirmations"] != target_strat["min_confirmations"]:
        strat_fields["min_confirmations"] = FieldMutation(
            old=str(base_strat["min_confirmations"]),
            new=str(target_strat["min_confirmations"]),
        )
    if base_strat["utxo_consolidation_threshold"] != target_strat["utxo_consolidation_threshold"]:
        strat_fields["utxo_consolidation_threshold"] = FieldMutation(
            old=str(base_strat["utxo_consolidation_threshold"]),
            new=str(target_strat["utxo_consolidation_threshold"]),
        )
    if base_strat["dca_amount_sat"] != target_strat["dca_amount_sat"]:
        strat_fields["dca_amount_sat"] = FieldMutation(
            old=str(base_strat["dca_amount_sat"]),
            new=str(target_strat["dca_amount_sat"]),
        )
    if base_strat["dca_interval_blocks"] != target_strat["dca_interval_blocks"]:
        strat_fields["dca_interval_blocks"] = FieldMutation(
            old=str(base_strat["dca_interval_blocks"]),
            new=str(target_strat["dca_interval_blocks"]),
        )
    if base_strat["lightning_rebalance_threshold"] != target_strat["lightning_rebalance_threshold"]:
        strat_fields["lightning_rebalance_threshold"] = FieldMutation(
            old=str(base_strat["lightning_rebalance_threshold"]),
            new=str(target_strat["lightning_rebalance_threshold"]),
        )
    if base_strat["coin_selection"] != target_strat["coin_selection"]:
        strat_fields["coin_selection"] = FieldMutation(
            old=base_strat["coin_selection"], new=target_strat["coin_selection"]
        )
    if base_strat["simulation_mode"] != target_strat["simulation_mode"]:
        strat_fields["simulation_mode"] = FieldMutation(
            old=str(base_strat["simulation_mode"]),
            new=str(target_strat["simulation_mode"]),
        )
    return [
        MutateOp(
            op="mutate",
            address=path,
            entity_id="agent_strategy",
            old_content_id=b_id,
            new_content_id=t_id,
            fields=strat_fields,
            old_summary=strategy_summary_line(base_strat),
            new_summary=strategy_summary_line(target_strat),
            position=None,
        )
    ]


def _diff_execution(
    path: str,
    old_data: bytes,
    new_data: bytes,
) -> list[DomainOp]:
    """Diff execution event logs (append-only by timestamp+txid)."""
    base_set: set[str] = {
        f"{e['timestamp']}:{e['txid'] or ''}" for e in _load_execution(old_data)
    }
    ops: list[DomainOp] = []
    for ev in _load_execution(new_data):
        key = f"{ev['timestamp']}:{ev['txid'] or ''}"
        if key not in base_set:
            ops.append(
                InsertOp(
                    op="insert",
                    address=f"{path}::{key}",
                    position=None,
                    content_id=_jhash(json.dumps(ev, sort_keys=True, separators=(",", ":"))),
                    content_summary=f"event {ev['event_type']}: {ev['note'][:60]}",
                )
            )
    return ops


def _diff_time_series(
    path: str,
    old_data: bytes,
    new_data: bytes,
    loader_key: Literal["prices", "fees"],
) -> list[DomainOp]:
    """Diff an append-only time-series dimension (prices or fees)."""
    ops: list[DomainOp] = []

    if loader_key == "prices":
        base_tss: set[int] = {p["timestamp"] for p in _load_prices(old_data)}
        for tick in _load_prices(new_data):
            if tick["timestamp"] not in base_tss:
                price_str = f"${tick['price_usd']:,.2f}"
                ops.append(
                    InsertOp(
                        op="insert",
                        address=f"{path}::{tick['timestamp']}",
                        position=None,
                        content_id=_jhash(json.dumps(tick, sort_keys=True, separators=(",", ":"))),
                        content_summary=f"BTC/USD {price_str} from {tick['source']}",
                    )
                )
    else:
        base_tss_f: set[int] = {e["timestamp"] for e in _load_fees(old_data)}
        for est in _load_fees(new_data):
            if est["timestamp"] not in base_tss_f:
                ops.append(
                    InsertOp(
                        op="insert",
                        address=f"{path}::{est['timestamp']}",
                        position=None,
                        content_id=_jhash(json.dumps(est, sort_keys=True, separators=(",", ":"))),
                        content_summary=fee_surface_str(est),
                    )
                )
    return ops


def _diff_peers(
    path: str,
    old_data: bytes,
    new_data: bytes,
) -> list[DomainOp]:
    """Diff peer lists (by pubkey identity)."""
    base_map: dict[str, NetworkPeerRecord] = {
        p["pubkey"]: p for p in _load_peers(old_data)
    }
    target_map: dict[str, NetworkPeerRecord] = {
        p["pubkey"]: p for p in _load_peers(new_data)
    }
    ops: list[DomainOp] = []
    for pk in sorted(set(target_map) - set(base_map)):
        peer = target_map[pk]
        ops.append(
            InsertOp(
                op="insert",
                address=f"{path}::{pk}",
                position=None,
                content_id=_jhash(json.dumps(peer, sort_keys=True, separators=(",", ":"))),
                content_summary=f"peer {peer['alias'] or pk[:16]}… connected={peer['connected']}",
            )
        )
    for pk in sorted(set(base_map) - set(target_map)):
        peer = base_map[pk]
        ops.append(
            DeleteOp(
                op="delete",
                address=f"{path}::{pk}",
                position=None,
                content_id=_jhash(json.dumps(peer, sort_keys=True, separators=(",", ":"))),
                content_summary=f"peer removed: {peer['alias'] or pk[:16]}…",
            )
        )
    return ops


def _diff_mempool(
    path: str,
    old_data: bytes,
    new_data: bytes,
) -> list[DomainOp]:
    """Diff mempool snapshots (volatile set by txid)."""
    base_txids: set[str] = {t["txid"] for t in _load_mempool(old_data)}
    target_map: dict[str, PendingTxRecord] = {
        t["txid"]: t for t in _load_mempool(new_data)
    }
    ops: list[DomainOp] = []
    for txid in sorted(set(target_map) - base_txids):
        tx = target_map[txid]
        ops.append(
            InsertOp(
                op="insert",
                address=f"{path}::{txid}",
                position=None,
                content_id=_jhash(json.dumps(tx, sort_keys=True, separators=(",", ":"))),
                content_summary=mempool_summary_line([tx]),
            )
        )
    for txid in sorted(base_txids - set(target_map)):
        ops.append(
            DeleteOp(
                op="delete",
                address=f"{path}::{txid}",
                position=None,
                content_id=txid,
                content_summary=f"tx {txid[:12]}… left mempool",
            )
        )
    return ops


def _semantic_child_ops(
    path: str,
    old_data: bytes,
    new_data: bytes,
    handler: str,
) -> list[DomainOp]:
    """Dispatch to the correct semantic diff helper for a known file."""
    if handler == "utxos":
        return _diff_utxos(path, old_data, new_data)
    if handler == "transactions":
        return _diff_transactions(path, old_data, new_data)
    if handler == "labels":
        return _diff_labels(path, old_data, new_data)
    if handler == "channels":
        return _diff_channels(path, old_data, new_data)
    if handler == "strategy":
        return _diff_strategy(path, old_data, new_data)
    if handler == "execution":
        return _diff_execution(path, old_data, new_data)
    if handler == "prices":
        return _diff_time_series(path, old_data, new_data, "prices")
    if handler == "fees":
        return _diff_time_series(path, old_data, new_data, "fees")
    if handler == "peers":
        return _diff_peers(path, old_data, new_data)
    if handler == "mempool":
        return _diff_mempool(path, old_data, new_data)
    return []


def _handler_for_path(path: str) -> str | None:
    """Return the semantic handler key for a workdir path, or ``None``."""
    for suffix, handler in _SEMANTIC_SUFFIXES.items():
        if path.endswith(suffix):
            return handler
    return None


def _diff_modified_file(
    path: str,
    old_hash: str,
    new_hash: str,
    repo_root: pathlib.Path | None,
) -> DomainOp:
    """Produce the best available op for a single modified file.

    With ``repo_root``: load blobs, run semantic diff, return ``PatchOp``.
    Without ``repo_root``: return coarse ``ReplaceOp``.
    """
    handler = _handler_for_path(path)
    if repo_root is not None and handler is not None:
        try:
            old_bytes = object_path(repo_root, old_hash).read_bytes()
            new_bytes = object_path(repo_root, new_hash).read_bytes()
            child_ops = _semantic_child_ops(path, old_bytes, new_bytes, handler)
            if child_ops:
                n = len(child_ops)
                return PatchOp(
                    op="patch",
                    address=path,
                    child_ops=child_ops,
                    child_domain=f"bitcoin.{handler}",
                    child_summary=f"{n} {handler} change{'s' if n != 1 else ''}",
                )
        except OSError:
            logger.debug("bitcoin diff: blob not found for %s, falling back", path)

    return ReplaceOp(
        op="replace",
        address=path,
        position=None,
        old_content_id=old_hash,
        new_content_id=new_hash,
        old_summary=f"{path} (prev)",
        new_summary=f"{path} (updated)",
    )


# ---------------------------------------------------------------------------
# CRDT state helpers
# ---------------------------------------------------------------------------

_EMPTY_AW: AWMapDict = {"entries": [], "tombstones": []}
_EMPTY_OR: ORSetDict = {"entries": [], "tombstones": []}


def _load_aw(crdt_state: dict[str, str], key: str) -> AWMap:
    raw = crdt_state.get(key, "{}")
    data: AWMapDict = json.loads(raw) if raw != "{}" else _EMPTY_AW
    return AWMap.from_dict(data)


def _load_or(crdt_state: dict[str, str], key: str) -> ORSet:
    raw = crdt_state.get(key, "{}")
    data: ORSetDict = json.loads(raw) if raw != "{}" else _EMPTY_OR
    return ORSet.from_dict(data)


# ---------------------------------------------------------------------------
# BitcoinPlugin
# ---------------------------------------------------------------------------


class BitcoinPlugin:
    """Bitcoin domain plugin — the full MuseDomainPlugin + OT + CRDT stack.

    Implements three protocol levels:

    1. **Core** (:class:`~muse.domain.MuseDomainPlugin`) — snapshot, diff,
       merge, drift, apply, schema.
    2. **OT merge** (:class:`~muse.domain.StructuredMergePlugin`) — operation-
       level merge with double-spend detection.
    3. **CRDT** (:class:`~muse.domain.CRDTPlugin`) — convergent join for
       high-throughput multi-agent write scenarios.
    """

    # ------------------------------------------------------------------
    # 1. snapshot
    # ------------------------------------------------------------------

    def snapshot(self, live_state: LiveState) -> StateSnapshot:
        """Capture the working tree as a content-addressed manifest.

        Walks every non-hidden, non-ignored file in the working tree and
        records its SHA-256 digest. Private keys are never stored — the
        working tree must only contain watch-only descriptors (xpubs).

        Args:
            live_state: Repository root ``pathlib.Path`` or an existing
                        ``SnapshotManifest`` dict (returned unchanged).

        Returns:
            A ``SnapshotManifest`` mapping POSIX paths to SHA-256 digests.
        """
        if isinstance(live_state, pathlib.Path):
            from muse.core.ignore import is_ignored, load_ignore_config, resolve_patterns
            from muse.core.stat_cache import load_cache

            workdir = live_state
            repo_root = workdir
            patterns = resolve_patterns(load_ignore_config(repo_root), _DOMAIN_NAME)
            cache = load_cache(workdir)
            root_str = str(workdir)
            prefix_len = len(root_str) + 1
            files: dict[str, str] = {}

            for dirpath, dirnames, filenames in os.walk(root_str, followlinks=False):
                dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
                for fname in sorted(filenames):
                    if fname.startswith("."):
                        continue
                    abs_str = os.path.join(dirpath, fname)
                    try:
                        st = os.lstat(abs_str)
                    except OSError:
                        continue
                    if not stat_mod.S_ISREG(st.st_mode):
                        continue
                    rel = abs_str[prefix_len:]
                    if os.sep != "/":
                        rel = rel.replace(os.sep, "/")
                    if is_ignored(rel, patterns):
                        continue
                    files[rel] = cache.get_cached(
                        rel, abs_str, st.st_mtime, st.st_size
                    )

            cache.prune(set(files))
            cache.save()
            return SnapshotManifest(files=files, domain=_DOMAIN_NAME)

        return live_state

    # ------------------------------------------------------------------
    # 2. diff
    # ------------------------------------------------------------------

    def diff(
        self,
        base: StateSnapshot,
        target: StateSnapshot,
        *,
        repo_root: pathlib.Path | None = None,
    ) -> StateDelta:
        """Compute a structured delta between two Bitcoin state snapshots.

        With ``repo_root``: produces semantic ``PatchOp`` entries with
        element-level child ops (UTXO-level, channel-level, field-level).

        Without ``repo_root``: produces coarse ``InsertOp`` / ``DeleteOp`` /
        ``ReplaceOp`` at file granularity.

        Args:
            base:      Ancestor snapshot.
            target:    Later snapshot.
            repo_root: Repository root for object store access.

        Returns:
            A ``StructuredDelta`` describing every change from *base* to
            *target*.
        """
        base_files = base["files"]
        target_files = target["files"]
        base_paths = set(base_files)
        target_paths = set(target_files)

        ops: list[DomainOp] = []

        for path in sorted(target_paths - base_paths):
            ops.append(
                InsertOp(
                    op="insert",
                    address=path,
                    position=None,
                    content_id=target_files[path],
                    content_summary=f"new: {path}",
                )
            )

        for path in sorted(base_paths - target_paths):
            ops.append(
                DeleteOp(
                    op="delete",
                    address=path,
                    position=None,
                    content_id=base_files[path],
                    content_summary=f"removed: {path}",
                )
            )

        for path in sorted(
            p for p in base_paths & target_paths if base_files[p] != target_files[p]
        ):
            ops.append(
                _diff_modified_file(
                    path=path,
                    old_hash=base_files[path],
                    new_hash=target_files[path],
                    repo_root=repo_root,
                )
            )

        summary = _delta_summary(ops)
        return StructuredDelta(domain=_DOMAIN_NAME, ops=ops, summary=summary)

    # ------------------------------------------------------------------
    # 3. merge
    # ------------------------------------------------------------------

    def merge(
        self,
        base: StateSnapshot,
        left: StateSnapshot,
        right: StateSnapshot,
        *,
        repo_root: pathlib.Path | None = None,
    ) -> MergeResult:
        """Three-way merge with Bitcoin-aware double-spend detection.

        Performs standard three-way file-level merge. When ``repo_root`` is
        available and both branches touch ``wallet/utxos.json``, loads the
        UTXO sets to detect double-spend candidates and promotes them to
        structured ``ConflictRecord`` entries.

        Args:
            base:      Common ancestor snapshot.
            left:      Ours (current branch) snapshot.
            right:     Theirs (incoming branch) snapshot.
            repo_root: Repository root for double-spend analysis.

        Returns:
            A ``MergeResult`` with the reconciled manifest and any conflicts.
        """
        base_files = base["files"]
        left_files = left["files"]
        right_files = right["files"]

        merged: dict[str, str] = dict(base_files)
        conflicts: list[str] = []
        conflict_records: list[ConflictRecord] = []

        all_paths = set(base_files) | set(left_files) | set(right_files)
        for path in sorted(all_paths):
            b_val = base_files.get(path)
            l_val = left_files.get(path)
            r_val = right_files.get(path)

            if l_val == r_val:
                if l_val is None:
                    merged.pop(path, None)
                else:
                    merged[path] = l_val
            elif b_val == l_val:
                if r_val is None:
                    merged.pop(path, None)
                else:
                    merged[path] = r_val
            elif b_val == r_val:
                if l_val is None:
                    merged.pop(path, None)
                else:
                    merged[path] = l_val
            else:
                conflicts.append(path)
                merged[path] = l_val or r_val or b_val or ""

        # Bitcoin-specific: detect UTXO double-spend for utxos.json conflicts
        if repo_root is not None:
            for path in list(conflicts):
                if not path.endswith("utxos.json"):
                    continue
                b_hash = base_files.get(path)
                l_hash = left_files.get(path)
                r_hash = right_files.get(path)
                if b_hash is None or l_hash is None or r_hash is None:
                    continue
                try:
                    base_utxos = _load_utxos(
                        object_path(repo_root, b_hash).read_bytes()
                    )
                    left_utxos = _load_utxos(
                        object_path(repo_root, l_hash).read_bytes()
                    )
                    right_utxos = _load_utxos(
                        object_path(repo_root, r_hash).read_bytes()
                    )
                    base_keys = {utxo_key(u) for u in base_utxos}
                    left_keys = {utxo_key(u) for u in left_utxos}
                    right_keys = {utxo_key(u) for u in right_utxos}
                    our_spent = base_keys - left_keys
                    their_spent = base_keys - right_keys
                    dsc = double_spend_candidates(base_keys, our_spent, their_spent)
                    if dsc:
                        conflict_records.append(
                            ConflictRecord(
                                path=path,
                                conflict_type="double_spend",
                                ours_summary=f"spent {len(our_spent)} UTXO(s)",
                                theirs_summary=f"spent {len(their_spent)} UTXO(s)",
                                addresses=[f"{path}::{k}" for k in dsc],
                            )
                        )
                except OSError:
                    logger.debug("bitcoin merge: blob not found for %s", path)

        return MergeResult(
            merged=SnapshotManifest(files=merged, domain=_DOMAIN_NAME),
            conflicts=conflicts,
            conflict_records=conflict_records,
        )

    # ------------------------------------------------------------------
    # 4. drift
    # ------------------------------------------------------------------

    def drift(self, committed: StateSnapshot, live: LiveState) -> DriftReport:
        """Compare the last committed snapshot against the current live state.

        Used by ``muse status``. Produces a ``DriftReport`` describing every
        UTXO gained or spent, every channel state change, every strategy
        parameter update since the last commit.

        Args:
            committed: The last committed ``StateSnapshot``.
            live:      Current live state (path or snapshot dict).

        Returns:
            A ``DriftReport`` with ``has_drift``, ``summary``, and ``delta``.
        """
        current = self.snapshot(live)
        delta = self.diff(committed, current)
        has_drift = len(delta["ops"]) > 0
        return DriftReport(
            has_drift=has_drift,
            summary=delta.get("summary", "working tree clean"),
            delta=delta,
        )

    # ------------------------------------------------------------------
    # 5. apply
    # ------------------------------------------------------------------

    def apply(self, delta: StateDelta, live_state: LiveState) -> LiveState:
        """Apply a delta during ``muse checkout``.

        The core engine restores file-level objects from the object store.
        This hook exists for domain-level post-processing; Bitcoin currently
        requires none beyond file restoration.

        Args:
            delta:      The typed operation list to apply.
            live_state: Current live state.

        Returns:
            The unchanged ``live_state`` (post-processing is a no-op here).
        """
        return live_state

    # ------------------------------------------------------------------
    # 6. schema
    # ------------------------------------------------------------------

    def schema(self) -> DomainSchema:
        """Declare the multidimensional structure of Bitcoin state.

        Ten dimensions map to the ten recognized workdir file types. The
        ``merge_mode`` is ``"crdt"`` to signal that this plugin supports the
        :class:`~muse.domain.CRDTPlugin` convergent join path for multi-agent
        scenarios.

        Returns:
            A ``DomainSchema`` with all Bitcoin dimensions declared.
        """
        return DomainSchema(
            domain=_DOMAIN_NAME,
            description=(
                "Bitcoin domain — multidimensional version control for wallet state, "
                "Lightning channels, agent strategies, and oracle data. "
                "Watch-only (no private keys). UTXO-level diff, channel-level merge, "
                "agent-strategy branching, and CRDT convergent join for multi-agent "
                "scenarios with millions of concurrent agents."
            ),
            top_level=SetSchema(
                kind="set",
                element_type="bitcoin_state_file",
                identity="by_content",
            ),
            dimensions=[
                DimensionSpec(
                    name="utxos",
                    description=(
                        "Unspent transaction outputs — the coin set. "
                        "Identity: txid:vout. Double-spend detection activates "
                        "when two branches delete the same UTXO."
                    ),
                    schema=SetSchema(
                        kind="set",
                        element_type="utxo",
                        identity="by_id",
                    ),
                    independent_merge=True,
                ),
                DimensionSpec(
                    name="transactions",
                    description=(
                        "Confirmed transaction history. Append-only: the blockchain "
                        "never removes confirmed transactions. New txids from both "
                        "branches are merged by union."
                    ),
                    schema=SequenceSchema(
                        kind="sequence",
                        element_type="transaction",
                        identity="by_id",
                        diff_algorithm="lcs",
                        alphabet=None,
                    ),
                    independent_merge=True,
                ),
                DimensionSpec(
                    name="labels",
                    description=(
                        "Address semantic annotations. Additive: concurrent label "
                        "additions from multiple agents always survive. CRDT OR-Set "
                        "semantics in CRDT mode."
                    ),
                    schema=SetSchema(
                        kind="set",
                        element_type="address_label",
                        identity="by_id",
                    ),
                    independent_merge=True,
                ),
                DimensionSpec(
                    name="descriptors",
                    description="Watch-only wallet descriptors (xpubs). Never private keys.",
                    schema=SetSchema(
                        kind="set",
                        element_type="descriptor",
                        identity="by_id",
                    ),
                    independent_merge=True,
                ),
                DimensionSpec(
                    name="channels",
                    description=(
                        "Lightning payment channel states. Identity: channel_id. "
                        "Balance changes are MutateOps; open/close are Insert/DeleteOps."
                    ),
                    schema=SetSchema(
                        kind="set",
                        element_type="lightning_channel",
                        identity="by_id",
                    ),
                    independent_merge=True,
                ),
                DimensionSpec(
                    name="strategy",
                    description=(
                        "Agent DCA / fee / rebalancing configuration. Field-level "
                        "MutateOps enable per-parameter conflict detection and LWW "
                        "resolution in CRDT mode."
                    ),
                    schema=MapSchema(
                        kind="map",
                        key_type="strategy_field",
                        value_schema=SetSchema(
                            kind="set",
                            element_type="field_value",
                            identity="by_content",
                        ),
                        identity="by_key",
                    ),
                    independent_merge=False,
                ),
                DimensionSpec(
                    name="oracle_prices",
                    description=(
                        "BTC/USD price feed. Time-ordered sequence of oracle ticks. "
                        "Concurrent ticks from different sources are merged by union "
                        "ordered by timestamp."
                    ),
                    schema=SequenceSchema(
                        kind="sequence",
                        element_type="price_tick",
                        identity="by_id",
                        diff_algorithm="lcs",
                        alphabet=None,
                    ),
                    independent_merge=True,
                ),
                DimensionSpec(
                    name="oracle_fees",
                    description="Mempool fee surface time series (sat/vbyte per block target).",
                    schema=SequenceSchema(
                        kind="sequence",
                        element_type="fee_estimate",
                        identity="by_id",
                        diff_algorithm="lcs",
                        alphabet=None,
                    ),
                    independent_merge=True,
                ),
                DimensionSpec(
                    name="network",
                    description="Known P2P peers and local mempool state.",
                    schema=SetSchema(
                        kind="set",
                        element_type="network_peer",
                        identity="by_id",
                    ),
                    independent_merge=True,
                ),
                DimensionSpec(
                    name="execution",
                    description=(
                        "Agent execution event log — append-only audit trail of every "
                        "decision the agent made: DCA buys, fee bumps, channel opens, "
                        "rebalances. Never deleted."
                    ),
                    schema=SequenceSchema(
                        kind="sequence",
                        element_type="execution_event",
                        identity="by_id",
                        diff_algorithm="lcs",
                        alphabet=None,
                    ),
                    independent_merge=True,
                ),
            ],
            merge_mode="crdt",
            schema_version=1,
        )

    # ------------------------------------------------------------------
    # StructuredMergePlugin — operation-level OT merge
    # ------------------------------------------------------------------

    def merge_ops(
        self,
        base: StateSnapshot,
        ours_snap: StateSnapshot,
        theirs_snap: StateSnapshot,
        ours_ops: list[DomainOp],
        theirs_ops: list[DomainOp],
        *,
        repo_root: pathlib.Path | None = None,
    ) -> MergeResult:
        """Operation-level three-way merge with double-spend detection.

        Uses the OT engine's commutativity oracle to detect op-level conflicts.
        Bitcoin-specific rule applied on top of standard OT:

        **Double-spend signal**: if both ``ours_ops`` and ``theirs_ops``
        contain a ``DeleteOp`` for the same UTXO address
        (``"wallet/utxos.json::{txid}:{vout}"``), this is a strategy-layer
        double-spend attempt. The op is promoted to a structured conflict even
        if the standard OT oracle would allow it (since ``DeleteOp + DeleteOp``
        normally commutes).

        Args:
            base:        Common ancestor snapshot.
            ours_snap:   Our branch's final snapshot.
            theirs_snap: Their branch's final snapshot.
            ours_ops:    Our branch's typed operation list.
            theirs_ops:  Their branch's typed operation list.
            repo_root:   Repository root for ``.museattributes`` and object store.

        Returns:
            ``MergeResult`` with empty ``conflicts`` if all ops commute, or
            structured conflicts including double-spend records.
        """
        result = merge_op_lists(
            base_ops=[],
            ours_ops=ours_ops,
            theirs_ops=theirs_ops,
        )

        conflicts: list[str] = []
        conflict_records: list[ConflictRecord] = []

        # Standard OT conflicts
        if result.conflict_ops:
            seen: set[str] = set()
            for our_op, their_op in result.conflict_ops:
                addr = our_op["address"]
                seen.add(addr)
                conflict_records.append(
                    ConflictRecord(
                        path=addr.split("::")[0],
                        conflict_type="symbol_edit_overlap",
                        ours_summary=f"ours: {our_op['op']} {addr}",
                        theirs_summary=f"theirs: {their_op['op']} {addr}",
                        addresses=[addr],
                    )
                )
            conflicts = sorted(seen)

        # Bitcoin-specific: double-spend detection on UTXO delete ops
        our_utxo_deletes: set[str] = {
            op["address"]
            for op in ours_ops
            if op["op"] == "delete" and "utxos.json::" in op["address"]
        }
        their_utxo_deletes: set[str] = {
            op["address"]
            for op in theirs_ops
            if op["op"] == "delete" and "utxos.json::" in op["address"]
        }
        double_spends = sorted(our_utxo_deletes & their_utxo_deletes)
        for addr in double_spends:
            if addr not in conflicts:
                conflicts.append(addr)
            conflict_records.append(
                ConflictRecord(
                    path=addr.split("::")[0],
                    conflict_type="double_spend",
                    ours_summary=f"ours spent UTXO {addr.split('::')[-1]}",
                    theirs_summary=f"theirs spent UTXO {addr.split('::')[-1]}",
                    addresses=[addr],
                )
            )

        fallback = self.merge(base, ours_snap, theirs_snap, repo_root=repo_root)
        return MergeResult(
            merged=fallback.merged,
            conflicts=conflicts if conflicts else fallback.conflicts,
            conflict_records=conflict_records if conflict_records else fallback.conflict_records,
            applied_strategies=fallback.applied_strategies,
            dimension_reports=fallback.dimension_reports,
            op_log=result.merged_ops,
        )

    # ------------------------------------------------------------------
    # CRDTPlugin — convergent multi-agent join
    # ------------------------------------------------------------------

    def crdt_schema(self) -> list[CRDTDimensionSpec]:
        """Declare the CRDT primitive for each Bitcoin dimension.

        Returns:
            Seven ``CRDTDimensionSpec`` entries, one per CRDT-backed dimension.
        """
        return [
            CRDTDimensionSpec(
                name="files_manifest",
                description=(
                    "The file manifest itself — convergent AW-Map so concurrent "
                    "file additions from any agent are always preserved."
                ),
                crdt_type="aw_map",
                independent_merge=True,
            ),
            CRDTDimensionSpec(
                name="utxos",
                description=(
                    "UTXO set as AW-Map (txid:vout → UTXO data). Add-wins: "
                    "a UTXO received by one agent is preserved even if another "
                    "agent has a stale view. Spending is a remove; two concurrent "
                    "removes of the same UTXO produce a double-spend warning at "
                    "the OT layer."
                ),
                crdt_type="aw_map",
                independent_merge=True,
            ),
            CRDTDimensionSpec(
                name="labels",
                description=(
                    "Address labels as OR-Set. Concurrent label additions from "
                    "any agent win over removes. An agent labeling an address "
                    "'cold storage' is never silently overwritten by a concurrent "
                    "remove from another agent."
                ),
                crdt_type="or_set",
                independent_merge=True,
            ),
            CRDTDimensionSpec(
                name="channels",
                description=(
                    "Lightning channels as AW-Map (channel_id → state JSON). "
                    "Add-wins: a new channel opened by one agent is preserved "
                    "under concurrent state from other agents."
                ),
                crdt_type="aw_map",
                independent_merge=True,
            ),
            CRDTDimensionSpec(
                name="strategy",
                description=(
                    "Agent strategy as AW-Map (field_name → value). Each config "
                    "field is an independent LWW register (via token ordering). "
                    "One agent changing max_fee_rate never conflicts with another "
                    "agent changing dca_amount_sat."
                ),
                crdt_type="aw_map",
                independent_merge=False,
            ),
            CRDTDimensionSpec(
                name="transactions",
                description=(
                    "Confirmed transaction IDs as OR-Set. Append-only: once a "
                    "txid is added it is never legitimately removed. Any agent "
                    "that observes a confirmation adds it; the join is the union."
                ),
                crdt_type="or_set",
                independent_merge=True,
            ),
            CRDTDimensionSpec(
                name="mempool",
                description=(
                    "Pending txids as OR-Set. Volatile: txids are added when "
                    "seen in the mempool and removed when confirmed or evicted. "
                    "The join gives every agent the union of all pending txids "
                    "seen across the fleet."
                ),
                crdt_type="or_set",
                independent_merge=True,
            ),
        ]

    def join(
        self,
        a: CRDTSnapshotManifest,
        b: CRDTSnapshotManifest,
    ) -> CRDTSnapshotManifest:
        """Convergent join of two Bitcoin CRDT snapshots.

        Joins each dimension independently using its declared CRDT primitive.
        This operation is commutative, associative, and idempotent — any two
        agents that have received the same set of writes converge to identical
        state regardless of delivery order.

        The file manifest is rebuilt from the joined ``files_manifest`` AW-Map
        so that the core engine's content-addressed store remains consistent.

        Args:
            a: First CRDT snapshot manifest.
            b: Second CRDT snapshot manifest.

        Returns:
            The lattice join — a new ``CRDTSnapshotManifest`` that is the
            least upper bound of *a* and *b*.
        """
        vc_a = VectorClock.from_dict(a["vclock"])
        vc_b = VectorClock.from_dict(b["vclock"])
        merged_vc = vc_a.merge(vc_b)

        files_a = _load_aw(a["crdt_state"], "files_manifest")
        files_b = _load_aw(b["crdt_state"], "files_manifest")
        merged_files_map = files_a.join(files_b)

        utxos_a = _load_aw(a["crdt_state"], "utxos")
        utxos_b = _load_aw(b["crdt_state"], "utxos")
        merged_utxos = utxos_a.join(utxos_b)

        labels_a = _load_or(a["crdt_state"], "labels")
        labels_b = _load_or(b["crdt_state"], "labels")
        merged_labels = labels_a.join(labels_b)

        channels_a = _load_aw(a["crdt_state"], "channels")
        channels_b = _load_aw(b["crdt_state"], "channels")
        merged_channels = channels_a.join(channels_b)

        strategy_a = _load_aw(a["crdt_state"], "strategy")
        strategy_b = _load_aw(b["crdt_state"], "strategy")
        merged_strategy = strategy_a.join(strategy_b)

        txns_a = _load_or(a["crdt_state"], "transactions")
        txns_b = _load_or(b["crdt_state"], "transactions")
        merged_txns = txns_a.join(txns_b)

        mempool_a = _load_or(a["crdt_state"], "mempool")
        mempool_b = _load_or(b["crdt_state"], "mempool")
        merged_mempool = mempool_a.join(mempool_b)

        merged_files = merged_files_map.to_plain_dict()

        crdt_state: dict[str, str] = {
            "files_manifest": json.dumps(merged_files_map.to_dict()),
            "utxos": json.dumps(merged_utxos.to_dict()),
            "labels": json.dumps(merged_labels.to_dict()),
            "channels": json.dumps(merged_channels.to_dict()),
            "strategy": json.dumps(merged_strategy.to_dict()),
            "transactions": json.dumps(merged_txns.to_dict()),
            "mempool": json.dumps(merged_mempool.to_dict()),
        }

        return CRDTSnapshotManifest(
            files=merged_files,
            domain=_DOMAIN_NAME,
            vclock=merged_vc.to_dict(),
            crdt_state=crdt_state,
            schema_version=1,
        )

    def to_crdt_state(self, snapshot: StateSnapshot) -> CRDTSnapshotManifest:
        """Lift a plain snapshot into CRDT state representation.

        Initialises the ``files_manifest`` AW-Map from the snapshot's ``files``
        dict. All domain-specific CRDT dimensions start empty and are populated
        lazily as agents commit content.

        Args:
            snapshot: A plain ``StateSnapshot`` to lift.

        Returns:
            A ``CRDTSnapshotManifest`` with the snapshot's files and empty
            per-dimension CRDT metadata.
        """
        files_map = AWMap()
        for path, content_hash in snapshot["files"].items():
            files_map = files_map.set(path, content_hash)

        empty_aw = json.dumps(_EMPTY_AW)
        empty_or = json.dumps(_EMPTY_OR)

        crdt_state: dict[str, str] = {
            "files_manifest": json.dumps(files_map.to_dict()),
            "utxos": empty_aw,
            "labels": empty_or,
            "channels": empty_aw,
            "strategy": empty_aw,
            "transactions": empty_or,
            "mempool": empty_or,
        }

        return CRDTSnapshotManifest(
            files=snapshot["files"],
            domain=_DOMAIN_NAME,
            vclock=VectorClock().to_dict(),
            crdt_state=crdt_state,
            schema_version=1,
        )

    def from_crdt_state(self, crdt: CRDTSnapshotManifest) -> StateSnapshot:
        """Materialise a CRDT manifest back into a plain snapshot.

        Extracts the visible (non-tombstoned) file manifest from the
        ``files_manifest`` AW-Map. Used by ``muse show`` and CLI commands
        that need a standard ``StateSnapshot`` view.

        Args:
            crdt: A ``CRDTSnapshotManifest`` to materialise.

        Returns:
            A plain ``SnapshotManifest`` with the current visible files.
        """
        files_map = _load_aw(crdt["crdt_state"], "files_manifest")
        visible = files_map.to_plain_dict()
        files = visible if visible else crdt["files"]
        return SnapshotManifest(files=files, domain=_DOMAIN_NAME)


# ---------------------------------------------------------------------------
# Delta summary helper
# ---------------------------------------------------------------------------


def _delta_summary(ops: list[DomainOp]) -> str:
    """Produce a concise human-readable summary of a delta's operations."""
    if not ops:
        return "working tree clean"

    counts: dict[str, int] = {"insert": 0, "delete": 0, "replace": 0, "patch": 0, "mutate": 0}
    for op in ops:
        op_type = op["op"]
        if op_type in counts:
            counts[op_type] += 1

    parts: list[str] = []
    if counts["insert"]:
        parts.append(f"{counts['insert']} added")
    if counts["delete"]:
        parts.append(f"{counts['delete']} removed")
    if counts["patch"]:
        parts.append(f"{counts['patch']} updated")
    if counts["mutate"]:
        parts.append(f"{counts['mutate']} mutated")
    if counts["replace"]:
        parts.append(f"{counts['replace']} replaced")
    return ", ".join(parts) if parts else "changes present"

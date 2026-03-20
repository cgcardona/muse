"""Bitcoin domain TypedDicts — the complete multidimensional state model.

Every piece of Bitcoin state that Muse versions is expressed as one of these
TypedDicts. Private keys are explicitly excluded — the plugin is watch-only
by design. Signers live outside Muse; Muse versions your *relationship* with
the chain, not your secrets.

Dimensional layout
------------------
+--------------------------+-------------------------------------+-------------------+
| Workdir path             | Content TypedDict                   | CRDT primitive    |
+==========================+=====================================+===================+
| wallet/utxos.json        | list[UTXORecord]                    | AWMap (add-wins)  |
| wallet/transactions.json | list[TransactionRecord]             | ORSet (append)    |
| wallet/labels.json       | list[AddressLabelRecord]            | ORSet (add-wins)  |
| wallet/descriptors.json  | list[DescriptorRecord]              | AWMap             |
| channels/channels.json   | list[LightningChannelRecord]        | AWMap (add-wins)  |
| channels/routing.json    | list[RoutingPolicyRecord]           | AWMap             |
| strategy/agent.json      | AgentStrategyRecord                 | AWMap (LWW keys)  |
| strategy/execution.json  | list[ExecutionEventRecord]          | ORSet (append)    |
| oracles/prices.json      | list[OraclePriceTickRecord]         | RGA (time-series) |
| oracles/fees.json        | list[FeeEstimateRecord]             | RGA (time-series) |
| network/peers.json       | list[NetworkPeerRecord]             | AWMap             |
| network/mempool.json     | list[PendingTxRecord]               | ORSet (volatile)  |
+--------------------------+-------------------------------------+-------------------+
"""

from __future__ import annotations

from typing import Literal, TypedDict

# ---------------------------------------------------------------------------
# Shared Literal types
# ---------------------------------------------------------------------------

ScriptType = Literal[
    "p2pkh", "p2sh", "p2wpkh", "p2wsh", "p2tr", "op_return", "unknown"
]

CoinCategory = Literal[
    "income", "expense", "internal", "exchange", "fee", "unknown"
]

CoinSelectAlgo = Literal[
    "largest_first", "smallest_first", "branch_and_bound", "random"
]

DescriptorScriptType = Literal["p2wpkh", "p2sh-p2wpkh", "p2tr", "p2pkh"]

ExecutionEventType = Literal[
    "dca_buy",
    "fee_bump",
    "channel_open",
    "channel_close",
    "rebalance",
    "consolidation",
    "custom",
]

# ---------------------------------------------------------------------------
# UTXO dimension
# ---------------------------------------------------------------------------


class UTXORecord(TypedDict):
    """A single unspent transaction output tracked by this wallet.

    ``txid`` and ``vout`` together form the canonical UTXO identity used as
    the key in diff/merge operations (``"{txid}:{vout}"``).  ``amount_sat``
    is in satoshis — never floating-point BTC — to avoid rounding errors.
    ``coinbase`` marks mining rewards subject to the 100-block maturity rule.
    ``label`` is a human annotation; structured labels live in
    ``AddressLabelRecord``.
    """

    txid: str
    vout: int
    amount_sat: int
    script_type: ScriptType
    address: str
    confirmations: int
    block_height: int | None
    coinbase: bool
    label: str | None


# ---------------------------------------------------------------------------
# Transaction dimension
# ---------------------------------------------------------------------------


class TxInputRecord(TypedDict):
    """One input in a transaction.

    ``amount_sat`` is ``None`` when the UTXO being spent is not tracked by
    this wallet (external input in a collaborative transaction).
    """

    txid: str
    vout: int
    amount_sat: int | None


class TxOutputRecord(TypedDict):
    """One output in a transaction."""

    vout: int
    amount_sat: int
    address: str
    script_type: ScriptType
    spent: bool


class TransactionRecord(TypedDict):
    """A confirmed or pending Bitcoin transaction.

    ``block_height`` and ``block_time`` are ``None`` for mempool transactions.
    ``fee_sat`` is the miner fee.  ``weight`` is in weight units (WU);
    ``size_bytes`` is the stripped byte size.
    ``label`` is a human annotation for this transaction.
    """

    txid: str
    block_height: int | None
    block_time: int | None
    inputs: list[TxInputRecord]
    outputs: list[TxOutputRecord]
    fee_sat: int
    size_bytes: int
    weight: int
    confirmed: bool
    label: str | None


# ---------------------------------------------------------------------------
# Descriptor / wallet dimension
# ---------------------------------------------------------------------------


class DescriptorRecord(TypedDict):
    """A watch-only wallet descriptor — xpub only, never xpriv.

    ``id`` is a stable UUID assigned at import time and never changes, even
    if the label or gap_limit is updated — this is the entity identity for
    MutateOp tracking.  ``derivation_path`` follows BIP-32 notation, e.g.
    ``"m/84'/0'/0'"``.
    """

    id: str
    xpub: str
    script_type: DescriptorScriptType
    derivation_path: str
    label: str | None
    gap_limit: int


# ---------------------------------------------------------------------------
# Address label dimension
# ---------------------------------------------------------------------------


class AddressLabelRecord(TypedDict):
    """A semantic annotation attached to a Bitcoin address.

    ``category`` enables the wallet to auto-categorize inflows and outflows.
    ``created_at`` is a Unix timestamp in seconds.  Labels are CRDT OR-Set
    elements: concurrent additions from multiple agents always win.
    """

    address: str
    label: str
    category: CoinCategory
    created_at: int


# ---------------------------------------------------------------------------
# Lightning Network dimensions
# ---------------------------------------------------------------------------


class LightningChannelRecord(TypedDict):
    """State snapshot of a Lightning payment channel.

    ``channel_id`` is the 8-byte short channel ID in decimal string form
    (``"{block_height}x{tx_index}x{output_index}"``).  ``capacity_sat`` is
    fixed at channel open; ``local_balance_sat`` and ``remote_balance_sat``
    change with every payment.  ``htlc_count`` is the number of in-flight
    HTLCs at snapshot time.
    """

    channel_id: str
    peer_pubkey: str
    peer_alias: str | None
    capacity_sat: int
    local_balance_sat: int
    remote_balance_sat: int
    is_active: bool
    is_public: bool
    local_reserve_sat: int
    remote_reserve_sat: int
    unsettled_balance_sat: int
    htlc_count: int


class RoutingPolicyRecord(TypedDict):
    """Fee and HTLC policy for a Lightning channel.

    ``fee_rate_ppm`` is parts-per-million.  ``time_lock_delta`` is the CLTV
    delta in blocks.  ``base_fee_msat`` and HTLCs are in millisatoshis.
    """

    channel_id: str
    base_fee_msat: int
    fee_rate_ppm: int
    time_lock_delta: int
    min_htlc_msat: int
    max_htlc_msat: int


# ---------------------------------------------------------------------------
# Strategy / agent configuration dimension
# ---------------------------------------------------------------------------


class AgentStrategyRecord(TypedDict):
    """Agent DCA, fee, and channel management strategy configuration.

    ``simulation_mode`` gates all state-mutating actions — the agent records
    what it *would* do without broadcasting anything to the network.  This
    is how strategy branches work: simulate on a branch, merge if profitable.

    ``lightning_rebalance_threshold`` is a ratio [0.0, 1.0]: if the local
    channel balance falls below this fraction of capacity, the agent triggers
    a circular rebalance.
    """

    name: str
    max_fee_rate_sat_vbyte: int
    min_confirmations: int
    utxo_consolidation_threshold: int
    dca_amount_sat: int | None
    dca_interval_blocks: int | None
    lightning_rebalance_threshold: float
    coin_selection: CoinSelectAlgo
    simulation_mode: bool


class ExecutionEventRecord(TypedDict):
    """A single agent decision event, written append-only by the agent.

    ``txid`` is ``None`` for non-transaction events (e.g. a rebalance that
    failed to find a route).  ``amount_sat`` and ``fee_sat`` are ``None``
    when not applicable (e.g. a failed routing attempt).
    """

    timestamp: int
    block_height: int | None
    event_type: ExecutionEventType
    txid: str | None
    amount_sat: int | None
    fee_sat: int | None
    note: str


# ---------------------------------------------------------------------------
# Oracle dimensions
# ---------------------------------------------------------------------------


class OraclePriceTickRecord(TypedDict):
    """A single BTC/USD price observation from an oracle.

    ``block_height`` anchors the price to a specific block when available.
    ``source`` is a human-readable identifier (e.g. ``"coinbase"``,
    ``"kraken"``, ``"bisq-dex"``).  Prices are always USD floats; other
    currencies are converted at capture time.
    """

    timestamp: int
    block_height: int | None
    price_usd: float
    source: str


class FeeEstimateRecord(TypedDict):
    """Mempool fee surface snapshot at a point in time.

    All values are in sat/vbyte, rounded to the nearest integer. Targets
    correspond to the number of blocks within which a transaction should
    confirm: 1 block (next block), 6 blocks (~1 hour), 144 blocks (~1 day).
    """

    timestamp: int
    block_height: int | None
    target_1_block_sat_vbyte: int
    target_6_block_sat_vbyte: int
    target_144_block_sat_vbyte: int


# ---------------------------------------------------------------------------
# Network dimension
# ---------------------------------------------------------------------------


class NetworkPeerRecord(TypedDict):
    """A known peer in the Bitcoin P2P network.

    ``sync_height`` is the last block height reported by this peer.  A ``None``
    value means the peer is known but has never successfully synced with us.
    """

    pubkey: str
    alias: str | None
    address: str
    connected: bool
    sync_height: int | None


class PendingTxRecord(TypedDict):
    """A transaction in the local mempool awaiting confirmation.

    ``rbf_eligible`` means the transaction signals Replace-By-Fee (BIP 125).
    ``cpfp_eligible`` means a child transaction could be used to boost the
    effective fee rate of this parent.
    """

    txid: str
    amount_sat: int
    fee_sat: int
    fee_rate_sat_vbyte: float
    size_bytes: int
    rbf_eligible: bool
    cpfp_eligible: bool
    inputs: list[TxInputRecord]
    outputs: list[TxOutputRecord]

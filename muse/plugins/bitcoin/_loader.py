"""Bitcoin domain data loaders — typed I/O bridge between object store and analytics.

Every Tier 3 ``muse bitcoin …`` command calls functions here to obtain typed
Bitcoin state, either from the live working tree or from a historical commit's
content-addressed snapshot.  No analytics live here — pure I/O only.

Pattern mirrors ``muse.plugins.midi._query.load_track`` / ``load_track_from_workdir``.
"""

from __future__ import annotations

import json
import logging
import pathlib

from muse.core.object_store import read_object
from muse.core.store import get_commit_snapshot_manifest
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

# Canonical workdir paths (mirrors the schema declared in plugin.py)
_PATH_UTXOS        = "wallet/utxos.json"
_PATH_TRANSACTIONS = "wallet/transactions.json"
_PATH_LABELS       = "wallet/labels.json"
_PATH_DESCRIPTORS  = "wallet/descriptors.json"
_PATH_STRATEGY     = "strategy/agent.json"
_PATH_EXECUTION    = "strategy/execution.json"
_PATH_PRICES       = "oracles/prices.json"
_PATH_FEES         = "oracles/fees.json"
_PATH_PEERS        = "network/peers.json"
_PATH_MEMPOOL      = "network/mempool.json"
_PATH_CHANNELS     = "channels/channels.json"
_PATH_ROUTING      = "channels/routing.json"


# ---------------------------------------------------------------------------
# Internal helpers — return raw bytes only, no JSON parsing
# ---------------------------------------------------------------------------


def _get_blob(root: pathlib.Path, manifest: dict[str, str], path: str) -> bytes | None:
    """Return raw bytes for *path* from the object store, or ``None``."""
    oid = manifest.get(path)
    if oid is None:
        return None
    raw = read_object(root, oid)
    if raw is None:
        logger.debug("bitcoin loader: blob missing for %s", path)
    return raw


def _get_workdir_bytes(root: pathlib.Path, rel_path: str) -> bytes | None:
    """Return raw bytes from the live working tree, or ``None``."""
    p = root / rel_path
    if not p.exists():
        return None
    try:
        return p.read_bytes()
    except OSError as exc:
        logger.debug("bitcoin loader: workdir read error for %s: %s", rel_path, exc)
        return None


# ---------------------------------------------------------------------------
# Commit-based loaders (historical snapshot)
#
# json.loads() returns Any in typeshed.  Returning Any from a typed function
# is valid in mypy — the type flows through without needing type: ignore.
# ---------------------------------------------------------------------------


def load_utxos(root: pathlib.Path, commit_id: str) -> list[UTXORecord]:
    """Load ``wallet/utxos.json`` from the snapshot at *commit_id*."""
    manifest = get_commit_snapshot_manifest(root, commit_id) or {}
    raw = _get_blob(root, manifest, _PATH_UTXOS)
    if raw is None:
        return []
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except json.JSONDecodeError as exc:
        logger.debug("bitcoin loader: JSON error for %s: %s", _PATH_UTXOS, exc)
        return []


def load_transactions(root: pathlib.Path, commit_id: str) -> list[TransactionRecord]:
    """Load ``wallet/transactions.json`` from the snapshot at *commit_id*."""
    manifest = get_commit_snapshot_manifest(root, commit_id) or {}
    raw = _get_blob(root, manifest, _PATH_TRANSACTIONS)
    if raw is None:
        return []
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except json.JSONDecodeError as exc:
        logger.debug("bitcoin loader: JSON error for %s: %s", _PATH_TRANSACTIONS, exc)
        return []


def load_labels(root: pathlib.Path, commit_id: str) -> list[AddressLabelRecord]:
    """Load ``wallet/labels.json`` from the snapshot at *commit_id*."""
    manifest = get_commit_snapshot_manifest(root, commit_id) or {}
    raw = _get_blob(root, manifest, _PATH_LABELS)
    if raw is None:
        return []
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except json.JSONDecodeError as exc:
        logger.debug("bitcoin loader: JSON error for %s: %s", _PATH_LABELS, exc)
        return []


def load_descriptors(root: pathlib.Path, commit_id: str) -> list[DescriptorRecord]:
    """Load ``wallet/descriptors.json`` from the snapshot at *commit_id*."""
    manifest = get_commit_snapshot_manifest(root, commit_id) or {}
    raw = _get_blob(root, manifest, _PATH_DESCRIPTORS)
    if raw is None:
        return []
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except json.JSONDecodeError as exc:
        logger.debug("bitcoin loader: JSON error for %s: %s", _PATH_DESCRIPTORS, exc)
        return []


def load_strategy(root: pathlib.Path, commit_id: str) -> AgentStrategyRecord | None:
    """Load ``strategy/agent.json`` from the snapshot at *commit_id*.

    Returns ``None`` when the file is absent (no strategy configured yet).
    json.loads returns Any, which satisfies AgentStrategyRecord | None without narrowing.
    """
    manifest = get_commit_snapshot_manifest(root, commit_id) or {}
    raw = _get_blob(root, manifest, _PATH_STRATEGY)
    if raw is None:
        return None
    try:
        data = json.loads(raw)
        return data if data else None
    except json.JSONDecodeError as exc:
        logger.debug("bitcoin loader: JSON error for %s: %s", _PATH_STRATEGY, exc)
        return None


def load_execution_log(root: pathlib.Path, commit_id: str) -> list[ExecutionEventRecord]:
    """Load ``strategy/execution.json`` from the snapshot at *commit_id*."""
    manifest = get_commit_snapshot_manifest(root, commit_id) or {}
    raw = _get_blob(root, manifest, _PATH_EXECUTION)
    if raw is None:
        return []
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except json.JSONDecodeError as exc:
        logger.debug("bitcoin loader: JSON error for %s: %s", _PATH_EXECUTION, exc)
        return []


def load_prices(root: pathlib.Path, commit_id: str) -> list[OraclePriceTickRecord]:
    """Load ``oracles/prices.json`` from the snapshot at *commit_id*."""
    manifest = get_commit_snapshot_manifest(root, commit_id) or {}
    raw = _get_blob(root, manifest, _PATH_PRICES)
    if raw is None:
        return []
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except json.JSONDecodeError as exc:
        logger.debug("bitcoin loader: JSON error for %s: %s", _PATH_PRICES, exc)
        return []


def load_fees(root: pathlib.Path, commit_id: str) -> list[FeeEstimateRecord]:
    """Load ``oracles/fees.json`` from the snapshot at *commit_id*."""
    manifest = get_commit_snapshot_manifest(root, commit_id) or {}
    raw = _get_blob(root, manifest, _PATH_FEES)
    if raw is None:
        return []
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except json.JSONDecodeError as exc:
        logger.debug("bitcoin loader: JSON error for %s: %s", _PATH_FEES, exc)
        return []


def load_mempool(root: pathlib.Path, commit_id: str) -> list[PendingTxRecord]:
    """Load ``network/mempool.json`` from the snapshot at *commit_id*."""
    manifest = get_commit_snapshot_manifest(root, commit_id) or {}
    raw = _get_blob(root, manifest, _PATH_MEMPOOL)
    if raw is None:
        return []
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except json.JSONDecodeError as exc:
        logger.debug("bitcoin loader: JSON error for %s: %s", _PATH_MEMPOOL, exc)
        return []


def load_peers(root: pathlib.Path, commit_id: str) -> list[NetworkPeerRecord]:
    """Load ``network/peers.json`` from the snapshot at *commit_id*."""
    manifest = get_commit_snapshot_manifest(root, commit_id) or {}
    raw = _get_blob(root, manifest, _PATH_PEERS)
    if raw is None:
        return []
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except json.JSONDecodeError as exc:
        logger.debug("bitcoin loader: JSON error for %s: %s", _PATH_PEERS, exc)
        return []


def load_channels(root: pathlib.Path, commit_id: str) -> list[LightningChannelRecord]:
    """Load ``channels/channels.json`` from the snapshot at *commit_id*."""
    manifest = get_commit_snapshot_manifest(root, commit_id) or {}
    raw = _get_blob(root, manifest, _PATH_CHANNELS)
    if raw is None:
        return []
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except json.JSONDecodeError as exc:
        logger.debug("bitcoin loader: JSON error for %s: %s", _PATH_CHANNELS, exc)
        return []


def load_routing(root: pathlib.Path, commit_id: str) -> list[RoutingPolicyRecord]:
    """Load ``channels/routing.json`` from the snapshot at *commit_id*."""
    manifest = get_commit_snapshot_manifest(root, commit_id) or {}
    raw = _get_blob(root, manifest, _PATH_ROUTING)
    if raw is None:
        return []
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except json.JSONDecodeError as exc:
        logger.debug("bitcoin loader: JSON error for %s: %s", _PATH_ROUTING, exc)
        return []


# ---------------------------------------------------------------------------
# Workdir loaders (live working tree)
# ---------------------------------------------------------------------------


def load_utxos_from_workdir(root: pathlib.Path) -> list[UTXORecord]:
    """Load ``wallet/utxos.json`` from the live working tree."""
    raw = _get_workdir_bytes(root, _PATH_UTXOS)
    if raw is None:
        return []
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


def load_transactions_from_workdir(root: pathlib.Path) -> list[TransactionRecord]:
    """Load ``wallet/transactions.json`` from the live working tree."""
    raw = _get_workdir_bytes(root, _PATH_TRANSACTIONS)
    if raw is None:
        return []
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


def load_labels_from_workdir(root: pathlib.Path) -> list[AddressLabelRecord]:
    """Load ``wallet/labels.json`` from the live working tree."""
    raw = _get_workdir_bytes(root, _PATH_LABELS)
    if raw is None:
        return []
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


def load_strategy_from_workdir(root: pathlib.Path) -> AgentStrategyRecord | None:
    """Load ``strategy/agent.json`` from the live working tree.

    json.loads returns Any, which satisfies AgentStrategyRecord | None without narrowing.
    """
    raw = _get_workdir_bytes(root, _PATH_STRATEGY)
    if raw is None:
        return None
    try:
        data = json.loads(raw)
        return data if data else None
    except json.JSONDecodeError:
        return None


def load_execution_log_from_workdir(root: pathlib.Path) -> list[ExecutionEventRecord]:
    """Load ``strategy/execution.json`` from the live working tree."""
    raw = _get_workdir_bytes(root, _PATH_EXECUTION)
    if raw is None:
        return []
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


def load_prices_from_workdir(root: pathlib.Path) -> list[OraclePriceTickRecord]:
    """Load ``oracles/prices.json`` from the live working tree."""
    raw = _get_workdir_bytes(root, _PATH_PRICES)
    if raw is None:
        return []
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


def load_fees_from_workdir(root: pathlib.Path) -> list[FeeEstimateRecord]:
    """Load ``oracles/fees.json`` from the live working tree."""
    raw = _get_workdir_bytes(root, _PATH_FEES)
    if raw is None:
        return []
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


def load_mempool_from_workdir(root: pathlib.Path) -> list[PendingTxRecord]:
    """Load ``network/mempool.json`` from the live working tree."""
    raw = _get_workdir_bytes(root, _PATH_MEMPOOL)
    if raw is None:
        return []
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


# ---------------------------------------------------------------------------
# Repo metadata helpers (shared by all commands)
# ---------------------------------------------------------------------------


def read_repo_id(root: pathlib.Path) -> str:
    """Return the repo UUID from ``.muse/repo.json``."""
    return str(json.loads((root / ".muse" / "repo.json").read_text())["repo_id"])



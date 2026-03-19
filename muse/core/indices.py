"""Optional local index layer for Muse repositories.

Indexes live under ``.muse/indices/`` and are derived, versioned, and fully
rebuildable from the commit history.  **No index is required for repository
correctness** — all commands work without them; indexes only accelerate
repeated queries.

Available indexes
-----------------

``symbol_history``
    Maps symbol addresses to their event timeline across all commits.
    Enables O(1) ``muse symbol-log``, ``muse lineage``, and ``muse query-history``
    instead of O(commits × files) scans.

    Schema v1::

        {
          "schema_version": 1,
          "index": "symbol_history",
          "updated_at": "2026-03-18T12:00:00+00:00",
          "entries": {
            "src/billing.py::compute_total": [
              {
                "commit_id": "<sha256>",
                "committed_at": "2026-01-01T00:00:00+00:00",
                "op": "insert",
                "content_id": "<sha256>",
                "body_hash": "<sha256>",
                "signature_id": "<sha256>"
              },
              ...
            ],
            ...
          }
        }

``hash_occurrence``
    Maps ``body_hash`` values to the list of symbol addresses that share them.
    Enables O(1) ``muse clones`` and ``muse find-symbol hash=``.

    Schema v1::

        {
          "schema_version": 1,
          "index": "hash_occurrence",
          "updated_at": "2026-03-18T12:00:00+00:00",
          "entries": {
            "<body_hash>": ["src/billing.py::compute_total", ...]
          }
        }

Rebuild
-------

Indexes are rebuilt by ``muse index rebuild``.  They can also be built
incrementally: the ``update_*_index`` functions accept an existing index
dict and patch it rather than rebuilding from scratch.
"""

import datetime
import json
import logging
import pathlib

logger = logging.getLogger(__name__)

_INDICES_DIR = pathlib.PurePosixPath(".muse") / "indices"

_SCHEMA_VERSION = 1


# ---------------------------------------------------------------------------
# Typed index entry shapes (TypedDicts)
# ---------------------------------------------------------------------------


class SymbolHistoryEntry:
    """One event in a symbol's history timeline."""

    __slots__ = (
        "commit_id", "committed_at", "op",
        "content_id", "body_hash", "signature_id",
    )

    def __init__(
        self,
        commit_id: str,
        committed_at: str,
        op: str,
        content_id: str,
        body_hash: str,
        signature_id: str,
    ) -> None:
        self.commit_id = commit_id
        self.committed_at = committed_at
        self.op = op
        self.content_id = content_id
        self.body_hash = body_hash
        self.signature_id = signature_id

    def to_dict(self) -> dict[str, str]:
        return {
            "commit_id": self.commit_id,
            "committed_at": self.committed_at,
            "op": self.op,
            "content_id": self.content_id,
            "body_hash": self.body_hash,
            "signature_id": self.signature_id,
        }

    @classmethod
    def from_dict(cls, d: dict[str, str]) -> "SymbolHistoryEntry":
        return cls(
            commit_id=d["commit_id"],
            committed_at=d["committed_at"],
            op=d["op"],
            content_id=d["content_id"],
            body_hash=d["body_hash"],
            signature_id=d["signature_id"],
        )


# ---------------------------------------------------------------------------
# Index I/O helpers
# ---------------------------------------------------------------------------


def _indices_dir(root: pathlib.Path) -> pathlib.Path:
    return root / ".muse" / "indices"


def _index_path(root: pathlib.Path, name: str) -> pathlib.Path:
    return _indices_dir(root) / f"{name}.json"


def _ensure_dir(root: pathlib.Path) -> None:
    _indices_dir(root).mkdir(parents=True, exist_ok=True)


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Symbol history index
# ---------------------------------------------------------------------------


SymbolHistoryIndex = dict[str, list[SymbolHistoryEntry]]


def load_symbol_history(root: pathlib.Path) -> SymbolHistoryIndex:
    """Load the symbol history index, returning an empty dict if absent."""
    path = _index_path(root, "symbol_history")
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text())
        result: SymbolHistoryIndex = {}
        for address, entries in raw.get("entries", {}).items():
            result[address] = [SymbolHistoryEntry.from_dict(e) for e in entries]
        return result
    except (json.JSONDecodeError, KeyError) as exc:
        logger.warning("⚠️ Corrupt symbol_history index: %s — returning empty", exc)
        return {}


def save_symbol_history(root: pathlib.Path, index: SymbolHistoryIndex) -> None:
    """Persist the symbol history index."""
    _ensure_dir(root)
    path = _index_path(root, "symbol_history")
    entries: dict[str, list[dict[str, str]]] = {
        addr: [e.to_dict() for e in evts]
        for addr, evts in sorted(index.items())
    }
    data = {
        "schema_version": _SCHEMA_VERSION,
        "index": "symbol_history",
        "updated_at": _now_iso(),
        "entries": entries,
    }
    path.write_text(json.dumps(data, indent=2) + "\n")
    logger.debug("✅ Saved symbol_history index (%d addresses)", len(index))


# ---------------------------------------------------------------------------
# Hash occurrence index
# ---------------------------------------------------------------------------


HashOccurrenceIndex = dict[str, list[str]]


def load_hash_occurrence(root: pathlib.Path) -> HashOccurrenceIndex:
    """Load the hash occurrence index, returning an empty dict if absent."""
    path = _index_path(root, "hash_occurrence")
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text())
        result: HashOccurrenceIndex = {}
        for body_hash, addresses in raw.get("entries", {}).items():
            result[body_hash] = list(addresses)
        return result
    except (json.JSONDecodeError, KeyError) as exc:
        logger.warning("⚠️ Corrupt hash_occurrence index: %s — returning empty", exc)
        return {}


def save_hash_occurrence(root: pathlib.Path, index: HashOccurrenceIndex) -> None:
    """Persist the hash occurrence index."""
    _ensure_dir(root)
    path = _index_path(root, "hash_occurrence")
    data = {
        "schema_version": _SCHEMA_VERSION,
        "index": "hash_occurrence",
        "updated_at": _now_iso(),
        "entries": {h: sorted(addrs) for h, addrs in sorted(index.items())},
    }
    path.write_text(json.dumps(data, indent=2) + "\n")
    logger.debug("✅ Saved hash_occurrence index (%d hashes)", len(index))


# ---------------------------------------------------------------------------
# Index metadata
# ---------------------------------------------------------------------------


def index_info(root: pathlib.Path) -> list[dict[str, str]]:
    """Return status information about all known indexes."""
    names = ["symbol_history", "hash_occurrence"]
    result: list[dict[str, str]] = []
    for name in names:
        path = _index_path(root, name)
        if path.exists():
            try:
                raw = json.loads(path.read_text())
                updated_at = raw.get("updated_at", "unknown")
                entries = len(raw.get("entries", {}))
                result.append({
                    "name": name,
                    "status": "present",
                    "updated_at": updated_at,
                    "entries": str(entries),
                })
            except (json.JSONDecodeError, KeyError):
                result.append({"name": name, "status": "corrupt", "updated_at": "", "entries": "0"})
        else:
            result.append({"name": name, "status": "absent", "updated_at": "", "entries": "0"})
    return result

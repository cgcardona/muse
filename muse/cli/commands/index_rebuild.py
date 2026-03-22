"""muse index — manage and rebuild the optional local index layer.

Indexes live under ``.muse/indices/`` and are fully derived from the commit
history.  They are optional — all commands work without them, but indexes
dramatically accelerate repeated queries on large repositories.

Available indexes
-----------------

``symbol_history``
    Maps every symbol address to its full event timeline across all commits.
    Reduces ``muse symbol-log``, ``muse lineage``, and ``muse query-history``
    from O(commits × files) to O(1) lookups.

``hash_occurrence``
    Maps every ``body_hash`` to the list of addresses that share it.
    Reduces ``muse clones`` and ``muse find-symbol hash=`` to O(1).

Sub-commands
------------

``muse index status``
    Show the status, entry count, and last-updated time of each index.

``muse index rebuild [--index NAME]``
    Rebuild one or all indexes by walking the entire commit history.
    Safe to run multiple times.

Usage::

    muse index status
    muse index status --json
    muse index rebuild
    muse index rebuild --json
    muse index rebuild --index symbol_history
    muse index rebuild --index hash_occurrence

JSON output — ``muse index status --json``::

    [
      {"name": "symbol_history", "status": "present", "entries": 1024,
       "updated_at": "2026-03-21T12:00:00"},
      {"name": "hash_occurrence", "status": "absent",  "entries": 0,
       "updated_at": null}
    ]

JSON output — ``muse index rebuild --json``::

    {"rebuilt": ["symbol_history", "hash_occurrence"],
     "symbol_history_addresses": 512, "symbol_history_events": 2048,
     "hash_occurrence_clusters": 31,  "hash_occurrence_addresses": 87}
"""

from __future__ import annotations

import argparse
import json
import logging
import pathlib
import sys

from muse.core.errors import ExitCode
from muse.core.indices import (
    HashOccurrenceIndex,
    SymbolHistoryEntry,
    SymbolHistoryIndex,
    index_info,
    load_hash_occurrence,
    load_symbol_history,
    save_hash_occurrence,
    save_symbol_history,
)
from muse.core.object_store import read_object
from muse.core.repo import require_repo
from muse.core.store import get_all_commits, get_commit_snapshot_manifest, read_current_branch
from muse.plugins.code._query import is_semantic
from muse.plugins.code.ast_parser import parse_symbols

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Index build logic
# ---------------------------------------------------------------------------


def _build_symbol_history(root: pathlib.Path) -> SymbolHistoryIndex:
    """Walk all commits oldest-first and build the symbol history index."""
    all_commits = sorted(
        get_all_commits(root),
        key=lambda c: c.committed_at,
    )
    index: SymbolHistoryIndex = {}

    for commit in all_commits:
        if commit.structured_delta is None:
            continue
        committed_at = commit.committed_at.isoformat()
        ops = commit.structured_delta.get("ops", [])

        for op in ops:
            if op["op"] != "patch":
                continue
            for child in op.get("child_ops", []):
                addr = child["address"]
                if "::" not in addr:
                    continue
                file_path = addr.split("::")[0]
                if not is_semantic(file_path):
                    continue
                child_op = child["op"]
                if child_op not in ("insert", "delete", "replace"):
                    continue

                # Extract hash fields: need to re-parse the snapshot blob for
                # body_hash and signature_id (not stored in the delta).
                manifest = get_commit_snapshot_manifest(root, commit.commit_id) or {}
                obj_id = manifest.get(file_path)
                body_hash = ""
                signature_id = ""
                content_id = ""
                if obj_id:
                    raw = read_object(root, obj_id)
                    if raw:
                        tree = parse_symbols(raw, file_path)
                        rec = tree.get(addr)
                        if rec:
                            body_hash = rec["body_hash"]
                            signature_id = rec["signature_id"]
                            content_id = rec["content_id"]

                if not content_id:
                    # Fall back to delta content_id using discriminated access.
                    if child_op == "insert" and child["op"] == "insert":
                        content_id = child["content_id"]
                    elif child_op == "delete" and child["op"] == "delete":
                        content_id = child["content_id"]
                    elif child_op == "replace" and child["op"] == "replace":
                        content_id = child["new_content_id"]

                entry = SymbolHistoryEntry(
                    commit_id=commit.commit_id,
                    committed_at=committed_at,
                    op=child_op,
                    content_id=content_id,
                    body_hash=body_hash,
                    signature_id=signature_id,
                )
                index.setdefault(addr, []).append(entry)

    return index


def _build_hash_occurrence(root: pathlib.Path) -> HashOccurrenceIndex:
    """Walk the HEAD snapshot and build the hash occurrence index."""
    # Determine HEAD commit.
    head_ref_path = root / ".muse" / "HEAD"
    if not head_ref_path.exists():
        return {}
    head_ref = read_current_branch(root)
    branch_ref_path = root / ".muse" / "refs" / "heads" / head_ref
    if not branch_ref_path.exists():
        return {}
    head_commit_id = branch_ref_path.read_text().strip()

    manifest = get_commit_snapshot_manifest(root, head_commit_id) or {}
    index: HashOccurrenceIndex = {}

    for file_path, obj_id in sorted(manifest.items()):
        if not is_semantic(file_path):
            continue
        raw = read_object(root, obj_id)
        if raw is None:
            continue
        tree = parse_symbols(raw, file_path)
        for addr, rec in tree.items():
            if rec["kind"] == "import":
                continue
            bh = rec["body_hash"]
            index.setdefault(bh, []).append(addr)

    # Remove trivial (size-1) entries — they are not clones.
    index = {h: addrs for h, addrs in index.items() if len(addrs) > 1}
    return index


# ---------------------------------------------------------------------------
# Sub-commands
# ---------------------------------------------------------------------------


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the index subcommand."""
    parser = subparsers.add_parser(
        "index",
        help="Manage the optional local index layer.",
        description=__doc__,
    )
    subs = parser.add_subparsers(dest="subcommand", metavar="SUBCOMMAND")
    subs.required = True

    status_p = subs.add_parser("status", help="Show the status and entry count of each local index.")
    status_p.add_argument("--json", dest="as_json", action="store_true", help="Emit index status as JSON.")
    status_p.set_defaults(func=run_status)

    rebuild_p = subs.add_parser(
        "rebuild",
        help="Rebuild local indexes from the full commit history.",
        description=(
            "Rebuilds ``symbol_history`` and/or ``hash_occurrence`` indexes under "
            "``.muse/indices/``.  Safe to run multiple times — overwrites existing data.\n\n"
            "Both indexes are derived entirely from the commit history and working "
            "snapshots; the canonical storage is never modified."
        ),
    )
    rebuild_p.add_argument(
        "--index", "-i",
        dest="index_name",
        default=None,
        metavar="NAME",
        help="Rebuild a specific index: symbol_history or hash_occurrence. Default: rebuild all.",
    )
    rebuild_p.add_argument("--verbose", "-v", action="store_true", help="Show progress.")
    rebuild_p.add_argument("--json", dest="as_json", action="store_true", help="Emit rebuild summary as JSON.")
    rebuild_p.set_defaults(func=run_rebuild)


def run_status(args: argparse.Namespace) -> None:
    """Show the status and entry count of each local index."""
    as_json: bool = args.as_json

    root = require_repo()
    infos = index_info(root)

    if as_json:
        out: list[dict[str, str | int | None]] = []
        for info in infos:
            out.append({
                "name": info["name"],
                "status": info["status"],
                "entries": int(info.get("entries", 0)),
                "updated_at": info.get("updated_at") or None,
            })
        print(json.dumps(out, indent=2))
        return

    print("\nLocal index status:")
    print("─" * 50)
    for info in infos:
        status = info["status"]
        name = info["name"]
        updated = info.get("updated_at", "")[:19]
        entries = info.get("entries", "0")
        if status == "present":
            print(f"  ✅  {name:<20}  {entries:>8} entries  (updated {updated})")
        elif status == "absent":
            print(f"  ⬜  {name:<20}  (not built — run: muse index rebuild)")
        else:
            print(f"  ❌  {name:<20}  corrupt — run: muse index rebuild")
    print()


def run_rebuild(args: argparse.Namespace) -> None:
    """Rebuild local indexes from the full commit history.

    Rebuilds ``symbol_history`` and/or ``hash_occurrence`` indexes under
    ``.muse/indices/``.  Safe to run multiple times — overwrites existing data.

    Both indexes are derived entirely from the commit history and working
    snapshots; the canonical storage is never modified.

    Examples::

        muse index rebuild
        muse index rebuild --json
        muse index rebuild --index symbol_history
        muse index rebuild --index hash_occurrence --verbose
    """
    index_name: str | None = args.index_name
    verbose: bool = args.verbose
    as_json: bool = args.as_json

    root = require_repo()

    if index_name is not None and index_name not in ("symbol_history", "hash_occurrence"):
        print(
            f"❌ Unknown index '{index_name}'. "
            "Valid names: symbol_history, hash_occurrence.",
            file=sys.stderr,
        )
        raise SystemExit(ExitCode.USER_ERROR)

    build_all = index_name is None
    built: list[str] = []
    result: dict[str, int | list[str]] = {}

    if build_all or index_name == "symbol_history":
        if verbose and not as_json:
            print("Building symbol_history index…")
        idx = _build_symbol_history(root)
        save_symbol_history(root, idx)
        n_events = sum(len(evts) for evts in idx.values())
        result["symbol_history_addresses"] = len(idx)
        result["symbol_history_events"] = n_events
        if not as_json:
            print(f"  ✅  symbol_history  — {len(idx)} addresses, {n_events} events")
        built.append("symbol_history")

    if build_all or index_name == "hash_occurrence":
        if verbose and not as_json:
            print("Building hash_occurrence index…")
        idx2 = _build_hash_occurrence(root)
        save_hash_occurrence(root, idx2)
        n_clones = sum(len(addrs) for addrs in idx2.values())
        result["hash_occurrence_clusters"] = len(idx2)
        result["hash_occurrence_addresses"] = n_clones
        if not as_json:
            print(f"  ✅  hash_occurrence — {len(idx2)} clone clusters, {n_clones} addresses")
        built.append("hash_occurrence")

    result["rebuilt"] = built

    if as_json:
        print(json.dumps(result, indent=2))
        return

    print(f"\nRebuilt {len(built)} index(es) under .muse/indices/")
    print("Run 'muse index status' to verify.")

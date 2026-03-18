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
    muse index rebuild
    muse index rebuild --index symbol_history
    muse index rebuild --index hash_occurrence
"""
from __future__ import annotations

import json
import logging
import pathlib

import typer

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
from muse.core.store import get_all_commits, get_commit_snapshot_manifest
from muse.plugins.code._query import is_semantic
from muse.plugins.code.ast_parser import parse_symbols

logger = logging.getLogger(__name__)

app = typer.Typer(name="index", help="Manage the optional local index layer.")


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
    head_ref = head_ref_path.read_text().strip().removeprefix("refs/heads/").strip()
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


@app.command("status")
def index_status() -> None:
    """Show the status and entry count of each local index."""
    root = require_repo()
    infos = index_info(root)

    typer.echo("\nLocal index status:")
    typer.echo("─" * 50)
    for info in infos:
        status = info["status"]
        name = info["name"]
        updated = info.get("updated_at", "")[:19]
        entries = info.get("entries", "0")
        if status == "present":
            typer.echo(f"  ✅  {name:<20}  {entries:>8} entries  (updated {updated})")
        elif status == "absent":
            typer.echo(f"  ⬜  {name:<20}  (not built — run: muse index rebuild)")
        else:
            typer.echo(f"  ❌  {name:<20}  corrupt — run: muse index rebuild")
    typer.echo()


@app.command("rebuild")
def index_rebuild(
    index_name: str | None = typer.Option(
        None, "--index", "-i", metavar="NAME",
        help="Rebuild a specific index: symbol_history or hash_occurrence. "
             "Default: rebuild all.",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show progress."),
) -> None:
    """Rebuild local indexes from the full commit history.

    Rebuilds ``symbol_history`` and/or ``hash_occurrence`` indexes under
    ``.muse/indices/``.  Safe to run multiple times — overwrites existing data.

    Both indexes are derived entirely from the commit history and working
    snapshots; the canonical storage is never modified.

    Examples::

        muse index rebuild
        muse index rebuild --index symbol_history
        muse index rebuild --index hash_occurrence --verbose
    """
    root = require_repo()

    if index_name is not None and index_name not in ("symbol_history", "hash_occurrence"):
        typer.echo(
            f"❌ Unknown index '{index_name}'. "
            "Valid names: symbol_history, hash_occurrence.",
            err=True,
        )
        raise typer.Exit(code=ExitCode.USER_ERROR)

    build_all = index_name is None
    built: list[str] = []

    if build_all or index_name == "symbol_history":
        if verbose:
            typer.echo("Building symbol_history index…")
        idx = _build_symbol_history(root)
        save_symbol_history(root, idx)
        n_events = sum(len(evts) for evts in idx.values())
        typer.echo(
            f"  ✅  symbol_history  — {len(idx)} addresses, {n_events} events"
        )
        built.append("symbol_history")

    if build_all or index_name == "hash_occurrence":
        if verbose:
            typer.echo("Building hash_occurrence index…")
        idx2 = _build_hash_occurrence(root)
        save_hash_occurrence(root, idx2)
        n_clones = sum(len(addrs) for addrs in idx2.values())
        typer.echo(
            f"  ✅  hash_occurrence — {len(idx2)} clone clusters, {n_clones} addresses"
        )
        built.append("hash_occurrence")

    typer.echo(f"\nRebuilt {len(built)} index(es) under .muse/indices/")
    typer.echo("Run 'muse index status' to verify.")

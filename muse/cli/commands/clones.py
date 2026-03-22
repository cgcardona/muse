"""muse clones — find duplicate and near-duplicate symbols.

Detects two tiers of code duplication from committed snapshot data:

**Exact clones**
    Symbols with the same ``body_hash`` at different addresses.  The body is
    character-for-character identical (after normalisation) even if the name or
    surrounding context differs.  These are true copy-paste duplicates.

**Near-clones**
    Symbols with the same ``signature_id`` but different ``body_hash``.  Same
    function signature, different implementation — strong candidates for
    consolidation behind a shared abstraction.

Git has no concept of these.  Git stores file diffs; Muse stores symbol
identity hashes.  Clone detection is a single pass over the snapshot index.

Usage::

    muse clones
    muse clones --tier exact
    muse clones --tier near
    muse clones --kind function
    muse clones --commit HEAD~10
    muse clones --min-cluster 3
    muse clones --json

Output::

    Clone analysis — commit a1b2c3d4
    ──────────────────────────────────────────────────────────────

    Exact clones (2 clusters):
      body_hash a1b2c3d4:
        src/billing.py::compute_hash       function
        src/utils.py::compute_hash         function
        src/legacy.py::_hash               function

    Near-clones — same signature (3 clusters):
      signature_id e5f6a7b8:
        src/billing.py::validate           function
        src/auth.py::validate              function

Flags:

``--tier {exact|near|both}``
    Which tier to report (default: both).

``--kind KIND``
    Restrict to symbols of this kind.

``--min-cluster N``
    Only show clusters with at least N members (default: 2).

``--commit, -c REF``
    Analyse a historical snapshot instead of HEAD.

``--json``
    Emit results as JSON.
"""

from __future__ import annotations

import argparse
import json
import logging
import pathlib
import sys
from typing import Literal

from muse._version import __version__
from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.store import get_commit_snapshot_manifest, read_current_branch, resolve_commit_ref
from muse.plugins.code._query import language_of, symbols_for_snapshot
from muse.plugins.code.ast_parser import SymbolRecord

logger = logging.getLogger(__name__)

CloneTier = Literal["exact", "near", "both"]


def _read_repo_id(root: pathlib.Path) -> str:
    return str(json.loads((root / ".muse" / "repo.json").read_text())["repo_id"])


def _read_branch(root: pathlib.Path) -> str:
    return read_current_branch(root)


class _CloneCluster:
    def __init__(
        self,
        tier: CloneTier,
        hash_value: str,
        members: list[tuple[str, SymbolRecord]],
    ) -> None:
        self.tier = tier
        self.hash_value = hash_value
        self.members = members  # (address, record)

    def to_dict(self) -> dict[str, str | list[dict[str, str]]]:
        return {
            "tier": self.tier,
            "hash": self.hash_value[:8],
            "count": str(len(self.members)),
            "members": [
                {
                    "address": addr,
                    "kind": rec["kind"],
                    "language": language_of(addr.split("::")[0]),
                    "body_hash": rec["body_hash"][:8],
                    "signature_id": rec["signature_id"][:8],
                    "content_id": rec["content_id"][:8],
                }
                for addr, rec in self.members
            ],
        }


def find_clones(
    root: pathlib.Path,
    manifest: dict[str, str],
    tier: CloneTier,
    kind_filter: str | None,
    min_cluster: int,
) -> list[_CloneCluster]:
    """Build clone clusters from *manifest*."""
    sym_map = symbols_for_snapshot(root, manifest, kind_filter=kind_filter)

    # Flatten to list of (address, record).
    all_syms: list[tuple[str, SymbolRecord]] = [
        (addr, rec)
        for _fp, tree in sorted(sym_map.items())
        for addr, rec in sorted(tree.items())
        if rec["kind"] != "import"
    ]

    clusters: list[_CloneCluster] = []

    if tier in ("exact", "both"):
        body_index: dict[str, list[tuple[str, SymbolRecord]]] = {}
        for addr, rec in all_syms:
            body_index.setdefault(rec["body_hash"], []).append((addr, rec))
        for body_hash, members in sorted(body_index.items()):
            if len(members) >= min_cluster:
                clusters.append(_CloneCluster("exact", body_hash, members))

    if tier in ("near", "both"):
        sig_index: dict[str, list[tuple[str, SymbolRecord]]] = {}
        for addr, rec in all_syms:
            sig_index.setdefault(rec["signature_id"], []).append((addr, rec))
        for sig_id, members in sorted(sig_index.items()):
            # Near-clone: same signature, at least two DIFFERENT body hashes.
            unique_bodies = {r["body_hash"] for _, r in members}
            if len(members) >= min_cluster and len(unique_bodies) > 1:
                # Don't re-emit clusters already reported as exact clones.
                clusters.append(_CloneCluster("near", sig_id, members))

    # Sort: largest clusters first, then by tier, then by hash.
    clusters.sort(key=lambda c: (-len(c.members), c.tier, c.hash_value))
    return clusters


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the clones subcommand."""
    parser = subparsers.add_parser(
        "clones",
        help="Find exact and near-duplicate symbols in the committed snapshot.",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--tier", "-t", default="both",
        help="Tier to report: exact, near, or both.",
    )
    parser.add_argument(
        "--kind", "-k", default=None, metavar="KIND", dest="kind_filter",
        help="Restrict to symbols of this kind.",
    )
    parser.add_argument(
        "--min-cluster", "-m", type=int, default=2, metavar="N", dest="min_cluster",
        help="Only show clusters with at least N members.",
    )
    parser.add_argument(
        "--commit", "-c", default=None, metavar="REF", dest="ref",
        help="Analyse this commit instead of HEAD.",
    )
    parser.add_argument(
        "--json", action="store_true", dest="as_json",
        help="Emit results as JSON.",
    )
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> None:
    """Find exact and near-duplicate symbols in the committed snapshot.

    Exact clones share the same ``body_hash`` (identical implementation).
    Near-clones share the same ``signature_id`` but differ in body — same
    contract, different implementation.  Both tiers are candidates for
    consolidation behind shared abstractions.

    Uses content-addressed hashes from the snapshot — no AST recomputation
    or file parsing at query time.
    """
    tier: str = args.tier
    kind_filter: str | None = args.kind_filter
    min_cluster: int = args.min_cluster
    ref: str | None = args.ref
    as_json: bool = args.as_json

    root = require_repo()
    repo_id = _read_repo_id(root)
    branch = _read_branch(root)

    if tier not in ("exact", "near", "both"):
        print(f"❌ --tier must be 'exact', 'near', or 'both' (got: {tier!r})", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)

    commit = resolve_commit_ref(root, repo_id, branch, ref)
    if commit is None:
        print(f"❌ Commit '{ref or 'HEAD'}' not found.", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)

    manifest = get_commit_snapshot_manifest(root, commit.commit_id) or {}
    # Validated above — safe to narrow.
    if tier == "exact":
        cluster_list = find_clones(root, manifest, "exact", kind_filter, min_cluster)
    elif tier == "near":
        cluster_list = find_clones(root, manifest, "near", kind_filter, min_cluster)
    else:
        cluster_list = find_clones(root, manifest, "both", kind_filter, min_cluster)

    exact_clusters = [c for c in cluster_list if c.tier == "exact"]
    near_clusters = [c for c in cluster_list if c.tier == "near"]

    if as_json:
        print(json.dumps(
            {
                "schema_version": __version__,
                "commit": commit.commit_id[:8],
                "tier": tier,
                "min_cluster": min_cluster,
                "kind_filter": kind_filter,
                "exact_clone_clusters": len(exact_clusters),
                "near_clone_clusters": len(near_clusters),
                "clusters": [c.to_dict() for c in cluster_list],
            },
            indent=2,
        ))
        return

    print(f"\nClone analysis — commit {commit.commit_id[:8]}")
    if kind_filter:
        print(f"  (kind: {kind_filter})")
    print("─" * 62)

    if not cluster_list:
        print("\n  ✅ No clones detected.")
        return

    if exact_clusters and tier in ("exact", "both"):
        print(f"\nExact clones ({len(exact_clusters)} cluster(s)):")
        for cl in exact_clusters:
            print(f"  body_hash {cl.hash_value[:8]}:")
            for addr, rec in cl.members:
                print(f"    {addr}  {rec['kind']}")

    if near_clusters and tier in ("near", "both"):
        print(f"\nNear-clones — same signature ({len(near_clusters)} cluster(s)):")
        for cl in near_clusters:
            print(f"  signature_id {cl.hash_value[:8]}:")
            for addr, rec in cl.members:
                print(f"    {addr}  {rec['kind']}  (body {rec['body_hash'][:8]})")

    total = sum(len(c.members) for c in cluster_list)
    print(f"\n  {len(cluster_list)} clone cluster(s), {total} total symbol(s) involved")
    print("  Consider consolidating behind shared abstractions.")

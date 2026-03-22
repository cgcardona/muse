"""muse reconcile — recommend merge ordering and integration strategy.

Reads active reservations, intents, and branch divergence to recommend:

1. **Merge ordering** — which branches should be merged first to minimize
   downstream conflicts.
2. **Integration strategy** — fast-forward, squash, or rebase for each branch.
3. **Conflict hotspots** — symbols reserved by multiple agents that need
   special attention.

``muse reconcile`` is a *read-only* planning command.  It does not write to
branches, commit history, or the coordination layer.  It provides the plan;
agents execute it.

Why this exists
---------------
In a system with millions of concurrent agents, merges happen constantly.
Without coordination, every merge introduces friction.  ``muse reconcile``
gives an orchestration agent a complete picture of the current coordination
state and a recommended action plan.

Usage::

    muse reconcile
    muse reconcile --json

Output::

    Reconciliation report
    ──────────────────────────────────────────────────────────────

    Active reservations: 3  Active intents: 2
    Conflict hotspots: 1

    Recommended merge order:
      1. feature/billing  (3 symbols, 0 predicted conflicts)
      2. feature/auth     (5 symbols, 1 predicted conflict)

    Conflict hotspot:
      src/billing.py::compute_total
      reserved by: agent-41 (feature/billing), agent-42 (feature/auth)
      recommendation: resolve feature/billing first; agent-42 must rebase

    Integration strategy:
      feature/billing → fast-forward (no conflicts predicted)
      feature/auth    → rebase onto main after feature/billing lands

Flags:

``--json``
    Emit the reconciliation report as JSON.
"""

from __future__ import annotations

import argparse
import json
import logging

from muse._version import __version__
from muse.core.coordination import active_reservations, load_all_intents
from muse.core.repo import require_repo

logger = logging.getLogger(__name__)


class _BranchSummary:
    def __init__(self, branch: str) -> None:
        self.branch = branch
        self.reserved_addresses: list[str] = []
        self.intents: list[str] = []
        self.run_ids: set[str] = set()
        self.conflict_count: int = 0

    def to_dict(self) -> dict[str, str | int | list[str]]:
        return {
            "branch": self.branch,
            "reserved_addresses": self.reserved_addresses,
            "intents": self.intents,
            "run_ids": sorted(self.run_ids),
            "predicted_conflicts": self.conflict_count,
        }


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the reconcile subcommand."""
    parser = subparsers.add_parser(
        "reconcile",
        help="Recommend merge ordering and integration strategy.",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--json", dest="as_json", action="store_true", help="Emit report as JSON.")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> None:
    """Recommend merge ordering and integration strategy.

    Reads coordination state (reservations + intents) and produces a
    recommended action plan: which branches to merge first, what strategy
    to use, and which conflict hotspots need manual attention.

    Does not write anything — purely advisory output.
    """
    as_json: bool = args.as_json

    root = require_repo()
    reservations = active_reservations(root)
    intents = load_all_intents(root)

    # Aggregate by branch.
    branch_map: dict[str, _BranchSummary] = {}
    for res in reservations:
        b = res.branch
        if b not in branch_map:
            branch_map[b] = _BranchSummary(b)
        branch_map[b].reserved_addresses.extend(res.addresses)
        branch_map[b].run_ids.add(res.run_id)

    for it in intents:
        b = it.branch
        if b not in branch_map:
            branch_map[b] = _BranchSummary(b)
        branch_map[b].intents.append(it.operation)
        branch_map[b].run_ids.add(it.run_id)

    # Detect conflict hotspots.
    addr_branches: dict[str, list[str]] = {}
    for res in reservations:
        for addr in res.addresses:
            addr_branches.setdefault(addr, []).append(res.branch)

    hotspots: dict[str, list[str]] = {
        addr: branches
        for addr, branches in addr_branches.items()
        if len(set(branches)) > 1
    }

    # Compute conflict counts per branch based on hotspot participation.
    for addr, branches in hotspots.items():
        unique_branches = list(dict.fromkeys(branches))
        for b in unique_branches:
            if b in branch_map:
                branch_map[b].conflict_count += 1

    # Recommend merge order: fewer conflicts → merge first.
    ordered = sorted(
        branch_map.values(),
        key=lambda bs: (bs.conflict_count, len(bs.reserved_addresses)),
    )

    # Recommend integration strategies.
    strategies: dict[str, str] = {}
    for bs in ordered:
        if bs.conflict_count == 0:
            strategies[bs.branch] = "fast-forward (no conflicts predicted)"
        elif bs.conflict_count <= 2:
            strategies[bs.branch] = "rebase onto main before merging"
        else:
            strategies[bs.branch] = "manual conflict resolution required"

    if as_json:
        print(json.dumps(
            {
                "schema_version": __version__,
                "active_reservations": len(reservations),
                "active_intents": len(intents),
                "conflict_hotspots": len(hotspots),
                "branches": [bs.to_dict() for bs in ordered],
                "recommended_merge_order": [bs.branch for bs in ordered],
                "strategies": strategies,
                "hotspots": [
                    {"address": addr, "branches": list(dict.fromkeys(brs))}
                    for addr, brs in sorted(hotspots.items())
                ],
            },
            indent=2,
        ))
        return

    print("\nReconciliation report")
    print("─" * 62)
    print(
        f"  Active reservations: {len(reservations)}  "
        f"Active intents: {len(intents)}  "
        f"Conflict hotspots: {len(hotspots)}"
    )

    if not reservations and not intents:
        print(
            "\n  (no active coordination data — run 'muse reserve' or 'muse intent' first)"
        )
        return

    if ordered:
        print(f"\n  Recommended merge order:")
        for rank, bs in enumerate(ordered, 1):
            c = bs.conflict_count
            print(
                f"    {rank}. {bs.branch:<30}  ({len(bs.reserved_addresses)} addresses, "
                f"{c} conflict(s))"
            )

    if hotspots:
        print(f"\n  Conflict hotspot(s):")
        for addr, branches in sorted(hotspots.items()):
            unique = list(dict.fromkeys(branches))
            print(f"    {addr}")
            print(f"      reserved by: {', '.join(unique)}")
            first = unique[0]
            rest = ", ".join(unique[1:])
            print(f"      → resolve {first!r} first; {rest} must rebase")

    print(f"\n  Integration strategies:")
    for bs in ordered:
        print(f"    {bs.branch:<30}  → {strategies[bs.branch]}")

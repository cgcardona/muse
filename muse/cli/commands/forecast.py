"""muse forecast — predict merge conflicts before they happen.

Reads all active reservations and intents across branches, then uses the
reverse call graph to compute *likely* conflicts before any code is written.

This turns merge conflict resolution from a reactive ("it broke") problem into
a proactive ("we predicted it") workflow — essential when many agents operate
on a codebase simultaneously.

Conflict types detected
-----------------------
``address_overlap``
    Two agents have reserved the same symbol address.  Direct collision.

``blast_radius_overlap``
    Agent A's reserved symbol is in the call chain of Agent B's target, or
    vice versa.  A change to A's symbol will affect B's symbol.

``operation_conflict``
    Agent A intends to delete/rename a symbol that Agent B intends to modify.
    Classic use-after-free / use-after-rename semantic conflict.

Usage::

    muse forecast
    muse forecast --branch feature-x
    muse forecast --json

Output::

    Conflict forecast — 3 active reservations, 1 intent
    ──────────────────────────────────────────────────────────────

    ⚠️  address_overlap  (confidence 1.00)
        src/billing.py::compute_total
        agent-41 (branch: main)  ↔  agent-42 (branch: feature/billing)

    ⚠️  blast_radius_overlap  (confidence 0.75)
        agent-42 reserved src/billing.py::compute_total
        agent-39 reserved src/api.py::process_payment
        → compute_total is in the call chain of process_payment

    No operation conflicts detected.

    1 high-risk, 1 medium-risk, 0 low-risk conflict(s)

Flags:

``--branch BRANCH``
    Filter to reservations on a specific branch.

``--json``
    Emit the full forecast as JSON.
"""

from __future__ import annotations

import argparse
import json
import logging
import pathlib

from muse._version import __version__
from muse.core.coordination import active_reservations, load_all_intents
from muse.core.repo import require_repo
from muse.core.store import get_commit_snapshot_manifest, read_current_branch, resolve_commit_ref
from muse.plugins.code._callgraph import build_reverse_graph, transitive_callers

logger = logging.getLogger(__name__)


class _ConflictPrediction:
    def __init__(
        self,
        conflict_type: str,
        addresses: list[str],
        agents: list[str],
        confidence: float,
        description: str,
    ) -> None:
        self.conflict_type = conflict_type
        self.addresses = addresses
        self.agents = agents
        self.confidence = confidence
        self.description = description

    def to_dict(self) -> dict[str, str | float | list[str]]:
        return {
            "conflict_type": self.conflict_type,
            "addresses": self.addresses,
            "agents": self.agents,
            "confidence": round(self.confidence, 3),
            "description": self.description,
        }


def _read_repo_id(root: pathlib.Path) -> str:
    return str(json.loads((root / ".muse" / "repo.json").read_text())["repo_id"])


def _read_branch(root: pathlib.Path) -> str:
    return read_current_branch(root)


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the forecast subcommand."""
    parser = subparsers.add_parser(
        "forecast",
        help="Predict merge conflicts from active reservations and intents.",
        description=__doc__,
    )
    parser.add_argument(
        "--branch", "-b",
        dest="branch_filter",
        default=None,
        metavar="BRANCH",
        help="Restrict to reservations on this branch.",
    )
    parser.add_argument("--json", dest="as_json", action="store_true", help="Emit forecast as JSON.")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> None:
    """Predict merge conflicts from active reservations and intents.

    Reads ``.muse/coordination/reservations/`` and ``intents/``, then:

    1. Reports direct address-level overlaps (confidence 1.0).
    2. Computes blast-radius overlaps using the Python call graph.
    3. Reports operation-type conflicts (delete vs modify on same address).

    Use ``muse reserve`` and ``muse intent`` to register your agent's work
    plan before this command becomes useful.
    """
    import pathlib

    branch_filter: str | None = args.branch_filter
    as_json: bool = args.as_json

    root = require_repo()
    repo_id = _read_repo_id(root)
    branch = _read_branch(root)

    reservations = active_reservations(root)
    intents = load_all_intents(root)

    if branch_filter:
        reservations = [r for r in reservations if r.branch == branch_filter]
        intents = [i for i in intents if i.branch == branch_filter]

    conflicts: list[_ConflictPrediction] = []

    # ── Direct address overlap ─────────────────────────────────────────────
    # Build: address → list of (run_id, branch)
    addr_agents: dict[str, list[str]] = {}
    for res in reservations:
        for addr in res.addresses:
            addr_agents.setdefault(addr, []).append(f"{res.run_id}@{res.branch}")

    for addr, agents in sorted(addr_agents.items()):
        unique_agents = list(dict.fromkeys(agents))
        if len(unique_agents) > 1:
            conflicts.append(_ConflictPrediction(
                conflict_type="address_overlap",
                addresses=[addr],
                agents=unique_agents,
                confidence=1.0,
                description=f"{addr} reserved by {len(unique_agents)} agents simultaneously",
            ))

    # ── Blast-radius overlap ───────────────────────────────────────────────
    # Use the call graph to check if any two reservations' addresses are in
    # each other's transitive call chains.
    commit = resolve_commit_ref(root, repo_id, branch, None)
    if commit is not None:
        manifest = get_commit_snapshot_manifest(root, commit.commit_id) or {}
        try:
            reverse = build_reverse_graph(root, manifest)
            all_addresses = list(addr_agents.keys())
            for i, addr_a in enumerate(all_addresses):
                callers_a = transitive_callers(addr_a, reverse, max_depth=5)
                callers_set: set[str] = {c for lvl in callers_a.values() for c in lvl}
                for addr_b in all_addresses[i + 1:]:
                    if addr_b in callers_set:
                        agents_a = addr_agents.get(addr_a, [])
                        agents_b = addr_agents.get(addr_b, [])
                        if set(agents_a) != set(agents_b):
                            conflicts.append(_ConflictPrediction(
                                conflict_type="blast_radius_overlap",
                                addresses=[addr_a, addr_b],
                                agents=list(set(agents_a) | set(agents_b)),
                                confidence=0.75,
                                description=(
                                    f"{addr_b} is in the transitive call chain of {addr_a}"
                                ),
                            ))
        except Exception as exc:  # noqa: BLE001
            logger.debug("Call graph unavailable for forecast: %s", exc)

    # ── Operation conflicts ────────────────────────────────────────────────
    # Collect intents by address.
    intent_ops: dict[str, list[str]] = {}  # address → list of operations
    intent_agents: dict[str, list[str]] = {}  # address → list of run_ids
    for it in intents:
        for addr in it.addresses:
            intent_ops.setdefault(addr, []).append(it.operation)
            intent_agents.setdefault(addr, []).append(f"{it.run_id}@{it.branch}")

    for addr, ops in sorted(intent_ops.items()):
        if len(set(ops)) <= 1 and len(set(intent_agents.get(addr, []))) <= 1:
            continue  # Same op by same agent — not a conflict.
        has_delete = "delete" in ops
        has_modify = any(op in ("modify", "rename", "extract") for op in ops)
        if has_delete and has_modify:
            agents = list(dict.fromkeys(intent_agents.get(addr, [])))
            conflicts.append(_ConflictPrediction(
                conflict_type="operation_conflict",
                addresses=[addr],
                agents=agents,
                confidence=0.9,
                description=f"delete vs modify conflict on {addr}",
            ))

    if as_json:
        print(json.dumps(
            {
                "schema_version": __version__,
                "active_reservations": len(reservations),
                "intents": len(intents),
                "branch_filter": branch_filter,
                "conflicts": [c.to_dict() for c in conflicts],
                "high_risk": sum(1 for c in conflicts if c.confidence >= 0.9),
                "medium_risk": sum(1 for c in conflicts if 0.5 <= c.confidence < 0.9),
                "low_risk": sum(1 for c in conflicts if c.confidence < 0.5),
            },
            indent=2,
        ))
        return

    print(
        f"\nConflict forecast — "
        f"{len(reservations)} active reservation(s), {len(intents)} intent(s)"
    )
    print("─" * 62)

    if not conflicts:
        print("\n  ✅ No conflicts predicted.")
        if not reservations:
            print("  (no active reservations — run 'muse reserve' first)")
        return

    for c in conflicts:
        icon = "🔴" if c.confidence >= 0.9 else "⚠️ "
        print(f"\n{icon}  {c.conflict_type}  (confidence {c.confidence:.2f})")
        for addr in c.addresses:
            print(f"    {addr}")
        for agent in c.agents:
            print(f"    agent: {agent}")
        print(f"    → {c.description}")

    high = sum(1 for c in conflicts if c.confidence >= 0.9)
    med = sum(1 for c in conflicts if 0.5 <= c.confidence < 0.9)
    print(
        f"\n  {high} high-risk, {med} medium-risk conflict(s) predicted"
    )
    print("  Run 'muse plan-merge' for a detailed merge strategy.")

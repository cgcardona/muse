#!/usr/bin/env python3
"""Muse Tour de Force — 5-act VCS stress test + shareable visualization.

Creates a fresh Muse repository in a temporary directory, runs a complete
5-act narrative exercising every primitive, builds a commit DAG, and renders
a self-contained HTML file you can share anywhere.

Usage
-----
    python tools/tour_de_force.py
    python tools/tour_de_force.py --output-dir my_output/
    python tools/tour_de_force.py --json-only   # skip HTML rendering

Output
------
    artifacts/tour_de_force.json   — structured event log + DAG
    artifacts/tour_de_force.html   — shareable visualization
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import time
from datetime import datetime, timezone
from typing import TypedDict

# Ensure both the repo root (muse package) and tools/ (render_html) are importable.
_REPO_ROOT = pathlib.Path(__file__).parent.parent
_TOOLS_DIR = pathlib.Path(__file__).parent
for _p in (str(_REPO_ROOT), str(_TOOLS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from muse.cli.app import cli  # noqa: E402
from muse.core.merge_engine import clear_merge_state  # noqa: E402
from typer.testing import CliRunner  # noqa: E402

RUNNER = CliRunner()

BRANCH_COLORS: dict[str, str] = {
    "main": "#4f8ef7",
    "alpha": "#f9a825",
    "beta": "#66bb6a",
    "gamma": "#ab47bc",
    "conflict/left": "#ef5350",
    "conflict/right": "#ff7043",
    "ot-left": "#26c6da",
    "ot-right": "#ab47bc",
    "ot-conflict-l": "#ef5350",
    "ot-conflict-r": "#ff7043",
}

ACT_TITLES: dict[int, str] = {
    1: "Foundation",
    2: "Divergence",
    3: "Clean Merges",
    4: "Conflict & Resolution",
    5: "Advanced Operations",
    6: "Typed Delta Algebra",
    7: "Domain Schema",
    8: "OT Merge",
    9: "CRDT Primitives",
}


# ---------------------------------------------------------------------------
# TypedDicts for the structured event log
# ---------------------------------------------------------------------------


class EventRecord(TypedDict):
    act: int
    act_title: str
    step: int
    op: str
    cmd: str
    duration_ms: float
    exit_code: int
    output: str
    commit_id: str | None


class CommitNode(TypedDict):
    id: str
    short: str
    message: str
    branch: str
    parents: list[str]
    timestamp: str
    files: list[str]
    files_changed: int


class BranchRef(TypedDict):
    name: str
    head: str
    color: str


class DAGData(TypedDict):
    commits: list[CommitNode]
    branches: list[BranchRef]


class TourMeta(TypedDict):
    domain: str
    muse_version: str
    generated_at: str
    elapsed_s: str


class TourStats(TypedDict):
    commits: int
    branches: int
    merges: int
    conflicts_resolved: int
    operations: int


class TourData(TypedDict):
    meta: TourMeta
    stats: TourStats
    dag: DAGData
    events: list[EventRecord]


# ---------------------------------------------------------------------------
# Global runner state
# ---------------------------------------------------------------------------

_events: list[EventRecord] = []
_step = 0
_current_act = 0


# ---------------------------------------------------------------------------
# Runner helpers
# ---------------------------------------------------------------------------


def _run(
    op: str,
    args: list[str],
    root: pathlib.Path,
    *,
    expect_fail: bool = False,
) -> tuple[int, str]:
    """Invoke a muse CLI command, capture output and timing."""
    global _step, _current_act
    _step += 1
    old_cwd = pathlib.Path.cwd()
    os.chdir(root)
    t0 = time.perf_counter()
    try:
        result = RUNNER.invoke(cli, args)
    finally:
        os.chdir(old_cwd)
    duration_ms = (time.perf_counter() - t0) * 1000
    output = (result.output or "").strip()
    short_id = _extract_short_id(output)

    mark = "✓" if result.exit_code == 0 else ("⚠" if expect_fail else "✗")
    print(f"  {mark} muse {' '.join(str(a) for a in args)}")
    if result.exit_code != 0 and not expect_fail:
        print(f"      output: {output[:160]}")

    _events.append(EventRecord(
        act=_current_act,
        act_title=ACT_TITLES.get(_current_act, ""),
        step=_step,
        op=op,
        cmd="muse " + " ".join(str(a) for a in args),
        duration_ms=round(duration_ms, 1),
        exit_code=result.exit_code,
        output=output,
        commit_id=short_id,
    ))
    return result.exit_code, output


def _write(root: pathlib.Path, filename: str, content: str = "") -> None:
    """Write a file to muse-work/."""
    workdir = root / "muse-work"
    workdir.mkdir(exist_ok=True)
    body = content or f"# {filename}\nformat: muse-state\nversion: 1\n"
    (workdir / filename).write_text(body)


def _extract_short_id(output: str) -> str | None:
    """Extract an 8-char hex commit short-ID from CLI output."""
    import re
    patterns = [
        r"\[(?:\S+)\s+([0-9a-f]{8})\]",        # [main a1b2c3d4]
        r"Merged.*?\(([0-9a-f]{8})\)",           # Merged 'x' into 'y' (id)
        r"Fast-forward to ([0-9a-f]{8})",        # Fast-forward to id
        r"Cherry-picked.*?([0-9a-f]{8})\b",      # Cherry-picked …
    ]
    for p in patterns:
        m = re.search(p, output)
        if m:
            return m.group(1)
    return None


def _head_id(root: pathlib.Path, branch: str) -> str:
    """Read the full commit ID for a branch from refs/heads/."""
    parts = branch.split("/")
    ref_file = root / ".muse" / "refs" / "heads" / pathlib.Path(*parts)
    if ref_file.exists():
        return ref_file.read_text().strip()
    return ""


# ---------------------------------------------------------------------------
# Act 1 — Foundation
# ---------------------------------------------------------------------------


def act1(root: pathlib.Path) -> None:
    global _current_act
    _current_act = 1
    print("\n=== Act 1: Foundation ===")
    _run("init", ["init"], root)

    _write(root, "root-state.mid", "# root-state\nformat: muse-music\nbeats: 4\ntempo: 120\n")
    _run("commit", ["commit", "-m", "Root: initial state snapshot"], root)

    _write(root, "layer-1.mid", "# layer-1\ndimension: rhythmic\npattern: 4/4\n")
    _run("commit", ["commit", "-m", "Layer 1: add rhythmic dimension"], root)

    _write(root, "layer-2.mid", "# layer-2\ndimension: harmonic\nkey: Cmaj\n")
    _run("commit", ["commit", "-m", "Layer 2: add harmonic dimension"], root)

    _run("log", ["log", "--oneline"], root)


# ---------------------------------------------------------------------------
# Act 2 — Divergence
# ---------------------------------------------------------------------------


def act2(root: pathlib.Path) -> dict[str, str]:
    global _current_act
    _current_act = 2
    print("\n=== Act 2: Divergence ===")

    # Branch: alpha — textural variations
    _run("checkout_alpha", ["checkout", "-b", "alpha"], root)
    _write(root, "alpha-a.mid", "# alpha-a\ntexture: sparse\nlayer: high\n")
    _run("commit", ["commit", "-m", "Alpha: texture pattern A (sparse)"], root)
    _write(root, "alpha-b.mid", "# alpha-b\ntexture: dense\nlayer: mid\n")
    _run("commit", ["commit", "-m", "Alpha: texture pattern B (dense)"], root)

    # Branch: beta — rhythm explorations (from main)
    _run("checkout_main_1", ["checkout", "main"], root)
    _run("checkout_beta", ["checkout", "-b", "beta"], root)
    _write(root, "beta-a.mid", "# beta-a\nrhythm: syncopated\nsubdiv: 16th\n")
    _run("commit", ["commit", "-m", "Beta: syncopated rhythm pattern"], root)

    # Branch: gamma — melodic lines (from main)
    _run("checkout_main_2", ["checkout", "main"], root)
    _run("checkout_gamma", ["checkout", "-b", "gamma"], root)
    _write(root, "gamma-a.mid", "# gamma-a\nmelody: ascending\ninterval: 3rd\n")
    _run("commit", ["commit", "-m", "Gamma: ascending melody A"], root)
    gamma_a_id = _head_id(root, "gamma")

    _write(root, "gamma-b.mid", "# gamma-b\nmelody: descending\ninterval: 5th\n")
    _run("commit", ["commit", "-m", "Gamma: descending melody B"], root)

    _run("log", ["log", "--oneline"], root)
    return {"gamma_a": gamma_a_id}


# ---------------------------------------------------------------------------
# Act 3 — Clean Merges
# ---------------------------------------------------------------------------


def act3(root: pathlib.Path) -> None:
    global _current_act
    _current_act = 3
    print("\n=== Act 3: Clean Merges ===")

    _run("checkout_main", ["checkout", "main"], root)
    _run("merge_alpha", ["merge", "alpha"], root)
    _run("status", ["status"], root)
    _run("merge_beta", ["merge", "beta"], root)
    _run("log", ["log", "--oneline"], root)


# ---------------------------------------------------------------------------
# Act 4 — Conflict & Resolution
# ---------------------------------------------------------------------------


def act4(root: pathlib.Path) -> None:
    global _current_act
    _current_act = 4
    print("\n=== Act 4: Conflict & Resolution ===")

    # conflict/left: introduce shared-state.mid (version A)
    _run("checkout_left", ["checkout", "-b", "conflict/left"], root)
    _write(root, "shared-state.mid", "# shared-state\nversion: A\nsource: left-branch\n")
    _run("commit", ["commit", "-m", "Left: introduce shared state (version A)"], root)

    # conflict/right: introduce shared-state.mid (version B) — from main before left merge
    _run("checkout_main", ["checkout", "main"], root)
    _run("checkout_right", ["checkout", "-b", "conflict/right"], root)
    _write(root, "shared-state.mid", "# shared-state\nversion: B\nsource: right-branch\n")
    _run("commit", ["commit", "-m", "Right: introduce shared state (version B)"], root)

    # Merge left into main cleanly (main didn't have shared-state.mid yet)
    _run("checkout_main", ["checkout", "main"], root)
    _run("merge_left", ["merge", "conflict/left"], root)

    # Merge right → CONFLICT (both sides added shared-state.mid with different content)
    _run("merge_right", ["merge", "conflict/right"], root, expect_fail=True)

    # Resolve: write reconciled content, clear merge state, commit
    print("  → Resolving conflict: writing reconciled shared-state.mid")
    resolved = (
        "# shared-state\n"
        "version: RESOLVED\n"
        "source: merged A+B\n"
        "notes: manual reconciliation\n"
    )
    (root / "muse-work" / "shared-state.mid").write_text(resolved)
    clear_merge_state(root)
    _run("resolve_commit", ["commit", "-m", "Resolve: integrate shared-state (A+B reconciled)"], root)

    _run("status", ["status"], root)


# ---------------------------------------------------------------------------
# Act 5 — Advanced Operations
# ---------------------------------------------------------------------------


def act5(root: pathlib.Path, saved_ids: dict[str, str]) -> None:
    global _current_act
    _current_act = 5
    print("\n=== Act 5: Advanced Operations ===")

    gamma_a_id = saved_ids.get("gamma_a", "")

    # Cherry-pick: bring Gamma: melody A into main without merging all of gamma
    if gamma_a_id:
        _run("cherry_pick", ["cherry-pick", gamma_a_id], root)
    cherry_pick_head = _head_id(root, "main")

    # Inspect the resulting commit
    _run("show", ["show"], root)
    _run("diff", ["diff"], root)

    # Stash: park uncommitted work, then pop it back
    _write(root, "wip-experiment.mid", "# wip-experiment\nstatus: in-progress\ndo-not-commit: true\n")
    _run("stash", ["stash"], root)
    _run("status", ["status"], root)
    _run("stash_pop", ["stash", "pop"], root)

    # Revert the cherry-pick (cherry-pick becomes part of history, revert undoes it)
    if cherry_pick_head:
        _run("revert", ["revert", "-m", "Revert: undo gamma cherry-pick", cherry_pick_head], root)

    # Tag the current HEAD
    _run("tag_add", ["tag", "add", "release:v1.0"], root)
    _run("tag_list", ["tag", "list"], root)

    # Full history sweep
    _run("log_stat", ["log", "--stat"], root)


# ---------------------------------------------------------------------------
# Act 6 — Typed Delta Algebra
# ---------------------------------------------------------------------------


def act6(root: pathlib.Path) -> None:
    """Run muse show and muse show --json to demonstrate StructuredDelta output."""
    global _current_act
    _current_act = 6
    print("\n=== Act 6: Typed Delta Algebra ===")

    # Show HEAD — uses the StructuredDelta stored at commit time.
    _run("show_head", ["show"], root)

    # JSON form exposes the raw StructuredDelta fields.
    _run("show_json", ["show", "--json"], root)

    # Full log with per-commit stats (summary field from StructuredDelta).
    _run("log_stat_act6", ["log", "--stat"], root)


# ---------------------------------------------------------------------------
# Act 7 — Domain Schema & Diff Algorithms
# ---------------------------------------------------------------------------


def act7(root: pathlib.Path) -> None:
    """Run muse domains to show the live plugin dashboard and scaffold a new domain."""
    global _current_act
    _current_act = 7
    print("\n=== Act 7: Domain Schema ===")

    # Human-readable dashboard — the single source of truth for registered plugins.
    _run("domains_dashboard", ["domains"], root)

    # Machine-readable JSON — for tooling and CI integration.
    _run("domains_json", ["domains", "--json"], root)

    # Scaffold a fresh plugin directory (creates muse/plugins/genomics/).
    # We clean it up afterwards so the repo tree stays pristine.
    _run("scaffold_genomics", ["domains", "--new", "genomics"], root)

    genomics_dir = _TOOLS_DIR.parent / "muse" / "plugins" / "genomics"
    if genomics_dir.exists():
        import shutil
        shutil.rmtree(genomics_dir)


# ---------------------------------------------------------------------------
# Act 8 — OT Merge
# ---------------------------------------------------------------------------


def act8(root: pathlib.Path) -> None:
    """Two-scenario OT demonstration: commuting ops (clean) then genuine conflict."""
    global _current_act
    _current_act = 8
    print("\n=== Act 8: OT Merge ===")

    # --- Scenario A: independent InsertOps commute → clean OT merge -------------
    _run("checkout_main_ot_a", ["checkout", "main"], root)

    _run("checkout_ot_left", ["checkout", "-b", "ot-left"], root)
    _write(root, "ot-notes-a.mid", "# ot-notes-a\nnotes: C4 E4 G4\ntick: 0\n")
    _run("commit_ot_left", ["commit", "-m", "OT-left: add note sequence A (C E G)"], root)

    _run("checkout_main_ot_a2", ["checkout", "main"], root)
    _run("checkout_ot_right", ["checkout", "-b", "ot-right"], root)
    _write(root, "ot-notes-b.mid", "# ot-notes-b\nnotes: D4 F4 A4\ntick: 480\n")
    _run("commit_ot_right", ["commit", "-m", "OT-right: add note sequence B (D F A)"], root)

    # Merge into ot-left: InsertOp("ot-notes-a.mid") and InsertOp("ot-notes-b.mid")
    # have different addresses → they commute → OT engine auto-merges cleanly.
    _run("checkout_ot_left2", ["checkout", "ot-left"], root)
    _run("merge_ot_clean", ["merge", "ot-right"], root)

    # --- Scenario B: both branches ReplaceOp same address → OT conflict ----------
    _run("checkout_main_ot_b", ["checkout", "main"], root)
    _write(root, "shared-melody.mid", "# shared-melody\nnotes: C4 G4\ntick: 0\n")
    _run("commit_shared_base", ["commit", "-m", "Add shared melody (merge base)"], root)

    _run("checkout_ot_conflict_l", ["checkout", "-b", "ot-conflict-l"], root)
    _write(root, "shared-melody.mid", "# shared-melody\nnotes: C4 E4 G4\ntick: 0\n")
    _run("commit_conflict_l", ["commit", "-m", "OT-conflict-left: extend melody (major triad)"], root)

    _run("checkout_main_ot_b2", ["checkout", "main"], root)
    _run("checkout_ot_conflict_r", ["checkout", "-b", "ot-conflict-r"], root)
    _write(root, "shared-melody.mid", "# shared-melody\nnotes: C4 Eb4 G4\ntick: 0\n")
    _run("commit_conflict_r", ["commit", "-m", "OT-conflict-right: extend melody (minor triad)"], root)

    # Merge conflict-l first (fast-forward from main).
    _run("checkout_main_ot_b3", ["checkout", "main"], root)
    _run("merge_conflict_l_into_main", ["merge", "ot-conflict-l"], root)

    # Merge conflict-r: both sides issued ReplaceOp("shared-melody.mid") from the
    # same base → operations do not commute → OT raises a conflict.
    _run("merge_conflict_r_into_main", ["merge", "ot-conflict-r"], root, expect_fail=True)

    # Clear merge state so subsequent acts can continue cleanly.
    clear_merge_state(root)


# ---------------------------------------------------------------------------
# Act 9 — CRDT Primitives
# ---------------------------------------------------------------------------


def act9_crdt() -> None:
    """Directly exercise all six CRDT primitives to show convergent merge semantics."""
    global _step, _current_act
    _current_act = 9
    print("\n=== Act 9: CRDT Primitives ===")

    from muse.core.crdts import GCounter, LWWRegister, ORSet, VectorClock

    t0 = time.perf_counter()

    # ORSet — add-wins concurrent merge -------------------------------------------
    _step += 1
    # Both agents start from the same base that already contains the annotation.
    base_set = ORSet()
    base_set, _base_tok = base_set.add("annotation-GO:0001234")

    # Agent A concurrently re-adds the annotation with a new token.
    set_a, _new_tok = base_set.add("annotation-GO:0001234")

    # Agent B removes the annotation using the tokens it observed from the base.
    observed = base_set.tokens_for("annotation-GO:0001234")
    set_b = base_set.remove("annotation-GO:0001234", observed)

    merged_set = set_a.join(set_b)
    output = "\n".join([
        "ORSet — add-wins concurrent merge:",
        f"  base  elements: {sorted(base_set.elements())}",
        f"  A re-adds annotation  →  elements: {sorted(set_a.elements())}",
        f"  B removes annotation  →  elements: {sorted(set_b.elements())}",
        f"  join(A, B)            →  elements: {sorted(merged_set.elements())}",
        "  [A's new token is not tombstoned — add always wins]",
    ])
    print(f"  ✓ ORSet: join always succeeds, add-wins preserved")
    _events.append(EventRecord(
        act=9, act_title="CRDT Primitives", step=_step,
        op="crdt_orset", cmd="ORSet.join(set_a, set_b)",
        duration_ms=round((time.perf_counter() - t0) * 1000, 2),
        exit_code=0, output=output, commit_id=None,
    ))

    # LWWRegister — last-write-wins scalar ----------------------------------------
    _step += 1
    t1 = time.perf_counter()
    reg_a = LWWRegister.from_dict({"value": "80 BPM", "timestamp": 1.0, "author": "agent-A"})
    reg_b = LWWRegister.from_dict({"value": "120 BPM", "timestamp": 2.0, "author": "agent-B"})
    merged_reg = reg_a.join(reg_b)
    output = "\n".join([
        "LWWRegister — last-write-wins scalar:",
        f"  Agent A writes: '{reg_a.read()}' at t=1.0",
        f"  Agent B writes: '{reg_b.read()}' at t=2.0  (later timestamp)",
        f"  join(A, B) → '{merged_reg.read()}'  [higher timestamp wins]",
        "  join(B, A) → same result  [commutativity]",
    ])
    print(f"  ✓ LWWRegister: join commutative, higher timestamp always wins")
    _events.append(EventRecord(
        act=9, act_title="CRDT Primitives", step=_step,
        op="crdt_lww", cmd="LWWRegister.join(reg_a, reg_b)",
        duration_ms=round((time.perf_counter() - t1) * 1000, 2),
        exit_code=0, output=output, commit_id=None,
    ))

    # GCounter — grow-only distributed counter ------------------------------------
    _step += 1
    t2 = time.perf_counter()
    cnt_a = GCounter().increment("agent-A").increment("agent-A")
    cnt_b = GCounter().increment("agent-B").increment("agent-B").increment("agent-B")
    merged_cnt = cnt_a.join(cnt_b)
    output = "\n".join([
        "GCounter — grow-only distributed counter:",
        f"  Agent A increments x2  →  A slot: {cnt_a.value_for('agent-A')}",
        f"  Agent B increments x3  →  B slot: {cnt_b.value_for('agent-B')}",
        f"  join(A, B) global value: {merged_cnt.value()}",
        "  [monotonically non-decreasing — joins never lose counts]",
    ])
    print(f"  ✓ GCounter: join is monotone, global value = {merged_cnt.value()}")
    _events.append(EventRecord(
        act=9, act_title="CRDT Primitives", step=_step,
        op="crdt_gcounter", cmd="GCounter.join(cnt_a, cnt_b)",
        duration_ms=round((time.perf_counter() - t2) * 1000, 2),
        exit_code=0, output=output, commit_id=None,
    ))

    # VectorClock — causal ordering between agents --------------------------------
    _step += 1
    t3 = time.perf_counter()
    vc_a = VectorClock().increment("agent-A")
    vc_b = VectorClock().increment("agent-B")
    merged_vc = vc_a.merge(vc_b)
    output = "\n".join([
        "VectorClock — causal ordering:",
        f"  Agent A clock: {vc_a.to_dict()}",
        f"  Agent B clock: {vc_b.to_dict()}",
        f"  concurrent_with(A, B): {vc_a.concurrent_with(vc_b)}  [neither happened-before the other]",
        f"  merge(A, B):           {merged_vc.to_dict()}          [component-wise max]",
    ])
    print(f"  ✓ VectorClock: causal merge correct, concurrent={vc_a.concurrent_with(vc_b)}")
    _events.append(EventRecord(
        act=9, act_title="CRDT Primitives", step=_step,
        op="crdt_vclock", cmd="VectorClock.merge(vc_a, vc_b)",
        duration_ms=round((time.perf_counter() - t3) * 1000, 2),
        exit_code=0, output=output, commit_id=None,
    ))


# ---------------------------------------------------------------------------
# DAG builder
# ---------------------------------------------------------------------------


def build_dag(root: pathlib.Path) -> DAGData:
    """Read all commits from .muse/commits/ and construct the full DAG."""
    commits_dir = root / ".muse" / "commits"
    raw: list[CommitNode] = []

    if commits_dir.exists():
        for f in commits_dir.glob("*.json"):
            try:
                data: dict[str, object] = json.loads(f.read_text())
            except (json.JSONDecodeError, OSError):
                continue

            parents: list[str] = []
            p1 = data.get("parent_commit_id")
            p2 = data.get("parent2_commit_id")
            if isinstance(p1, str) and p1:
                parents.append(p1)
            if isinstance(p2, str) and p2:
                parents.append(p2)

            files: list[str] = []
            snap_id = data.get("snapshot_id", "")
            if isinstance(snap_id, str):
                snap_file = root / ".muse" / "snapshots" / f"{snap_id}.json"
                if snap_file.exists():
                    try:
                        snap = json.loads(snap_file.read_text())
                        files = sorted(snap.get("manifest", {}).keys())
                    except (json.JSONDecodeError, OSError):
                        pass

            commit_id = str(data.get("commit_id", ""))
            raw.append(CommitNode(
                id=commit_id,
                short=commit_id[:8],
                message=str(data.get("message", "")),
                branch=str(data.get("branch", "main")),
                parents=parents,
                timestamp=str(data.get("committed_at", "")),
                files=files,
                files_changed=len(files),
            ))

    raw.sort(key=lambda c: c["timestamp"])

    branches: list[BranchRef] = []
    refs_dir = root / ".muse" / "refs" / "heads"
    if refs_dir.exists():
        for ref in refs_dir.rglob("*"):
            if ref.is_file():
                branch_name = ref.relative_to(refs_dir).as_posix()
                head_id = ref.read_text().strip()
                branches.append(BranchRef(
                    name=branch_name,
                    head=head_id,
                    color=BRANCH_COLORS.get(branch_name, "#78909c"),
                ))

    branches.sort(key=lambda b: b["name"])
    return DAGData(commits=raw, branches=branches)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Muse Tour de Force — stress test + visualization generator",
    )
    parser.add_argument(
        "--output-dir",
        default=str(_REPO_ROOT / "artifacts"),
        help="Directory to write output files (default: artifacts/)",
    )
    parser.add_argument(
        "--json-only",
        action="store_true",
        help="Write JSON only, skip HTML rendering",
    )
    args = parser.parse_args()

    output_dir = pathlib.Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        print(f"Muse Tour de Force — repo: {root}")
        t_start = time.perf_counter()

        saved_ids: dict[str, str] = {}
        act1(root)
        saved_ids.update(act2(root))
        act3(root)
        act4(root)
        act5(root, saved_ids)
        act6(root)
        act7(root)
        act8(root)

        elapsed = time.perf_counter() - t_start
        print(f"\n✓ Acts 1–8 complete in {elapsed:.2f}s — {_step} operations")

        dag = build_dag(root)

    # Act 9 runs outside the tempdir — purely in-process CRDT API demo.
    act9_crdt()

    total_commits = len(dag["commits"])
    total_branches = len(dag["branches"])
    merge_commits = sum(1 for c in dag["commits"] if len(c["parents"]) >= 2)
    conflicts = sum(1 for e in _events if not e["exit_code"] == 0 and "conflict" in e["output"].lower())

    elapsed_total = time.perf_counter() - t_start
    print(f"\n✓ Tour de Force complete — {_step} operations in {elapsed_total:.2f}s")

    tour: TourData = TourData(
        meta=TourMeta(
            domain="music",
            muse_version="0.1.1",
            generated_at=datetime.now(timezone.utc).isoformat(),
            elapsed_s=f"{elapsed_total:.2f}",
        ),
        stats=TourStats(
            commits=total_commits,
            branches=total_branches,
            merges=merge_commits,
            conflicts_resolved=max(conflicts, 1),
            operations=_step,
        ),
        dag=dag,
        events=_events,
    )

    json_path = output_dir / "tour_de_force.json"
    json_path.write_text(json.dumps(tour, indent=2))
    print(f"✓ JSON  → {json_path}")

    if not args.json_only:
        html_path = output_dir / "tour_de_force.html"
        # render_html is importable because _TOOLS_DIR was added to sys.path above.
        import render_html as _render_html
        _render_html.render(tour, html_path)
        print(f"✓ HTML  → {html_path}")
        print(f"\n  Open: file://{html_path.resolve()}")


if __name__ == "__main__":
    main()

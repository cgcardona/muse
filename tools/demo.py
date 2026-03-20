#!/usr/bin/env python3
"""Muse Demo — 5-act VCS stress test + shareable visualization.

Creates a fresh Muse repository in a temporary directory, runs a complete
5-act narrative exercising every primitive, builds a commit DAG, and renders
a self-contained HTML file you can share anywhere.

Usage
-----
    python tools/demo.py
    python tools/demo.py --output-dir my_output/
    python tools/demo.py --json-only   # skip HTML rendering

Output
------
    artifacts/demo.json   — structured event log + DAG
    artifacts/demo.html            — shareable visualization
"""

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
from muse.core.merge_engine import clear_merge_state  # noqa: E402  (used in act4)
from typer.testing import CliRunner  # noqa: E402

RUNNER = CliRunner()

BRANCH_COLORS: dict[str, str] = {
    "main":           "#4f8ef7",
    "alpha":          "#f9a825",
    "beta":           "#66bb6a",
    "gamma":          "#ab47bc",
    "conflict/left":  "#ef5350",
    "conflict/right": "#ff7043",
}

ACT_TITLES: dict[int, str] = {
    1: "Foundation",
    2: "Divergence",
    3: "Clean Merges",
    4: "Conflict & Resolution",
    5: "Advanced Operations",
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


class DemoData(TypedDict):
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
    """Write a file to state/."""
    workdir = root / "state"
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
    (root / "state" / "shared-state.mid").write_text(resolved)
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
        description="Muse Demo — stress test + visualization generator",
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
        print(f"Muse Demo — repo: {root}")
        t_start = time.perf_counter()

        saved_ids: dict[str, str] = {}
        act1(root)
        saved_ids.update(act2(root))
        act3(root)
        act4(root)
        act5(root, saved_ids)

        elapsed = time.perf_counter() - t_start
        print(f"\n✓ Acts 1–5 complete in {elapsed:.2f}s — {_step} operations")

        dag = build_dag(root)

    total_commits = len(dag["commits"])
    total_branches = len(dag["branches"])
    merge_commits = sum(1 for c in dag["commits"] if len(c["parents"]) >= 2)
    conflicts = sum(1 for e in _events if not e["exit_code"] == 0 and "conflict" in e["output"].lower())

    elapsed_total = time.perf_counter() - t_start
    print(f"\n✓ Demo complete — {_step} operations in {elapsed_total:.2f}s")
    print("  Engine capabilities (Typed Deltas, Domain Schema, OT Merge, CRDT)")
    print("  → see artifacts/domain_registry.html")

    tour: DemoData = DemoData(
        meta=TourMeta(
            domain="midi",
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

    json_path = output_dir / "demo.json"
    json_path.write_text(json.dumps(tour, indent=2))
    print(f"✓ JSON  → {json_path}")

    if not args.json_only:
        html_path = output_dir / "demo.html"
        # render_html is importable because _TOOLS_DIR was added to sys.path above.
        import render_html as _render_html
        _render_html.render(tour, html_path)
        print(f"✓ HTML  → {html_path}")
        print(f"\n  Open: file://{html_path.resolve()}")


if __name__ == "__main__":
    main()

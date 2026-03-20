"""Bisect engine — binary search through commit history to locate regressions.

``muse bisect`` is a power-tool for both human engineers and autonomous agents.
Given a known-bad commit and a known-good commit, it performs a binary search
through the commits between them, asking at each midpoint "is this good or
bad?" until the first bad commit is isolated.

Agent-safety
------------
The ``run`` subcommand accepts an arbitrary shell command.  The command is
executed in a subprocess; the exit code determines the verdict:

    0         → good (the bug is NOT present)
    125       → skip (cannot test this commit — e.g. build fails)
    any other → bad  (the bug IS present)

This mirrors Git's bisect protocol so any existing test harness works without
modification.

State file
----------
Bisect state is stored at ``.muse/BISECT_STATE.toml``::

    bad_id = "<sha256>"
    good_ids = ["<sha256>", …]
    skipped_ids = ["<sha256>", …]
    remaining = ["<sha256>", …]        # sorted oldest-first
    log = ["<sha256> <verdict> <ts>", …]

The remaining list is rebuilt at every step so it tolerates interruptions.
"""

from __future__ import annotations

import datetime
import logging
import pathlib
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from typing import TypedDict

logger = logging.getLogger(__name__)

_BISECT_STATE_FILE = ".muse/BISECT_STATE.toml"
_SKIP_EXIT_CODE = 125


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------


class BisectStateDict(TypedDict, total=False):
    """On-disk shape of the bisect state."""

    bad_id: str
    good_ids: list[str]
    skipped_ids: list[str]
    remaining: list[str]
    log: list[str]
    branch: str


def _state_path(repo_root: pathlib.Path) -> pathlib.Path:
    return repo_root / ".muse" / "BISECT_STATE.toml"


def _load_state(repo_root: pathlib.Path) -> BisectStateDict | None:
    """Return the bisect state if a session is active, else None."""
    import tomllib

    path = _state_path(repo_root)
    if not path.exists():
        return None
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("⚠️ Could not read bisect state: %s", exc)
        return None
    state: BisectStateDict = {}
    if "bad_id" in raw and isinstance(raw["bad_id"], str):
        state["bad_id"] = raw["bad_id"]
    if "branch" in raw and isinstance(raw["branch"], str):
        state["branch"] = raw["branch"]
    for key in ("good_ids", "skipped_ids", "remaining", "log"):
        val = raw.get(key)
        if isinstance(val, list) and all(isinstance(x, str) for x in val):
            str_list: list[str] = list(val)
            if key == "good_ids":
                state["good_ids"] = str_list
            elif key == "skipped_ids":
                state["skipped_ids"] = str_list
            elif key == "remaining":
                state["remaining"] = str_list
            elif key == "log":
                state["log"] = str_list
    return state


def _save_state(repo_root: pathlib.Path, state: BisectStateDict) -> None:
    """Write the bisect state to disk (TOML, atomic)."""
    path = _state_path(repo_root)
    lines = [f'bad_id = "{state.get("bad_id", "")}"']
    if "branch" in state:
        lines.append(f'branch = "{state["branch"]}"')
    for key, items in (
        ("good_ids", state.get("good_ids", [])),
        ("skipped_ids", state.get("skipped_ids", [])),
        ("remaining", state.get("remaining", [])),
        ("log", state.get("log", [])),
    ):
        formatted = ", ".join(f'"{v}"' for v in items)
        lines.append(f"{key} = [{formatted}]")
    tmp = path.with_suffix(".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    tmp.replace(path)


# ---------------------------------------------------------------------------
# Commit graph helpers
# ---------------------------------------------------------------------------


def _ancestors(
    repo_root: pathlib.Path,
    start_id: str,
) -> list[str]:
    """Return commit IDs from start_id back to root, oldest first."""
    from muse.core.store import read_commit

    visited: list[str] = []
    queue = [start_id]
    seen: set[str] = set()
    while queue:
        cid = queue.pop()
        if cid in seen:
            continue
        seen.add(cid)
        commit = read_commit(repo_root, cid)
        if commit is None:
            continue
        visited.append(cid)
        if commit.parent_commit_id:
            queue.append(commit.parent_commit_id)
        if commit.parent2_commit_id:
            queue.append(commit.parent2_commit_id)
    # Reverse to get oldest-first chronological order.
    return list(reversed(visited))


def _reachable_from_good(
    repo_root: pathlib.Path,
    good_ids: list[str],
) -> set[str]:
    """Return all commits reachable (inclusive) from any good commit."""
    from muse.core.store import read_commit

    reachable: set[str] = set()
    queue = list(good_ids)
    while queue:
        cid = queue.pop()
        if cid in reachable:
            continue
        reachable.add(cid)
        commit = read_commit(repo_root, cid)
        if commit is None:
            continue
        if commit.parent_commit_id:
            queue.append(commit.parent_commit_id)
        if commit.parent2_commit_id:
            queue.append(commit.parent2_commit_id)
    return reachable


def _build_remaining(
    repo_root: pathlib.Path,
    bad_id: str,
    good_ids: list[str],
    skipped_ids: list[str],
) -> list[str]:
    """Return commits between good and bad (exclusive of good, inclusive of bad)."""
    ancestors_of_bad = _ancestors(repo_root, bad_id)
    good_reachable = _reachable_from_good(repo_root, good_ids)
    skipped = set(skipped_ids)
    return [
        cid for cid in ancestors_of_bad
        if cid not in good_reachable and cid not in skipped
    ]


def _midpoint(remaining: list[str]) -> str | None:
    if not remaining:
        return None
    return remaining[len(remaining) // 2]


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class BisectResult:
    """Result of a single bisect step."""

    done: bool = False
    """True when we have isolated the first bad commit."""

    first_bad: str | None = None
    """Commit ID of the isolated first-bad commit."""

    next_to_test: str | None = None
    """Commit ID to test next (None when done)."""

    remaining_count: int = 0
    """How many commits remain to test."""

    steps_remaining: int = 0
    """Approximate remaining binary-search steps."""

    verdict: str = ""
    """The verdict just applied: 'good', 'bad', 'skip'."""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def start_bisect(
    repo_root: pathlib.Path,
    bad_id: str,
    good_ids: list[str],
    branch: str = "",
) -> BisectResult:
    """Start a new bisect session.

    Args:
        repo_root:  Repository root.
        bad_id:     Known-bad commit.
        good_ids:   One or more known-good commits.
        branch:     Current branch name (for display only).
    """
    remaining = _build_remaining(repo_root, bad_id, good_ids, skipped_ids=[])
    state: BisectStateDict = {
        "bad_id": bad_id,
        "good_ids": good_ids,
        "skipped_ids": [],
        "remaining": remaining,
        "log": [
            f"{bad_id} bad {_ts()}",
            *[f"{g} good {_ts()}" for g in good_ids],
        ],
    }
    if branch:
        state["branch"] = branch
    _save_state(repo_root, state)

    next_id = _midpoint(remaining)
    import math
    steps = int(math.log2(len(remaining) + 1)) if remaining else 0
    return BisectResult(
        done=next_id is None,
        first_bad=bad_id if next_id is None else None,
        next_to_test=next_id,
        remaining_count=len(remaining),
        steps_remaining=steps,
        verdict="started",
    )


def _ts() -> str:
    return datetime.datetime.now(tz=datetime.timezone.utc).isoformat()


def _apply_verdict(
    repo_root: pathlib.Path,
    commit_id: str,
    verdict: str,
) -> BisectResult:
    """Apply a 'good', 'bad', or 'skip' verdict and return the next step."""
    import math

    state = _load_state(repo_root)
    if state is None:
        raise RuntimeError("No bisect session in progress. Run 'muse bisect start' first.")

    bad_id = state.get("bad_id", "")
    good_ids = list(state.get("good_ids", []))
    skipped_ids = list(state.get("skipped_ids", []))
    log = list(state.get("log", []))

    if verdict == "good":
        good_ids.append(commit_id)
    elif verdict == "bad":
        bad_id = commit_id
    else:
        skipped_ids.append(commit_id)

    log.append(f"{commit_id} {verdict} {_ts()}")

    remaining = _build_remaining(repo_root, bad_id, good_ids, skipped_ids)
    new_state: BisectStateDict = {
        "bad_id": bad_id,
        "good_ids": good_ids,
        "skipped_ids": skipped_ids,
        "remaining": remaining,
        "log": log,
    }
    if "branch" in state:
        new_state["branch"] = state["branch"]
    _save_state(repo_root, new_state)

    if len(remaining) <= 1:
        # Done — bad_id is the first bad commit.
        return BisectResult(
            done=True,
            first_bad=bad_id,
            next_to_test=None,
            remaining_count=0,
            steps_remaining=0,
            verdict=verdict,
        )

    next_id = _midpoint(remaining)
    steps = int(math.log2(len(remaining) + 1))
    return BisectResult(
        done=False,
        first_bad=None,
        next_to_test=next_id,
        remaining_count=len(remaining),
        steps_remaining=steps,
        verdict=verdict,
    )


def mark_good(repo_root: pathlib.Path, commit_id: str) -> BisectResult:
    """Mark *commit_id* as good and advance the bisect."""
    return _apply_verdict(repo_root, commit_id, "good")


def mark_bad(repo_root: pathlib.Path, commit_id: str) -> BisectResult:
    """Mark *commit_id* as bad and advance the bisect."""
    return _apply_verdict(repo_root, commit_id, "bad")


def skip_commit(repo_root: pathlib.Path, commit_id: str) -> BisectResult:
    """Skip *commit_id* (e.g. build fails) and advance the bisect."""
    return _apply_verdict(repo_root, commit_id, "skip")


def reset_bisect(repo_root: pathlib.Path) -> None:
    """End the bisect session and remove state."""
    path = _state_path(repo_root)
    if path.exists():
        path.unlink()


def get_bisect_log(repo_root: pathlib.Path) -> list[str]:
    """Return the bisect log entries, oldest first."""
    state = _load_state(repo_root)
    if state is None:
        return []
    return list(state.get("log", []))


def is_bisect_active(repo_root: pathlib.Path) -> bool:
    """Return True if a bisect session is in progress."""
    return _state_path(repo_root).exists()


def run_bisect_command(
    repo_root: pathlib.Path,
    command: str,
    current_commit_id: str,
) -> BisectResult:
    """Run *command* in a shell, interpret exit code, and apply verdict.

    Exit codes::

        0    → good
        125  → skip
        1-124, 126-255 → bad

    The command is executed with the repository root as the working directory.
    """
    result = subprocess.run(
        command,
        shell=True,
        cwd=str(repo_root),
    )
    code = result.returncode
    if code == 0:
        verdict = "good"
    elif code == _SKIP_EXIT_CODE:
        verdict = "skip"
    else:
        verdict = "bad"
    return _apply_verdict(repo_root, current_commit_id, verdict)

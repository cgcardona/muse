"""muse plumbing name-rev — map commit IDs to branch-relative names.

For each supplied commit ID, walks the commit DAG from all branch tips
simultaneously and finds the branch + distance that best describes it.
The result is expressed as ``<branch>~N`` where N is the number of parent
hops from that branch tip to the commit (0 means the commit IS the tip).

The multi-source BFS ensures O(total-commits) time regardless of the number
of branches or input commit IDs — every commit is visited at most once.

Output (JSON, default)::

    {
      "results": [
        {
          "commit_id": "<sha256>",
          "name":      "main~3",
          "branch":    "main",
          "distance":  3,
          "undefined": false
        },
        {
          "commit_id": "<sha256>",
          "name":      null,
          "branch":    null,
          "distance":  null,
          "undefined": true
        }
      ]
    }

Text output (``--format text``)::

    <sha256>  main~3
    <sha256>  undefined

With ``--name-only``::

    main~3
    undefined

Plumbing contract
-----------------

- Exit 0: all names resolved (some may be ``undefined`` when unreachable).
- Exit 1: bad ``--format``; missing arguments.
- Exit 3: I/O error reading commit records.
"""

from __future__ import annotations

import argparse
import json
import logging
import pathlib
import sys
from collections import deque
from typing import TypedDict

from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.store import read_commit

logger = logging.getLogger(__name__)

_FORMAT_CHOICES = ("json", "text")
_MAX_WALK = 50_000  # Safety ceiling — prevents runaway on pathological graphs


class _NameRevEntry(TypedDict):
    commit_id: str
    name: str | None
    branch: str | None
    distance: int | None
    undefined: bool


def _build_name_map(
    root: pathlib.Path,
    targets: set[str],
) -> dict[str, tuple[str, int]]:
    """Return a map of commit_id → (branch, distance) for all reachable commits.

    Multi-source BFS from every branch tip.  Each commit is visited at most
    once — whichever branch reaches it first (shortest distance) wins.
    Stops early once all *targets* have been found, or the walk ceiling is hit.
    """
    heads_dir = root / ".muse" / "refs" / "heads"
    if not heads_dir.exists():
        return {}

    # (commit_id, branch_name, distance)
    queue: deque[tuple[str, str, int]] = deque()
    visited: dict[str, tuple[str, int]] = {}

    for ref_file in sorted(heads_dir.iterdir()):
        if not ref_file.is_file():
            continue
        branch = ref_file.name
        tip_id = ref_file.read_text(encoding="utf-8").strip()
        if tip_id and tip_id not in visited:
            visited[tip_id] = (branch, 0)
            queue.append((tip_id, branch, 0))

    found = set(targets) & set(visited)
    steps = 0

    while queue and steps < _MAX_WALK:
        cid, branch, dist = queue.popleft()
        steps += 1

        if cid in targets:
            found.add(cid)
            if found >= targets:
                break

        try:
            record = read_commit(root, cid)
        except Exception as exc:
            logger.debug("name-rev: cannot read commit %s: %s", cid[:12], exc)
            continue

        if record is None:
            continue

        for parent_id in (record.parent_commit_id, record.parent2_commit_id):
            if parent_id and parent_id not in visited:
                visited[parent_id] = (branch, dist + 1)
                queue.append((parent_id, branch, dist + 1))

    return visited


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the name-rev subcommand."""
    parser = subparsers.add_parser(
        "name-rev",
        help="Map commit IDs to descriptive branch-relative names.",
        description=__doc__,
    )
    parser.add_argument(
        "commit_ids",
        nargs="+",
        help="One or more commit IDs to map to branch-relative names.",
    )
    parser.add_argument(
        "--name-only", "-n",
        action="store_true",
        dest="name_only",
        help="Emit only the name (or 'undefined'), not the commit ID.",
    )
    parser.add_argument(
        "--undefined", "-u",
        default="undefined",
        dest="undefined_name",
        metavar="STRING",
        help="String to emit when a commit cannot be named. (default: 'undefined')",
    )
    parser.add_argument(
        "--format", "-f",
        dest="fmt",
        default="json",
        metavar="FORMAT",
        help="Output format: json or text. (default: json)",
    )
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> None:
    """Map commit IDs to descriptive branch-relative names.

    For each commit ID, finds the branch tip that is closest (fewest parent
    hops) and returns a name of the form ``<branch>~N``.  When ``N`` is 0 the
    commit is the branch tip itself.
    """
    fmt: str = args.fmt
    commit_ids: list[str] = args.commit_ids
    name_only: bool = args.name_only
    undefined_name: str = args.undefined_name

    if fmt not in _FORMAT_CHOICES:
        print(
            json.dumps(
                {"error": f"Unknown format {fmt!r}. Valid: {', '.join(_FORMAT_CHOICES)}"}
            )
        )
        raise SystemExit(ExitCode.USER_ERROR)

    if not commit_ids:
        print(json.dumps({"error": "At least one commit ID is required."}))
        raise SystemExit(ExitCode.USER_ERROR)

    root = require_repo()

    try:
        name_map = _build_name_map(root, set(commit_ids))
    except OSError as exc:
        logger.debug("name-rev I/O error: %s", exc)
        print(json.dumps({"error": str(exc)}))
        raise SystemExit(ExitCode.INTERNAL_ERROR)

    results: list[_NameRevEntry] = []
    for cid in commit_ids:
        if cid in name_map:
            branch, dist = name_map[cid]
            human_name = branch if dist == 0 else f"{branch}~{dist}"
            results.append(
                _NameRevEntry(
                    commit_id=cid,
                    name=human_name,
                    branch=branch,
                    distance=dist,
                    undefined=False,
                )
            )
        else:
            results.append(
                _NameRevEntry(
                    commit_id=cid,
                    name=None,
                    branch=None,
                    distance=None,
                    undefined=True,
                )
            )

    if fmt == "text":
        for r in results:
            display_name = r["name"] if r["name"] is not None else undefined_name
            if name_only:
                print(display_name)
            else:
                print(f"{r['commit_id']}  {display_name}")
        return

    print(json.dumps({"results": [dict(r) for r in results]}))

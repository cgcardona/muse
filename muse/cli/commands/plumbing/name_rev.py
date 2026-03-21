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

import json
import logging
import pathlib
from collections import deque
from typing import TypedDict

import typer

from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.store import read_commit

logger = logging.getLogger(__name__)

app = typer.Typer()

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


@app.callback(invoke_without_command=True)
def name_rev(
    ctx: typer.Context,
    commit_ids: list[str] = typer.Argument(
        ..., help="One or more commit IDs to map to branch-relative names."
    ),
    name_only: bool = typer.Option(
        False,
        "--name-only",
        "-n",
        help="Emit only the name (or 'undefined'), not the commit ID.",
    ),
    undefined_name: str = typer.Option(
        "undefined",
        "--undefined",
        "-u",
        help="String to emit when a commit cannot be named (default: 'undefined').",
    ),
    fmt: str = typer.Option(
        "json", "--format", "-f", help="Output format: json or text."
    ),
) -> None:
    """Map commit IDs to descriptive branch-relative names.

    For each commit ID, finds the branch tip that is closest (fewest parent
    hops) and returns a name of the form ``<branch>~N``.  When ``N`` is 0 the
    commit is the branch tip itself.

    A single multi-source BFS from all branch tips is performed, so the
    command is efficient regardless of the number of input IDs or branches.

    Commits that are not reachable from any branch tip are reported as
    ``undefined`` (or the value of ``--undefined``).

    Example::

        muse plumbing name-rev $(muse plumbing rev-parse HEAD -f text)
        # → main~0
    """
    if fmt not in _FORMAT_CHOICES:
        typer.echo(
            json.dumps(
                {"error": f"Unknown format {fmt!r}. Valid: {', '.join(_FORMAT_CHOICES)}"}
            )
        )
        raise typer.Exit(code=ExitCode.USER_ERROR)

    if not commit_ids:
        typer.echo(json.dumps({"error": "At least one commit ID is required."}))
        raise typer.Exit(code=ExitCode.USER_ERROR)

    root = require_repo()

    try:
        name_map = _build_name_map(root, set(commit_ids))
    except OSError as exc:
        logger.debug("name-rev I/O error: %s", exc)
        typer.echo(json.dumps({"error": str(exc)}))
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)

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
                typer.echo(display_name)
            else:
                typer.echo(f"{r['commit_id']}  {display_name}")
        return

    typer.echo(json.dumps({"results": [dict(r) for r in results]}))

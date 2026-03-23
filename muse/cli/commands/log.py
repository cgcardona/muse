"""muse log \033[1m—\033[0m display commit history.

Output modes
------------

Default::

    \033[33mcommit a1b2c3d4\033[0m \033[33m(\033[0m\033[1m\033[36mHEAD\033[0m -> \033[1m\033[32mmain\033[0m\033[33m)\033[0m
    Author: gabriel
    \033[2mDate:   2026-03-16 12:00:00 UTC\033[0m

        Add verse melody

\033[1m--oneline\033[0m::

    \033[33ma1b2c3d4\033[0m \033[33m(\033[0m\033[1m\033[36mHEAD\033[0m -> \033[1m\033[32mmain\033[0m\033[33m)\033[0m Add verse melody
    \033[33mf9e8d7c6\033[0m Initial commit

\033[1m--graph\033[0m::

    \033[1m*\033[0m \033[33ma1b2c3d4\033[0m \033[33m(\033[0m\033[1m\033[36mHEAD\033[0m -> \033[1m\033[32mmain\033[0m\033[33m)\033[0m Add verse melody
    \033[1m*\033[0m \033[33mf9e8d7c6\033[0m Initial commit

\033[1m--stat\033[0m::

    \033[33mcommit a1b2c3d4\033[0m \033[33m(\033[0m\033[1m\033[36mHEAD\033[0m -> \033[1m\033[32mmain\033[0m\033[33m)\033[0m
    \033[2mDate:   2026-03-16 12:00:00 UTC\033[0m

        Add verse melody

    \033[32m + tracks/drums.mid\033[0m
    \033[2m 1 added, 0 removed\033[0m

SemVer bumps are coloured: \033[32mPATCH\033[0m  \033[33mMINOR\033[0m  \033[31mMAJOR\033[0m

Filters: --since, --until, --author, --section, --track, --emotion
"""

from __future__ import annotations

import argparse
import heapq
import json
import logging
import pathlib
import re
import sys
from datetime import datetime, timedelta, timezone

from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.store import (
    CommitRecord,
    get_commit_snapshot_manifest,
    get_commits_for_branch,
    get_head_commit_id,
    read_commit,
    read_current_branch,
)
from muse.core.validation import sanitize_display

logger = logging.getLogger(__name__)

_DEFAULT_LIMIT = 1000

# ANSI colour helpers — only emitted when stdout is a TTY.
_RESET  = "\033[0m"
_BOLD   = "\033[1m"
_YELLOW = "\033[33m"
_GREEN  = "\033[32m"
_RED    = "\033[31m"
_CYAN   = "\033[36m"
_DIM    = "\033[2m"


def _c(text: str, *codes: str, tty: bool) -> str:
    """Wrap *text* in ANSI *codes* when *tty* is True."""
    if not tty:
        return text
    return "".join(codes) + text + _RESET


def _ref_label(branch: str, is_head: bool, tty: bool) -> str:
    """Format the ``(HEAD -> branch)`` decoration."""
    if not is_head:
        return ""
    if not tty:
        return f" (HEAD -> {branch})"
    head = _c("HEAD", _BOLD, _CYAN, tty=tty)
    arrow = _c(" -> ", _RESET, tty=tty)
    br = _c(branch, _BOLD, _GREEN, tty=tty)
    paren_open  = _c("(", _YELLOW, tty=tty)
    paren_close = _c(")", _YELLOW, tty=tty)
    return f" {paren_open}{head}{arrow}{br}{paren_close}"


_SEMVER_COLOUR: dict[str, str] = {
    "major": _RED,
    "minor": _YELLOW,
    "patch": _GREEN,
}


def _read_branch(root: pathlib.Path) -> str:
    return read_current_branch(root)


def _read_repo_id(root: pathlib.Path) -> str:
    return str(json.loads((root / ".muse" / "repo.json").read_text())["repo_id"])


def _parse_date(text: str) -> datetime:
    text = text.strip().lower()
    now = datetime.now(timezone.utc)
    if text == "today":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if text == "yesterday":
        return (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    m = re.match(r"^(\d+)\s+(day|week|month|year)s?\s+ago$", text)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        deltas = {"day": timedelta(days=n), "week": timedelta(weeks=n),
                  "month": timedelta(days=n * 30), "year": timedelta(days=n * 365)}
        return now - deltas[unit]
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    raise ValueError(f"Cannot parse date: {text!r}")


def _file_diff(root: pathlib.Path, commit: CommitRecord) -> tuple[list[str], list[str]]:
    """Return (added, removed) file lists relative to the commit's parent."""
    current_manifest = get_commit_snapshot_manifest(root, commit.commit_id) or {}
    if commit.parent_commit_id:
        parent_manifest = get_commit_snapshot_manifest(root, commit.parent_commit_id) or {}
    else:
        parent_manifest = {}
    added = sorted(set(current_manifest) - set(parent_manifest))
    removed = sorted(set(parent_manifest) - set(current_manifest))
    return added, removed


def _format_date(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S UTC") if dt.tzinfo else str(dt)


# ---------------------------------------------------------------------------
# DAG graph rendering helpers
# ---------------------------------------------------------------------------


def _branch_tips(root: pathlib.Path) -> dict[str, list[str]]:
    """Return ``{commit_id: [branch_name, …]}`` for all local branch tips."""
    heads_dir = root / ".muse" / "refs" / "heads"
    if not heads_dir.exists():
        return {}
    tips: dict[str, list[str]] = {}
    for p in heads_dir.rglob("*"):
        if p.is_file():
            cid = p.read_text().strip()
            name = p.relative_to(heads_dir).as_posix()
            if cid:
                tips.setdefault(cid, []).append(name)
    return tips


def _collect_all_commits(
    root: pathlib.Path, start_ids: list[str]
) -> dict[str, CommitRecord]:
    """BFS from *start_ids*, returning every reachable commit."""
    seen: dict[str, CommitRecord] = {}
    queue = list(start_ids)
    while queue:
        cid = queue.pop(0)
        if cid in seen:
            continue
        rec = read_commit(root, cid)
        if rec is None:
            continue
        seen[cid] = rec
        for parent in (rec.parent_commit_id, rec.parent2_commit_id):
            if parent and parent not in seen:
                queue.append(parent)
    return seen


def _topo_sort(commits: dict[str, CommitRecord]) -> list[CommitRecord]:
    """Return commits newest-first using Kahn's algorithm.

    In-degree counts the number of *child* commits that reference each commit
    as a parent, so commits with no children (branch tips) are processed first.
    Ties are broken by timestamp (most recent first).
    """
    in_degree: dict[str, int] = {cid: 0 for cid in commits}
    for rec in commits.values():
        for parent in (rec.parent_commit_id, rec.parent2_commit_id):
            if parent and parent in commits:
                in_degree[parent] += 1

    # Seeds: commits nobody points to (branch tips / leaves)
    heap: list[tuple[float, str]] = []
    for cid, deg in in_degree.items():
        if deg == 0:
            ts = -commits[cid].committed_at.timestamp()
            heapq.heappush(heap, (ts, cid))

    result: list[CommitRecord] = []
    while heap:
        _, cid = heapq.heappop(heap)
        result.append(commits[cid])
        rec = commits[cid]
        for parent in (rec.parent_commit_id, rec.parent2_commit_id):
            if parent and parent in commits:
                in_degree[parent] -= 1
                if in_degree[parent] == 0:
                    ts = -commits[parent].committed_at.timestamp()
                    heapq.heappush(heap, (ts, parent))
    return result


def _deco_str(
    cid: str,
    head_cid: str,
    current: str,
    tips: dict[str, list[str]],
    tty: bool,
) -> str:
    """Format the ``(HEAD -> branch, other-branch)`` decoration for a commit."""
    branches = tips.get(cid, [])
    if not branches:
        return ""
    labels: list[str] = []
    if cid == head_cid and current in branches:
        head = _c("HEAD", _BOLD, _CYAN, tty=tty)
        br = _c(current, _BOLD, _GREEN, tty=tty)
        labels.append(f"{head}{_c(' -> ', _RESET, tty=tty)}{br}")
        for b in branches:
            if b != current:
                labels.append(_c(b, _BOLD, _GREEN, tty=tty))
    else:
        for b in branches:
            labels.append(_c(b, _BOLD, _GREEN, tty=tty))
    inner = ", ".join(labels)
    return f" {_c('(', _YELLOW, tty=tty)}{inner}{_c(')', _YELLOW, tty=tty)}"


def _render_graph(
    root: pathlib.Path,
    branch: str,
    all_branches: bool,
    tty: bool,
) -> None:
    """Render a lane-based ASCII DAG, git-log-style."""
    current = read_current_branch(root)
    tips = _branch_tips(root)

    if all_branches:
        start_ids = list(tips.keys())
    else:
        head = get_head_commit_id(root, branch)
        start_ids = [head] if head else []

    if not start_ids:
        print("(no commits)")
        return

    all_commits = _collect_all_commits(root, start_ids)
    if not all_commits:
        print("(no commits)")
        return

    head_cid = get_head_commit_id(root, current) or ""
    sorted_commits = _topo_sort(all_commits)

    # lanes: list of commit IDs we're "awaiting" (open lines of descent).
    # None marks a closed/empty column slot.
    lanes: list[str | None] = []

    for idx, commit in enumerate(sorted_commits):
        cid = commit.commit_id
        parents = [
            p for p in (commit.parent_commit_id, commit.parent2_commit_id)
            if p and p in all_commits
        ]

        # Assign this commit to a column.
        col = lanes.index(cid) if cid in lanes else -1
        if col == -1:
            # New tip — place in the first empty slot or append.
            if None in lanes:
                col = lanes.index(None)
                lanes[col] = cid
            else:
                col = len(lanes)
                lanes.append(cid)

        # Build the graph character row.
        width = len(lanes)
        row: list[str] = []
        for i in range(width):
            if i == col:
                row.append(_c("*", _BOLD, tty=tty))
            elif lanes[i] is not None:
                row.append("|")
            else:
                row.append(" ")

        graph_prefix = " ".join(row).rstrip()
        short_hash = _c(cid[:8], _YELLOW, tty=tty)
        deco = _deco_str(cid, head_cid, current, tips, tty)
        msg = sanitize_display(commit.message.splitlines()[0])
        print(f"{graph_prefix} {short_hash}{deco} {msg}")

        # Update lanes for the next commit:
        # replace this commit's slot with its first parent;
        # insert additional parents (merge) into empty slots or new columns.
        if parents:
            lanes[col] = parents[0]
            for extra in parents[1:]:
                if extra not in lanes:
                    if None in lanes:
                        lanes[lanes.index(None)] = extra
                    else:
                        lanes.append(extra)
        else:
            lanes[col] = None  # root commit — close this lane

        # Draw a connector line between commits (skip after the last one).
        if idx < len(sorted_commits) - 1:
            # Detect merge: current commit has two parents → next line needs `|\`
            is_merge = len(parents) >= 2
            connector: list[str] = []
            for i in range(len(lanes)):
                if i == col and is_merge:
                    connector.append("|\\")
                elif lanes[i] is not None:
                    connector.append("| ")
                else:
                    connector.append("  ")
            line = "".join(connector).rstrip()
            if line:
                print(line)

        # Trim trailing empty slots to keep the lane list compact.
        while lanes and lanes[-1] is None:
            lanes.pop()


# ---------------------------------------------------------------------------
# CLI registration
# ---------------------------------------------------------------------------


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the log subcommand."""
    parser = subparsers.add_parser(
        "log",
        help="Display commit history.",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("ref", nargs="?", default=None, help="Branch or commit to start from.")
    parser.add_argument("--oneline", action="store_true", help="One line per commit.")
    parser.add_argument("--graph", "-g", action="store_true", help="ASCII DAG graph.")
    parser.add_argument("--all", "-A", action="store_true", dest="all_branches",
                        help="Include all local branches in the graph (implies --graph).")
    parser.add_argument("--stat", action="store_true", help="Show file change summary.")
    parser.add_argument("--patch", "-p", action="store_true", help="Show file change summary (added/removed/modified counts) alongside each commit.")
    parser.add_argument("-n", "--max-count", type=int, default=_DEFAULT_LIMIT, dest="limit", help="Limit number of commits.")
    parser.add_argument("--since", default=None, help="Show commits after date.")
    parser.add_argument("--until", default=None, help="Show commits before date.")
    parser.add_argument("--author", default=None, help="Filter by author.")
    parser.add_argument("--section", default=None, help="Filter by section metadata.")
    parser.add_argument("--track", default=None, help="Filter by track metadata.")
    parser.add_argument("--emotion", default=None, help="Filter by emotion metadata.")
    parser.add_argument("--format", "-f", default="text", dest="fmt", help="Output format: text or json.")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> None:
    """Display commit history.

    Agents should pass ``--format json`` to receive a JSON array where each
    element is a commit object with fields: ``commit_id``, ``branch``,
    ``message``, ``author``, ``committed_at``, ``parent_commit_id``,
    ``snapshot_id``, ``metadata``, and ``sem_ver_bump``.
    """
    ref: str | None = args.ref
    oneline: bool = args.oneline
    graph: bool = args.graph
    all_branches: bool = args.all_branches
    stat: bool = args.stat
    patch: bool = args.patch
    limit: int = args.limit
    since: str | None = args.since
    until: str | None = args.until
    author: str | None = args.author
    section: str | None = args.section
    track: str | None = args.track
    emotion: str | None = args.emotion
    fmt: str = args.fmt

    # --all implies --graph
    if all_branches:
        graph = True

    # Support git-style -<n> shorthand (e.g. `muse log -5` as alias for `-n 5`).
    # argparse captures "-5" as the positional `ref` since it looks like an option;
    # detect that pattern here and reinterpret it as a limit.
    if ref is not None and ref.lstrip("-").isdigit() and ref.startswith("-"):
        limit = int(ref.lstrip("-"))
        ref = None

    if fmt not in ("text", "json"):
        print(f"❌ Unknown --format '{sanitize_display(fmt)}'. Choose text or json.", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)
    if limit < 1:
        print("❌ --max-count must be at least 1.", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)
    root = require_repo()
    repo_id = _read_repo_id(root)
    # `ref` may have been cleared above if it was a `-<n>` shorthand.
    branch = ref or _read_branch(root)

    # Graph mode bypasses the normal linear walk entirely.
    if graph and fmt == "text":
        _render_graph(root, branch=branch, all_branches=all_branches, tty=sys.stdout.isatty())
        return

    since_dt = _parse_date(since) if since else None
    until_dt = _parse_date(until) if until else None

    # When no filters are active the walk can stop as soon as it has collected
    # `limit` commits — no need to read the entire chain.  With any filter we
    # must read ahead because commits may be skipped, so we pass max_count=0
    # (unbounded) and let the filter loop enforce the limit.
    has_filters = any([since_dt, until_dt, author, section, track, emotion])
    walk_limit = 0 if has_filters else limit
    commits = get_commits_for_branch(root, repo_id, branch, max_count=walk_limit)

    # Apply filters
    filtered: list[CommitRecord] = []
    for c in commits:
        if since_dt and c.committed_at < since_dt:
            continue
        if until_dt and c.committed_at > until_dt:
            continue
        if author and author.lower() not in c.author.lower():
            continue
        if section and c.metadata.get("section") != section:
            continue
        if track and c.metadata.get("track") != track:
            continue
        if emotion and c.metadata.get("emotion") != emotion:
            continue
        filtered.append(c)
        # Guard against zero or negative limit causing unbounded traversal.
        if limit > 0 and len(filtered) >= limit:
            break

    if not filtered:
        if fmt == "json":
            print("[]")
        else:
            print("(no commits)")
        return

    if fmt == "json":
        print(json.dumps([{
            "commit_id": c.commit_id,
            "branch": c.branch,
            "message": c.message,
            "author": c.author,
            "committed_at": c.committed_at.isoformat(),
            "parent_commit_id": c.parent_commit_id,
            "snapshot_id": c.snapshot_id,
            "metadata": c.metadata,
            "sem_ver_bump": c.sem_ver_bump,
        } for c in filtered], indent=2, default=str))
        return

    head_commit_id = filtered[0].commit_id if filtered else None
    tty: bool = sys.stdout.isatty()

    for c in filtered:
        is_head = c.commit_id == head_commit_id
        decoration = _ref_label(branch, is_head, tty)

        short_hash = _c(c.commit_id[:8], _YELLOW, tty=tty)
        # Always use first line only for the subject; full body printed below in default mode.
        subject = sanitize_display(c.message.splitlines()[0])
        author_display = sanitize_display(c.author)

        if oneline:
            print(f"{short_hash}{decoration} {subject}")

        else:
            commit_word = _c("commit", _YELLOW, tty=tty)
            print(f"{commit_word} {short_hash}{decoration}")
            if author_display:
                print(f"Author: {author_display}")
            print(f"Date:   {_c(_format_date(c.committed_at), _DIM, tty=tty)}")

            if c.sem_ver_bump and c.sem_ver_bump != "none":
                bump_key = c.sem_ver_bump.lower()
                bump_colour = _SEMVER_COLOUR.get(bump_key, "")
                bump_label = _c(c.sem_ver_bump.upper(), bump_colour, tty=tty) if bump_colour else c.sem_ver_bump.upper()
                print(f"SemVer: {bump_label}")
                if c.breaking_changes:
                    safe_breaks = [sanitize_display(b) for b in c.breaking_changes[:3]]
                    breaking_text = ", ".join(safe_breaks)
                    if len(c.breaking_changes) > 3:
                        breaking_text += f" +{len(c.breaking_changes) - 3} more"
                    print(f"Breaking: {_c(breaking_text, _RED, tty=tty)}")

            if c.metadata:
                meta_parts = [
                    f"{sanitize_display(k)}: {sanitize_display(v)}"
                    for k, v in sorted(c.metadata.items())
                ]
                print(f"Meta:   {', '.join(meta_parts)}")

            print(f"\n    {sanitize_display(c.message)}\n")

            if stat or patch:
                added, removed = _file_diff(root, c)
                for p in added:
                    print(_c(f" + {p}", _GREEN, tty=tty))
                for p in removed:
                    print(_c(f" - {p}", _RED, tty=tty))
                if added or removed:
                    summary = f" {len(added)} added, {len(removed)} removed"
                    print(_c(summary, _DIM, tty=tty) + "\n")

"""muse symbol-log — track a single semantic symbol through commit history.

This command is impossible in Git: Git's ``git log -p src/utils.py`` shows
every line that changed in a file; it has no concept of a *function*.
``muse symbol-log`` tracks the full lifecycle of a single named symbol —
when it was created, when its implementation changed, when it was renamed,
and when it was deleted — across the entire commit DAG.

Usage::

    muse symbol-log "src/utils.py::calculate_total"
    muse symbol-log "src/models.py::User.save"

Output::

    Symbol: src/utils.py::calculate_total
    ──────────────────────────────────────────────────────────────

    ● a3f2c9  2026-03-14  "Refactor: extract validation logic"
      created  function calculate_total

    ● cb4afa  2026-03-15  "Perf: optimise total calculation"
      modified  implementation changed

    ● 1d2e3f  2026-03-16  "Rename: calculate_total → compute_total"
      renamed   calculate_total → compute_total
      (tracking continues as src/utils.py::compute_total)

    ● 4a5b6c  2026-03-17  "Move: refactor to helpers module"
      moved     src/utils.py::compute_total → src/helpers.py::compute_total
      (tracking continues at src/helpers.py::compute_total)

    4 events  (created: 1  modified: 1  renamed: 1  moved: 1)

Flags:

``--from <ref>``
    Start walking from this commit instead of HEAD.

``--max <n>``
    Stop after *n* commits (default: unlimited).

``--json``
    Emit the event list as JSON.
"""

from __future__ import annotations

import json
import logging
import pathlib
from typing import Literal

import typer

from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.store import (
    CommitRecord,
    get_head_commit_id,
    read_commit,
    read_current_branch,
    resolve_commit_ref,
)
from muse.domain import DomainOp

logger = logging.getLogger(__name__)

app = typer.Typer()

_EventKind = Literal["created", "modified", "renamed", "moved", "deleted", "signature"]


def _read_repo_id(root: pathlib.Path) -> str:
    return str(json.loads((root / ".muse" / "repo.json").read_text())["repo_id"])


def _read_branch(root: pathlib.Path) -> str:
    return read_current_branch(root)


def _walk_commits(
    root: pathlib.Path,
    start_commit_id: str,
    max_commits: int,
) -> list[CommitRecord]:
    """Walk the parent chain from *start_commit_id*, newest first."""
    commits: list[CommitRecord] = []
    seen: set[str] = set()
    current_id: str | None = start_commit_id
    while current_id and current_id not in seen and len(commits) < max_commits:
        seen.add(current_id)
        commit = read_commit(root, current_id)
        if commit is None:
            break
        commits.append(commit)
        current_id = commit.parent_commit_id
    return commits


def _flat_ops(ops: list[DomainOp]) -> list[DomainOp]:
    """Flatten PatchOp children into a single list for easy address scanning."""
    result: list[DomainOp] = []
    for op in ops:
        if op["op"] == "patch":
            result.extend(op["child_ops"])
        else:
            result.append(op)
    return result


def _normalize_address(address: str) -> str:
    """Return the file-path prefix and symbol name from *address*."""
    return address


class SymbolEvent:
    """A single event in a symbol's lifecycle."""

    def __init__(
        self,
        kind: _EventKind,
        commit: CommitRecord,
        address: str,
        detail: str,
        new_address: str | None = None,
    ) -> None:
        self.kind = kind
        self.commit = commit
        self.address = address
        self.detail = detail
        self.new_address = new_address

    def to_dict(self) -> dict[str, str | None]:
        return {
            "event": self.kind,
            "commit_id": self.commit.commit_id,
            "message": self.commit.message,
            "committed_at": self.commit.committed_at.isoformat(),
            "address": self.address,
            "detail": self.detail,
            "new_address": self.new_address,
        }


def _find_events_in_commit(
    commit: CommitRecord,
    address: str,
) -> tuple[list[SymbolEvent], str]:
    """Scan *commit*'s structured delta for events touching *address*.

    Returns ``(events, next_address)`` where *next_address* is the address to
    track in older commits (updated on rename/move events).
    """
    events: list[SymbolEvent] = []
    next_address = address

    if commit.structured_delta is None:
        return events, next_address

    all_ops = _flat_ops(commit.structured_delta["ops"])

    for op in all_ops:
        op_address = op["address"]

        if op["op"] == "insert" and op_address == address:
            events.append(SymbolEvent(
                kind="created",
                commit=commit,
                address=address,
                detail=op.get("content_summary", "created"),
            ))

        elif op["op"] == "delete" and op_address == address:
            detail = op.get("content_summary", "deleted")
            # Cross-file move: address shows in content_summary "moved to …"
            if "moved to" in detail:
                events.append(SymbolEvent(
                    kind="moved",
                    commit=commit,
                    address=address,
                    detail=detail,
                    new_address=None,
                ))
            else:
                events.append(SymbolEvent(
                    kind="deleted",
                    commit=commit,
                    address=address,
                    detail=detail,
                ))

        elif op["op"] == "replace" and op_address == address:
            new_summary: str = op.get("new_summary", "")
            if new_summary.startswith("renamed to "):
                new_name = new_summary.removeprefix("renamed to ").strip()
                file_prefix = address.rsplit("::", 1)[0]
                new_addr = f"{file_prefix}::{new_name}"
                events.append(SymbolEvent(
                    kind="renamed",
                    commit=commit,
                    address=address,
                    detail=f"{address.rsplit('::', 1)[-1]} → {new_name}",
                    new_address=new_addr,
                ))
                next_address = new_addr
            elif new_summary.startswith("moved to "):
                events.append(SymbolEvent(
                    kind="moved",
                    commit=commit,
                    address=address,
                    detail=new_summary,
                    new_address=None,
                ))
            elif "signature" in new_summary:
                events.append(SymbolEvent(
                    kind="signature",
                    commit=commit,
                    address=address,
                    detail=new_summary,
                ))
            elif "implementation" in new_summary or "modified" in new_summary:
                events.append(SymbolEvent(
                    kind="modified",
                    commit=commit,
                    address=address,
                    detail=new_summary,
                ))
            else:
                events.append(SymbolEvent(
                    kind="modified",
                    commit=commit,
                    address=address,
                    detail=new_summary or "modified",
                ))

    return events, next_address


def _print_human(address: str, events: list[SymbolEvent]) -> None:
    typer.echo(f"\nSymbol: {address}")
    typer.echo("─" * 62)

    if not events:
        typer.echo("  (no events found — symbol may not exist in this repo)")
        return

    # Events are collected newest-first; reverse for chronological display.
    chrono = list(reversed(events))

    counts: dict[str, int] = {}
    for ev in chrono:
        counts[ev.kind] = counts.get(ev.kind, 0) + 1
        date_str = ev.commit.committed_at.strftime("%Y-%m-%d")
        short_id = ev.commit.commit_id[:8]
        typer.echo(f'\n● {short_id}  {date_str}  "{ev.commit.message}"')
        typer.echo(f"  {ev.kind:<12}  {ev.detail}")
        if ev.new_address:
            typer.echo(f"  (tracking continues as {ev.new_address})")

    total = len(events)
    summary_parts = [f"{k}: {v}" for k, v in sorted(counts.items())]
    typer.echo(f"\n{total} event(s)  ({',  '.join(summary_parts)})")


@app.callback(invoke_without_command=True)
def symbol_log(
    ctx: typer.Context,
    address: str = typer.Argument(
        ...,
        metavar="ADDRESS",
        help='Symbol address, e.g. "src/utils.py::calculate_total" or "src/models.py::User.save".',
    ),
    from_ref: str | None = typer.Option(
        None, "--from", metavar="REF",
        help="Start walking from this commit / branch (default: HEAD).",
    ),
    max_commits: int = typer.Option(
        500, "--max", metavar="N",
        help="Maximum number of commits to inspect (default: 500).",
    ),
    as_json: bool = typer.Option(
        False, "--json", help="Emit the event list as JSON.",
    ),
) -> None:
    """Track a single symbol through the entire commit history.

    ``muse symbol-log`` is impossible in Git: Git tracks file lines, not
    semantic symbols.  This command follows a function, class, or method
    across every commit — detecting creation, implementation changes,
    renames, cross-file moves, and deletion.

    ADDRESS must be a fully-qualified symbol address::

        muse symbol-log "src/utils.py::calculate_total"
        muse symbol-log "src/models.py::User.save"
        muse symbol-log "api/handlers.go::Server.HandleRequest"
    """
    root = require_repo()
    repo_id = _read_repo_id(root)
    branch = _read_branch(root)

    start_commit = resolve_commit_ref(root, repo_id, branch, from_ref)
    if start_commit is None:
        label = from_ref or "HEAD"
        typer.echo(f"❌ Commit '{label}' not found.", err=True)
        raise typer.Exit(code=ExitCode.USER_ERROR)

    commits = _walk_commits(root, start_commit.commit_id, max_commits)

    # Walk commits newest-first, tracking address changes (renames).
    current_address = address
    all_events: list[SymbolEvent] = []

    for commit in commits:
        evs, current_address = _find_events_in_commit(commit, current_address)
        all_events.extend(evs)

    if as_json:
        typer.echo(json.dumps([e.to_dict() for e in reversed(all_events)], indent=2))
        return

    _print_human(address, all_events)

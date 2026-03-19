"""muse blame — symbol-level attribution.

``git blame`` attributes every *line* to a commit — a 300-line class gives
you 300 attribution entries.  ``muse blame`` attributes the *symbol* as a
semantic unit: one answer per function, class, or method, regardless of how
many lines it occupies.

Usage::

    muse blame "src/billing.py::compute_invoice_total"
    muse blame "api/server.go::Server.HandleRequest"
    muse blame "src/models.py::User.save" --json

Output::

    src/billing.py::compute_invoice_total
    ──────────────────────────────────────────────────────────────
    last touched:  cb4afaed  2026-03-16
    author:        alice
    message:       "Perf: optimise compute_invoice_total"
    change:        implementation changed

    previous:      1d2e3faa  2026-03-15  (renamed from calculate_total)
    before that:   a3f2c9e1  2026-03-14  (created)
"""

import json
import logging
import pathlib
from dataclasses import dataclass
from typing import Literal

import typer

from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.store import CommitRecord, resolve_commit_ref
from muse.domain import DomainOp
from muse.plugins.code._query import walk_commits

logger = logging.getLogger(__name__)

app = typer.Typer()

_EventKind = Literal["created", "modified", "renamed", "moved", "deleted", "signature"]


@dataclass
class _BlameEvent:
    kind: str
    commit: CommitRecord
    address: str
    detail: str
    new_address: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        return {
            "event": self.kind,
            "commit_id": self.commit.commit_id,
            "author": self.commit.author,
            "message": self.commit.message,
            "committed_at": self.commit.committed_at.isoformat(),
            "address": self.address,
            "detail": self.detail,
            "new_address": self.new_address,
        }


def _flat_ops(ops: list[DomainOp]) -> list[DomainOp]:
    result: list[DomainOp] = []
    for op in ops:
        if op["op"] == "patch":
            result.extend(op["child_ops"])
        else:
            result.append(op)
    return result


def _events_in_commit(
    commit: CommitRecord,
    address: str,
) -> tuple[list[_BlameEvent], str]:
    """Scan *commit* for events touching *address*; return ``(events, next_address)``."""
    events: list[_BlameEvent] = []
    next_address = address
    if commit.structured_delta is None:
        return events, next_address
    for op in _flat_ops(commit.structured_delta["ops"]):
        if op["address"] != address:
            continue
        if op["op"] == "insert":
            events.append(_BlameEvent("created", commit, address, op.get("content_summary", "created")))
        elif op["op"] == "delete":
            detail = op.get("content_summary", "deleted")
            kind = "moved" if "moved to" in detail else "deleted"
            events.append(_BlameEvent(kind, commit, address, detail))
        elif op["op"] == "replace":
            ns: str = op.get("new_summary", "")
            if ns.startswith("renamed to "):
                new_name = ns.removeprefix("renamed to ").strip()
                file_prefix = address.rsplit("::", 1)[0]
                new_addr = f"{file_prefix}::{new_name}"
                events.append(_BlameEvent("renamed", commit, address, f"renamed to {new_name}", new_addr))
                next_address = new_addr
            elif ns.startswith("moved to "):
                events.append(_BlameEvent("moved", commit, address, ns))
            elif "signature" in ns:
                events.append(_BlameEvent("signature", commit, address, ns or "signature changed"))
            else:
                events.append(_BlameEvent("modified", commit, address, ns or "modified"))
    return events, next_address


def _read_repo_id(root: pathlib.Path) -> str:
    return str(json.loads((root / ".muse" / "repo.json").read_text())["repo_id"])


def _read_branch(root: pathlib.Path) -> str:
    head_ref = (root / ".muse" / "HEAD").read_text().strip()
    return head_ref.removeprefix("refs/heads/").strip()


@app.callback(invoke_without_command=True)
def blame(
    ctx: typer.Context,
    address: str = typer.Argument(
        ..., metavar="ADDRESS",
        help='Symbol address, e.g. "src/billing.py::compute_invoice_total".',
    ),
    from_ref: str | None = typer.Option(
        None, "--from", metavar="REF",
        help="Start walking from this commit / branch (default: HEAD).",
    ),
    show_all: bool = typer.Option(
        False, "--all", "-a",
        help="Show the full change history, not just the three most recent events.",
    ),
    as_json: bool = typer.Option(
        False, "--json", help="Emit attribution as JSON.",
    ),
) -> None:
    """Show which commit last touched a specific symbol.

    ``muse blame`` attributes the symbol as a semantic unit — one answer
    per function, class, or method, regardless of line count.  The full
    chain of prior events (renames, signature changes, etc.) is available
    via ``--all``.

    Unlike ``git blame``, which gives per-line attribution across an entire
    file, ``muse blame`` gives a single clear answer: *this commit last
    changed this symbol, and this is what changed*.
    """
    root = require_repo()
    repo_id = _read_repo_id(root)
    branch = _read_branch(root)

    start_commit = resolve_commit_ref(root, repo_id, branch, from_ref)
    if start_commit is None:
        typer.echo(f"❌ Commit '{from_ref or 'HEAD'}' not found.", err=True)
        raise typer.Exit(code=ExitCode.USER_ERROR)

    commits = walk_commits(root, start_commit.commit_id)

    current_address = address
    all_events: list[_BlameEvent] = []
    for commit in commits:
        evs, current_address = _events_in_commit(commit, current_address)
        all_events.extend(evs)

    if as_json:
        typer.echo(json.dumps(
            {"address": address, "events": [e.to_dict() for e in reversed(all_events)]},
            indent=2,
        ))
        return

    typer.echo(f"\n{address}")
    typer.echo("─" * 62)

    if not all_events:
        typer.echo("  (no events found — symbol may not exist in this repository)")
        return

    events_to_show = all_events if show_all else all_events[:3]
    labels = ["last touched:", "previous:    ", "before that: "]

    for idx, ev in enumerate(events_to_show):
        label = labels[idx] if idx < len(labels) else "            :"
        date_str = ev.commit.committed_at.strftime("%Y-%m-%d")
        short_id = ev.commit.commit_id[:8]
        typer.echo(f"{label}  {short_id}  {date_str}")
        if idx == 0:
            typer.echo(f"author:        {ev.commit.author or 'unknown'}")
            typer.echo(f'message:       "{ev.commit.message}"')
        typer.echo(f"change:        {ev.detail}")
        if ev.new_address:
            typer.echo(f"               (tracking continues as {ev.new_address})")
        typer.echo("")

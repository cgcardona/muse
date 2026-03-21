"""muse lineage — full symbol provenance chain.

Traces the complete life of a symbol through the commit history:
created → renamed → moved → copied → deleted, in chronological order.

Each transition is classified by comparing hashes across consecutive commits:

* **created**      — first InsertOp for this address (no prior body_hash match)
* **copied_from**  — InsertOp whose body_hash matches a living symbol at a
                     different address (same body, new address)
* **renamed_from** — InsertOp + DeleteOp in same commit with matching body_hash
                     (content preserved, address changed)
* **moved_from**   — InsertOp + DeleteOp in same commit with matching body_hash
                     AND different file (cross-file move)
* **modified**     — ReplaceOp at this address; sub-classified by which hashes
                     changed: impl_only, signature_only, full_rewrite
* **deleted**      — DeleteOp at this address

Usage::

    muse lineage "src/billing.py::compute_invoice_total"
    muse lineage "src/auth.py::validate_token" --commit HEAD~5
    muse lineage "src/core.py::hash_content" --json

Output::

    Lineage: src/billing.py::compute_invoice_total
    ──────────────────────────────────────────────────────────────

    2026-02-01  a1b2c3d4  created
    2026-02-10  e5f6a7b8  modified (impl_only)
    2026-02-15  c9d0e1f2  renamed_from  src/billing.py::_compute_total
    2026-03-01  a3b4c5d6  moved_from    old/billing.py::compute_invoice_total
    2026-03-10  f7a8b9c0  modified (full_rewrite)

    5 events — first seen 2026-02-01 · last seen 2026-03-10

Flags:

``--commit, -c REF``
    Walk history starting from this commit instead of HEAD.

``--json``
    Emit the full provenance chain as JSON.
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
    get_all_commits,
    read_current_branch,
    resolve_commit_ref,
)
from muse.plugins.code._query import flat_symbol_ops


class _InsertFields:
    """Extracted fields from an InsertOp — typed slots avoid untyped dict issues."""
    __slots__ = ("address", "content_id")

    def __init__(self, address: str, content_id: str) -> None:
        self.address = address
        self.content_id = content_id


class _DeleteFields:
    __slots__ = ("address", "content_id")

    def __init__(self, address: str, content_id: str) -> None:
        self.address = address
        self.content_id = content_id


class _ReplaceFields:
    __slots__ = ("address", "old_content_id", "new_content_id", "old_summary", "new_summary")

    def __init__(
        self,
        address: str,
        old_content_id: str,
        new_content_id: str,
        old_summary: str,
        new_summary: str,
    ) -> None:
        self.address = address
        self.old_content_id = old_content_id
        self.new_content_id = new_content_id
        self.old_summary = old_summary
        self.new_summary = new_summary

logger = logging.getLogger(__name__)

app = typer.Typer()

EventKind = Literal[
    "created",
    "renamed_from",
    "moved_from",
    "copied_from",
    "modified",
    "deleted",
]

_FUNCTION_KINDS = frozenset({
    "function", "async_function", "method", "async_method", "class",
})


def _read_repo_id(root: pathlib.Path) -> str:
    return str(json.loads((root / ".muse" / "repo.json").read_text())["repo_id"])


def _read_branch(root: pathlib.Path) -> str:
    return read_current_branch(root)



class _LineageEvent:
    def __init__(
        self,
        commit_id: str,
        committed_at: str,
        kind: EventKind,
        detail: str = "",
        old_body_hash: str = "",
        new_body_hash: str = "",
        old_content_id: str = "",
        new_content_id: str = "",
    ) -> None:
        self.commit_id = commit_id
        self.committed_at = committed_at
        self.kind = kind
        self.detail = detail
        self.old_body_hash = old_body_hash
        self.new_body_hash = new_body_hash
        self.old_content_id = old_content_id
        self.new_content_id = new_content_id

    def to_dict(self) -> dict[str, str]:
        d: dict[str, str] = {
            "commit_id": self.commit_id[:8],
            "committed_at": self.committed_at,
            "event": self.kind,
        }
        if self.detail:
            d["detail"] = self.detail
        if self.old_body_hash:
            d["old_body_hash"] = self.old_body_hash[:8]
        if self.new_body_hash:
            d["new_body_hash"] = self.new_body_hash[:8]
        if self.old_content_id:
            d["old_content_id"] = self.old_content_id[:8]
        if self.new_content_id:
            d["new_content_id"] = self.new_content_id[:8]
        return d


def _classify_replace(old_content_id: str, new_content_id: str,
                       old_summary: str, new_summary: str) -> str:
    """Classify a ReplaceOp by examining summary strings for hash markers."""
    if "signature" in old_summary or "signature" in new_summary:
        return "signature_change"
    if old_content_id[:8] != new_content_id[:8]:
        return "full_rewrite"
    return "impl_only"


def build_lineage(
    root: pathlib.Path,
    address: str,
) -> list[_LineageEvent]:
    """Walk all commits oldest-first and build the provenance chain.

    Copy detection uses an incremental ``content_id → set[address]`` registry
    maintained from structured_delta ops as commits are processed.  This is
    O(total ops across all commits) — no blob re-parsing, no snapshot scans.
    The previous implementation was O(commits × files × symbols) because it
    re-parsed every blob in every snapshot to look for matching body_hashes.
    """
    all_commits = sorted(
        get_all_commits(root),
        key=lambda c: c.committed_at,
    )

    events: list[_LineageEvent] = []
    address_live = False

    # Incremental registry: content_id → set of live symbol addresses.
    # Updated from structured_delta ops as we walk commits chronologically.
    # Replaces the O(commits × files × symbols) blob-scan copy detection.
    live_by_content_id: dict[str, set[str]] = {}

    for commit in all_commits:
        if commit.structured_delta is None:
            continue
        ops = commit.structured_delta.get("ops", [])
        committed_at = commit.committed_at.isoformat()

        inserts: dict[str, _InsertFields] = {}
        deletes: dict[str, _DeleteFields] = {}
        replaces: dict[str, _ReplaceFields] = {}

        for op in flat_symbol_ops(ops):
            addr = op["address"]
            if op["op"] == "insert":
                inserts[addr] = _InsertFields(
                    address=addr,
                    content_id=op["content_id"],
                )
            elif op["op"] == "delete":
                deletes[addr] = _DeleteFields(
                    address=addr,
                    content_id=op["content_id"],
                )
            elif op["op"] == "replace":
                replaces[addr] = _ReplaceFields(
                    address=addr,
                    old_content_id=op["old_content_id"],
                    new_content_id=op["new_content_id"],
                    old_summary=op["old_summary"],
                    new_summary=op["new_summary"],
                )

        if address in replaces:
            rep = replaces[address]
            old_cid = rep.old_content_id
            new_cid = rep.new_content_id
            detail = _classify_replace(old_cid, new_cid, rep.old_summary, rep.new_summary)
            events.append(_LineageEvent(
                commit_id=commit.commit_id,
                committed_at=committed_at,
                kind="modified",
                detail=detail,
                old_content_id=old_cid,
                new_content_id=new_cid,
            ))
            # Update registry: old content_id is no longer live at this address.
            live_by_content_id.get(old_cid, set()).discard(address)
            live_by_content_id.setdefault(new_cid, set()).add(address)

        if address in inserts:
            ins = inserts[address]
            ins_cid = ins.content_id

            # Rename / move: DeleteOp in same commit with the same content_id.
            source_addr: str | None = None
            for del_addr, del_op in deletes.items():
                if del_addr == address:
                    continue
                if del_op.content_id == ins_cid:
                    source_addr = del_addr
                    break

            if source_addr is not None:
                del_file = source_addr.split("::")[0]
                ins_file = address.split("::")[0]
                kind: EventKind = "moved_from" if del_file != ins_file else "renamed_from"
                events.append(_LineageEvent(
                    commit_id=commit.commit_id,
                    committed_at=committed_at,
                    kind=kind,
                    detail=source_addr,
                    new_content_id=ins_cid,
                ))
            else:
                # Copy detection: check the incremental registry for another live
                # address that already carries this content_id.  O(1) lookup —
                # no blob re-parsing, no snapshot scan.
                existing = live_by_content_id.get(ins_cid, set()) - {address}
                if existing and not address_live:
                    copy_source: str | None = next(iter(sorted(existing)))
                    ev_kind: EventKind = "copied_from"
                else:
                    copy_source = None
                    ev_kind = "created"
                events.append(_LineageEvent(
                    commit_id=commit.commit_id,
                    committed_at=committed_at,
                    kind=ev_kind,
                    detail=copy_source or "",
                    new_content_id=ins_cid,
                ))

            live_by_content_id.setdefault(ins_cid, set()).add(address)
            address_live = True

        if address in deletes:
            del_f = deletes[address]
            events.append(_LineageEvent(
                commit_id=commit.commit_id,
                committed_at=committed_at,
                kind="deleted",
                old_content_id=del_f.content_id,
            ))
            live_by_content_id.get(del_f.content_id, set()).discard(address)
            address_live = False

        # Update registry for all other ops in this commit (not the target address),
        # so copy detection is accurate for future commits.
        for addr, ins in inserts.items():
            if addr != address:
                live_by_content_id.setdefault(ins.content_id, set()).add(addr)
        for addr, del_op in deletes.items():
            if addr != address:
                live_by_content_id.get(del_op.content_id, set()).discard(addr)

    return events


@app.callback(invoke_without_command=True)
def lineage(
    ctx: typer.Context,
    address: str = typer.Argument(
        ..., metavar="ADDRESS",
        help='Symbol address, e.g. "src/billing.py::compute_invoice_total".',
    ),
    ref: str | None = typer.Option(
        None, "--commit", "-c", metavar="REF",
        help="Walk history from this commit instead of HEAD.",
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit results as JSON."),
) -> None:
    """Show the full provenance chain of a symbol through commit history.

    Classifies every event as: created, renamed_from, moved_from, copied_from,
    modified (impl_only / signature_change / full_rewrite), or deleted.

    Rename and move detection works by matching body hashes across DeleteOp and
    InsertOp pairs within the same commit.  Copy detection looks for another
    living symbol with the same body hash at the time of insertion.
    """
    root = require_repo()
    events = build_lineage(root, address)

    if as_json:
        typer.echo(json.dumps(
            {
                "address": address,
                "events": [e.to_dict() for e in events],
                "total": len(events),
            },
            indent=2,
        ))
        return

    typer.echo(f"\nLineage: {address}")
    typer.echo("─" * 62)

    if not events:
        typer.echo(
            "\n  (no events found — address may not exist in this repository's history)"
        )
        return

    for ev in events:
        date = ev.committed_at[:10]
        short = ev.commit_id[:8]
        label: str = ev.kind
        if ev.detail:
            label = f"{ev.kind}  {ev.detail}"
        typer.echo(f"  {date}  {short}  {label}")

    typer.echo()
    first = events[0].committed_at[:10]
    last = events[-1].committed_at[:10]
    typer.echo(
        f"  {len(events)} event(s) — first seen {first} · last seen {last}"
    )

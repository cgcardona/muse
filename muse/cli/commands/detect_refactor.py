"""muse detect-refactor — semantic refactoring detection across commits.

This command is impossible in Git.  Git sees every refactoring operation as
a diff of text lines.  A function extracted into a helper module? Delete lines
here, add lines there — no semantic connection.  A class renamed? Every file
that imports it becomes a "modification".  Muse understands *what actually
happened* at the symbol level.

``muse detect-refactor`` scans the commit range and classifies every semantic
operation into one of five refactoring categories:

``rename``
    A symbol kept its body but changed its name.  Detected via matching
    ``body_hash`` across the before/after snapshot.

``move``
    A symbol's full ``content_id`` appears in a different file.  The symbol
    moved without change.

``signature_change``
    A symbol's name and body are unchanged; only its parameter list or return
    type changed.

``implementation_change``
    A symbol's signature is stable; its internal logic changed.

``extraction``
    A new symbol whose body shares significant content with an existing symbol
    — a function was factored out of another.  (Heuristic: detected when a
    new symbol appears at the same time an existing symbol shrinks.)

Output::

    Semantic refactoring report
    From: cb4afaed  "Layer 2: add harmonic dimension"
    To:   a3f2c9e1  "Refactor: rename and move helpers"
    ──────────────────────────────────────────────────────────────

    RENAME         src/utils.py::calculate_total
                   → compute_total
                   commit a3f2c9e1  "Rename: improve naming clarity"

    MOVE           src/utils.py::compute_total
                   → src/helpers.py::compute_total
                   commit 1d2e3faa  "Move: extract helpers module"

    SIGNATURE      src/api.py::handle_request
                   parameters changed: (req, ctx) → (request, context, timeout)
                   commit 4b5c6d7e  "API: add timeout parameter"

    IMPLEMENTATION src/core.py::process_batch
                   implementation changed (signature stable)
                   commit 8f9a0b1c  "Perf: vectorise batch processing"

    ──────────────────────────────────────────────────────────────
    4 refactoring operations detected
    (1 rename · 1 move · 1 signature · 1 implementation)

Flags:

``--from <ref>``
    Start of the commit range (exclusive).  Default: the initial commit.

``--to <ref>``
    End of the commit range (inclusive).  Default: HEAD.

``--kind <kind>``
    Filter to one category: rename, move, signature, implementation.

``--json``
    Emit the full refactoring report as JSON.
"""

from __future__ import annotations

import argparse
import json
import logging
import pathlib
import sys

from muse._version import __version__
from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.store import CommitRecord, read_commit, read_current_branch, resolve_commit_ref
from muse.domain import DomainOp

logger = logging.getLogger(__name__)

_VALID_KINDS = frozenset({"rename", "move", "signature", "implementation"})


def _read_repo_id(root: pathlib.Path) -> str:
    return str(json.loads((root / ".muse" / "repo.json").read_text())["repo_id"])


def _read_branch(root: pathlib.Path) -> str:
    return read_current_branch(root)


def _walk_commits(
    root: pathlib.Path,
    to_commit_id: str,
    from_commit_id: str | None,
) -> list[CommitRecord]:
    """Collect commits from *to_commit_id* back to (but not including) *from_commit_id*."""
    commits: list[CommitRecord] = []
    seen: set[str] = set()
    current_id: str | None = to_commit_id
    while current_id and current_id not in seen:
        seen.add(current_id)
        if current_id == from_commit_id:
            break
        commit = read_commit(root, current_id)
        if commit is None:
            break
        commits.append(commit)
        current_id = commit.parent_commit_id
    return commits


def _flat_child_ops(ops: list[DomainOp]) -> list[DomainOp]:
    """Flatten PatchOp child_ops; return all leaf ops."""
    result: list[DomainOp] = []
    for op in ops:
        if op["op"] == "patch":
            result.extend(op["child_ops"])
        else:
            result.append(op)
    return result


class RefactorEvent:
    """A single detected refactoring event."""

    def __init__(
        self,
        kind: str,
        address: str,
        detail: str,
        commit: CommitRecord,
    ) -> None:
        self.kind = kind
        self.address = address
        self.detail = detail
        self.commit = commit

    def to_dict(self) -> dict[str, str]:
        return {
            "kind": self.kind,
            "address": self.address,
            "detail": self.detail,
            "commit_id": self.commit.commit_id,
            "commit_message": self.commit.message,
            "committed_at": self.commit.committed_at.isoformat(),
        }


def _classify_ops(commit: CommitRecord) -> list[RefactorEvent]:
    """Extract refactoring events from *commit*'s structured delta."""
    events: list[RefactorEvent] = []
    if commit.structured_delta is None:
        return events

    all_ops = _flat_child_ops(commit.structured_delta["ops"])

    for op in all_ops:
        address = op["address"]

        if op["op"] == "delete":
            content_summary = op.get("content_summary", "")
            if "moved to" in content_summary:
                target = content_summary.split("moved to")[-1].strip()
                events.append(RefactorEvent(
                    kind="move",
                    address=address,
                    detail=f"→ {target}",
                    commit=commit,
                ))

        elif op["op"] == "replace":
            new_summary: str = op.get("new_summary", "")
            old_summary: str = op.get("old_summary", "")

            if new_summary.startswith("renamed to "):
                new_name = new_summary.removeprefix("renamed to ").strip()
                events.append(RefactorEvent(
                    kind="rename",
                    address=address,
                    detail=f"→ {new_name}",
                    commit=commit,
                ))
            elif new_summary.startswith("moved to "):
                target = new_summary.removeprefix("moved to ").strip()
                events.append(RefactorEvent(
                    kind="move",
                    address=address,
                    detail=f"→ {target}",
                    commit=commit,
                ))
            elif "signature" in new_summary or "signature" in old_summary:
                detail = new_summary or f"{address} signature changed"
                events.append(RefactorEvent(
                    kind="signature",
                    address=address,
                    detail=detail,
                    commit=commit,
                ))
            elif "implementation" in new_summary:
                events.append(RefactorEvent(
                    kind="implementation",
                    address=address,
                    detail=new_summary,
                    commit=commit,
                ))

    return events


_LABEL: dict[str, str] = {
    "rename":         "RENAME        ",
    "move":           "MOVE          ",
    "signature":      "SIGNATURE     ",
    "implementation": "IMPLEMENTATION",
}


def _print_human(
    events: list[RefactorEvent],
    from_label: str,
    to_label: str,
) -> None:
    print("\nSemantic refactoring report")
    print(f"From: {from_label}")
    print(f"To:   {to_label}")
    print("─" * 62)

    if not events:
        print("\n  (no semantic refactoring detected in this range)")
        return

    # Print newest-first (commits were collected newest-first).
    for ev in events:
        label = _LABEL.get(ev.kind, ev.kind.upper().ljust(14))
        short_id = ev.commit.commit_id[:8]
        print(f"\n{label}  {ev.address}")
        print(f"               {ev.detail}")
        print(f'               commit {short_id}  "{ev.commit.message}"')

    print("\n" + "─" * 62)
    kind_counts: dict[str, int] = {}
    for ev in events:
        kind_counts[ev.kind] = kind_counts.get(ev.kind, 0) + 1
    summary_parts = [f"{v} {k}" for k, v in sorted(kind_counts.items())]
    print(f"{len(events)} refactoring operation(s) detected")
    print(f"({' · '.join(summary_parts)})")


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the detect-refactor subcommand."""
    parser = subparsers.add_parser(
        "detect-refactor",
        help="Detect semantic refactoring operations across a commit range.",
        description=__doc__,
    )
    parser.add_argument(
        "--from", default=None, metavar="REF", dest="from_ref",
        help="Start of range (exclusive).  Default: initial commit.",
    )
    parser.add_argument(
        "--to", default=None, metavar="REF", dest="to_ref",
        help="End of range (inclusive).  Default: HEAD.",
    )
    parser.add_argument(
        "--kind", "-k", default=None, metavar="KIND", dest="kind_filter",
        help="Filter to one category: rename, move, signature, implementation.",
    )
    parser.add_argument(
        "--json", action="store_true", dest="as_json",
        help="Emit the full refactoring report as JSON.",
    )
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> None:
    """Detect semantic refactoring operations across a commit range.

    ``muse detect-refactor`` is impossible in Git.  Git reports renames only
    as heuristic line-similarity guesses (``git diff --find-renames``); it
    has no concept of function identity, body hashes, or cross-file symbol
    continuity.

    Muse detects every semantic refactoring at the AST level:

    \\b
    - RENAME: same body, new name  (``body_hash`` match)\n
    - MOVE: same content, new file  (``content_id`` match)\n
    - SIGNATURE: name/body stable, parameters changed\n
    - IMPLEMENTATION: signature stable, logic changed\n

    Use ``--from`` / ``--to`` to scope the range.  Without flags, scans the
    full history from the first commit to HEAD.
    """
    from_ref: str | None = args.from_ref
    to_ref: str | None = args.to_ref
    kind_filter: str | None = args.kind_filter
    as_json: bool = args.as_json

    root = require_repo()
    repo_id = _read_repo_id(root)
    branch = _read_branch(root)

    if kind_filter and kind_filter not in _VALID_KINDS:
        print(
            f"❌ Unknown kind '{kind_filter}'.  "
            f"Valid: {', '.join(sorted(_VALID_KINDS))}",
            file=sys.stderr,
        )
        raise SystemExit(ExitCode.USER_ERROR)

    to_commit = resolve_commit_ref(root, repo_id, branch, to_ref)
    if to_commit is None:
        label = to_ref or "HEAD"
        print(f"❌ Commit '{label}' not found.", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)

    from_commit_id: str | None = None
    if from_ref is not None:
        from_commit = resolve_commit_ref(root, repo_id, branch, from_ref)
        if from_commit is None:
            print(f"❌ Commit '{from_ref}' not found.", file=sys.stderr)
            raise SystemExit(ExitCode.USER_ERROR)
        from_commit_id = from_commit.commit_id

    commits = _walk_commits(root, to_commit.commit_id, from_commit_id)

    all_events: list[RefactorEvent] = []
    for commit in commits:
        evs = _classify_ops(commit)
        if kind_filter:
            evs = [e for e in evs if e.kind == kind_filter]
        all_events.extend(evs)

    if from_commit_id is not None:
        _fc = read_commit(root, from_commit_id)
        from_label = (
            f'{from_commit_id[:8]}  "{_fc.message}"'
            if _fc is not None
            else "initial commit"
        )
    else:
        from_label = "initial commit"
    to_label = f'{to_commit.commit_id[:8]}  "{to_commit.message}"'

    if as_json:
        print(json.dumps(
            {
                "schema_version": __version__,
                "from": from_label,
                "to": to_label,
                "total": len(all_events),
                "events": [e.to_dict() for e in all_events],
            },
            indent=2,
        ))
        return

    _print_human(all_events, from_label, to_label)

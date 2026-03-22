"""muse find-symbol — cross-commit, cross-branch symbol search.

Closes two architectural gaps that ``muse query`` cannot address:

1. **Temporal search**: ``muse query hash=a3f2c9`` queries *one* snapshot.
   ``muse find-symbol --hash a3f2c9`` searches *every commit ever recorded*,
   finding the exact moment a function body first entered the repository.

2. **Cross-branch presence**: if two branches independently introduced the
   same function body, ``muse find-symbol --hash a3f2c9 --all-branches``
   finds both.

How it works
------------
Every ``CommitRecord`` carries a ``structured_delta`` — the typed ``DomainOp``
tree produced at commit time.  ``InsertOp`` entries in that delta record
exactly which symbols were *added* in each commit, including their
``content_id``, ``body_hash``, and ``name`` (embedded in the address and
``content_summary``).

``muse find-symbol`` walks *all* commits in the object store (not just the
current branch's linear history) ordered oldest-first, and scans their
``InsertOp`` entries for symbols matching the given predicates.  This gives
true cross-branch, temporally-ordered results.

With ``--all-branches``, it also checks the current HEAD snapshot of every
branch tip to show where the symbol lives right now.

Usage::

    muse find-symbol --hash a3f2c9            # by content_id prefix
    muse find-symbol --name validate_amount   # by exact name
    muse find-symbol --name "validate*"       # by name prefix (glob-style)
    muse find-symbol --hash a3f2c9 --all-branches
    muse find-symbol --kind function --name compute

Flags:

``--hash HASH``
    Match symbols whose ``content_id`` starts with this prefix.

``--name NAME``
    Match symbols whose name exactly equals NAME (case-insensitive).
    Append ``*`` for prefix matching.

``--kind KIND``
    Restrict to a specific symbol kind.

``--all-branches``
    Also report which branch tips currently have a matching symbol in
    their HEAD snapshot.

``--first``
    Stop after finding the very first appearance of each unique content_id.

``--json``
    Emit results as JSON.
"""

from __future__ import annotations

import argparse
import json
import logging
import pathlib
import sys

from muse.core.errors import ExitCode
from muse.core.object_store import read_object
from muse.core.repo import require_repo
from muse.core.store import CommitRecord, get_all_commits, get_head_commit_id
from muse.domain import DomainOp
from muse.plugins.code._query import symbols_for_snapshot
from muse.plugins.code.ast_parser import SymbolRecord

logger = logging.getLogger(__name__)


def _read_repo_id(root: pathlib.Path) -> str:
    return str(json.loads((root / ".muse" / "repo.json").read_text())["repo_id"])


# ---------------------------------------------------------------------------
# List branches from .muse/refs/heads/
# ---------------------------------------------------------------------------


def _list_branches(root: pathlib.Path) -> list[str]:
    """Return all branch names recorded in ``.muse/refs/heads/``."""
    heads_dir = root / ".muse" / "refs" / "heads"
    if not heads_dir.exists():
        return []
    return sorted(p.name for p in heads_dir.iterdir() if p.is_file())


# ---------------------------------------------------------------------------
# Op flattening and InsertOp extraction
# ---------------------------------------------------------------------------


def _flat_insert_ops(ops: list[DomainOp]) -> list[DomainOp]:
    """Return all InsertOp leaves (including children of PatchOps)."""
    result: list[DomainOp] = []
    for op in ops:
        if op["op"] == "patch":
            for child in op["child_ops"]:
                if child["op"] == "insert":
                    result.append(child)
        elif op["op"] == "insert":
            result.append(op)
    return result


# ---------------------------------------------------------------------------
# Matching predicates
# ---------------------------------------------------------------------------


def _matches(
    address: str,
    content_summary: str,
    hash_prefix: str | None,
    name_pattern: str | None,
    kind_filter: str | None,
) -> bool:
    """Return True if this InsertOp entry matches all specified predicates."""
    sym_name = address.split("::")[-1].split(".")[-1] if "::" in address else ""
    sym_kind_hint = ""

    # The content_summary for symbol InsertOps typically looks like:
    # "function calculate_total" or "class Invoice"
    parts = content_summary.strip().split(None, 1)
    if parts:
        sym_kind_hint = parts[0]
        if len(parts) > 1:
            sym_name = parts[1].split()[0]

    # hash= predicate: content_summary starts with content_id for direct file
    # ops, but for symbol ops inside a PatchOp the address encodes the name.
    # We embed the content_id in content_summary when it's available.
    # Fallback: check if any hash info is embedded.
    if hash_prefix:
        # For cross-commit hash search, the content_id is stored in the
        # commit's snapshot. We check the address to see if we can match.
        # The cleanest check is against the content_id embedded in the object.
        # Since InsertOp only has content_summary, we accept address-substring
        # matches as a secondary heuristic and do full re-parsing in callers.
        pass  # resolved by caller using content_id directly

    if name_pattern and sym_name:
        pattern_lower = name_pattern.lower()
        name_lower = sym_name.lower()
        if pattern_lower.endswith("*"):
            if not name_lower.startswith(pattern_lower[:-1]):
                return False
        else:
            if name_lower != pattern_lower:
                return False

    if kind_filter and sym_kind_hint:
        if sym_kind_hint.lower() != kind_filter.lower():
            return False

    return True


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


class _Appearance:
    """One occurrence of a matching symbol across history."""

    def __init__(
        self,
        content_id: str,
        address: str,
        commit: CommitRecord,
        name: str,
        kind: str,
    ) -> None:
        self.content_id = content_id
        self.address = address
        self.commit = commit
        self.name = name
        self.kind = kind

    def to_dict(self) -> dict[str, str]:
        return {
            "content_id": self.content_id,
            "address": self.address,
            "name": self.name,
            "kind": self.kind,
            "commit_id": self.commit.commit_id,
            "commit_message": self.commit.message,
            "committed_at": self.commit.committed_at.isoformat(),
            "branch": self.commit.branch,
        }


# ---------------------------------------------------------------------------
# Core search (walks ALL commits via object store)
# ---------------------------------------------------------------------------


def _search_all_commits(
    root: pathlib.Path,
    hash_prefix: str | None,
    name_pattern: str | None,
    kind_filter: str | None,
    first_only: bool,
) -> list[_Appearance]:
    """Walk all CommitRecords in the store, oldest-first, collecting matches.

    Uses the structured_delta InsertOps for speed — no re-parsing of blobs.
    When ``hash_prefix`` is given we do a second-pass re-parse of the snapshot
    blob to verify the content_id precisely, since InsertOps don't embed it.
    """
    all_commits = get_all_commits(root)
    if not all_commits:
        return []

    # Sort oldest-first by committed_at.
    sorted_commits = sorted(all_commits, key=lambda c: c.committed_at)

    appearances: list[_Appearance] = []
    # Track which (content_id, address) pairs we've already reported when
    # first_only is True.
    seen_ids: set[str] = set()

    for commit in sorted_commits:
        if commit.structured_delta is None:
            continue

        insert_ops = _flat_insert_ops(commit.structured_delta["ops"])

        for op in insert_ops:
            address = op["address"]
            if "::" not in address:
                continue  # File-level op, not a symbol op.
            # Discriminated access: only InsertOp has content_summary.
            if op["op"] == "insert":
                content_summary: str = op["content_summary"]
            else:
                content_summary = ""

            # Fast name/kind filter on summary text.
            if name_pattern or kind_filter:
                if not _matches(address, content_summary, None, name_pattern, kind_filter):
                    continue

            sym_name_from_addr = address.split("::")[-1]

            # For hash= predicate: re-parse the snapshot to get exact content_id.
            if hash_prefix:
                # We need the actual content_id. Look it up from the snapshot.
                snapshot_manifest = _get_manifest_for_commit(root, commit)
                if snapshot_manifest is None:
                    continue
                file_path = address.split("::")[0]
                content_id = _content_id_for_address(root, snapshot_manifest, address)
                if content_id is None:
                    continue
                if not content_id.startswith(hash_prefix.lower()):
                    continue
            else:
                content_id = ""

            dedup_key = f"{content_id}::{address}" if hash_prefix else address
            if first_only and dedup_key in seen_ids:
                continue
            seen_ids.add(dedup_key)

            # Extract kind from content_summary.
            parts = content_summary.strip().split(None, 1)
            kind = parts[0] if parts else "function"
            name = parts[1].split()[0] if len(parts) > 1 else sym_name_from_addr

            appearances.append(_Appearance(
                content_id=content_id,
                address=address,
                commit=commit,
                name=name,
                kind=kind,
            ))

    return appearances


_manifest_cache: dict[str, dict[str, str]] = {}


def _get_manifest_for_commit(
    root: pathlib.Path,
    commit: CommitRecord,
) -> dict[str, str] | None:
    """Load (with caching) the snapshot manifest for *commit*."""
    cid = commit.commit_id
    if cid in _manifest_cache:
        return _manifest_cache[cid]
    snap_path = root / ".muse" / "snapshots" / f"{commit.snapshot_id}.json"
    if not snap_path.exists():
        return None
    try:
        data = json.loads(snap_path.read_text())
        manifest: dict[str, str] = data.get("manifest", {})
        _manifest_cache[cid] = manifest
        return manifest
    except (json.JSONDecodeError, KeyError):
        return None


def _content_id_for_address(
    root: pathlib.Path,
    manifest: dict[str, str],
    address: str,
) -> str | None:
    """Re-parse the blob for *address* and return its content_id, or None."""
    if "::" not in address:
        return None
    file_path = address.split("::")[0]
    obj_id = manifest.get(file_path)
    if obj_id is None:
        return None
    raw = read_object(root, obj_id)
    if raw is None:
        return None
    from muse.plugins.code.ast_parser import parse_symbols as _parse
    tree = _parse(raw, file_path)
    rec = tree.get(address)
    if rec is None:
        return None
    return rec["content_id"]


# ---------------------------------------------------------------------------
# Branch presence check
# ---------------------------------------------------------------------------


class _BranchPresence:
    """Whether a matching symbol currently lives in a branch's HEAD."""

    def __init__(self, branch: str, address: str, content_id: str) -> None:
        self.branch = branch
        self.address = address
        self.content_id = content_id

    def to_dict(self) -> dict[str, str]:
        return {
            "branch": self.branch,
            "address": self.address,
            "content_id": self.content_id,
        }


def _branch_presence(
    root: pathlib.Path,
    repo_id: str,
    hash_prefix: str | None,
    name_pattern: str | None,
    kind_filter: str | None,
) -> list[_BranchPresence]:
    """Check every branch HEAD snapshot for matching symbols."""
    results: list[_BranchPresence] = []
    for branch in _list_branches(root):
        commit_id = get_head_commit_id(root, branch)
        if commit_id is None:
            continue
        snap_path = root / ".muse" / "snapshots"
        # Find the snapshot for this commit.
        commit_snap_path = root / ".muse" / "commits" / f"{commit_id}.json"
        if not commit_snap_path.exists():
            continue
        try:
            cdata = json.loads(commit_snap_path.read_text())
            snap_id = cdata.get("snapshot_id", "")
        except (json.JSONDecodeError, KeyError):
            continue
        manifest_path = root / ".muse" / "snapshots" / f"{snap_id}.json"
        if not manifest_path.exists():
            continue
        try:
            manifest: dict[str, str] = json.loads(manifest_path.read_text()).get("manifest", {})
        except (json.JSONDecodeError, KeyError):
            continue

        sym_map = symbols_for_snapshot(root, manifest, kind_filter=kind_filter)
        for file_path, tree in sym_map.items():
            for address, rec in tree.items():
                if name_pattern:
                    pattern_lower = name_pattern.lower()
                    name_lower = rec["name"].lower()
                    if pattern_lower.endswith("*"):
                        if not name_lower.startswith(pattern_lower[:-1]):
                            continue
                    elif name_lower != pattern_lower:
                        continue
                if hash_prefix and not rec["content_id"].startswith(hash_prefix.lower()):
                    continue
                results.append(_BranchPresence(branch, address, rec["content_id"]))
    return results


# ---------------------------------------------------------------------------
# Command
# ---------------------------------------------------------------------------


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the find-symbol subcommand."""
    parser = subparsers.add_parser(
        "find-symbol",
        help="Search across ALL commits (every branch) for a symbol.",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--hash", default=None, metavar="HASH", dest="hash_prefix",
        help="Find symbols whose content_id starts with this prefix.",
    )
    parser.add_argument(
        "--name", "-n", default=None, metavar="NAME", dest="name_pattern",
        help="Find symbols with this name (exact, case-insensitive). Append * for prefix.",
    )
    parser.add_argument(
        "--kind", "-k", default=None, metavar="KIND", dest="kind_filter",
        help="Restrict to symbols of this kind (function, class, method, …).",
    )
    parser.add_argument(
        "--all-branches", action="store_true", dest="all_branches",
        help="Also report which branch tips currently contain matching symbols.",
    )
    parser.add_argument(
        "--first", action="store_true", dest="first_only",
        help="Show only the first appearance of each unique content_id.",
    )
    parser.add_argument(
        "--json", action="store_true", dest="as_json",
        help="Emit results as JSON.",
    )
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> None:
    """Search across ALL commits (every branch) for a symbol.

    Closes two gaps in ``muse query``:

    \\b
    1. **Temporal**: find the exact commit when a function body first appeared.
    2. **Cross-branch**: find a function that exists on multiple branches.

    At least one of ``--hash``, ``--name``, or ``--kind`` is required.

    \\b
    Examples::

        muse find-symbol --hash a3f2c9
        muse find-symbol --name validate_amount --kind function
        muse find-symbol --name "compute*" --first
        muse find-symbol --hash a3f2c9 --all-branches
    """
    hash_prefix: str | None = args.hash_prefix
    name_pattern: str | None = args.name_pattern
    kind_filter: str | None = args.kind_filter
    all_branches: bool = args.all_branches
    first_only: bool = args.first_only
    as_json: bool = args.as_json

    root = require_repo()
    repo_id = _read_repo_id(root)

    if not hash_prefix and not name_pattern and not kind_filter:
        print(
            "❌ At least one of --hash, --name, or --kind is required.", file=sys.stderr
        )
        raise SystemExit(ExitCode.USER_ERROR)

    appearances = _search_all_commits(
        root,
        hash_prefix=hash_prefix,
        name_pattern=name_pattern,
        kind_filter=kind_filter,
        first_only=first_only,
    )

    branch_hits: list[_BranchPresence] = []
    if all_branches:
        branch_hits = _branch_presence(root, repo_id, hash_prefix, name_pattern, kind_filter)

    if as_json:
        print(json.dumps(
            {
                "query": {
                    "hash": hash_prefix,
                    "name": name_pattern,
                    "kind": kind_filter,
                    "first_only": first_only,
                },
                "appearances": [a.to_dict() for a in appearances],
                "branch_presence": [b.to_dict() for b in branch_hits] if all_branches else None,
            },
            indent=2,
        ))
        return

    # Human-readable output.
    print(f"\nfind-symbol — searching {len(appearances)} match(es) across all commits")

    query_parts: list[str] = []
    if hash_prefix:
        query_parts.append(f"hash prefix={hash_prefix}")
    if name_pattern:
        query_parts.append(f"name={name_pattern}")
    if kind_filter:
        query_parts.append(f"kind={kind_filter}")
    print(f"Query: {',  '.join(query_parts)}")
    print("─" * 62)

    if not appearances:
        print("  (no matching symbols found in commit history)")
    else:
        for ap in appearances:
            date_str = ap.commit.committed_at.strftime("%Y-%m-%d")
            short_id = ap.commit.commit_id[:8]
            branch_label = f"  [{ap.commit.branch}]" if ap.commit.branch else ""
            print(
                f"\n  {ap.address}"
            )
            print(
                f"  {short_id}  {date_str}  \"{ap.commit.message}\"{branch_label}"
            )
            if ap.content_id:
                print(f"  content_id: {ap.content_id[:16]}..")

    if all_branches:
        print(f"\nBranch presence ({len(branch_hits)} hit(s)):")
        print("─" * 62)
        if not branch_hits:
            print("  (symbol not found in any branch HEAD)")
        else:
            for bh in branch_hits:
                print(f"  [{bh.branch}]  {bh.address}  {bh.content_id[:16]}..")

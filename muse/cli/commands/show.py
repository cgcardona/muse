"""muse show — inspect a commit: metadata, diff, and files."""

from __future__ import annotations

import argparse
import json
import logging
import pathlib
import sys

from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.store import get_commit_snapshot_manifest, read_commit, read_current_branch, read_snapshot, resolve_commit_ref
from muse.core.validation import sanitize_display
from muse.domain import DomainOp

logger = logging.getLogger(__name__)


def _read_branch(root: pathlib.Path) -> str:
    return read_current_branch(root)


def _read_repo_id(root: pathlib.Path) -> str:
    return str(json.loads((root / ".muse" / "repo.json").read_text())["repo_id"])


def _format_op(op: DomainOp) -> list[str]:
    """Return one or more display lines for a single domain op.

    Each branch checks ``op["op"]`` directly so mypy can narrow the
    TypedDict union to the specific subtype before accessing its fields.
    """
    if op["op"] == "insert":
        return [f" A  {op['address']}"]
    if op["op"] == "delete":
        return [f" D  {op['address']}"]
    if op["op"] == "replace":
        return [f" M  {op['address']}"]
    if op["op"] == "move":
        return [f" R  {op['address']}  ({op['from_position']} → {op['to_position']})"]
    if op["op"] == "mutate":
        fields = ", ".join(
            f"{k}: {v['old']}→{v['new']}" for k, v in op.get("fields", {}).items()
        )
        return [f" ~ {op['address']}  ({fields or op.get('old_summary', '')}→{op.get('new_summary', '')})"]
    # op["op"] == "patch" — the only remaining variant.
    lines = [f" M  {op['address']}"]
    if op["child_summary"]:
        lines.append(f"    └─ {op['child_summary']}")
    return lines


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the show subcommand."""
    parser = subparsers.add_parser(
        "show",
        help="Inspect a commit: metadata, diff, and files.",
        description=__doc__,
    )
    parser.add_argument("ref", nargs="?", default=None, help="Commit ID or branch (default: HEAD).")
    parser.add_argument("--stat", action="store_true", default=True, help="Show file change summary.")
    parser.add_argument("--no-stat", dest="stat", action="store_false", help="Omit file change summary.")
    parser.add_argument("--json", action="store_true", dest="json_out", help="Output as JSON.")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> None:
    """Inspect a commit: metadata, diff, and files.

    Agents should pass ``--json`` to receive full commit metadata plus a file
    change summary::

        {
          "commit_id":      "<sha256>",
          "branch":         "main",
          "message":        "Add verse melody",
          "author":         "gabriel",
          "committed_at":   "2026-03-21T12:00:00+00:00",
          "snapshot_id":    "<sha256>",
          "parent_commit_id": "<sha256> | null",
          "files_added":    ["new_track.mid"],
          "files_removed":  [],
          "files_modified": ["tracks/bass.mid"]
        }

    Pass ``--no-stat`` to omit the ``files_added/removed/modified`` fields.
    """
    ref: str | None = args.ref
    stat: bool = args.stat
    json_out: bool = args.json_out

    root = require_repo()
    repo_id = _read_repo_id(root)
    branch = _read_branch(root)

    commit = resolve_commit_ref(root, repo_id, branch, ref)
    if commit is None:
        print(f"❌ Commit '{sanitize_display(str(ref))}' not found.")
        raise SystemExit(ExitCode.USER_ERROR)

    if json_out:
        commit_data = commit.to_dict()
        if stat:
            # Read current snapshot directly via snapshot_id (avoids re-reading
            # the commit we already have in memory).
            cur_snap = read_snapshot(root, commit.snapshot_id)
            cur = cur_snap.manifest if cur_snap is not None else {}
            par: dict[str, str] = {}
            if commit.parent_commit_id:
                par_manifest = get_commit_snapshot_manifest(root, commit.parent_commit_id)
                par = par_manifest if par_manifest is not None else {}
            stats = {
                "files_added": sorted(set(cur) - set(par)),
                "files_removed": sorted(set(par) - set(cur)),
                "files_modified": sorted(
                    p for p in set(cur) & set(par) if cur[p] != par[p]
                ),
            }
            print(json.dumps({**commit_data, **stats}, indent=2, default=str))
        else:
            print(json.dumps(commit_data, indent=2, default=str))
        return

    print(f"commit {commit.commit_id}")
    if commit.parent_commit_id:
        print(f"Parent: {commit.parent_commit_id[:8]}")
    if commit.parent2_commit_id:
        print(f"Parent: {commit.parent2_commit_id[:8]} (merge)")
    if commit.author:
        print(f"Author: {sanitize_display(commit.author)}")
    print(f"Date:   {commit.committed_at}")
    if commit.metadata:
        for k, v in sorted(commit.metadata.items()):
            print(f"        {sanitize_display(k)}: {sanitize_display(str(v))}")
    print(f"\n    {sanitize_display(commit.message)}\n")

    if not stat:
        return

    # Prefer the structured delta stored on the commit.
    # It carries rich note-level detail and is faster (no blob reloading).
    if commit.structured_delta is not None:
        delta = commit.structured_delta
        if not delta["ops"]:
            print(" (no changes)")
            return
        lines: list[str] = []
        for op in delta["ops"]:
            lines.extend(_format_op(op))
        for line in lines:
            print(line)
        print(f"\n {delta['summary']}")
        return

    # Fallback for initial commits or pre-Phase-1 commits: compute file-level
    # diff from snapshot manifests directly.
    current = get_commit_snapshot_manifest(root, commit.commit_id) or {}
    parent: dict[str, str] = {}
    if commit.parent_commit_id:
        parent = get_commit_snapshot_manifest(root, commit.parent_commit_id) or {}

    added = sorted(set(current) - set(parent))
    removed = sorted(set(parent) - set(current))
    modified = sorted(p for p in set(current) & set(parent) if current[p] != parent[p])

    for p in added:
        print(f" A  {p}")
    for p in removed:
        print(f" D  {p}")
    for p in modified:
        print(f" M  {p}")

    total = len(added) + len(removed) + len(modified)
    if total:
        print(f"\n {total} file(s) changed")

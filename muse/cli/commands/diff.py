"""muse diff — compare working tree against HEAD, or compare two commits."""

from __future__ import annotations

import argparse
import difflib
import json
import logging
import pathlib
import sys

from muse.core.errors import ExitCode
from muse.core.object_store import read_object
from muse.core.repo import require_repo
from muse.core.store import get_commit_snapshot_manifest, get_head_snapshot_manifest, read_current_branch, resolve_commit_ref
from muse.core.validation import sanitize_display
from muse.domain import DomainOp, SnapshotManifest
from muse.plugins.registry import read_domain, resolve_plugin

logger = logging.getLogger(__name__)


def _read_branch(root: pathlib.Path) -> str:
    return read_current_branch(root)


def _read_repo_id(root: pathlib.Path) -> str:
    return str(json.loads((root / ".muse" / "repo.json").read_text())["repo_id"])


_MAX_INLINE_CHILDREN = 12


def _green(text: str) -> str:
    return f"\033[32m{text}\033[0m"


def _red(text: str) -> str:
    return f"\033[31m{text}\033[0m"


def _yellow(text: str) -> str:
    return f"\033[33m{text}\033[0m"


def _cyan(text: str) -> str:
    return f"\033[36m{text}\033[0m"


_LOC_SEP = "  L"


def _split_loc(summary: str) -> tuple[str, str]:
    """Split 'added function foo  L4–8' into ('added function foo', 'L4–8').

    Returns the original string and an empty loc when no location suffix is
    present (e.g. cross-file move annotations that carry no line data).
    """
    if _LOC_SEP in summary:
        label, _, loc = summary.rpartition(_LOC_SEP)
        return label, f"L{loc}"
    return summary, ""


def _print_child_ops(child_ops: list[DomainOp]) -> None:
    """Render symbol-level child ops with aligned columns and colours.

    Labels are left-padded to a uniform width within the group so the
    line-range column (``L{start}–{end}``) lines up vertically.  Shows up
    to ``_MAX_INLINE_CHILDREN`` entries inline; summarises the rest on a
    single trailing line.
    """
    visible = child_ops[:_MAX_INLINE_CHILDREN]
    overflow = len(child_ops) - len(visible)

    # First pass: gather (op_type, unstyled_label, loc) for each visible op.
    # We need unstyled widths before applying ANSI colour codes.
    rows: list[tuple[str, str, str]] = []
    for cop in visible:
        if cop["op"] == "insert":
            label, loc = _split_loc(cop["content_summary"])
            rows.append(("insert", label, loc))
        elif cop["op"] == "delete":
            label, loc = _split_loc(cop["content_summary"])
            rows.append(("delete", label, loc))
        elif cop["op"] == "replace":
            label, loc = _split_loc(cop["new_summary"])
            rows.append(("replace", label, loc))
        elif cop["op"] == "move":
            label = f"{cop['address']}  ({cop['from_position']} → {cop['to_position']})"
            rows.append(("move", label, ""))
        else:
            rows.append(("unknown", "", ""))

    for i, (op_type, label, loc) in enumerate(rows):
        is_last = (i == len(rows) - 1) and overflow == 0
        connector = "└─" if is_last else "├─"
        if op_type == "insert":
            styled = _green(label)
        elif op_type == "delete":
            styled = _red(label)
        elif op_type == "replace":
            styled = _yellow(label)
        elif op_type == "move":
            styled = _cyan(label)
        else:
            styled = label
        suffix = f"  {loc}" if loc else ""
        print(f"   {connector} {styled}{suffix}")

    if overflow > 0:
        print(f"   └─ … and {overflow} more")


def _print_structured_delta(ops: list[DomainOp]) -> int:
    """Print a colour-coded delta op-by-op. Returns the number of ops printed.

    Colour scheme mirrors standard diff conventions:
    - Green  → added   (A)
    - Red    → deleted (D)
    - Yellow → modified (M)
    - Cyan   → moved / renamed (R)

    Each branch checks ``op["op"]`` directly so mypy can narrow the
    TypedDict union to the specific subtype before accessing its fields.
    """
    for op in ops:
        if op["op"] == "insert":
            print(_green(f"A  {op['address']}"))
        elif op["op"] == "delete":
            print(_red(f"D  {op['address']}"))
        elif op["op"] == "replace":
            print(_yellow(f"M  {op['address']}"))
        elif op["op"] == "move":
            print(
                _cyan(f"R  {op['address']}  ({op['from_position']} → {op['to_position']})")
            )
        elif op["op"] == "patch":
            child_ops = op["child_ops"]
            from_address = op.get("from_address")
            if from_address:
                # File was renamed AND edited simultaneously.
                print(_cyan(f"R  {from_address} → {op['address']}"))
            else:
                # Classify the patch: all-inserts = new file, all-deletes =
                # removed file, mixed = modification.  Use the right status
                # prefix so the output reads like `git diff --name-status`.
                all_insert = all(c["op"] == "insert" for c in child_ops)
                all_delete = all(c["op"] == "delete" for c in child_ops)
                if all_insert:
                    print(_green(f"A  {op['address']}"))
                elif all_delete:
                    print(_red(f"D  {op['address']}"))
                else:
                    print(_yellow(f"M  {op['address']}"))
            _print_child_ops(child_ops)
    return len(ops)


def _print_text_diff(
    base_files: dict[str, str],
    target_files: dict[str, str],
    root: pathlib.Path,
    workdir: pathlib.Path | None,
) -> int:
    """Print a coloured unified diff for every changed file. Returns change count."""
    base_paths = set(base_files)
    target_paths = set(target_files)
    changed = (
        sorted(target_paths - base_paths)          # added
        + sorted(base_paths - target_paths)        # removed
        + sorted(                                   # modified
            p for p in base_paths & target_paths
            if base_files[p] != target_files[p]
        )
    )

    for path in changed:
        # Read base content.
        if path in base_files:
            raw_base = read_object(root, base_files[path])
            base_lines = raw_base.decode("utf-8", errors="replace").splitlines(keepends=True) if raw_base else []
            base_label = f"a/{path}"
        else:
            base_lines = []
            base_label = "/dev/null"

        # Read target content (object store first, then disk for working tree).
        if path in target_files:
            raw_target = read_object(root, target_files[path])
            if raw_target is None and workdir is not None:
                disk = workdir / path
                if disk.is_file():
                    raw_target = disk.read_bytes()
            target_lines = raw_target.decode("utf-8", errors="replace").splitlines(keepends=True) if raw_target else []
            target_label = f"b/{path}"
        else:
            target_lines = []
            target_label = "/dev/null"

        hunks = list(difflib.unified_diff(
            base_lines, target_lines,
            fromfile=base_label, tofile=target_label,
            lineterm="",
        ))
        if not hunks:
            continue

        for line in hunks:
            if line.startswith("---") or line.startswith("+++"):
                print(f"\033[1m{line}\033[0m")
            elif line.startswith("@@"):
                print(_cyan(line))
            elif line.startswith("+"):
                print(_green(line))
            elif line.startswith("-"):
                print(_red(line))
            else:
                print(line)

    return len(changed)


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the diff subcommand."""
    parser = subparsers.add_parser(
        "diff",
        help="Compare working tree against HEAD, or compare two commits.",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("commit_a", nargs="?", default=None, help="Base commit ID (default: HEAD).")
    parser.add_argument("commit_b", nargs="?", default=None, help="Target commit ID (default: working tree).")
    parser.add_argument("--stat", action="store_true", help="Show summary statistics only.")
    parser.add_argument("--text", action="store_true", help="Show line-level unified diff instead of semantic symbols.")
    parser.add_argument("--format", "-f", default="text", dest="fmt", help="Output format: text or json.")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> None:
    """Compare working tree against HEAD, or compare two commits.

    Agents should pass ``--format json`` to receive a structured result::

        {
          "summary":       "3 changes",
          "added":         ["path/to/new_file"],
          "deleted":       ["path/to/removed_file"],
          "modified":      ["path/to/changed_file"],
          "total_changes": 3
        }
    """
    commit_a: str | None = args.commit_a
    commit_b: str | None = args.commit_b
    stat: bool = args.stat
    text: bool = args.text
    fmt: str = args.fmt

    if fmt not in ("text", "json"):
        print(f"❌ Unknown --format '{sanitize_display(fmt)}'. Choose text or json.", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)
    root = require_repo()
    repo_id = _read_repo_id(root)
    branch = _read_branch(root)
    domain = read_domain(root)
    plugin = resolve_plugin(root)

    def _resolve_manifest(ref: str) -> dict[str, str]:
        """Resolve a ref (branch, short SHA, full SHA) to its snapshot manifest."""
        resolved = resolve_commit_ref(root, repo_id, branch, ref)
        if resolved is None:
            print(f"⚠️ Commit '{sanitize_display(ref)}' not found.")
            raise SystemExit(ExitCode.USER_ERROR)
        return get_commit_snapshot_manifest(root, resolved.commit_id) or {}

    if commit_a is None:
        base_snap = SnapshotManifest(
            files=get_head_snapshot_manifest(root, repo_id, branch) or {},
            domain=domain,
        )
        target_snap = plugin.snapshot(root)
    elif commit_b is None:
        # Single ref provided: diff HEAD vs that ref's snapshot.
        base_snap = SnapshotManifest(
            files=get_head_snapshot_manifest(root, repo_id, branch) or {},
            domain=domain,
        )
        target_snap = SnapshotManifest(
            files=_resolve_manifest(commit_a),
            domain=domain,
        )
    else:
        base_snap = SnapshotManifest(
            files=_resolve_manifest(commit_a),
            domain=domain,
        )
        target_snap = SnapshotManifest(
            files=_resolve_manifest(commit_b),
            domain=domain,
        )

    if text and fmt != "json":
        workdir = root if commit_a is None else None
        changed = _print_text_diff(
            base_snap["files"], target_snap["files"], root, workdir
        )
        if changed == 0:
            print("No differences.")
        return

    delta = plugin.diff(base_snap, target_snap, repo_root=root)

    if fmt == "json":
        added = [op["address"] for op in delta["ops"] if op["op"] == "insert"]
        deleted = [op["address"] for op in delta["ops"] if op["op"] == "delete"]
        modified = [op["address"] for op in delta["ops"]
                    if op["op"] in ("replace", "patch", "mutate", "move")]
        print(json.dumps({
            "summary": delta["summary"],
            "added": sorted(added),
            "deleted": sorted(deleted),
            "modified": sorted(modified),
            "total_changes": len(delta["ops"]),
        }))
        return

    if stat:
        print(delta["summary"] if delta["ops"] else "No differences.")
        return

    changed = _print_structured_delta(delta["ops"])

    if changed == 0:
        print("No differences.")
    else:
        print(f"\n{delta['summary']}")

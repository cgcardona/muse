"""muse checkout — switch branches or restore working tree from a commit.

Usage::

    muse checkout <branch>           — switch to existing branch
    muse checkout -b <branch>        — create and switch to new branch
    muse checkout <commit-id>        — detach HEAD at a specific commit
"""

from __future__ import annotations

import argparse
import json
import logging
import pathlib
import sys

from muse.core.errors import ExitCode
from muse.core.object_store import restore_object
from muse.core.repo import require_repo
from muse.core.store import (
    get_head_commit_id,
    get_head_snapshot_id,
    read_current_branch,
    read_snapshot,
    resolve_commit_ref,
    write_head_branch,
    write_head_commit,
)
from muse.core.reflog import append_reflog
from muse.core.validation import contain_path, sanitize_display, validate_branch_name
from muse.cli.guard import require_clean_workdir
from muse.domain import SnapshotManifest
from muse.plugins.registry import read_domain, resolve_plugin

logger = logging.getLogger(__name__)


def _read_current_branch(root: pathlib.Path) -> str:
    return read_current_branch(root)


def _read_repo_id(root: pathlib.Path) -> str:
    return str(json.loads((root / ".muse" / "repo.json").read_text())["repo_id"])


def _checkout_snapshot(
    root: pathlib.Path,
    target_snapshot_id: str,
    current_snapshot_id: str | None,
) -> None:
    """Incrementally update state/ from current to target snapshot.

    Uses the domain plugin to compute the delta between the two snapshots and
    only touches files that actually changed — removing deleted paths and
    restoring added/modified ones from the object store.  Calls
    ``plugin.apply()`` as the domain-level post-checkout hook.
    """
    plugin = resolve_plugin(root)
    domain = read_domain(root)

    target_snap_rec = read_snapshot(root, target_snapshot_id)
    if target_snap_rec is None:
        print(f"❌ Snapshot {target_snapshot_id[:8]} not found in object store.")
        raise SystemExit(ExitCode.INTERNAL_ERROR)

    target_snap = SnapshotManifest(files=target_snap_rec.manifest, domain=domain)

    if current_snapshot_id is not None:
        cur_rec = read_snapshot(root, current_snapshot_id)
        current_snap = (
            SnapshotManifest(files=cur_rec.manifest, domain=domain)
            if cur_rec else SnapshotManifest(files={}, domain=domain)
        )
    else:
        current_snap = SnapshotManifest(files={}, domain=domain)

    delta = plugin.diff(current_snap, target_snap)

    # Remove files that no longer exist in the target snapshot.
    removed = [op["address"] for op in delta["ops"] if op["op"] == "delete"]
    for rel_path in removed:
        fp = root / rel_path
        if fp.exists():
            fp.unlink()

    # Restore added and modified files from the content-addressed store.
    # InsertOp, ReplaceOp, and PatchOp all mean the file's content changed;
    # the authoritative hash for each is in the target snapshot manifest.
    to_restore = [
        op["address"] for op in delta["ops"]
        if op["op"] in ("insert", "replace", "patch")
    ]
    for rel_path in to_restore:
        object_id = target_snap_rec.manifest[rel_path]
        try:
            safe_dest = contain_path(root, rel_path)
        except ValueError as exc:
            logger.warning("⚠️ Skipping unsafe manifest path %r: %s", rel_path, exc)
            continue
        if not restore_object(root, object_id, safe_dest):
            print(f"⚠️  Object {object_id[:8]} for '{sanitize_display(rel_path)}' not in local store — skipped.")

    # Domain-level post-checkout hook: rescan the workdir to confirm state.
    plugin.apply(delta, root)


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the checkout subcommand."""
    parser = subparsers.add_parser(
        "checkout",
        help="Switch branches or restore working tree from a commit.",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("target", help="Branch name or commit ID to check out.")
    parser.add_argument("-b", "--create", action="store_true", help="Create a new branch.")
    parser.add_argument("--force", "-f", action="store_true", help="Discard uncommitted changes.")
    parser.add_argument("--format", default="text", dest="fmt", help="Output format: text or json.")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> None:
    """Switch branches or restore working tree from a commit.

    Agents should pass ``--format json`` to get a machine-readable result::

        {"action": "created|switched|detached|already_on", "branch": "<name>", "commit_id": "<sha256>"}
    """
    target: str = args.target
    create: bool = args.create
    force: bool = args.force
    fmt: str = args.fmt

    if fmt not in ("text", "json"):
        from muse.core.validation import sanitize_display as _sd
        print(f"❌ Unknown --format '{_sd(fmt)}'. Choose text or json.", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)
    root = require_repo()
    require_clean_workdir(root, "checkout", force=force)
    repo_id = _read_repo_id(root)
    current_branch = _read_current_branch(root)
    muse_dir = root / ".muse"

    current_snapshot_id = get_head_snapshot_id(root, repo_id, current_branch)

    if create:
        try:
            validate_branch_name(target)
        except ValueError as exc:
            print(f"❌ Invalid branch name: {exc}")
            raise SystemExit(ExitCode.USER_ERROR)
        ref_file = muse_dir / "refs" / "heads" / target
        if ref_file.exists():
            print(f"❌ Branch '{sanitize_display(target)}' already exists. Use 'muse checkout {sanitize_display(target)}' to switch to it.")
            raise SystemExit(ExitCode.USER_ERROR)
        current_commit = get_head_commit_id(root, current_branch) or ""
        ref_file.parent.mkdir(parents=True, exist_ok=True)
        ref_file.write_text(current_commit)
        write_head_branch(root, target)
        append_reflog(
            root, target, old_id=None, new_id=current_commit or ("0" * 64),
            author="user", operation=f"branch: created from {sanitize_display(current_branch)}",
        )
        if fmt == "json":
            print(json.dumps({"action": "created", "branch": target, "commit_id": current_commit}))
        else:
            print(f"Switched to a new branch '{sanitize_display(target)}'")
        return

    # Check if target is a known branch — validate name before using as path component.
    try:
        validate_branch_name(target)
    except ValueError as exc:
        print(f"❌ Invalid branch name: {exc}", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)

    ref_file = muse_dir / "refs" / "heads" / target
    if ref_file.exists():
        if target == current_branch:
            if fmt == "json":
                print(json.dumps({"action": "already_on", "branch": target,
                                  "commit_id": get_head_commit_id(root, target) or ""}))
            else:
                print(f"Already on '{sanitize_display(target)}'")
            return

        target_commit_id = get_head_commit_id(root, target) or ""
        current_commit_id = get_head_commit_id(root, current_branch) or ""
        target_snapshot_id = get_head_snapshot_id(root, repo_id, target)
        if target_snapshot_id:
            _checkout_snapshot(root, target_snapshot_id, current_snapshot_id)

        write_head_branch(root, target)
        append_reflog(
            root, target, old_id=current_commit_id or None, new_id=target_commit_id or ("0" * 64),
            author="user",
            operation=f"checkout: moving from {sanitize_display(current_branch)} to {sanitize_display(target)}",
        )
        if fmt == "json":
            print(json.dumps({"action": "switched", "branch": target, "commit_id": target_commit_id}))
        else:
            print(f"Switched to branch '{sanitize_display(target)}'")
        return

    # Try as a commit ID (detached HEAD)
    commit = resolve_commit_ref(root, repo_id, current_branch, target)
    if commit is None:
        print(f"❌ '{target}' is not a branch or commit ID.")
        raise SystemExit(ExitCode.USER_ERROR)

    current_commit_id = get_head_commit_id(root, current_branch) or ""
    _checkout_snapshot(root, commit.snapshot_id, current_snapshot_id)
    write_head_commit(root, commit.commit_id)
    append_reflog(
        root, current_branch, old_id=current_commit_id or None, new_id=commit.commit_id,
        author="user",
        operation=f"checkout: detaching HEAD at {commit.commit_id[:12]}",
    )
    if fmt == "json":
        print(json.dumps({"action": "detached", "branch": None, "commit_id": commit.commit_id}))
    else:
        print(f"HEAD is now at {commit.commit_id[:8]} {sanitize_display(commit.message)}")

"""muse merge — three-way merge a branch into the current branch.

Algorithm
---------
1. Find the merge base (LCA) of HEAD and the target branch.
2. Delegate conflict detection and manifest reconciliation to the domain plugin.
3. If clean → apply merged manifest, write new commit, advance HEAD.
4. If conflicts → write conflict markers to the working tree, write
   ``.muse/MERGE_STATE.json``, exit non-zero.
"""

from __future__ import annotations

import argparse
import datetime
import json
import logging
import pathlib
import sys

from muse.core.errors import ExitCode
from muse.core.merge_engine import (
    find_merge_base,
    write_merge_state,
)
from muse.core.repo import require_repo
from muse.core.rerere import auto_apply as rerere_auto_apply
from muse.core.snapshot import compute_commit_id, compute_snapshot_id
from muse.core.store import (
    CommitRecord,
    SnapshotRecord,
    get_head_commit_id,
    get_head_snapshot_manifest,
    read_commit,
    read_current_branch,
    read_snapshot,
    write_commit,
    write_snapshot,
)
from muse.core.reflog import append_reflog
from muse.core.validation import sanitize_display, validate_branch_name
from muse.core.workdir import apply_manifest
from muse.cli.guard import require_clean_workdir
from muse.domain import MergeResult, SnapshotManifest, StructuredMergePlugin
from muse.plugins.registry import read_domain, resolve_plugin

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# ANSI helpers
# ---------------------------------------------------------------------------

_RESET  = "\033[0m"
_BOLD   = "\033[1m"
_DIM    = "\033[2m"
_GREEN  = "\033[32m"
_RED    = "\033[31m"
_YELLOW = "\033[33m"
_CYAN   = "\033[36m"


def _c(text: str, *codes: str, tty: bool) -> str:
    """Wrap *text* in ANSI escape *codes* only when writing to a TTY."""
    if not tty:
        return text
    return "".join(codes) + text + _RESET


def _diff_stats(
    old: dict[str, str],
    new: dict[str, str],
) -> tuple[int, int, int]:
    """Return (added, modified, deleted) file counts between two manifests."""
    added    = sum(1 for k in new if k not in old)
    deleted  = sum(1 for k in old if k not in new)
    modified = sum(1 for k in new if k in old and old[k] != new[k])
    return added, modified, deleted


def _print_file_stats(
    added: int,
    modified: int,
    deleted: int,
    tty: bool,
) -> None:
    """Emit the 'N files changed (A added, M modified, D deleted)' summary line."""
    total = added + modified + deleted
    if total == 0:
        return
    files_word = "file" if total == 1 else "files"
    parts: list[str] = []
    if added:
        parts.append(_c(f"{added} added", _GREEN, tty=tty))
    if modified:
        parts.append(f"{modified} modified")
    if deleted:
        parts.append(_c(f"{deleted} deleted", _RED, tty=tty))
    detail = ", ".join(parts)
    print(f" {_c(str(total), _BOLD, tty=tty)} {files_word} changed  ({detail})")


def _read_branch(root: pathlib.Path) -> str:
    """Return the current branch name by reading ``.muse/HEAD``."""
    return read_current_branch(root)


def _read_repo_id(root: pathlib.Path) -> str:
    """Return the repository UUID from ``.muse/repo.json``."""
    return str(json.loads((root / ".muse" / "repo.json").read_text())["repo_id"])


def _restore_from_manifest(root: pathlib.Path, manifest: dict[str, str]) -> None:
    """Apply *manifest* to the working tree at *root*.

    Delegates to :func:`muse.core.workdir.apply_manifest` which surgically
    removes files no longer present in the target and restores the rest from
    the content-addressed object store.

    Args:
        root:     Repository root (the directory containing ``.muse/``).
        manifest: Mapping of POSIX-relative paths to SHA-256 object IDs.
    """
    apply_manifest(root, manifest)


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the merge subcommand."""
    parser = subparsers.add_parser(
        "merge",
        help="Three-way merge a branch into the current branch.",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("branch", help="Branch to merge into the current branch.")
    parser.add_argument("--no-ff", action="store_true", help="Always create a merge commit, even for fast-forward.")
    parser.add_argument("-m", "--message", default=None, help="Override the merge commit message.")
    parser.add_argument("--rerere-autoupdate", action="store_true", default=True, dest="rerere_autoupdate",
                        help="Automatically apply cached rerere resolutions to matching conflicts (default: on).")
    parser.add_argument("--no-rerere-autoupdate", action="store_false", dest="rerere_autoupdate",
                        help="Disable rerere auto-update.")
    parser.add_argument("--force", action="store_true",
                        help="Proceed even if the working tree has uncommitted changes (data-loss risk).")
    parser.add_argument("--format", "-f", default="text", dest="fmt", help="Output format: text or json.")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> None:
    """Three-way merge a branch into the current branch.

    Agents should pass ``--format json`` to receive a structured result with
    ``status`` (merged|fast_forward|conflict|up_to_date), ``commit_id``,
    ``branch``, ``current_branch``, and ``conflicts`` list.
    """
    branch: str = args.branch
    no_ff: bool = args.no_ff
    message: str | None = args.message
    rerere_autoupdate: bool = args.rerere_autoupdate
    force: bool = args.force
    fmt: str = args.fmt

    if fmt not in ("text", "json"):
        print(f"❌ Unknown --format '{sanitize_display(fmt)}'. Choose text or json.", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)
    root = require_repo()
    require_clean_workdir(root, "merge", force=force)
    repo_id = _read_repo_id(root)
    current_branch = _read_branch(root)
    domain = read_domain(root)
    plugin = resolve_plugin(root)

    if branch == current_branch:
        print("❌ Cannot merge a branch into itself.")
        raise SystemExit(ExitCode.USER_ERROR)

    ours_commit_id = get_head_commit_id(root, current_branch)
    theirs_commit_id = get_head_commit_id(root, branch)

    if theirs_commit_id is None:
        print(f"❌ Branch '{branch}' has no commits.")
        raise SystemExit(ExitCode.USER_ERROR)

    if ours_commit_id is None:
        print("❌ Current branch has no commits.")
        raise SystemExit(ExitCode.USER_ERROR)

    base_commit_id = find_merge_base(root, ours_commit_id, theirs_commit_id)

    if base_commit_id == theirs_commit_id:
        if fmt == "json":
            print(json.dumps({"status": "up_to_date", "commit_id": ours_commit_id,
                              "branch": branch, "current_branch": current_branch, "conflicts": []}))
        else:
            print("Already up to date.")
        return

    if base_commit_id == ours_commit_id and not no_ff:
        theirs_commit = read_commit(root, theirs_commit_id)
        ff_manifest: dict[str, str] = {}
        if theirs_commit:
            ff_snap = read_snapshot(root, theirs_commit.snapshot_id)
            if ff_snap:
                ff_manifest = ff_snap.manifest
                _restore_from_manifest(root, ff_manifest)
        try:
            validate_branch_name(current_branch)
        except ValueError as exc:
            print(f"❌ Current branch name is invalid: {exc}")
            raise SystemExit(ExitCode.INTERNAL_ERROR)
        (root / ".muse" / "refs" / "heads" / current_branch).write_text(theirs_commit_id)
        append_reflog(
            root, current_branch, old_id=ours_commit_id, new_id=theirs_commit_id,
            author="user",
            operation=f"merge: fast-forward {sanitize_display(branch)} → {sanitize_display(current_branch)}",
        )
        if fmt == "json":
            print(json.dumps({"status": "fast_forward", "commit_id": theirs_commit_id,
                              "branch": branch, "current_branch": current_branch, "conflicts": []}))
        else:
            tty = sys.stdout.isatty()
            # Compute manifest diff for the stat line.
            ours_commit_rec = read_commit(root, ours_commit_id)
            ours_ff_manifest: dict[str, str] = {}
            if ours_commit_rec:
                ours_snap_rec = read_snapshot(root, ours_commit_rec.snapshot_id)
                if ours_snap_rec:
                    ours_ff_manifest = ours_snap_rec.manifest
            added, modified, deleted = _diff_stats(ours_ff_manifest, ff_manifest)
            print(
                f"{_c('Updating', _DIM, tty=tty)} "
                f"{_c(ours_commit_id[:8], _YELLOW, tty=tty)}.."
                f"{_c(theirs_commit_id[:8], _YELLOW, tty=tty)}"
            )
            print(_c("Fast-forward", _BOLD, tty=tty) + f"  {sanitize_display(branch)} → {sanitize_display(current_branch)}")
            _print_file_stats(added, modified, deleted, tty=tty)
        return

    ours_manifest = get_head_snapshot_manifest(root, repo_id, current_branch) or {}
    theirs_manifest = get_head_snapshot_manifest(root, repo_id, branch) or {}
    base_manifest: dict[str, str] = {}
    if base_commit_id:
        base_commit = read_commit(root, base_commit_id)
        if base_commit:
            base_snap = read_snapshot(root, base_commit.snapshot_id)
            if base_snap:
                base_manifest = base_snap.manifest

    base_snap_obj = SnapshotManifest(files=base_manifest, domain=domain)
    ours_snap_obj = SnapshotManifest(files=ours_manifest, domain=domain)
    theirs_snap_obj = SnapshotManifest(files=theirs_manifest, domain=domain)

    # Prefer operation-level merge when the plugin supports it.
    # Produces finer-grained conflict detection (sub-file / note level).
    # Falls back to file-level merge() for plugins without this capability.
    if isinstance(plugin, StructuredMergePlugin):
        ours_delta = plugin.diff(base_snap_obj, ours_snap_obj, repo_root=root)
        theirs_delta = plugin.diff(base_snap_obj, theirs_snap_obj, repo_root=root)
        result = plugin.merge_ops(
            base_snap_obj,
            ours_snap_obj,
            theirs_snap_obj,
            ours_delta["ops"],
            theirs_delta["ops"],
            repo_root=root,
        )
        logger.debug(
            "merge: used operation-level merge (%s); %d conflict(s)",
            type(plugin).__name__,
            len(result.conflicts),
        )
    else:
        result = plugin.merge(base_snap_obj, ours_snap_obj, theirs_snap_obj, repo_root=root)

    # Report any .museattributes auto-resolutions.
    if result.applied_strategies:
        for p, strategy in sorted(result.applied_strategies.items()):
            safe_p = sanitize_display(p)
            safe_strategy = sanitize_display(strategy)
            if strategy == "dimension-merge":
                dim_detail = result.dimension_reports.get(p, {})
                dim_summary = ", ".join(
                    f"{sanitize_display(d)}={sanitize_display(str(v))}"
                    for d, v in sorted(dim_detail.items())
                )
                print(f"  ✔ dimension-merge: {safe_p} ({dim_summary})")
            elif strategy != "manual":
                print(f"  ✔ [{safe_strategy}] {safe_p}")

    if not result.is_clean:
        # Try to auto-resolve conflicts using cached rerere resolutions.
        # Paths with no cached resolution get a preimage recorded so the
        # user's manual resolution can be saved when they run muse commit.
        rerere_resolved: dict[str, str] = {}
        remaining_conflicts = result.conflicts

        if rerere_autoupdate:
            rerere_resolved, remaining_conflicts = rerere_auto_apply(
                root,
                result.conflicts,
                ours_manifest,
                theirs_manifest,
                domain,
                plugin,
            )
            for p in sorted(rerere_resolved):
                print(f"  ✔ [rerere] auto-resolved: {sanitize_display(p)}")

        if remaining_conflicts:
            write_merge_state(
                root,
                base_commit=base_commit_id or "",
                ours_commit=ours_commit_id,
                theirs_commit=theirs_commit_id,
                conflict_paths=remaining_conflicts,
                other_branch=branch,
            )
            if fmt == "json":
                print(json.dumps({
                    "status": "conflict",
                    "commit_id": None,
                    "branch": branch,
                    "current_branch": current_branch,
                    "conflicts": sorted(remaining_conflicts),
                }))
            else:
                print(f"❌ Merge conflict in {len(remaining_conflicts)} file(s):")
                for p in sorted(remaining_conflicts):
                    print(f"  CONFLICT (both modified): {sanitize_display(p)}")
                print('\nFix conflicts and run "muse commit" to complete the merge.')
            raise SystemExit(ExitCode.USER_ERROR)

        # All conflicts resolved by rerere — rebuild result and fall through
        # to the clean-merge path so a merge commit is created normally.
        merged_files = dict(result.merged["files"])
        merged_files.update(rerere_resolved)
        result = MergeResult(
            merged=SnapshotManifest(files=merged_files, domain=domain),
            conflicts=[],
            applied_strategies=result.applied_strategies,
            dimension_reports=result.dimension_reports,
            op_log=result.op_log,
            conflict_records=result.conflict_records,
        )

    merged_manifest = result.merged["files"]
    _restore_from_manifest(root, merged_manifest)

    snapshot_id = compute_snapshot_id(merged_manifest)
    committed_at = datetime.datetime.now(datetime.timezone.utc)
    merge_message = message or f"Merge branch '{branch}' into {current_branch}"
    commit_id = compute_commit_id(
        parent_ids=[ours_commit_id, theirs_commit_id],
        snapshot_id=snapshot_id,
        message=merge_message,
        committed_at_iso=committed_at.isoformat(),
    )

    write_snapshot(root, SnapshotRecord(snapshot_id=snapshot_id, manifest=merged_manifest))
    write_commit(root, CommitRecord(
        commit_id=commit_id,
        repo_id=repo_id,
        branch=current_branch,
        snapshot_id=snapshot_id,
        message=merge_message,
        committed_at=committed_at,
        parent_commit_id=ours_commit_id,
        parent2_commit_id=theirs_commit_id,
    ))
    try:
        validate_branch_name(current_branch)
    except ValueError as exc:
        print(f"❌ Current branch name is invalid: {exc}")
        raise SystemExit(ExitCode.INTERNAL_ERROR)
    (root / ".muse" / "refs" / "heads" / current_branch).write_text(commit_id)

    append_reflog(
        root, current_branch, old_id=ours_commit_id, new_id=commit_id,
        author="user",
        operation=f"merge: {sanitize_display(branch)} into {sanitize_display(current_branch)}",
    )

    if fmt == "json":
        print(json.dumps({
            "status": "merged",
            "commit_id": commit_id,
            "branch": branch,
            "current_branch": current_branch,
            "conflicts": [],
        }))
    else:
        tty = sys.stdout.isatty()
        added, modified, deleted = _diff_stats(ours_manifest, merged_manifest)
        print(_c("Merge", _BOLD, tty=tty) + f" made by the three-way strategy.")
        print(
            f"  {sanitize_display(branch)} → {sanitize_display(current_branch)}"
            f"  {_c(commit_id[:8], _YELLOW, tty=tty)}"
        )
        _print_file_stats(added, modified, deleted, tty=tty)

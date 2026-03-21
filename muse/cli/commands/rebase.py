"""``muse rebase`` — replay commits from one branch onto another.

Muse rebase is cherry-pick of a range: it takes the commits unique to the
current branch (those not reachable from the upstream) and replays them
one-by-one on top of the upstream.  Because commits are content-addressed,
each replayed commit gets a new ID — the originals are untouched in the store.

Usage::

    muse rebase <upstream>                     # replay HEAD's unique commits onto upstream
    muse rebase --onto <newbase> <upstream>    # replay onto a different base
    muse rebase --squash [<upstream>]          # collapse all commits into one
    muse rebase --abort                        # restore original HEAD
    muse rebase --continue                     # resume after resolving a conflict

Exit codes::

    0 — rebase completed or aborted successfully
    1 — conflict encountered, or bad arguments
    3 — internal error
"""

from __future__ import annotations

import datetime
import json
import logging
import pathlib
from typing import Annotated

import typer

from muse.core.errors import ExitCode
from muse.core.merge_engine import find_merge_base, write_merge_state
from muse.core.rebase import (
    RebaseState,
    _write_branch_ref,
    clear_rebase_state,
    collect_commits_to_replay,
    load_rebase_state,
    replay_one,
    save_rebase_state,
)
from muse.core.reflog import append_reflog
from muse.core.repo import require_repo
from muse.domain import MuseDomainPlugin
from muse.core.snapshot import compute_commit_id, compute_snapshot_id
from muse.core.store import (
    CommitRecord,
    SnapshotRecord,
    get_head_commit_id,
    read_commit,
    read_current_branch,
    read_snapshot,
    resolve_commit_ref,
    write_commit,
    write_snapshot,
)
from muse.core.validation import sanitize_display
from muse.core.workdir import apply_manifest
from muse.plugins.registry import read_domain, resolve_plugin

logger = logging.getLogger(__name__)

app = typer.Typer(help="Replay commits from the current branch onto a new base.")


def _read_repo_id(root: pathlib.Path) -> str:
    return str(json.loads((root / ".muse" / "repo.json").read_text(encoding="utf-8"))["repo_id"])


def _resolve_ref_to_id(
    root: pathlib.Path,
    repo_id: str,
    branch: str,
    ref: str,
) -> str | None:
    """Resolve a ref string (branch name, commit SHA, or HEAD) to a commit ID."""
    if ref.upper() == "HEAD":
        return get_head_commit_id(root, branch)

    # Try as a branch ref first.
    ref_path = root / ".muse" / "refs" / "heads" / ref
    if ref_path.exists():
        raw = ref_path.read_text(encoding="utf-8").strip()
        if raw:
            return raw

    # Fall back to commit SHA prefix resolution.
    rec = resolve_commit_ref(root, repo_id, branch, ref)
    return rec.commit_id if rec else None


def _run_replay_loop(
    root: pathlib.Path,
    state: RebaseState,
    repo_id: str,
    branch: str,
    plugin: "MuseDomainPlugin",
    domain: str,
) -> bool:
    """Run the replay loop. Returns True if completed cleanly, False on conflict."""
    current_parent = state["completed"][-1] if state["completed"] else state["onto"]

    while state["remaining"]:
        orig_commit_id = state["remaining"][0]
        commit = read_commit(root, orig_commit_id)
        if commit is None:
            typer.echo(f"⚠️ Commit {orig_commit_id[:12]} not found — skipping.")
            state["remaining"].pop(0)
            save_rebase_state(root, state)
            continue

        typer.echo(f"  Replaying {orig_commit_id[:12]}: {sanitize_display(commit.message)}")

        result = replay_one(
            root, commit, current_parent, plugin, domain, repo_id, branch
        )

        if isinstance(result, list):
            # Conflict — write state and pause.
            state["remaining"].pop(0)  # will retry via --continue
            state["remaining"].insert(0, orig_commit_id)
            save_rebase_state(root, state)

            write_merge_state(
                root,
                base_commit=commit.parent_commit_id or "",
                ours_commit=current_parent,
                theirs_commit=orig_commit_id,
                conflict_paths=result,
            )
            typer.echo(f"\n❌ Rebase stopped at {orig_commit_id[:12]} due to conflict(s):")
            for p in sorted(result):
                typer.echo(f"  CONFLICT: {p}")
            typer.echo(
                "\nResolve conflicts then run:\n"
                "  muse rebase --continue    to resume\n"
                "  muse rebase --abort       to restore original HEAD"
            )
            return False

        # Clean replay — advance.
        current_parent = result.commit_id
        state["remaining"].pop(0)
        state["completed"].append(result.commit_id)
        save_rebase_state(root, state)

        append_reflog(
            root, branch,
            old_id=state["completed"][-2] if len(state["completed"]) >= 2 else state["onto"],
            new_id=result.commit_id,
            author="user",
            operation=f"rebase: replayed {orig_commit_id[:12]} onto {state['onto'][:12]}",
        )

    return True


@app.callback(invoke_without_command=True)
def rebase(
    ctx: typer.Context,
    upstream: Annotated[
        str | None,
        typer.Argument(help="Branch or commit to rebase onto."),
    ] = None,
    onto: Annotated[
        str | None,
        typer.Option("--onto", "-o", help="New base commit (replay commits between <upstream> and HEAD onto this)."),
    ] = None,
    squash: Annotated[
        bool,
        typer.Option("--squash", "-s", help="Collapse all replayed commits into one."),
    ] = False,
    squash_message: Annotated[
        str | None,
        typer.Option("--message", "-m", help="Commit message for --squash (default: last commit's message)."),
    ] = None,
    abort: Annotated[
        bool,
        typer.Option("--abort", "-a", help="Abort an in-progress rebase and restore the original HEAD."),
    ] = False,
    continue_: Annotated[
        bool,
        typer.Option("--continue", "-c", help="Resume after resolving a conflict."),
    ] = False,
) -> None:
    """Replay commits from the current branch onto a new base.

    The most common invocation replays all commits unique to the current
    branch on top of *upstream*'s HEAD::

        muse rebase main        # rebase current branch onto main

    Use ``--onto`` when you need to replay onto a commit that is not the
    tip of *upstream*::

        muse rebase --onto newbase upstream

    Use ``--squash`` to collapse all replayed commits into a single commit
    for a clean merge workflow::

        muse rebase --squash main

    When a conflict is encountered, the rebase pauses.  Resolve the conflict,
    then::

        muse rebase --continue

    Or discard the entire rebase::

        muse rebase --abort

    Examples::

        muse rebase main
        muse rebase --onto main feat/base
        muse rebase --squash --message "feat: combined" main
        muse rebase --abort
        muse rebase --continue
    """
    root = require_repo()
    repo_id = _read_repo_id(root)
    branch = read_current_branch(root)
    plugin = resolve_plugin(root)
    domain = read_domain(root)

    active_state = load_rebase_state(root)

    # --abort
    if abort:
        if active_state is None:
            typer.echo("❌ No rebase in progress.")
            raise typer.Exit(code=ExitCode.USER_ERROR)

        original_head = active_state["original_head"]
        original_branch = active_state["original_branch"]
        _write_branch_ref(root, original_branch, original_head)

        # Restore working tree to original HEAD.
        orig_commit = read_commit(root, original_head)
        if orig_commit:
            snap = read_snapshot(root, orig_commit.snapshot_id)
            if snap:
                apply_manifest(root, snap.manifest)

        append_reflog(
            root, original_branch,
            old_id=active_state["completed"][-1] if active_state["completed"] else active_state["onto"],
            new_id=original_head,
            author="user",
            operation="rebase: abort",
        )
        clear_rebase_state(root)
        typer.echo(f"✅ Rebase aborted. HEAD restored to {original_head[:12]}.")
        return

    # --continue
    if continue_:
        if active_state is None:
            typer.echo("❌ No rebase in progress. Nothing to continue.")
            raise typer.Exit(code=ExitCode.USER_ERROR)

        # The user has resolved the conflict manually. Snapshot the current
        # working tree and create the commit for the paused step.
        current_parent = (
            active_state["completed"][-1]
            if active_state["completed"]
            else active_state["onto"]
        )
        orig_commit_id = active_state["remaining"][0] if active_state["remaining"] else ""
        orig_commit = read_commit(root, orig_commit_id) if orig_commit_id else None

        snap_result = plugin.snapshot(root)
        manifest: dict[str, str] = snap_result["files"]
        snapshot_id = compute_snapshot_id(manifest)
        committed_at = datetime.datetime.now(datetime.timezone.utc)
        message = orig_commit.message if orig_commit else "rebase: continued"
        new_commit_id = compute_commit_id(
            parent_ids=[current_parent] if current_parent else [],
            snapshot_id=snapshot_id,
            message=message,
            committed_at_iso=committed_at.isoformat(),
        )
        write_snapshot(root, SnapshotRecord(snapshot_id=snapshot_id, manifest=manifest))
        new_commit = CommitRecord(
            commit_id=new_commit_id,
            repo_id=repo_id,
            branch=branch,
            snapshot_id=snapshot_id,
            message=message,
            committed_at=committed_at,
            parent_commit_id=current_parent if current_parent else None,
            author=orig_commit.author if orig_commit else "",
        )
        write_commit(root, new_commit)
        active_state["completed"].append(new_commit_id)
        if active_state["remaining"]:
            active_state["remaining"].pop(0)
        save_rebase_state(root, active_state)

        append_reflog(
            root, branch,
            old_id=current_parent,
            new_id=new_commit_id,
            author="user",
            operation=f"rebase: continue — replayed {orig_commit_id[:12] if orig_commit_id else '?'}",
        )

        if not active_state["remaining"]:
            _write_branch_ref(root, branch, new_commit_id)
            clear_rebase_state(root)
            typer.echo(f"✅ Rebase complete. HEAD is now {new_commit_id[:12]}.")
            return

        # More commits to replay.
        clean = _run_replay_loop(root, active_state, repo_id, branch, plugin, domain)
        if clean:
            final_id = active_state["completed"][-1]
            _write_branch_ref(root, branch, final_id)
            clear_rebase_state(root)
            typer.echo(f"✅ Rebase complete. HEAD is now {final_id[:12]}.")
        return

    # New rebase — check not already in progress.
    if active_state is not None:
        typer.echo(
            "❌ Rebase in progress. Use --continue or --abort."
        )
        raise typer.Exit(code=ExitCode.USER_ERROR)

    if upstream is None:
        typer.echo("❌ Provide an upstream branch or commit to rebase onto.", err=True)
        raise typer.Exit(code=ExitCode.USER_ERROR)

    # Resolve HEAD and upstream.
    head_commit_id = get_head_commit_id(root, branch)
    if head_commit_id is None:
        typer.echo("❌ Current branch has no commits.", err=True)
        raise typer.Exit(code=ExitCode.USER_ERROR)

    upstream_id = _resolve_ref_to_id(root, repo_id, branch, upstream)
    if upstream_id is None:
        typer.echo(f"❌ Upstream '{sanitize_display(upstream)}' not found.", err=True)
        raise typer.Exit(code=ExitCode.USER_ERROR)

    # Determine the new base.
    if onto is not None:
        onto_id = _resolve_ref_to_id(root, repo_id, branch, onto)
        if onto_id is None:
            typer.echo(f"❌ --onto '{sanitize_display(onto)}' not found.", err=True)
            raise typer.Exit(code=ExitCode.USER_ERROR)
    else:
        onto_id = upstream_id

    # Find merge base to determine which commits to replay.
    merge_base_id = find_merge_base(root, head_commit_id, upstream_id)
    stop_at = merge_base_id or ""

    if head_commit_id == upstream_id or head_commit_id == onto_id:
        typer.echo("Already up to date.")
        return

    commits_to_replay = collect_commits_to_replay(root, stop_at, head_commit_id)
    if not commits_to_replay:
        typer.echo("Already up to date.")
        return

    typer.echo(
        f"Rebasing {len(commits_to_replay)} commit(s) "
        f"onto {onto_id[:12]} (from {branch})"
    )

    if squash:
        # Replay all commits and produce one final squashed commit.
        current_parent = onto_id
        squash_manifest: dict[str, str] = {}

        # Get onto base snapshot.
        onto_commit = read_commit(root, onto_id)
        if onto_commit:
            onto_snap = read_snapshot(root, onto_commit.snapshot_id)
            if onto_snap:
                squash_manifest = dict(onto_snap.manifest)

        conflict_occurred = False
        for commit in commits_to_replay:
            from muse.domain import SnapshotManifest as _SM

            base_manifest: dict[str, str] = {}
            if commit.parent_commit_id:
                pc = read_commit(root, commit.parent_commit_id)
                if pc:
                    ps = read_snapshot(root, pc.snapshot_id)
                    if ps:
                        base_manifest = ps.manifest

            theirs_snap = read_snapshot(root, commit.snapshot_id)
            theirs_manifest = theirs_snap.manifest if theirs_snap else {}

            result = plugin.merge(
                _SM(files=base_manifest, domain=domain),
                _SM(files=squash_manifest, domain=domain),
                _SM(files=theirs_manifest, domain=domain),
                repo_root=root,
            )
            if not result.is_clean:
                typer.echo(f"❌ Conflict during squash at {commit.commit_id[:12]}:")
                for p in sorted(result.conflicts):
                    typer.echo(f"  CONFLICT: {p}")
                typer.echo("Resolve conflicts and try again. Squash does not support --continue.")
                conflict_occurred = True
                break
            squash_manifest = result.merged["files"]

        if conflict_occurred:
            raise typer.Exit(code=ExitCode.USER_ERROR)

        apply_manifest(root, squash_manifest)
        snapshot_id = compute_snapshot_id(squash_manifest)
        committed_at = datetime.datetime.now(datetime.timezone.utc)
        final_message = squash_message or commits_to_replay[-1].message
        new_commit_id = compute_commit_id(
            parent_ids=[onto_id],
            snapshot_id=snapshot_id,
            message=final_message,
            committed_at_iso=committed_at.isoformat(),
        )
        write_snapshot(root, SnapshotRecord(snapshot_id=snapshot_id, manifest=squash_manifest))
        write_commit(root, CommitRecord(
            commit_id=new_commit_id,
            repo_id=repo_id,
            branch=branch,
            snapshot_id=snapshot_id,
            message=final_message,
            committed_at=committed_at,
            parent_commit_id=onto_id,
        ))
        _write_branch_ref(root, branch, new_commit_id)
        append_reflog(
            root, branch,
            old_id=head_commit_id,
            new_id=new_commit_id,
            author="user",
            operation=f"rebase --squash onto {onto_id[:12]}",
        )
        typer.echo(f"✅ Squash-rebase complete. HEAD is now {new_commit_id[:12]}.")
        return

    # Normal replay loop.
    state = RebaseState(
        original_branch=branch,
        original_head=head_commit_id,
        onto=onto_id,
        remaining=[c.commit_id for c in commits_to_replay],
        completed=[],
        squash=False,
    )
    save_rebase_state(root, state)

    clean = _run_replay_loop(root, state, repo_id, branch, plugin, domain)

    if clean:
        final_id = state["completed"][-1] if state["completed"] else onto_id
        _write_branch_ref(root, branch, final_id)
        clear_rebase_state(root)
        append_reflog(
            root, branch,
            old_id=head_commit_id,
            new_id=final_id,
            author="user",
            operation=f"rebase: finished onto {onto_id[:12]}",
        )
        typer.echo(f"✅ Rebase complete. HEAD is now {final_id[:12]}.")

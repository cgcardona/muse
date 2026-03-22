"""``muse bisect`` — binary search through commit history to find regressions.

``muse bisect`` is Muse's power-tool for regression hunting.  Given a
known-bad commit and a known-good commit it performs a binary search through
the history between them, asking at each midpoint: *"does the bug exist
here?"* until the first bad commit is isolated.

It is fully agent-safe: ``muse bisect run <cmd>`` automates the search by
running an arbitrary command at each step and interpreting the exit code:

    0    → good (bug not present)
    125  → skip (commit untestable)
    else → bad  (bug present)

Subcommands::

    muse bisect start [--bad <ref>] [--good <ref>] …  — begin session
    muse bisect bad [<ref>]                            — mark current/ref as bad
    muse bisect good [<ref>]                           — mark current/ref as good
    muse bisect skip [<ref>]                           — skip untestable commit
    muse bisect run <command>                          — auto-bisect
    muse bisect log                                    — show session log
    muse bisect reset                                  — end session
"""

from __future__ import annotations

import argparse
import json
import logging
import pathlib
import sys


from muse.core.bisect import (
    BisectResult,
    get_bisect_log,
    is_bisect_active,
    mark_bad,
    mark_good,
    reset_bisect,
    run_bisect_command,
    skip_commit,
    start_bisect,
)
from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.store import get_head_commit_id, read_current_branch, resolve_commit_ref
from muse.core.validation import sanitize_display

logger = logging.getLogger(__name__)


def _read_branch(root: pathlib.Path) -> str:
    return read_current_branch(root)


def _read_repo_id(root: pathlib.Path) -> str:
    return str(json.loads((root / ".muse" / "repo.json").read_text())["repo_id"])


def _resolve_ref(root: pathlib.Path, ref: str | None) -> str:
    """Resolve ref to full commit_id; fall back to HEAD if ref is None."""
    branch = _read_branch(root)
    repo_id = _read_repo_id(root)
    if ref is None:
        commit_id = get_head_commit_id(root, branch)
        if not commit_id:
            print("❌ No commits on current branch.")
            raise SystemExit(ExitCode.USER_ERROR)
        return commit_id
    commit = resolve_commit_ref(root, repo_id, branch, ref)
    if commit is None:
        print(f"❌ Ref '{sanitize_display(ref)}' not found.")
        raise SystemExit(ExitCode.USER_ERROR)
    return commit.commit_id


def _print_result(result: BisectResult) -> None:
    if result.done:
        print(f"\n✅ First bad commit found: {sanitize_display(result.first_bad or '')}")
        print("   Run 'muse bisect reset' to end the session.")
    else:
        print(
            f"Next to test: {sanitize_display(result.next_to_test or '')}  "
            f"({result.remaining_count} remaining, ~{result.steps_remaining} step(s) left)"
        )


def run_bisect_start(args: argparse.Namespace) -> None:
    """Start a bisect session.

    Mark the first bad and last good commits.  Muse will immediately suggest
    the midpoint commit to test.

    Examples::

        muse bisect start --bad HEAD --good v1.0.0
        muse bisect start --bad a1b2c3 --good d4e5f6 --good g7h8i9
    """
    bad: str | None = args.bad
    good: list[str] | None = args.good

    root = require_repo()
    if is_bisect_active(root):
        print("⚠️  A bisect session is already active. Run 'muse bisect reset' first.")
        raise SystemExit(ExitCode.USER_ERROR)

    bad_id = _resolve_ref(root, bad)
    good_ids = [_resolve_ref(root, g) for g in (good or [])]
    if not good_ids:
        print("❌ Provide at least one --good commit: muse bisect start --bad HEAD --good <ref>")
        raise SystemExit(ExitCode.USER_ERROR)

    branch = _read_branch(root)
    result = start_bisect(root, bad_id, good_ids, branch=branch)
    print(f"Bisect session started.  bad={bad_id[:12]}  good=[{', '.join(g[:12] for g in good_ids)}]")
    _print_result(result)


def run_bisect_bad(args: argparse.Namespace) -> None:
    """Mark a commit as bad (bug present)."""
    ref: str | None = args.ref

    root = require_repo()
    if not is_bisect_active(root):
        print("❌ No bisect session in progress. Run 'muse bisect start' first.")
        raise SystemExit(ExitCode.USER_ERROR)
    commit_id = _resolve_ref(root, ref)
    result = mark_bad(root, commit_id)
    print(f"Marked {commit_id[:12]} as bad.")
    _print_result(result)


def run_bisect_good(args: argparse.Namespace) -> None:
    """Mark a commit as good (bug absent)."""
    ref: str | None = args.ref

    root = require_repo()
    if not is_bisect_active(root):
        print("❌ No bisect session in progress. Run 'muse bisect start' first.")
        raise SystemExit(ExitCode.USER_ERROR)
    commit_id = _resolve_ref(root, ref)
    result = mark_good(root, commit_id)
    print(f"Marked {commit_id[:12]} as good.")
    _print_result(result)


def run_bisect_skip(args: argparse.Namespace) -> None:
    """Skip a commit that cannot be tested (exit code 125 in auto mode)."""
    ref: str | None = args.ref

    root = require_repo()
    if not is_bisect_active(root):
        print("❌ No bisect session in progress. Run 'muse bisect start' first.")
        raise SystemExit(ExitCode.USER_ERROR)
    commit_id = _resolve_ref(root, ref)
    result = skip_commit(root, commit_id)
    print(f"Skipped {commit_id[:12]}.")
    _print_result(result)


def run_bisect_run(args: argparse.Namespace) -> None:
    """Automatically bisect by running a command at each step.

    The command exit code determines the verdict:

    \\b
        0    → good
        125  → skip
        else → bad

    The command is run in the repository root.  Muse will automatically apply
    verdicts and advance until the first bad commit is found.

    Example::

        muse bisect run "pytest tests/test_regression.py -x -q"
    """
    command: str = args.command

    root = require_repo()
    if not is_bisect_active(root):
        print("❌ No bisect session in progress. Run 'muse bisect start' first.")
        raise SystemExit(ExitCode.USER_ERROR)

    from muse.core.bisect import _load_state

    while True:
        state = _load_state(root)
        if state is None:
            print("❌ Bisect state lost.")
            raise SystemExit(ExitCode.INTERNAL_ERROR)
        remaining = state.get("remaining", [])
        if not remaining:
            print("✅ Bisect complete. Run 'muse bisect reset' to end.")
            return
        current = remaining[len(remaining) // 2]
        print(f"  → Testing {current[:12]} …")
        result = run_bisect_command(root, command, current)
        print(f"     verdict: {result.verdict}")
        if result.done:
            print(f"\n✅ First bad commit: {result.first_bad}")
            return


def run_bisect_log(args: argparse.Namespace) -> None:
    """Show the full bisect session log."""
    root = require_repo()
    entries = get_bisect_log(root)
    if not entries:
        print("No bisect log. Start a session with 'muse bisect start'.")
        return
    print("Bisect log:")
    for entry in entries:
        print(f"  {sanitize_display(entry)}")


def run_bisect_reset(args: argparse.Namespace) -> None:
    """End the bisect session and clean up state."""
    root = require_repo()
    reset_bisect(root)
    print("Bisect session reset.")


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the bisect subcommand."""
    parser = subparsers.add_parser(
        "bisect",
        help="Binary search through commit history to find regressions.",
        description=__doc__,
    )
    subs = parser.add_subparsers(dest="subcommand", metavar="SUBCOMMAND")
    subs.required = True

    # start
    start_p = subs.add_parser("start", help="Begin a bisect session.")
    start_p.add_argument(
        "--bad", default=None, metavar="REF",
        help="Known-bad commit (default: HEAD).",
    )
    start_p.add_argument(
        "--good", nargs="*", default=None, metavar="REF",
        help="Known-good commit(s).  Repeat for multiple: --good v1.0 --good v0.9.",
    )
    start_p.set_defaults(func=run_bisect_start)

    # bad
    bad_p = subs.add_parser("bad", help="Mark a commit as bad (bug present).")
    bad_p.add_argument(
        "ref", nargs="?", default=None, metavar="REF",
        help="Commit to mark bad (default: HEAD).",
    )
    bad_p.set_defaults(func=run_bisect_bad)

    # good
    good_p = subs.add_parser("good", help="Mark a commit as good (bug absent).")
    good_p.add_argument(
        "ref", nargs="?", default=None, metavar="REF",
        help="Commit to mark good (default: HEAD).",
    )
    good_p.set_defaults(func=run_bisect_good)

    # skip
    skip_p = subs.add_parser("skip", help="Skip an untestable commit.")
    skip_p.add_argument(
        "ref", nargs="?", default=None, metavar="REF",
        help="Commit to skip (default: HEAD).",
    )
    skip_p.set_defaults(func=run_bisect_skip)

    # run
    run_p = subs.add_parser("run", help="Automatically bisect by running a command.")
    run_p.add_argument(
        "command", metavar="COMMAND",
        help="Shell command to run at each step (exit 0=good, 125=skip, else=bad).",
    )
    run_p.set_defaults(func=run_bisect_run)

    # log
    log_p = subs.add_parser("log", help="Show the bisect session log.")
    log_p.set_defaults(func=run_bisect_log)

    # reset
    reset_p = subs.add_parser("reset", help="End the bisect session and clean up state.")
    reset_p.set_defaults(func=run_bisect_reset)

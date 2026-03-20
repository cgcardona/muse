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

import logging
from typing import Annotated

import typer

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
from muse.core.store import get_head_commit_id, resolve_commit_ref
from muse.core.validation import sanitize_display

logger = logging.getLogger(__name__)
app = typer.Typer(
    help="Binary search through commit history to find the first bad commit.",
    no_args_is_help=True,
)


import json
import pathlib


def _read_branch(root: pathlib.Path) -> str:
    head = (root / ".muse" / "HEAD").read_text().strip()
    return head.removeprefix("refs/heads/").strip()


def _read_repo_id(root: pathlib.Path) -> str:
    return str(json.loads((root / ".muse" / "repo.json").read_text())["repo_id"])


def _resolve_ref(root: pathlib.Path, ref: str | None) -> str:
    """Resolve ref to full commit_id; fall back to HEAD if ref is None."""
    branch = _read_branch(root)
    repo_id = _read_repo_id(root)
    if ref is None:
        commit_id = get_head_commit_id(root, branch)
        if not commit_id:
            typer.echo("❌ No commits on current branch.")
            raise typer.Exit(code=ExitCode.USER_ERROR)
        return commit_id
    commit = resolve_commit_ref(root, repo_id, branch, ref)
    if commit is None:
        typer.echo(f"❌ Ref '{sanitize_display(ref)}' not found.")
        raise typer.Exit(code=ExitCode.USER_ERROR)
    return commit.commit_id


def _print_result(result: BisectResult) -> None:
    if result.done:
        typer.echo(f"\n✅ First bad commit found: {result.first_bad}")
        typer.echo("   Run 'muse bisect reset' to end the session.")
    else:
        typer.echo(
            f"Next to test: {result.next_to_test}  "
            f"({result.remaining_count} remaining, ~{result.steps_remaining} step(s) left)"
        )


@app.command("start")
def bisect_start(
    bad: Annotated[str | None, typer.Option("--bad", help="Known-bad commit (default: HEAD).")] = None,
    good: Annotated[list[str] | None, typer.Option("--good", help="Known-good commit (repeatable).")] = None,
) -> None:
    """Start a bisect session.

    Mark the first bad and last good commits.  Muse will immediately suggest
    the midpoint commit to test.

    Examples::

        muse bisect start --bad HEAD --good v1.0.0
        muse bisect start --bad a1b2c3 --good d4e5f6 --good g7h8i9
    """
    root = require_repo()
    if is_bisect_active(root):
        typer.echo("⚠️  A bisect session is already active. Run 'muse bisect reset' first.")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    bad_id = _resolve_ref(root, bad)
    good_ids = [_resolve_ref(root, g) for g in (good or [])]
    if not good_ids:
        typer.echo("❌ Provide at least one --good commit: muse bisect start --bad HEAD --good <ref>")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    branch = _read_branch(root)
    result = start_bisect(root, bad_id, good_ids, branch=branch)
    typer.echo(f"Bisect session started.  bad={bad_id[:12]}  good=[{', '.join(g[:12] for g in good_ids)}]")
    _print_result(result)


@app.command("bad")
def bisect_bad(
    ref: Annotated[str | None, typer.Argument(help="Commit to mark bad (default: HEAD).")] = None,
) -> None:
    """Mark a commit as bad (bug present)."""
    root = require_repo()
    if not is_bisect_active(root):
        typer.echo("❌ No bisect session in progress. Run 'muse bisect start' first.")
        raise typer.Exit(code=ExitCode.USER_ERROR)
    commit_id = _resolve_ref(root, ref)
    result = mark_bad(root, commit_id)
    typer.echo(f"Marked {commit_id[:12]} as bad.")
    _print_result(result)


@app.command("good")
def bisect_good(
    ref: Annotated[str | None, typer.Argument(help="Commit to mark good (default: HEAD).")] = None,
) -> None:
    """Mark a commit as good (bug absent)."""
    root = require_repo()
    if not is_bisect_active(root):
        typer.echo("❌ No bisect session in progress. Run 'muse bisect start' first.")
        raise typer.Exit(code=ExitCode.USER_ERROR)
    commit_id = _resolve_ref(root, ref)
    result = mark_good(root, commit_id)
    typer.echo(f"Marked {commit_id[:12]} as good.")
    _print_result(result)


@app.command("skip")
def bisect_skip(
    ref: Annotated[str | None, typer.Argument(help="Commit to skip (default: HEAD).")] = None,
) -> None:
    """Skip a commit that cannot be tested (exit code 125 in auto mode)."""
    root = require_repo()
    if not is_bisect_active(root):
        typer.echo("❌ No bisect session in progress. Run 'muse bisect start' first.")
        raise typer.Exit(code=ExitCode.USER_ERROR)
    commit_id = _resolve_ref(root, ref)
    result = skip_commit(root, commit_id)
    typer.echo(f"Skipped {commit_id[:12]}.")
    _print_result(result)


@app.command("run")
def bisect_run(
    command: Annotated[str, typer.Argument(help="Shell command to run at each step.")],
) -> None:
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
    root = require_repo()
    if not is_bisect_active(root):
        typer.echo("❌ No bisect session in progress. Run 'muse bisect start' first.")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    from muse.core.bisect import _load_state

    while True:
        state = _load_state(root)
        if state is None:
            typer.echo("❌ Bisect state lost.")
            raise typer.Exit(code=ExitCode.INTERNAL_ERROR)
        remaining = state.get("remaining", [])
        if not remaining:
            typer.echo("✅ Bisect complete. Run 'muse bisect reset' to end.")
            return
        current = remaining[len(remaining) // 2]
        typer.echo(f"  → Testing {current[:12]} …")
        result = run_bisect_command(root, command, current)
        typer.echo(f"     verdict: {result.verdict}")
        if result.done:
            typer.echo(f"\n✅ First bad commit: {result.first_bad}")
            return


@app.command("log")
def bisect_log() -> None:
    """Show the full bisect session log."""
    root = require_repo()
    entries = get_bisect_log(root)
    if not entries:
        typer.echo("No bisect log. Start a session with 'muse bisect start'.")
        return
    typer.echo("Bisect log:")
    for entry in entries:
        typer.echo(f"  {entry}")


@app.command("reset")
def bisect_reset() -> None:
    """End the bisect session and clean up state."""
    root = require_repo()
    reset_bisect(root)
    typer.echo("Bisect session reset.")

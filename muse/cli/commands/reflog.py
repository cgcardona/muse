"""``muse reflog`` — inspect the history of HEAD and branch movements.

The reflog is a chronological journal of every time a ref moved: commits,
checkouts, merges, resets, cherry-picks, stash pops.  It is your safety net
when you need to undo an operation that moved HEAD.

Usage::

    muse reflog                     # HEAD reflog, last 20 entries
    muse reflog --branch dev        # dev branch reflog
    muse reflog --limit 100         # show more entries
    muse reflog --all               # list all refs that have a reflog

Each row shows::

    @{0}  <new_sha12>  (<old_sha12>) <when>    <operation>

The ``@{N}`` syntax mirrors Git so scripts that already understand Git reflogs
need no translation.
"""

from __future__ import annotations

import json
import logging
from typing import Annotated

import typer

from muse.core.reflog import ReflogEntry, list_reflog_refs, read_reflog
from muse.core.repo import require_repo
from muse.core.validation import sanitize_display, validate_branch_name

logger = logging.getLogger(__name__)
app = typer.Typer(help="Show the history of HEAD and branch-ref movements.")


def _fmt_entry(idx: int, entry: ReflogEntry, short: int = 12) -> str:
    new_short = entry.new_id[:short]
    old_short = entry.old_id[:short] if entry.old_id != "0" * 64 else "initial"
    when = entry.timestamp.strftime("%Y-%m-%d %H:%M:%S UTC")
    # Sanitize operation — it is a free-form string stored in the reflog.
    safe_op = sanitize_display(entry.operation)
    return f"@{{{idx}:<3}} {new_short}  ({old_short})  {when}  {safe_op}"


@app.callback(invoke_without_command=True)
def reflog(
    branch: Annotated[
        str | None,
        typer.Option("--branch", "-b", help="Show reflog for a specific branch (default: HEAD)."),
    ] = None,
    limit: Annotated[
        int,
        typer.Option("--limit", "-n", help="Maximum entries to display.", min=1),
    ] = 20,
    all_refs: Annotated[
        bool,
        typer.Option("--all", help="List every ref that has a reflog."),
    ] = False,
    fmt: Annotated[
        str,
        typer.Option("--format", "-f", help="Output format: text or json."),
    ] = "text",
) -> None:
    """Show the history of HEAD and branch-ref movements.

    Every time HEAD or a branch ref moves — commit, checkout, merge, reset,
    cherry-pick, stash pop — Muse appends an entry to the reflog.  Use this
    command to find lost commits and undo accidental resets.  Agents should
    pass ``--format json`` to receive a JSON array of entries with
    ``new_id``, ``old_id``, ``timestamp``, and ``operation``.

    Examples::

        muse reflog                       # HEAD history
        muse reflog --branch feat/audio   # branch history
        muse reflog --all                 # all tracked refs
        muse reflog --format json         # machine-readable
    """
    if fmt not in ("text", "json"):
        typer.echo(f"❌ Unknown --format '{sanitize_display(fmt)}'. Choose text or json.", err=True)
        raise typer.Exit(code=1)

    repo_root = require_repo()

    if all_refs:
        refs = list_reflog_refs(repo_root)
        if fmt == "json":
            typer.echo(json.dumps([f"refs/heads/{r}" for r in refs]))
        else:
            if not refs:
                typer.echo("No reflog entries found.")
                return
            typer.echo("Refs with reflog entries:")
            for ref in refs:
                typer.echo(f"  refs/heads/{ref}")
        return

    if branch is not None:
        try:
            validate_branch_name(branch)
        except ValueError as exc:
            typer.echo(f"❌ Invalid branch name: {exc}", err=True)
            raise typer.Exit(code=1)

    entries = read_reflog(repo_root, branch=branch, limit=limit)

    if fmt == "json":
        typer.echo(json.dumps([{
            "index": idx,
            "new_id": e.new_id,
            "old_id": e.old_id,
            "timestamp": e.timestamp.isoformat(),
            "operation": e.operation,
            "author": e.author,
        } for idx, e in enumerate(entries)]))
        return

    label = f"refs/heads/{sanitize_display(branch)}" if branch else "HEAD"
    if not entries:
        typer.echo(f"No reflog entries for {label}.")
        return

    typer.echo(f"Reflog for {label}  (newest first)\n")
    for idx, entry in enumerate(entries):
        typer.echo(_fmt_entry(idx, entry))

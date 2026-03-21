"""``muse blame`` (core VCS) — line-level attribution for any text file.

Shows which commit last modified each line of a tracked text file — the
universal, domain-agnostic counterpart to ``muse code blame`` (symbol-level)
and ``muse midi note-blame`` (bar-level).

Usage::

    muse blame README.md
    muse blame --ref v1.0.0 docs/design.md
    muse blame --porcelain state/config.toml    # machine-readable output

Output format (default)::

    <sha12>  (<author>  <date>)   1  line content here

The ``--porcelain`` flag emits one JSON object per line for pipeline use.

Note: this command lives as ``muse blame`` at the Tier 2 porcelain level.
Domain-specific blame (symbols, notes) lives under ``muse code`` and
``muse midi`` respectively.
"""

from __future__ import annotations

import json
import logging
import pathlib
from typing import Annotated

import typer

from muse.core.blame import blame_file
from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.store import get_head_commit_id, read_current_branch, resolve_commit_ref
from muse.core.validation import sanitize_display

logger = logging.getLogger(__name__)
app = typer.Typer(help="Show which commit last modified each line of a text file.")


def _read_branch(root: pathlib.Path) -> str:
    return read_current_branch(root)


def _read_repo_id(root: pathlib.Path) -> str:
    return str(__import__("json").loads((root / ".muse" / "repo.json").read_text())["repo_id"])


@app.callback(invoke_without_command=True)
def blame(
    file: Annotated[
        str,
        typer.Argument(help="File path relative to state/ (e.g. README.md)."),
    ],
    ref: Annotated[
        str | None,
        typer.Option("--ref", "-r", help="Commit or branch to blame from (default: HEAD)."),
    ] = None,
    porcelain: Annotated[
        bool,
        typer.Option("--porcelain", "-p", help="Emit JSON objects instead of human-readable output."),
    ] = False,
    short: Annotated[
        int,
        typer.Option("--short", help="Length of commit SHA prefix to display."),
    ] = 12,
) -> None:
    """Show which commit last modified each line of a text file.

    Walks the commit history backwards and attributes each line to the
    oldest commit that introduced or last changed it.

    Examples::

        muse blame README.md
        muse blame --ref v1.0.0 src/main.py
        muse blame --porcelain config.toml | jq '.commit_id'
    """
    root = require_repo()
    branch = _read_branch(root)
    repo_id = _read_repo_id(root)

    if ref is None:
        commit_id = get_head_commit_id(root, branch)
        if not commit_id:
            typer.echo("❌ No commits yet on this branch.")
            raise typer.Exit(code=ExitCode.USER_ERROR)
    else:
        commit = resolve_commit_ref(root, repo_id, branch, ref)
        if commit is None:
            typer.echo(f"❌ Ref '{sanitize_display(ref)}' not found.")
            raise typer.Exit(code=ExitCode.USER_ERROR)
        commit_id = commit.commit_id

    lines = blame_file(root, file, commit_id)
    if lines is None:
        typer.echo(f"❌ File '{sanitize_display(file)}' not found at {commit_id[:short]}.")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    if not lines:
        typer.echo(f"(empty file '{sanitize_display(file)}')")
        return

    if porcelain:
        for bl in lines:
            typer.echo(json.dumps({
                "lineno": bl.lineno,
                "commit_id": bl.commit_id,
                "author": bl.author,
                "committed_at": bl.committed_at,
                "message": bl.message,
                "content": bl.content,
            }))
        return

    # Human-readable output: align columns.
    sha_w = short
    author_w = min(20, max((len(bl.author) for bl in lines), default=0))
    date_w = 10
    lineno_w = len(str(len(lines)))

    for bl in lines:
        sha = bl.commit_id[:sha_w]
        author = bl.author[:author_w].ljust(author_w)
        date = bl.committed_at[:date_w]
        lineno = str(bl.lineno).rjust(lineno_w)
        content = sanitize_display(bl.content)
        typer.echo(f"{sha}  ({author}  {date})  {lineno}  {content}")

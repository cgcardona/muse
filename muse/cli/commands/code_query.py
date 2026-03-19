"""``muse code-query`` — predicate query over code commit history.

Search the commit graph for code changes matching a structured predicate::

    muse code-query "symbol == 'my_function' and change == 'added'"
    muse code-query "language == 'Python' and author == 'agent-x'"
    muse code-query "agent_id == 'claude' and sem_ver_bump == 'major'"
    muse code-query "file == 'src/core.py'"
    muse code-query "change == 'removed' and kind == 'class'"
    muse code-query "model_id contains 'claude'"

Fields
------

``symbol``       Qualified symbol name (e.g. ``"MyClass.method"``).
``file``         Workspace-relative file path.
``language``     Language name (``"Python"``, ``"TypeScript"``…).
``kind``         Symbol kind (``"function"``, ``"class"``, ``"method"``…).
``change``       ``"added"``, ``"removed"``, or ``"modified"``.
``author``       Commit author string.
``agent_id``     Agent identity from commit provenance.
``model_id``     Model ID from commit provenance.
``toolchain_id`` Toolchain string from commit provenance.
``sem_ver_bump`` ``"none"``, ``"patch"``, ``"minor"``, or ``"major"``.
``branch``       Branch name.

Operators: ``==``, ``!=``, ``contains``, ``startswith``

Usage::

    muse code-query QUERY
    muse code-query QUERY --branch dev --max 100
    muse code-query QUERY --json
"""

from __future__ import annotations

import json
import logging
import pathlib
import sys

import typer

from muse.core.query_engine import format_matches, walk_history
from muse.core.repo import require_repo
from muse.plugins.code._code_query import build_evaluator

logger = logging.getLogger(__name__)

app = typer.Typer()


def _current_branch(root: pathlib.Path) -> str:
    head_ref = (root / ".muse" / "HEAD").read_text().strip()
    return head_ref.removeprefix("refs/heads/").strip()


@app.callback(invoke_without_command=True)
def code_query(
    ctx: typer.Context,
    query: str = typer.Argument(..., help="Query expression (see muse code-query --help)."),
    branch: str | None = typer.Option(None, "--branch", help="Branch to search (default: HEAD branch)."),
    max_commits: int = typer.Option(200, "--max", help="Maximum commits to inspect."),
    output_json: bool = typer.Option(False, "--json", help="Emit JSON array of matches."),
) -> None:
    """Query the code commit history using a structured predicate.

    Walks up to *max* commits from HEAD on the specified branch and returns
    all commits (and symbol-level changes) matching the predicate.

    Examples::

        muse code-query "symbol == 'parse_query' and change == 'added'"
        muse code-query "agent_id contains 'claude' and sem_ver_bump == 'major'"
        muse code-query "file == 'muse/core/store.py'"
    """
    root = require_repo()

    try:
        evaluator = build_evaluator(query)
    except ValueError as exc:
        typer.echo(f"❌ Query parse error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    resolved_branch = branch or _current_branch(root)
    matches = walk_history(root, resolved_branch, evaluator, max_commits=max_commits)

    if output_json:
        typer.echo(json.dumps(list(matches)))
        return

    typer.echo(format_matches(matches))

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

import argparse
import json
import logging
import pathlib
import sys

from muse.core.store import read_current_branch
from muse.core.query_engine import format_matches, walk_history
from muse.core.repo import require_repo
from muse.plugins.code._code_query import build_evaluator

logger = logging.getLogger(__name__)


def _current_branch(root: pathlib.Path) -> str:
    return read_current_branch(root)


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the code-query subcommand."""
    parser = subparsers.add_parser(
        "code-query",
        help="Query the code commit history using a structured predicate.",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "query",
        help="Query expression (see muse code-query --help).",
    )
    parser.add_argument(
        "--branch", default=None,
        help="Branch to search (default: HEAD branch).",
    )
    parser.add_argument(
        "--max", type=int, default=200, dest="max_commits",
        help="Maximum commits to inspect.",
    )
    parser.add_argument(
        "--json", action="store_true", dest="as_json",
        help="Emit JSON array of matches.",
    )
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> None:
    """Query the code commit history using a structured predicate.

    Walks up to *max* commits from HEAD on the specified branch and returns
    all commits (and symbol-level changes) matching the predicate.

    Examples::

        muse code-query "symbol == 'parse_query' and change == 'added'"
        muse code-query "agent_id contains 'claude' and sem_ver_bump == 'major'"
        muse code-query "file == 'muse/core/store.py'"
    """
    query: str = args.query
    branch: str | None = args.branch
    max_commits: int = args.max_commits
    as_json: bool = args.as_json

    root = require_repo()

    try:
        evaluator = build_evaluator(query)
    except ValueError as exc:
        print(f"❌ Query parse error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    resolved_branch = branch or _current_branch(root)
    matches = walk_history(root, resolved_branch, evaluator, max_commits=max_commits)

    if as_json:
        print(json.dumps(list(matches)))
        return

    print(format_matches(matches))

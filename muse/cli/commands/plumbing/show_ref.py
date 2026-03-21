"""muse plumbing show-ref — list all refs known to this repository.

Enumerates every branch ref stored under ``.muse/refs/heads/`` and reports
the commit ID each one points to.  Optionally filters by a glob pattern,
reports only the HEAD ref, or verifies that a specific ref exists.

Output (JSON, default)::

    {
      "refs": [
        {"ref": "refs/heads/dev",  "commit_id": "<sha256>"},
        {"ref": "refs/heads/main", "commit_id": "<sha256>"}
      ],
      "head": {
        "ref":       "refs/heads/main",
        "branch":    "main",
        "commit_id": "<sha256>"
      },
      "count": 2
    }

Text output (``--format text``)::

    <sha256>  refs/heads/dev
    <sha256>  refs/heads/main
    * refs/heads/main  (HEAD)

Plumbing contract
-----------------

- Exit 0: refs enumerated successfully (list may be empty).
- Exit 1: bad ``--format`` value; verify mode and ref does not exist.
- Exit 3: I/O error reading refs directory.
"""

from __future__ import annotations

import fnmatch
import json
import logging
import pathlib
from typing import TypedDict

import typer

from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.store import (
    get_head_commit_id,
    read_current_branch,
)

logger = logging.getLogger(__name__)

app = typer.Typer()

_FORMAT_CHOICES = ("json", "text")


class _RefEntry(TypedDict):
    ref: str
    commit_id: str


class _ShowRefResult(TypedDict):
    refs: list[_RefEntry]
    head: _HeadInfo | None
    count: int


class _HeadInfo(TypedDict):
    ref: str
    branch: str
    commit_id: str


def _list_branch_refs(root: pathlib.Path) -> list[_RefEntry]:
    """Return every branch ref under ``.muse/refs/heads/``, sorted by name."""
    heads_dir = root / ".muse" / "refs" / "heads"
    if not heads_dir.exists():
        return []

    refs: list[_RefEntry] = []
    for child in sorted(heads_dir.iterdir()):
        if not child.is_file():
            continue
        branch = child.name
        commit_id = child.read_text(encoding="utf-8").strip()
        if commit_id:
            refs.append({"ref": f"refs/heads/{branch}", "commit_id": commit_id})
    return refs


def _head_info(root: pathlib.Path) -> _HeadInfo | None:
    """Return metadata about what HEAD currently points to, or ``None``."""
    try:
        branch = read_current_branch(root)
    except Exception:
        return None
    commit_id = get_head_commit_id(root, branch)
    if commit_id is None:
        return None
    return {
        "ref": f"refs/heads/{branch}",
        "branch": branch,
        "commit_id": commit_id,
    }


@app.callback(invoke_without_command=True)
def show_ref(
    ctx: typer.Context,
    pattern: str = typer.Option(
        "",
        "--pattern",
        "-p",
        help="fnmatch glob filter applied to the full ref name (e.g. 'refs/heads/feat/*').",
    ),
    head_only: bool = typer.Option(
        False,
        "--head",
        "-H",
        help="Print only the HEAD ref and its commit ID.",
    ),
    verify: str = typer.Option(
        "",
        "--verify",
        "-v",
        help="Exit 0 if the given ref exists, exit 1 otherwise (no other output).",
    ),
    fmt: str = typer.Option(
        "json", "--format", "-f", help="Output format: json or text."
    ),
) -> None:
    """List all refs known to this repository.

    Reads every branch pointer from ``.muse/refs/heads/`` and reports their
    commit IDs.  The output is sorted lexicographically by ref name.

    Use ``--pattern`` to restrict output to refs matching a given glob
    (e.g. ``'refs/heads/feat/*'``).

    Use ``--verify`` to check whether a specific ref exists — exits 0 on
    success, exits 1 if absent, and produces no other output.  Designed for
    use in shell conditionals:

    .. code-block:: sh

        muse plumbing show-ref --verify refs/heads/my-branch && echo exists

    Use ``--head`` to query only the currently checked-out branch.
    """
    if fmt not in _FORMAT_CHOICES:
        typer.echo(
            json.dumps(
                {"error": f"Unknown format {fmt!r}. Valid: {', '.join(_FORMAT_CHOICES)}"}
            )
        )
        raise typer.Exit(code=ExitCode.USER_ERROR)

    root = require_repo()

    # --verify mode: silent existence check, no normal output.
    if verify:
        try:
            all_refs = _list_branch_refs(root)
        except Exception as exc:
            logger.debug("show-ref I/O error: %s", exc)
            raise typer.Exit(code=ExitCode.INTERNAL_ERROR)
        exists = any(r["ref"] == verify for r in all_refs)
        raise typer.Exit(code=0 if exists else ExitCode.USER_ERROR)

    # --head mode: only HEAD.
    if head_only:
        info = _head_info(root)
        if info is None:
            if fmt == "text":
                typer.echo("(no HEAD commit)")
            else:
                typer.echo(json.dumps({"head": None}))
            return

        if fmt == "text":
            typer.echo(f"{info['commit_id']}  {info['ref']}  (HEAD)")
        else:
            typer.echo(json.dumps({"head": dict(info)}))
        return

    # Normal mode: all refs, optionally filtered.
    try:
        refs = _list_branch_refs(root)
    except Exception as exc:
        logger.debug("show-ref I/O error: %s", exc)
        typer.echo(json.dumps({"error": str(exc)}))
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)

    if pattern:
        refs = [r for r in refs if fnmatch.fnmatch(r["ref"], pattern)]

    head = _head_info(root)

    if fmt == "text":
        head_ref = head["ref"] if head else None
        for r in refs:
            marker = "* " if r["ref"] == head_ref else "  "
            suffix = "  (HEAD)" if r["ref"] == head_ref else ""
            typer.echo(f"{r['commit_id']}  {marker}{r['ref']}{suffix}")
        return

    result: _ShowRefResult = {
        "refs": refs,
        "head": head,
        "count": len(refs),
    }
    typer.echo(json.dumps(result))

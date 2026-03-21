"""``muse describe`` — label a commit by its nearest tag and hop distance.

Walks backward from a commit (default: HEAD) through the ancestry graph and
finds the nearest tag.  The output is ``<tag>~N`` where N is the number of
hops from the tag to the commit.  N=0 means the commit is exactly on the tag
and the ``~0`` suffix is omitted (bare tag name).

This is the porcelain equivalent of ``git describe`` — useful for generating
human-readable release labels in CI, changelogs, and agent pipelines.

Usage::

    muse describe                      # describe HEAD
    muse describe --ref feat/audio     # describe the tip of a branch
    muse describe --long               # always show distance + SHA
    muse describe --format json        # machine-readable output

Exit codes::

    0 — description produced
    1 — ref not found, or no tags exist in the repository
"""

from __future__ import annotations

import json
import logging
import pathlib
from typing import Annotated

import typer

from muse.core.describe import describe_commit
from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.store import get_head_commit_id, read_current_branch, resolve_commit_ref
from muse.core.validation import sanitize_display

logger = logging.getLogger(__name__)

app = typer.Typer(help="Label a commit by its nearest tag and hop distance.")


def _read_repo_id(root: pathlib.Path) -> str:
    return str(json.loads((root / ".muse" / "repo.json").read_text(encoding="utf-8"))["repo_id"])


@app.callback(invoke_without_command=True)
def describe(
    ref: Annotated[
        str | None,
        typer.Option("--ref", "-r", help="Branch name, commit SHA, or HEAD (default: HEAD)."),
    ] = None,
    long_format: Annotated[
        bool,
        typer.Option("--long", "-l", help="Always show <tag>-<distance>-g<sha> format."),
    ] = False,
    require_tag: Annotated[
        bool,
        typer.Option("--require-tag", "-t", help="Exit 1 if no tag is found in the ancestry."),
    ] = False,
    fmt: Annotated[
        str,
        typer.Option("--format", "-f", help="Output format: json or text."),
    ] = "text",
) -> None:
    """Label a commit by its nearest tag and hop distance.

    Walks backward from the commit's ancestry until it finds the nearest tag.
    The result is ``<tag>~N`` for N hops, or just ``<tag>`` when N=0.  Falls
    back to the 12-character short SHA when no tag is reachable.

    Examples::

        muse describe                       # → v1.0.0~3-gabc123456789
        muse describe --ref v1.0.0          # → v1.0.0  (on the tag itself)
        muse describe --long                # → v1.0.0-0-gabc123456789
        muse describe --require-tag         # → exit 1 if no tags exist
        muse describe --format json         # machine-readable
    """
    if fmt not in {"json", "text"}:
        typer.echo(f"❌ Unknown --format '{sanitize_display(fmt)}'. Choose json or text.", err=True)
        raise typer.Exit(code=ExitCode.USER_ERROR)

    root = require_repo()
    repo_id = _read_repo_id(root)
    branch = read_current_branch(root)

    if ref is None:
        commit_id = get_head_commit_id(root, branch)
        if commit_id is None:
            typer.echo("❌ No commits on current branch.", err=True)
            raise typer.Exit(code=ExitCode.USER_ERROR)
    else:
        commit_rec = resolve_commit_ref(root, repo_id, branch, ref)
        if commit_rec is None:
            typer.echo(f"❌ Ref '{sanitize_display(ref)}' not found.", err=True)
            raise typer.Exit(code=ExitCode.USER_ERROR)
        commit_id = commit_rec.commit_id

    result = describe_commit(root, repo_id, commit_id, long_format=long_format)

    if require_tag and result["tag"] is None:
        typer.echo(
            f"❌ No tags found in the ancestry of {commit_id[:12]}.", err=True
        )
        raise typer.Exit(code=ExitCode.USER_ERROR)

    if fmt == "json":
        typer.echo(json.dumps({
            "commit_id": result["commit_id"],
            "tag": result["tag"],
            "distance": result["distance"],
            "short_sha": result["short_sha"],
            "name": result["name"],
        }))
    else:
        typer.echo(result["name"])

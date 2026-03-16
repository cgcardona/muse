"""muse tag — attach and query semantic tags on commits.

Usage::

    muse tag add emotion:joyful <commit>    — tag a commit
    muse tag list                           — list all tags in the repo
    muse tag list <commit>                  — list tags on a specific commit
    muse tag remove <tag> <commit>          — remove a tag

Tag conventions::

    emotion:*     — emotional character (emotion:melancholic, emotion:tense)
    section:*     — song section (section:verse, section:chorus)
    stage:*       — production stage (stage:rough-mix, stage:master)
    key:*         — musical key (key:Am, key:Eb)
    tempo:*       — tempo annotation (tempo:120bpm)
    ref:*         — reference track (ref:beatles)
"""
from __future__ import annotations

import json
import logging
import pathlib
import uuid
from typing import Optional

import typer

from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.store import (
    TagRecord,
    get_all_tags,
    get_tags_for_commit,
    resolve_commit_ref,
    write_tag,
)

logger = logging.getLogger(__name__)

app = typer.Typer()
add_app = typer.Typer()
list_app = typer.Typer()
remove_app = typer.Typer()

app.add_typer(add_app, name="add", help="Attach a tag to a commit.")
app.add_typer(list_app, name="list", help="List tags.")
app.add_typer(remove_app, name="remove", help="Remove a tag from a commit.")


def _read_branch(root: pathlib.Path) -> str:
    head_ref = (root / ".muse" / "HEAD").read_text().strip()
    return head_ref.removeprefix("refs/heads/").strip()


def _read_repo_id(root: pathlib.Path) -> str:
    return str(json.loads((root / ".muse" / "repo.json").read_text())["repo_id"])


@add_app.callback(invoke_without_command=True)
def add(
    ctx: typer.Context,
    tag_name: str = typer.Argument(..., help="Tag string (e.g. emotion:joyful)."),
    ref: Optional[str] = typer.Argument(None, help="Commit ID or branch (default: HEAD)."),
) -> None:
    """Attach a tag to a commit."""
    root = require_repo()
    repo_id = _read_repo_id(root)
    branch = _read_branch(root)

    commit = resolve_commit_ref(root, repo_id, branch, ref)
    if commit is None:
        typer.echo(f"❌ Commit '{ref}' not found.")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    write_tag(root, TagRecord(
        tag_id=str(uuid.uuid4()),
        repo_id=repo_id,
        commit_id=commit.commit_id,
        tag=tag_name,
    ))
    typer.echo(f"Tagged {commit.commit_id[:8]} with '{tag_name}'")


@list_app.callback(invoke_without_command=True)
def list_tags(
    ctx: typer.Context,
    ref: Optional[str] = typer.Argument(None, help="Commit ID to list tags for (default: all)."),
) -> None:
    """List tags."""
    root = require_repo()
    repo_id = _read_repo_id(root)
    branch = _read_branch(root)

    if ref:
        commit = resolve_commit_ref(root, repo_id, branch, ref)
        if commit is None:
            typer.echo(f"❌ Commit '{ref}' not found.")
            raise typer.Exit(code=ExitCode.USER_ERROR)
        tags = get_tags_for_commit(root, repo_id, commit.commit_id)
        for t in sorted(tags, key=lambda x: x.tag):
            typer.echo(f"{t.commit_id[:8]}  {t.tag}")
    else:
        tags = get_all_tags(root, repo_id)
        for t in sorted(tags, key=lambda x: (x.tag, x.commit_id)):
            typer.echo(f"{t.commit_id[:8]}  {t.tag}")

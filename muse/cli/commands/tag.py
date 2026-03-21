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

import typer

from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.store import (
    TagRecord,
    delete_tag,
    get_all_tags,
    get_tags_for_commit,
    read_current_branch,
    resolve_commit_ref,
    write_tag,
)

from muse.core.validation import sanitize_display

logger = logging.getLogger(__name__)

app = typer.Typer()
add_app = typer.Typer()
list_app = typer.Typer()
remove_app = typer.Typer()

app.add_typer(add_app, name="add", help="Attach a tag to a commit.")
app.add_typer(list_app, name="list", help="List tags.")
app.add_typer(remove_app, name="remove", help="Remove a tag from a commit.")


def _read_branch(root: pathlib.Path) -> str:
    return read_current_branch(root)


def _read_repo_id(root: pathlib.Path) -> str:
    return str(json.loads((root / ".muse" / "repo.json").read_text())["repo_id"])


@add_app.callback(invoke_without_command=True)
def add(
    ctx: typer.Context,
    tag_name: str = typer.Argument(..., help="Tag string (e.g. emotion:joyful)."),
    ref: str | None = typer.Argument(None, help="Commit ID or branch (default: HEAD)."),
    fmt: str = typer.Option("text", "--format", "-f", help="Output format: text or json."),
) -> None:
    """Attach a tag to a commit.

    Agents should pass ``--format json`` to receive ``{tag_id, commit_id, tag}``
    rather than human-readable text.
    """
    if fmt not in ("text", "json"):
        typer.echo(f"❌ Unknown --format '{sanitize_display(fmt)}'. Choose text or json.", err=True)
        raise typer.Exit(code=ExitCode.USER_ERROR)
    root = require_repo()
    repo_id = _read_repo_id(root)
    branch = _read_branch(root)

    commit = resolve_commit_ref(root, repo_id, branch, ref)
    if commit is None:
        typer.echo(f"❌ Commit '{ref}' not found.")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    tag_id = str(uuid.uuid4())
    write_tag(root, TagRecord(
        tag_id=tag_id,
        repo_id=repo_id,
        commit_id=commit.commit_id,
        tag=tag_name,
    ))
    if fmt == "json":
        typer.echo(json.dumps({"tag_id": tag_id, "commit_id": commit.commit_id, "tag": tag_name}))
    else:
        typer.echo(f"Tagged {commit.commit_id[:8]} with '{sanitize_display(tag_name)}'")


@list_app.callback(invoke_without_command=True)
def list_tags(
    ctx: typer.Context,
    ref: str | None = typer.Argument(None, help="Commit ID to list tags for (default: all)."),
    fmt: str = typer.Option("text", "--format", "-f", help="Output format: text or json."),
) -> None:
    """List tags.

    Agents should pass ``--format json`` to receive a JSON array of
    ``{tag_id, commit_id, tag}`` objects.
    """
    if fmt not in ("text", "json"):
        typer.echo(f"❌ Unknown --format '{sanitize_display(fmt)}'. Choose text or json.", err=True)
        raise typer.Exit(code=ExitCode.USER_ERROR)
    root = require_repo()
    repo_id = _read_repo_id(root)
    branch = _read_branch(root)

    if ref:
        commit = resolve_commit_ref(root, repo_id, branch, ref)
        if commit is None:
            typer.echo(f"❌ Commit '{ref}' not found.")
            raise typer.Exit(code=ExitCode.USER_ERROR)
        tags = get_tags_for_commit(root, repo_id, commit.commit_id)
    else:
        tags = get_all_tags(root, repo_id)

    if fmt == "json":
        typer.echo(json.dumps([{
            "tag_id": t.tag_id, "commit_id": t.commit_id, "tag": t.tag,
        } for t in sorted(tags, key=lambda x: (x.tag, x.commit_id))]))
        return

    for t in sorted(tags, key=lambda x: (x.tag, x.commit_id)):
        typer.echo(f"{t.commit_id[:8]}  {sanitize_display(t.tag)}")


@remove_app.callback(invoke_without_command=True)
def remove_tag(
    ctx: typer.Context,
    tag_name: str = typer.Argument(..., help="Tag string to remove (e.g. emotion:joyful)."),
    ref: str | None = typer.Argument(None, help="Commit ID or branch (default: HEAD)."),
    fmt: str = typer.Option("text", "--format", "-f", help="Output format: text or json."),
) -> None:
    """Remove a tag from a commit.

    Finds all tags with the exact name on the given commit and deletes them.
    Agents should pass ``--format json`` to receive ``{removed_count, commit_id, tag}``.

    Exit codes::

        0 — tag removed
        1 — tag or commit not found
    """
    if fmt not in ("text", "json"):
        typer.echo(f"❌ Unknown --format '{sanitize_display(fmt)}'. Choose text or json.", err=True)
        raise typer.Exit(code=ExitCode.USER_ERROR)
    root = require_repo()
    repo_id = _read_repo_id(root)
    branch = _read_branch(root)

    commit = resolve_commit_ref(root, repo_id, branch, ref)
    if commit is None:
        typer.echo(f"❌ Commit '{sanitize_display(str(ref))}' not found.", err=True)
        raise typer.Exit(code=ExitCode.USER_ERROR)

    tags = get_tags_for_commit(root, repo_id, commit.commit_id)
    matching = [t for t in tags if t.tag == tag_name]
    if not matching:
        typer.echo(
            f"❌ Tag '{sanitize_display(tag_name)}' not found on commit {commit.commit_id[:8]}.",
            err=True,
        )
        raise typer.Exit(code=ExitCode.USER_ERROR)

    for t in matching:
        delete_tag(root, repo_id, t.tag_id)

    if fmt == "json":
        typer.echo(json.dumps({
            "removed_count": len(matching),
            "commit_id": commit.commit_id,
            "tag": tag_name,
        }))
    else:
        typer.echo(
            f"Removed {len(matching)} tag(s) '{sanitize_display(tag_name)}' "
            f"from commit {commit.commit_id[:8]}."
        )

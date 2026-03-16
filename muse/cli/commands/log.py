"""muse log — display commit history.

Output modes
------------

Default::

    commit a1b2c3d4 (HEAD -> main)
    Author: gabriel
    Date:   2026-03-16 12:00:00 UTC

        Add verse melody

--oneline::

    a1b2c3d4 (HEAD -> main) Add verse melody
    f9e8d7c6 Initial commit

--graph::

    * a1b2c3d4 (HEAD -> main) Add verse melody
    * f9e8d7c6 Initial commit

--stat::

    commit a1b2c3d4 (HEAD -> main)
    Date: 2026-03-16 12:00:00 UTC

        Add verse melody

     tracks/drums.mid | added
     1 file changed

Filters: --since, --until, --author, --section, --track, --emotion
"""
from __future__ import annotations

import json
import logging
import pathlib
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

import typer

from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.store import CommitRecord, get_commits_for_branch, get_commit_snapshot_manifest, read_snapshot

logger = logging.getLogger(__name__)

app = typer.Typer()

_DEFAULT_LIMIT = 1000


def _read_branch(root: pathlib.Path) -> str:
    head_ref = (root / ".muse" / "HEAD").read_text().strip()
    return head_ref.removeprefix("refs/heads/").strip()


def _read_repo_id(root: pathlib.Path) -> str:
    return str(json.loads((root / ".muse" / "repo.json").read_text())["repo_id"])


def _parse_date(text: str) -> datetime:
    text = text.strip().lower()
    now = datetime.now(timezone.utc)
    if text == "today":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if text == "yesterday":
        return (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    m = re.match(r"^(\d+)\s+(day|week|month|year)s?\s+ago$", text)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        deltas = {"day": timedelta(days=n), "week": timedelta(weeks=n),
                  "month": timedelta(days=n * 30), "year": timedelta(days=n * 365)}
        return now - deltas[unit]
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    raise ValueError(f"Cannot parse date: {text!r}")


def _file_diff(root: pathlib.Path, commit: CommitRecord) -> tuple[list[str], list[str]]:
    """Return (added, removed) file lists relative to the commit's parent."""
    current_manifest = get_commit_snapshot_manifest(root, commit.commit_id) or {}
    if commit.parent_commit_id:
        parent_manifest = get_commit_snapshot_manifest(root, commit.parent_commit_id) or {}
    else:
        parent_manifest = {}
    added = sorted(set(current_manifest) - set(parent_manifest))
    removed = sorted(set(parent_manifest) - set(current_manifest))
    return added, removed


def _format_date(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S UTC") if dt.tzinfo else str(dt)


@app.callback(invoke_without_command=True)
def log(
    ctx: typer.Context,
    ref: Optional[str] = typer.Argument(None, help="Branch or commit to start from."),
    oneline: bool = typer.Option(False, "--oneline", help="One line per commit."),
    graph: bool = typer.Option(False, "--graph", help="ASCII graph."),
    stat: bool = typer.Option(False, "--stat", help="Show file change summary."),
    patch: bool = typer.Option(False, "--patch", "-p", help="Show file diff."),
    limit: int = typer.Option(_DEFAULT_LIMIT, "-n", "--max-count", help="Limit number of commits."),
    since: Optional[str] = typer.Option(None, "--since", help="Show commits after date."),
    until: Optional[str] = typer.Option(None, "--until", help="Show commits before date."),
    author: Optional[str] = typer.Option(None, "--author", help="Filter by author."),
    section: Optional[str] = typer.Option(None, "--section", help="Filter by section metadata."),
    track: Optional[str] = typer.Option(None, "--track", help="Filter by track metadata."),
    emotion: Optional[str] = typer.Option(None, "--emotion", help="Filter by emotion metadata."),
) -> None:
    """Display commit history."""
    root = require_repo()
    repo_id = _read_repo_id(root)
    branch = ref or _read_branch(root)

    since_dt = _parse_date(since) if since else None
    until_dt = _parse_date(until) if until else None

    commits = get_commits_for_branch(root, repo_id, branch)

    # Apply filters
    filtered: list[CommitRecord] = []
    for c in commits:
        if since_dt and c.committed_at < since_dt:
            continue
        if until_dt and c.committed_at > until_dt:
            continue
        if author and author.lower() not in c.author.lower():
            continue
        if section and c.metadata.get("section") != section:
            continue
        if track and c.metadata.get("track") != track:
            continue
        if emotion and c.metadata.get("emotion") != emotion:
            continue
        filtered.append(c)
        if len(filtered) >= limit:
            break

    if not filtered:
        typer.echo("(no commits)")
        return

    head_commit_id = filtered[0].commit_id if filtered else None

    for c in filtered:
        is_head = c.commit_id == head_commit_id
        ref_label = f" (HEAD -> {branch})" if is_head else ""

        if oneline:
            typer.echo(f"{c.commit_id[:8]}{ref_label} {c.message}")

        elif graph:
            typer.echo(f"* {c.commit_id[:8]}{ref_label} {c.message}")

        else:
            typer.echo(f"commit {c.commit_id[:8]}{ref_label}")
            if c.author:
                typer.echo(f"Author: {c.author}")
            typer.echo(f"Date:   {_format_date(c.committed_at)}")
            if c.metadata:
                meta_parts = [f"{k}: {v}" for k, v in sorted(c.metadata.items())]
                typer.echo(f"Meta:   {', '.join(meta_parts)}")
            typer.echo(f"\n    {c.message}\n")

            if stat or patch:
                added, removed = _file_diff(root, c)
                for p in added:
                    typer.echo(f" + {p}")
                for p in removed:
                    typer.echo(f" - {p}")
                if added or removed:
                    typer.echo(f" {len(added)} added, {len(removed)} removed\n")

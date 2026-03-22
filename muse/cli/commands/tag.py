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

import argparse
import json
import logging
import pathlib
import sys
import uuid

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


def _read_branch(root: pathlib.Path) -> str:
    return read_current_branch(root)


def _read_repo_id(root: pathlib.Path) -> str:
    return str(json.loads((root / ".muse" / "repo.json").read_text())["repo_id"])


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the tag subcommand."""
    parser = subparsers.add_parser(
        "tag",
        help="Attach and query semantic tags on commits.",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subs = parser.add_subparsers(dest="subcommand", metavar="SUBCOMMAND")
    subs.required = True

    add_p = subs.add_parser("add", help="Attach a tag to a commit.")
    add_p.add_argument("tag_name", help="Tag string (e.g. emotion:joyful).")
    add_p.add_argument("ref", nargs="?", default=None, help="Commit ID or branch (default: HEAD).")
    add_p.add_argument("--format", "-f", default="text", dest="fmt", help="Output format: text or json.")
    add_p.set_defaults(func=run_add)

    list_p = subs.add_parser("list", help="List tags.")
    list_p.add_argument("ref", nargs="?", default=None, help="Commit ID to list tags for (default: all).")
    list_p.add_argument("--format", "-f", default="text", dest="fmt", help="Output format: text or json.")
    list_p.set_defaults(func=run_list)

    remove_p = subs.add_parser("remove", help="Remove a tag from a commit.")
    remove_p.add_argument("tag_name", help="Tag string to remove (e.g. emotion:joyful).")
    remove_p.add_argument("ref", nargs="?", default=None, help="Commit ID or branch (default: HEAD).")
    remove_p.add_argument("--format", "-f", default="text", dest="fmt", help="Output format: text or json.")
    remove_p.set_defaults(func=run_remove)


def run_add(args: argparse.Namespace) -> None:
    """Attach a tag to a commit.

    Agents should pass ``--format json`` to receive ``{tag_id, commit_id, tag}``
    rather than human-readable text.
    """
    tag_name: str = args.tag_name
    ref: str | None = args.ref
    fmt: str = args.fmt

    if fmt not in ("text", "json"):
        print(f"❌ Unknown --format '{sanitize_display(fmt)}'. Choose text or json.", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)
    root = require_repo()
    repo_id = _read_repo_id(root)
    branch = _read_branch(root)

    commit = resolve_commit_ref(root, repo_id, branch, ref)
    if commit is None:
        print(f"❌ Commit '{ref}' not found.")
        raise SystemExit(ExitCode.USER_ERROR)

    tag_id = str(uuid.uuid4())
    write_tag(root, TagRecord(
        tag_id=tag_id,
        repo_id=repo_id,
        commit_id=commit.commit_id,
        tag=tag_name,
    ))
    if fmt == "json":
        print(json.dumps({"tag_id": tag_id, "commit_id": commit.commit_id, "tag": tag_name}))
    else:
        print(f"Tagged {commit.commit_id[:8]} with '{sanitize_display(tag_name)}'")


def run_list(args: argparse.Namespace) -> None:
    """List tags.

    Agents should pass ``--format json`` to receive a JSON array of
    ``{tag_id, commit_id, tag}`` objects.
    """
    ref: str | None = args.ref
    fmt: str = args.fmt

    if fmt not in ("text", "json"):
        print(f"❌ Unknown --format '{sanitize_display(fmt)}'. Choose text or json.", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)
    root = require_repo()
    repo_id = _read_repo_id(root)
    branch = _read_branch(root)

    if ref:
        commit = resolve_commit_ref(root, repo_id, branch, ref)
        if commit is None:
            print(f"❌ Commit '{ref}' not found.")
            raise SystemExit(ExitCode.USER_ERROR)
        tags = get_tags_for_commit(root, repo_id, commit.commit_id)
    else:
        tags = get_all_tags(root, repo_id)

    if fmt == "json":
        print(json.dumps([{
            "tag_id": t.tag_id, "commit_id": t.commit_id, "tag": t.tag,
        } for t in sorted(tags, key=lambda x: (x.tag, x.commit_id))]))
        return

    for t in sorted(tags, key=lambda x: (x.tag, x.commit_id)):
        print(f"{t.commit_id[:8]}  {sanitize_display(t.tag)}")


def run_remove(args: argparse.Namespace) -> None:
    """Remove a tag from a commit.

    Finds all tags with the exact name on the given commit and deletes them.
    Agents should pass ``--format json`` to receive ``{removed_count, commit_id, tag}``.

    Exit codes::

        0 — tag removed
        1 — tag or commit not found
    """
    tag_name: str = args.tag_name
    ref: str | None = args.ref
    fmt: str = args.fmt

    if fmt not in ("text", "json"):
        print(f"❌ Unknown --format '{sanitize_display(fmt)}'. Choose text or json.", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)
    root = require_repo()
    repo_id = _read_repo_id(root)
    branch = _read_branch(root)

    commit = resolve_commit_ref(root, repo_id, branch, ref)
    if commit is None:
        print(f"❌ Commit '{sanitize_display(str(ref))}' not found.", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)

    tags = get_tags_for_commit(root, repo_id, commit.commit_id)
    matching = [t for t in tags if t.tag == tag_name]
    if not matching:
        print(
            f"❌ Tag '{sanitize_display(tag_name)}' not found on commit {commit.commit_id[:8]}.",
            file=sys.stderr,
        )
        raise SystemExit(ExitCode.USER_ERROR)

    for t in matching:
        delete_tag(root, repo_id, t.tag_id)

    if fmt == "json":
        print(json.dumps({
            "removed_count": len(matching),
            "commit_id": commit.commit_id,
            "tag": tag_name,
        }))
    else:
        print(
            f"Removed {len(matching)} tag(s) '{sanitize_display(tag_name)}' "
            f"from commit {commit.commit_id[:8]}."
        )

"""``muse clean`` — remove untracked files from the working tree.

Scans the working tree against HEAD's snapshot and removes files that are
not tracked in any commit.  By design, ``--force`` is required to actually
delete files; without it the command behaves as a dry-run.

Usage::

    muse clean -n              # preview — show what would be removed
    muse clean -f              # delete untracked files
    muse clean -f -d           # also delete untracked directories
    muse clean -f -x           # also delete .museignore-excluded files
    muse clean -f -d -x        # everything untracked + ignored

Exit codes::

    0 — nothing to clean, or clean completed successfully
    1 — untracked files exist but --force not given (user error)
    2 — not a Muse repository
    3 — I/O error during deletion
"""

from __future__ import annotations

import argparse
import json
import logging
import pathlib
import sys

from muse.core.errors import ExitCode
from muse.core.ignore import load_ignore_config, resolve_patterns
from muse.core.repo import require_repo
from muse.core.snapshot import walk_workdir
from muse.core.store import get_head_commit_id, read_current_branch, read_snapshot, read_commit
from muse.core.validation import sanitize_display
from muse.plugins.registry import read_domain

logger = logging.getLogger(__name__)


def _read_repo_id(root: pathlib.Path) -> str:
    return str(json.loads((root / ".muse" / "repo.json").read_text(encoding="utf-8"))["repo_id"])


def _is_ignored(path: str, patterns: list[str]) -> bool:
    """Return True if *path* matches any .museignore pattern (last-match-wins)."""
    import fnmatch
    result = False
    for pat in patterns:
        negate = pat.startswith("!")
        effective = pat[1:] if negate else pat
        if fnmatch.fnmatch(path, effective) or fnmatch.fnmatch(pathlib.Path(path).name, effective):
            result = not negate
    return result


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the clean subcommand."""
    parser = subparsers.add_parser(
        "clean",
        help="Remove untracked files from the working tree.",
        description=__doc__,
    )
    parser.add_argument("-n", "--dry-run", action="store_true", dest="dry_run",
                        help="Preview — show what would be removed.")
    parser.add_argument("-f", "--force", action="store_true",
                        help="Delete untracked files.")
    parser.add_argument("-x", "--include-ignored", action="store_true", dest="include_ignored",
                        help="Also delete .museignore-excluded files.")
    parser.add_argument("-d", "--directories", action="store_true",
                        help="Also delete untracked directories.")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> None:
    """Remove untracked files from the working tree.

    Files not tracked in the HEAD snapshot are considered untracked.
    ``--force`` is required to delete; without it the command previews
    what would be removed (equivalent to ``--dry-run``).

    The working tree is walked with the same rules as ``muse commit``:
    hidden files, symlinks, and ``.muse/`` itself are always excluded.

    Examples::

        muse clean -n          # preview
        muse clean -f          # delete untracked files
        muse clean -f -d -x    # delete untracked + empty dirs + ignored files
    """
    dry_run: bool = args.dry_run
    force: bool = args.force
    include_ignored: bool = args.include_ignored
    directories: bool = args.directories

    if not force and not dry_run:
        print(
            "⚠️  fatal: clean.requireForce is set to true.\n"
            "    Use --force to remove files, or --dry-run to preview.",
            file=sys.stderr,
        )
        raise SystemExit(ExitCode.USER_ERROR)

    root = require_repo()
    repo_id = _read_repo_id(root)
    branch = read_current_branch(root)
    domain = read_domain(root)

    # Build committed manifest (may be empty for initial branch).
    committed: dict[str, str] = {}
    head_commit_id = get_head_commit_id(root, branch)
    if head_commit_id:
        commit = read_commit(root, head_commit_id)
        if commit:
            snap = read_snapshot(root, commit.snapshot_id)
            if snap:
                committed = snap.manifest

    # Build current workdir manifest.
    workdir = root
    current = walk_workdir(workdir)

    # Ignored patterns for --include-ignored.
    ignored_patterns: list[str] = []
    if not include_ignored:
        try:
            ignore_cfg = load_ignore_config(root)
            ignored_patterns = resolve_patterns(ignore_cfg, domain)
        except Exception:
            pass

    # Collect untracked paths.
    untracked: list[str] = []
    for rel_path in sorted(current):
        if rel_path in committed:
            continue
        if not include_ignored and _is_ignored(rel_path, ignored_patterns):
            continue
        untracked.append(rel_path)

    if not untracked:
        print("Nothing to clean.")
        return

    prefix = "[dry-run] " if dry_run else ""
    verb = "Would remove" if dry_run else "Removing"

    removed_dirs: set[pathlib.Path] = set()
    for rel_path in untracked:
        print(f"{prefix}{verb}: {sanitize_display(rel_path)}")
        if not dry_run:
            target = root / rel_path
            try:
                target.unlink(missing_ok=True)
                if directories:
                    parent = target.parent
                    removed_dirs.add(parent)
            except OSError as exc:
                print(f"❌ Could not remove {sanitize_display(rel_path)}: {exc}", file=sys.stderr)
                raise SystemExit(ExitCode.INTERNAL_ERROR) from exc

    # Remove empty directories (bottom-up).
    if not dry_run and directories:
        for d in sorted(removed_dirs, key=lambda p: len(p.parts), reverse=True):
            if d == root or d == root / ".muse":
                continue
            try:
                # Only remove if truly empty.
                if d.is_dir() and not any(d.iterdir()):
                    d.rmdir()
                    print(f"Removing directory: {sanitize_display(str(d.relative_to(root)))}")
            except OSError:
                pass

    count = len(untracked)
    if dry_run:
        print(f"\n{count} untracked file(s) would be removed.")
    else:
        print(f"\n✅ Removed {count} untracked file(s).")

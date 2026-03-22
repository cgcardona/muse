"""``muse code add`` — stage files (or hunks) for the next commit.

Mirrors ``git add`` exactly.  Files added to the stage are the *only* ones
that ``muse commit`` will include; all other tracked files carry their
last-committed state forward unchanged.

Usage::

    muse code add <path> [<path> …]   # stage specific files/dirs
    muse code add .                   # stage everything (like current muse commit)
    muse code add -u                  # stage all tracked modified/deleted files
    muse code add -A / --all          # stage all changes including new files
    muse code add -p <file>           # interactive hunk-by-hunk staging
    muse code add --patch <file>      # same as -p

``muse code reset`` (or ``muse code reset HEAD <file>``) removes files from
the stage without touching the working tree.

Exit codes::

    0 — one or more files staged successfully
    1 — user error (path not found, not a code repo, etc.)
    3 — I/O error
"""

from __future__ import annotations

import argparse
import difflib
import logging
import os
import pathlib
import re
import stat as _stat
import sys
import tempfile
from typing import Literal

from muse.core.errors import ExitCode
from muse.core.object_store import read_object, write_object_from_path
from muse.core.repo import require_repo
from muse.core.snapshot import hash_file
from muse.core.store import read_commit, read_snapshot
from muse.core.validation import sanitize_display
from muse.plugins.code.stage import (
    StagedEntry,
    clear_stage,
    make_entry,
    read_stage,
    write_stage,
)
from muse.plugins.registry import read_domain

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level constants (computed once; never recreated per call)
# ---------------------------------------------------------------------------

# Directories that are never version-controlled.  Mirrors _ALWAYS_IGNORE_DIRS
# in the code plugin to avoid importing from the plugin layer.
_IGNORE_DIRS: frozenset[str] = frozenset({
    "__pycache__", "node_modules", ".git", ".hg", ".svn",
    ".venv", "venv", ".tox", "dist", "build", ".eggs", ".DS_Store",
})

# Precompiled regex for the unified-diff hunk header.
# Captures: src_start, src_count (optional), dst_start, dst_count (optional).
_HUNK_HEADER_RE = re.compile(
    r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@"
)

# ---------------------------------------------------------------------------
# argparse registration
# ---------------------------------------------------------------------------


def register_add(
    subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]",
) -> None:
    """Register the ``muse code add`` subcommand."""
    parser = subparsers.add_parser(
        "add",
        help="Stage file(s) for the next commit.",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "paths",
        nargs="*",
        metavar="PATH",
        help="File or directory to stage.  Use '.' to stage everything.",
    )
    parser.add_argument(
        "-p", "--patch",
        action="store_true",
        dest="patch",
        help="Interactively select hunks to stage (like git add -p).",
    )
    parser.add_argument(
        "-u", "--update",
        action="store_true",
        dest="update",
        help="Stage all tracked modified and deleted files (no new files).",
    )
    parser.add_argument(
        "-A", "--all",
        action="store_true",
        dest="all_files",
        help="Stage all changes including new (untracked) files.",
    )
    parser.add_argument(
        "-n", "--dry-run",
        action="store_true",
        dest="dry_run",
        help="Show what would be staged without actually staging.",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Show each file as it is staged.",
    )
    parser.set_defaults(func=run_add)


def register_reset(
    subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]",
) -> None:
    """Register the ``muse code reset`` subcommand."""
    parser = subparsers.add_parser(
        "reset",
        help="Unstage file(s) without touching the working tree.",
        description=(
            "Remove files from the stage index.  The working tree is unchanged.\n\n"
            "    muse code reset              # unstage everything\n"
            "    muse code reset HEAD <file>  # unstage a specific file\n"
            "    muse code reset <file>       # same as above\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "paths",
        nargs="*",
        metavar="PATH",
        help=(
            "File(s) to unstage.  Omit or pass 'HEAD' alone to unstage "
            "everything."
        ),
    )
    parser.set_defaults(func=run_reset)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _head_manifest(root: pathlib.Path) -> dict[str, str]:
    """Return the manifest from the current HEAD commit, or ``{}`` if none.

    Reads ``.muse/refs/heads/<branch>`` → commit record → snapshot record.
    Returns an empty dict for repositories with no commits yet (fresh init).
    Never raises — callers treat an empty manifest as "nothing committed."
    """
    from muse.core.store import read_current_branch

    try:
        branch = read_current_branch(root)
        ref = root / ".muse" / "refs" / "heads" / branch
        if not ref.exists():
            return {}
        commit_id = ref.read_text().strip()
        if not commit_id:
            return {}
        commit = read_commit(root, commit_id)
        if commit is None:
            return {}
        snap = read_snapshot(root, commit.snapshot_id)
        return dict(snap.manifest) if snap else {}
    except Exception:
        return {}


def _is_ignorable_dir(name: str) -> bool:
    """Return True when a directory should be skipped during tree walks."""
    return name.startswith(".") or name in _IGNORE_DIRS


def _walk_tree(base: pathlib.Path) -> list[pathlib.Path]:
    """Recursively yield regular files under *base*, honouring ignore rules.

    Skips hidden files/directories and directories in :data:`_IGNORE_DIRS`.
    Results are sorted for determinism.
    """
    result: list[pathlib.Path] = []
    for dirpath, dirnames, filenames in os.walk(str(base), followlinks=False):
        dirnames[:] = sorted(
            d for d in dirnames if not _is_ignorable_dir(d)
        )
        for fname in sorted(filenames):
            if fname.startswith("."):
                continue
            abs_path = pathlib.Path(dirpath) / fname
            try:
                st = abs_path.lstat()
            except OSError:
                continue
            if _stat.S_ISREG(st.st_mode):
                result.append(abs_path)
    return result


def _collect_paths(
    root: pathlib.Path,
    raw_paths: list[str],
    update_only: bool,
    all_files: bool,
    head_manifest: dict[str, str],
) -> list[pathlib.Path]:
    """Resolve user-supplied paths to absolute file paths for staging.

    Priority order (first matching rule wins):

    1. **``-u`` / ``update_only``** — every path in ``head_manifest``,
       whether it still exists on disk or not.  Deleted files are included
       so they can be recorded as mode-D entries.
    2. **``-A`` / ``all_files``, or no paths given** — full ``os.walk`` of
       the working tree (same scope as an unstaged ``muse commit``).
    3. **Explicit paths** — expanded recursively if a directory; allowed to
       name files that are absent from disk *iff* they appear in
       ``head_manifest`` (staged deletion).

    Args:
        root:          Repository root directory.
        raw_paths:     User-supplied path strings (may be relative or absolute).
        update_only:   ``-u`` flag — only tracked files.
        all_files:     ``-A`` flag — include untracked new files.
        head_manifest: The committed manifest (path → object_id).

    Returns:
        List of absolute :class:`pathlib.Path` values to stage.
    """
    paths: list[pathlib.Path] = []

    if update_only:
        # Stage all tracked files — modified ones and deleted ones.
        # Deleted files (absent from disk) receive mode "D" later in run_add.
        for rel in head_manifest:
            paths.append(root / pathlib.Path(rel))

    elif all_files or (not raw_paths):
        # Stage the entire working tree.
        paths = _walk_tree(root)

    else:
        for raw in raw_paths:
            p = pathlib.Path(raw)
            if not p.is_absolute():
                p = pathlib.Path.cwd() / p
            p = p.resolve()
            if p.is_dir():
                paths.extend(_walk_tree(p))
            elif p.exists():
                paths.append(p)
            else:
                # Allow staging a deleted tracked file by name.
                try:
                    rel_guess = str(p.relative_to(root)).replace("\\", "/")
                except ValueError:
                    rel_guess = raw
                if rel_guess in head_manifest:
                    paths.append(p)
                else:
                    print(
                        f"❌ Path not found: {sanitize_display(raw)}",
                        file=sys.stderr,
                    )

    return paths


def _infer_mode(
    rel: str,
    head_manifest: dict[str, str],
    exists_on_disk: bool,
) -> Literal["A", "M", "D"]:
    """Return the staging mode for a file.

    - ``"D"`` — the file no longer exists on disk; the next commit should
      remove it from the snapshot.
    - ``"M"`` — the file exists on disk *and* is already tracked (present
      in ``head_manifest``); the next commit replaces the committed version.
    - ``"A"`` — the file exists on disk but was not in the previous commit;
      the next commit introduces it as a new tracked file.

    Args:
        rel:             Workspace-relative POSIX path of the file.
        head_manifest:   The committed manifest (path → object_id).
        exists_on_disk:  Whether the file is currently present in the working tree.

    Returns:
        One of ``"A"``, ``"M"``, or ``"D"``.
    """
    if not exists_on_disk:
        return "D"
    if rel in head_manifest:
        return "M"
    return "A"


# ---------------------------------------------------------------------------
# Patch-mode (interactive hunk staging)
# ---------------------------------------------------------------------------


def _split_into_hunks(unified_diff_lines: list[str]) -> list[list[str]]:
    """Split unified-diff output into individual hunks.

    The file-header lines (``--- a/file`` / ``+++ b/file``) are prepended
    to every hunk so that each hunk can be displayed standalone with full
    file context.  This matches ``git add -p`` display behaviour.

    Args:
        unified_diff_lines: Lines produced by :func:`difflib.unified_diff`,
                            each line terminating with ``\\n``.

    Returns:
        A list of hunks, where each hunk is a list of lines starting with
        the file header followed by the ``@@ … @@`` marker.
    """
    header: list[str] = []
    hunks: list[list[str]] = []
    current: list[str] = []

    for line in unified_diff_lines:
        if line.startswith("@@"):
            if current:
                hunks.append(current)
            current = header[:] + [line]
        elif not hunks and not current:
            header.append(line)
        else:
            if current:
                current.append(line)

    if current:
        hunks.append(current)

    return hunks


def _colorize_hunk(lines: list[str]) -> str:
    """Return *lines* as a terminal-colored string for interactive display.

    Colors: added lines → green, removed lines → red, ``@@`` headers → cyan.
    File header lines (``---`` / ``+++``) are left unstyled.

    Args:
        lines: Hunk lines (including the file header and ``@@ … @@`` line).

    Returns:
        A single string with embedded ANSI escape codes, ready to print.
    """
    _GREEN = "\x1b[32m"
    _RED = "\x1b[31m"
    _CYAN = "\x1b[36m"
    _RESET = "\x1b[0m"
    out: list[str] = []
    for line in lines:
        if line.startswith("+") and not line.startswith("+++"):
            out.append(f"{_GREEN}{line}{_RESET}")
        elif line.startswith("-") and not line.startswith("---"):
            out.append(f"{_RED}{line}{_RESET}")
        elif line.startswith("@@"):
            out.append(f"{_CYAN}{line}{_RESET}")
        else:
            out.append(line)
    return "".join(out)


def _apply_hunks_to_bytes(
    original: bytes,
    accepted_hunks: list[list[str]],
) -> bytes:
    """Produce a partial file by applying only the *accepted_hunks*.

    This is the core of ``--patch`` mode: the user may accept some hunks
    and reject others.  The result is a version of the file that includes
    exactly the accepted changes on top of the original content.

    Algorithm:

    1. Decode *original* as UTF-8 (with replacement) into a mutable line list.
    2. Iterate accepted hunks in **reverse order** (highest line numbers first)
       so that earlier hunks' line indices remain valid after applying later
       ones.
    3. For each hunk: parse the ``@@ -start,count … @@`` header to find the
       source line range, then rebuild that range from the hunk's context
       (``' '``-prefixed) and added (``'+'``-prefixed) lines, discarding
       removed (``'-'``-prefixed) lines.
    4. Re-encode the result as UTF-8.

    Args:
        original:       Raw bytes of the file before any changes.
        accepted_hunks: Hunks the user chose to stage (output of
                        :func:`_split_into_hunks`, filtered by user input).

    Returns:
        Raw bytes of the partially-staged file containing only the accepted
        changes.  Binary files that cannot be decoded as UTF-8 will have
        replacement characters (U+FFFD) for non-decodable bytes.
    """
    original_lines = original.decode("utf-8", errors="replace").splitlines(keepends=True)
    result: list[str] = list(original_lines)

    for hunk in reversed(accepted_hunks):
        hunk_header = next((l for l in hunk if l.startswith("@@")), None)
        if hunk_header is None:
            continue

        m = _HUNK_HEADER_RE.search(hunk_header)
        if m is None:
            continue

        src_start = int(m.group(1)) - 1  # convert from 1-based to 0-based
        src_count = int(m.group(2)) if m.group(2) is not None else 1

        # Collect diff body lines (exclude file header and @@ marker).
        hunk_lines = [
            l for l in hunk
            if not l.startswith("---")
            and not l.startswith("+++")
            and not l.startswith("@@")
        ]

        new_block: list[str] = []
        for line in hunk_lines:
            if line.startswith(" ") or line.startswith("+"):
                # Context and added lines both go into the result.
                # Strip the leading diff prefix character.
                new_block.append(line[1:])
            # Lines starting with '-' are removed — intentionally dropped.

        result[src_start : src_start + src_count] = new_block

    return "".join(result).encode("utf-8")


def _interactive_patch(
    root: pathlib.Path,
    abs_path: pathlib.Path,
    rel: str,
    head_manifest: dict[str, str],
    current_stage: dict[str, StagedEntry],
) -> dict[str, StagedEntry]:
    """Interactively select hunks from *abs_path* to stage.

    Presents each diff hunk in the terminal with color, then prompts:

    - ``y`` — stage this hunk
    - ``n`` — skip this hunk
    - ``q`` — quit; commit whatever has been accepted so far
    - ``a`` — accept this and all remaining hunks
    - ``d`` — skip the rest of this file
    - ``?`` — show help

    The "before" content is determined in priority order:

    1. The currently-staged version (if the file is already partially staged).
    2. The HEAD-committed version.
    3. Empty bytes (new file with no prior state).

    The accepted hunks are applied to the "before" bytes with
    :func:`_apply_hunks_to_bytes`, the result is hashed and written to the
    object store, and the stage entry is updated.

    Args:
        root:          Repository root directory.
        abs_path:      Absolute path to the working-tree file.
        rel:           Workspace-relative POSIX path (for display and stage key).
        head_manifest: Committed manifest (path → object_id).
        current_stage: The stage index as it stands before this call.

    Returns:
        An updated stage entries dict (copy of *current_stage* with any new
        or changed entry for *rel* merged in).
    """
    before_bytes: bytes = b""
    if rel in current_stage:
        try:
            blob = read_object(root, current_stage[rel]["object_id"])
            if blob is not None:
                before_bytes = blob
        except OSError:
            pass
    elif rel in head_manifest:
        try:
            blob = read_object(root, head_manifest[rel])
            if blob is not None:
                before_bytes = blob
        except OSError:
            pass

    if not abs_path.exists():
        print(
            f"⚠️  {sanitize_display(rel)}: file not found — cannot stage hunks.",
            file=sys.stderr,
        )
        return current_stage

    after_bytes = abs_path.read_bytes()

    before_lines = before_bytes.decode("utf-8", errors="replace").splitlines(keepends=True)
    after_lines = after_bytes.decode("utf-8", errors="replace").splitlines(keepends=True)

    diff_lines = list(
        difflib.unified_diff(
            before_lines,
            after_lines,
            fromfile=f"a/{rel}",
            tofile=f"b/{rel}",
            lineterm="",
        )
    )
    diff_with_newlines = [l + "\n" for l in diff_lines]

    hunks = _split_into_hunks(diff_with_newlines)
    if not hunks:
        print(f"  (no changes in {sanitize_display(rel)})")
        return current_stage

    accepted: list[list[str]] = []
    total = len(hunks)

    for idx, hunk in enumerate(hunks, 1):
        print(f"\n{_colorize_hunk(hunk)}", end="")
        print(f"\nHunk {idx}/{total} — Stage this hunk?")
        print("  y = yes, n = no, q = quit, a = stage all remaining, d = skip rest of file, ? = help")

        choice = "n"
        while True:
            try:
                choice = input("Stage hunk? [y/n/q/a/d/?] ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print("\nAborted.")
                raise SystemExit(ExitCode.USER_ERROR)

            if choice == "y":
                accepted.append(hunk)
                break
            elif choice == "n":
                break
            elif choice == "q":
                break
            elif choice == "a":
                accepted.append(hunk)
                accepted.extend(hunks[idx:])
                break
            elif choice == "d":
                break
            elif choice == "?":
                print(
                    "  y — stage this hunk\n"
                    "  n — do not stage this hunk\n"
                    "  q — quit; do not stage this or any remaining hunks\n"
                    "  a — stage this and all remaining hunks\n"
                    "  d — skip the rest of this file\n"
                    "  ? — print this help\n"
                )
            else:
                print("  Unknown key. Press ? for help.")

            if choice in ("q", "d"):
                break

        if choice == "q":
            break
        if choice == "d":
            break

    if not accepted:
        return current_stage

    partial_bytes = _apply_hunks_to_bytes(before_bytes, accepted)

    with tempfile.NamedTemporaryFile(delete=False, suffix=".partial") as tf:
        tf.write(partial_bytes)
        tmp_path = pathlib.Path(tf.name)

    try:
        object_id = hash_file(tmp_path)
        write_object_from_path(root, object_id, tmp_path)
    finally:
        tmp_path.unlink(missing_ok=True)

    mode = _infer_mode(rel, head_manifest, abs_path.exists())
    updated = dict(current_stage)
    updated[rel] = make_entry(object_id, mode)
    print(f"  ✅ Staged {len(accepted)}/{total} hunk(s) from {sanitize_display(rel)}")
    return updated


# ---------------------------------------------------------------------------
# run_add
# ---------------------------------------------------------------------------


def run_add(args: argparse.Namespace) -> None:
    """Stage files or hunks for the next ``muse commit``.

    Agents should prefer ``muse code add <file>`` (non-interactive) and
    avoid ``--patch`` mode, which requires a terminal.
    """
    raw_paths: list[str] = args.paths
    patch: bool = args.patch
    update: bool = args.update
    all_files: bool = args.all_files
    dry_run: bool = args.dry_run
    verbose: bool = args.verbose

    root = require_repo()

    if read_domain(root) != "code":
        print("❌ 'muse code add' requires a code-domain repository.", file=sys.stderr)
        print("   Initialise one with: muse init --domain code", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)

    head_manifest = _head_manifest(root)
    current_stage = read_stage(root)

    # Patch mode: interactive hunk-by-hunk staging.
    if patch:
        if not raw_paths:
            print("❌ --patch requires at least one file path.", file=sys.stderr)
            raise SystemExit(ExitCode.USER_ERROR)
        for raw in raw_paths:
            p = pathlib.Path(raw)
            if not p.is_absolute():
                p = pathlib.Path.cwd() / p
            p = p.resolve()
            try:
                rel = str(p.relative_to(root)).replace("\\", "/")
            except ValueError:
                print(
                    f"❌ {sanitize_display(raw)} is outside the repo root.",
                    file=sys.stderr,
                )
                raise SystemExit(ExitCode.USER_ERROR)
            current_stage = _interactive_patch(
                root, p, rel, head_manifest, current_stage
            )
        write_stage(root, current_stage)
        return

    # Non-patch mode: stage whole files.
    collected = _collect_paths(
        root, raw_paths, update_only=update, all_files=all_files,
        head_manifest=head_manifest,
    )

    if not collected and not update:
        if raw_paths:
            print("❌ No matching files found.", file=sys.stderr)
            raise SystemExit(ExitCode.USER_ERROR)
        print("Nothing to stage.")
        return

    staged_count = 0
    updated_stage = dict(current_stage)

    for abs_path in collected:
        try:
            rel = str(abs_path.relative_to(root)).replace("\\", "/")
        except ValueError:
            print(
                f"⚠️  {sanitize_display(str(abs_path))} is outside the repo root — skipped.",
                file=sys.stderr,
            )
            continue

        exists = abs_path.exists()
        mode = _infer_mode(rel, head_manifest, exists)

        if dry_run:
            label = {"A": "new file", "M": "modified", "D": "deleted"}[mode]
            print(f"  {label}: {sanitize_display(rel)}")
            staged_count += 1
            continue

        if mode == "D":
            # Record the deletion with a sentinel empty object_id.
            # snapshot() will pop this path from the committed manifest.
            updated_stage[rel] = make_entry("", "D")
            if verbose:
                print(f"  deleted:  {sanitize_display(rel)}")
            staged_count += 1
            continue

        try:
            object_id = hash_file(abs_path)
            write_object_from_path(root, object_id, abs_path)
        except OSError as exc:
            print(f"❌ Cannot read {sanitize_display(rel)}: {exc}", file=sys.stderr)
            raise SystemExit(ExitCode.INTERNAL_ERROR)

        # Skip if the file's content is identical to the last committed version.
        # Without this check, `muse code add .` would stage every file in the
        # working tree regardless of whether anything actually changed — because
        # the "skip if already staged" guard below only fires after the first
        # `muse code add` run.
        committed_id = head_manifest.get(rel)
        if committed_id == object_id:
            if verbose:
                print(f"  (unchanged) {sanitize_display(rel)}")
            continue

        # Skip if the staged version is already current.
        existing = updated_stage.get(rel)
        if existing and existing["object_id"] == object_id:
            if verbose:
                print(f"  (unchanged) {sanitize_display(rel)}")
            continue

        updated_stage[rel] = make_entry(object_id, mode)
        if verbose:
            label = {"A": "new file", "M": "modified", "D": "deleted"}[mode]
            print(f"  {label}:  {sanitize_display(rel)}")
        staged_count += 1

    if dry_run:
        print(f"\n{staged_count} file(s) would be staged.")
        return

    write_stage(root, updated_stage)

    if staged_count == 0:
        print("Nothing new to stage — all files already up to date.")
    else:
        print(f"Staged {staged_count} file(s).")


# ---------------------------------------------------------------------------
# run_reset
# ---------------------------------------------------------------------------


def run_reset(args: argparse.Namespace) -> None:
    """Remove files from the stage without touching the working tree.

    ``muse code reset`` with no arguments clears the entire stage.
    ``muse code reset HEAD <file>`` (or just ``muse code reset <file>``)
    unstages a specific file, leaving the working-tree copy untouched.
    """
    raw_paths: list[str] = [p for p in args.paths if p != "HEAD"]

    root = require_repo()

    if read_domain(root) != "code":
        print("❌ 'muse code reset' requires a code-domain repository.", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)

    current_stage = read_stage(root)

    if not current_stage:
        print("Nothing staged.")
        return

    if not raw_paths:
        clear_stage(root)
        print(f"Unstaged all {len(current_stage)} file(s).")
        return

    updated = dict(current_stage)
    for raw in raw_paths:
        p = pathlib.Path(raw)
        if not p.is_absolute():
            p = pathlib.Path.cwd() / p
        try:
            rel = str(p.resolve().relative_to(root)).replace("\\", "/")
        except ValueError:
            rel = raw

        if rel in updated:
            del updated[rel]
            print(f"  unstaged: {sanitize_display(rel)}")
        else:
            print(f"  (not staged) {sanitize_display(rel)}")

    write_stage(root, updated)

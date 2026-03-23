"""``muse branch`` — list, create, rename, copy, and delete branches.

Git-idiomatic flags::

    muse branch                        # list all local branches
    muse branch <name>                 # create branch at HEAD
    muse branch <name> <start-point>   # create branch at commit or branch
    muse branch -d <name>              # safe delete (must be merged)
    muse branch -D <name>              # force delete
    muse branch -m [<old>] <new>       # rename (safe)
    muse branch -M [<old>] <new>       # rename (force)
    muse branch -c [<src>] <dest>      # copy (safe)
    muse branch -C [<src>] <dest>      # copy (force)
    muse branch -v                     # list with last commit SHA + subject
    muse branch -vv                    # also show upstream tracking ref
    muse branch -r                     # list remote-tracking branches
    muse branch -a                     # list local + remote-tracking branches
    muse branch --merged [<commit>]    # only branches merged into commit
    muse branch --no-merged [<commit>] # only branches NOT merged into commit
    muse branch --contains <commit>    # only branches that contain commit

Exit codes::

    0 — success
    1 — invalid branch name, branch not found, attempting to delete checked-out branch
"""

from __future__ import annotations

import argparse
import json
import logging
import pathlib
import sys

from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.store import get_head_commit_id, read_commit, read_current_branch
from muse.core.validation import sanitize_display, validate_branch_name

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# ANSI helpers — emitted only when stdout is a TTY.
# ---------------------------------------------------------------------------

_RESET  = "\033[0m"
_BOLD   = "\033[1m"
_DIM    = "\033[2m"
_GREEN  = "\033[32m"
_RED    = "\033[31m"
_YELLOW = "\033[33m"
_CYAN   = "\033[36m"


def _c(text: str, *codes: str, tty: bool) -> str:
    """Wrap *text* in ANSI escape *codes* only when writing to a TTY."""
    if not tty:
        return text
    return "".join(codes) + text + _RESET


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _ref_file(root: pathlib.Path, branch: str) -> pathlib.Path:
    """Return the ref-file path for a local branch."""
    return root / ".muse" / "refs" / "heads" / branch


def _list_local_branches(root: pathlib.Path) -> list[str]:
    """Return a sorted list of all local branch names."""
    heads_dir = root / ".muse" / "refs" / "heads"
    if not heads_dir.exists():
        return []
    return sorted(
        p.relative_to(heads_dir).as_posix()
        for p in heads_dir.rglob("*")
        if p.is_file()
    )


def _list_remotes(root: pathlib.Path) -> list[str]:
    """Return sorted remote-tracking branch names as ``remote/branch``."""
    remotes_dir = root / ".muse" / "remotes"
    if not remotes_dir.exists():
        return []
    results: list[str] = []
    for remote_dir in sorted(remotes_dir.iterdir()):
        if not remote_dir.is_dir():
            continue
        remote = remote_dir.name
        for ref_file in sorted(remote_dir.rglob("*")):
            if ref_file.is_file():
                branch_rel = ref_file.relative_to(remote_dir).as_posix()
                results.append(f"{remote}/{branch_rel}")
    return results


def _upstream_for(root: pathlib.Path, branch: str) -> str | None:
    """Return the upstream tracking ref for *branch*, or ``None`` if unset."""
    config_path = root / ".muse" / "config.toml"
    if not config_path.exists():
        return None
    try:
        import tomllib  # stdlib ≥ 3.11
        with config_path.open("rb") as f:
            config = tomllib.load(f)
        section = config.get("branch", {}).get(branch, {})
        remote: str | None = section.get("remote")
        merge_ref: str | None = section.get("merge")
        if remote and merge_ref:
            short = merge_ref.removeprefix("refs/heads/")
            return f"{remote}/{short}"
    except Exception:
        pass
    return None


def _commit_ancestors(root: pathlib.Path, commit_id: str) -> set[str]:
    """Return the set of all commit IDs reachable from *commit_id* (inclusive)."""
    seen: set[str] = set()
    queue: list[str] = [commit_id]
    while queue:
        cid = queue.pop()
        if cid in seen:
            continue
        seen.add(cid)
        rec = read_commit(root, cid)
        if rec is None:
            continue
        if rec.parent_commit_id:
            queue.append(rec.parent_commit_id)
        if rec.parent2_commit_id:
            queue.append(rec.parent2_commit_id)
    return seen


def _is_merged(root: pathlib.Path, branch: str, into: str) -> bool:
    """Return ``True`` if the tip of *branch* is an ancestor of the tip of *into*."""
    branch_tip = get_head_commit_id(root, branch)
    into_tip = get_head_commit_id(root, into)
    if branch_tip is None or into_tip is None:
        return False
    return branch_tip in _commit_ancestors(root, into_tip)


def _contains_commit(root: pathlib.Path, branch: str, commit_id: str) -> bool:
    """Return ``True`` if *commit_id* is reachable from the tip of *branch*."""
    tip = get_head_commit_id(root, branch)
    if tip is None:
        return False
    return commit_id in _commit_ancestors(root, tip)


def _cleanup_empty_dirs(ref_file: pathlib.Path, heads_dir: pathlib.Path) -> None:
    """Remove any empty parent directories left behind after unlinking *ref_file*."""
    for parent in ref_file.parents:
        if parent == heads_dir:
            break
        try:
            parent.rmdir()
        except OSError:
            break


# ---------------------------------------------------------------------------
# CLI registration
# ---------------------------------------------------------------------------


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the branch subcommand."""
    parser = subparsers.add_parser(
        "branch",
        help="List, create, rename, copy, or delete branches.",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("args", nargs="*", help="Branch name(s) — context-sensitive.")

    # Mutually exclusive operation flags (mirrors git branch).
    ops = parser.add_mutually_exclusive_group()
    ops.add_argument(
        "-d", "--delete", dest="op", action="store_const", const="delete",
        help="Delete a branch (safe — must be fully merged).",
    )
    ops.add_argument(
        "-D", dest="op", action="store_const", const="force_delete",
        help="Force-delete a branch regardless of merge status.",
    )
    ops.add_argument(
        "-m", "--move", dest="op", action="store_const", const="rename",
        help="Rename a branch (safe).",
    )
    ops.add_argument(
        "-M", dest="op", action="store_const", const="force_rename",
        help="Force-rename a branch.",
    )
    ops.add_argument(
        "-c", "--copy", dest="op", action="store_const", const="copy",
        help="Copy a branch (safe).",
    )
    ops.add_argument(
        "-C", dest="op", action="store_const", const="force_copy",
        help="Force-copy a branch.",
    )

    # Listing modifiers.
    parser.add_argument(
        "-v", action="count", default=0, dest="verbose",
        help="Show last commit SHA + subject. Repeat (-vv) to also show upstream.",
    )
    parser.add_argument(
        "-r", "--remotes", action="store_true",
        help="List remote-tracking branches.",
    )
    parser.add_argument(
        "-a", "--all", action="store_true", dest="all_branches",
        help="List both local and remote-tracking branches.",
    )
    parser.add_argument(
        "--merged", metavar="COMMIT", nargs="?", const="HEAD",
        help="Only list branches merged into COMMIT (default HEAD).",
    )
    parser.add_argument(
        "--no-merged", metavar="COMMIT", nargs="?", const="HEAD",
        help="Only list branches NOT merged into COMMIT (default HEAD).",
    )
    parser.add_argument(
        "--contains", metavar="COMMIT",
        help="Only list branches that contain COMMIT.",
    )
    parser.add_argument(
        "--format", "-f", default="text", dest="fmt",
        help="Output format: text or json.",
    )
    parser.set_defaults(func=run, op=None)


# ---------------------------------------------------------------------------
# Command handler
# ---------------------------------------------------------------------------


def run(args: argparse.Namespace) -> None:
    """List, create, rename, copy, or delete branches.

    Agents should pass ``--format json`` when listing to receive a JSON array
    of ``{name, current, commit_id}`` objects, or a single result object when
    creating, renaming, copying, or deleting a branch.
    """
    positional: list[str] = args.args
    op: str | None = args.op
    verbose: int = args.verbose
    remotes_only: bool = args.remotes
    all_branches: bool = args.all_branches
    merged_into: str | None = args.merged
    not_merged_into: str | None = args.no_merged
    contains_commit: str | None = args.contains
    fmt: str = args.fmt
    tty: bool = sys.stdout.isatty()

    if fmt not in ("text", "json"):
        print(f"❌ Unknown --format '{sanitize_display(fmt)}'. Choose text or json.", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)

    root = require_repo()
    current = read_current_branch(root)
    heads_dir = root / ".muse" / "refs" / "heads"

    # ------------------------------------------------------------------
    # DELETE / FORCE-DELETE
    # ------------------------------------------------------------------
    if op in ("delete", "force_delete"):
        if not positional:
            print("❌ Usage: muse branch -d|-D <branch> …", file=sys.stderr)
            raise SystemExit(ExitCode.USER_ERROR)
        force = op == "force_delete"
        for branch_name in positional:
            try:
                validate_branch_name(branch_name)
            except ValueError as exc:
                print(f"❌ Invalid branch name: {exc}", file=sys.stderr)
                raise SystemExit(ExitCode.USER_ERROR)
            if branch_name == current:
                print(
                    f"❌ Cannot delete the currently checked-out branch "
                    f"'{sanitize_display(branch_name)}'.",
                    file=sys.stderr,
                )
                raise SystemExit(ExitCode.USER_ERROR)
            rf = _ref_file(root, branch_name)
            if not rf.is_file():
                print(f"❌ Branch '{sanitize_display(branch_name)}' not found.", file=sys.stderr)
                raise SystemExit(ExitCode.USER_ERROR)
            if not force and not _is_merged(root, branch_name, current):
                print(
                    f"❌ Branch '{sanitize_display(branch_name)}' is not fully merged.\n"
                    f"   Use -D to force-delete.",
                    file=sys.stderr,
                )
                raise SystemExit(ExitCode.USER_ERROR)
            tip = rf.read_text().strip()
            rf.unlink()
            _cleanup_empty_dirs(rf, heads_dir)
            if fmt == "json":
                print(json.dumps({"action": "deleted", "branch": branch_name, "was": tip}))
            else:
                short = tip[:8] if tip else "unknown"
                print(
                    f"Deleted branch {_c(sanitize_display(branch_name), _RED, tty=tty)} "
                    f"({_c('was ' + short, _DIM, tty=tty)})."
                )
        return

    # ------------------------------------------------------------------
    # RENAME / FORCE-RENAME
    # ------------------------------------------------------------------
    if op in ("rename", "force_rename"):
        force = op == "force_rename"
        if len(positional) == 1:
            old_name, new_name = current, positional[0]
        elif len(positional) == 2:
            old_name, new_name = positional[0], positional[1]
        else:
            print("❌ Usage: muse branch -m|-M [<old>] <new>", file=sys.stderr)
            raise SystemExit(ExitCode.USER_ERROR)
        for n in (old_name, new_name):
            try:
                validate_branch_name(n)
            except ValueError as exc:
                print(f"❌ Invalid branch name: {exc}", file=sys.stderr)
                raise SystemExit(ExitCode.USER_ERROR)
        src = _ref_file(root, old_name)
        dst = _ref_file(root, new_name)
        if not src.is_file():
            print(f"❌ Branch '{sanitize_display(old_name)}' not found.", file=sys.stderr)
            raise SystemExit(ExitCode.USER_ERROR)
        if dst.is_file() and not force:
            print(
                f"❌ Branch '{sanitize_display(new_name)}' already exists. Use -M to force.",
                file=sys.stderr,
            )
            raise SystemExit(ExitCode.USER_ERROR)
        tip = src.read_text().strip()
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(tip)
        src.unlink()
        _cleanup_empty_dirs(src, heads_dir)
        if old_name == current:
            (root / ".muse" / "HEAD").write_text(f"ref: refs/heads/{new_name}\n")
        if fmt == "json":
            print(json.dumps({"action": "renamed", "from": old_name, "to": new_name}))
        else:
            print(
                f"Renamed branch "
                f"{_c(sanitize_display(old_name), _YELLOW, tty=tty)} → "
                f"{_c(sanitize_display(new_name), _GREEN, tty=tty)}."
            )
        return

    # ------------------------------------------------------------------
    # COPY / FORCE-COPY
    # ------------------------------------------------------------------
    if op in ("copy", "force_copy"):
        force = op == "force_copy"
        if len(positional) == 1:
            src_name, dst_name = current, positional[0]
        elif len(positional) == 2:
            src_name, dst_name = positional[0], positional[1]
        else:
            print("❌ Usage: muse branch -c|-C [<src>] <dest>", file=sys.stderr)
            raise SystemExit(ExitCode.USER_ERROR)
        for n in (src_name, dst_name):
            try:
                validate_branch_name(n)
            except ValueError as exc:
                print(f"❌ Invalid branch name: {exc}", file=sys.stderr)
                raise SystemExit(ExitCode.USER_ERROR)
        src = _ref_file(root, src_name)
        dst = _ref_file(root, dst_name)
        if not src.is_file():
            print(f"❌ Branch '{sanitize_display(src_name)}' not found.", file=sys.stderr)
            raise SystemExit(ExitCode.USER_ERROR)
        if dst.is_file() and not force:
            print(
                f"❌ Branch '{sanitize_display(dst_name)}' already exists. Use -C to force.",
                file=sys.stderr,
            )
            raise SystemExit(ExitCode.USER_ERROR)
        tip = src.read_text().strip()
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(tip)
        if fmt == "json":
            print(json.dumps({"action": "copied", "from": src_name, "to": dst_name}))
        else:
            print(
                f"Copied branch "
                f"{_c(sanitize_display(src_name), _YELLOW, tty=tty)} → "
                f"{_c(sanitize_display(dst_name), _GREEN, tty=tty)}."
            )
        return

    # ------------------------------------------------------------------
    # CREATE
    # ------------------------------------------------------------------
    if op is None and positional:
        new_name = positional[0]
        start_point = positional[1] if len(positional) > 1 else None
        try:
            validate_branch_name(new_name)
        except ValueError as exc:
            print(f"❌ Invalid branch name: {exc}", file=sys.stderr)
            raise SystemExit(ExitCode.USER_ERROR)
        rf = _ref_file(root, new_name)
        if rf.is_file():
            print(f"❌ Branch '{sanitize_display(new_name)}' already exists.", file=sys.stderr)
            raise SystemExit(ExitCode.USER_ERROR)
        if start_point:
            sp_tip = get_head_commit_id(root, start_point) or start_point
        else:
            sp_tip = get_head_commit_id(root, current) or ""
        rf.parent.mkdir(parents=True, exist_ok=True)
        rf.write_text(sp_tip)
        if fmt == "json":
            print(json.dumps({"action": "created", "branch": new_name, "commit_id": sp_tip}))
        else:
            print(f"Created branch {_c(sanitize_display(new_name), _GREEN, tty=tty)}.")
        return

    # ------------------------------------------------------------------
    # LIST
    # ------------------------------------------------------------------
    local_branches = _list_local_branches(root)
    if remotes_only:
        display_branches = [f"remotes/{b}" for b in _list_remotes(root)]
    elif all_branches:
        display_branches = local_branches + [f"remotes/{b}" for b in _list_remotes(root)]
    else:
        display_branches = list(local_branches)

    # --merged / --no-merged / --contains filters
    if merged_into or not_merged_into or contains_commit:
        resolved_current = current

        def _passes(b: str) -> bool:
            local_b = b.removeprefix("remotes/")
            if merged_into:
                into = resolved_current if merged_into == "HEAD" else merged_into
                if not _is_merged(root, local_b, into):
                    return False
            if not_merged_into:
                into = resolved_current if not_merged_into == "HEAD" else not_merged_into
                if _is_merged(root, local_b, into):
                    return False
            if contains_commit:
                if not _contains_commit(root, local_b, contains_commit):
                    return False
            return True

        display_branches = [b for b in display_branches if _passes(b)]

    if fmt == "json":
        result: list[dict[str, str | bool]] = []
        for b in display_branches:
            local_b = b.removeprefix("remotes/")
            commit_id = get_head_commit_id(root, local_b) or ""
            result.append({"name": b, "current": local_b == current, "commit_id": commit_id})
        print(json.dumps(result))
        return

    for b in display_branches:
        is_remote_entry = b.startswith("remotes/")
        local_b = b.removeprefix("remotes/")
        is_current = (local_b == current) and not is_remote_entry
        marker = _c("* ", _GREEN, tty=tty) if is_current else "  "
        name_str = _c(sanitize_display(b), _GREEN, tty=tty) if is_current else sanitize_display(b)
        if verbose >= 1:
            commit_id = get_head_commit_id(root, local_b) or ""
            short = commit_id[:8] if commit_id else "(empty)"
            rec = read_commit(root, commit_id) if commit_id else None
            msg = sanitize_display(rec.message.splitlines()[0][:48]) if rec else ""
            if verbose >= 2:
                upstream = _upstream_for(root, local_b)
                up_str = (
                    f" [{_c(sanitize_display(upstream), _CYAN, tty=tty)}]"
                    if upstream else ""
                )
                print(f"{marker}{name_str}  {_c(short, _YELLOW, tty=tty)}{up_str} {msg}")
            else:
                print(f"{marker}{name_str}  {_c(short, _YELLOW, tty=tty)} {msg}")
        else:
            print(f"{marker}{name_str}")

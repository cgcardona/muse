"""muse status — show working-tree drift against HEAD.

Output modes
------------

Default (color when stdout is a TTY)::

    On branch main

    Changes since last commit:
      (use "muse commit -m <msg>" to record changes)

            modified: tracks/drums.mid
            new file: tracks/lead.mp3
            deleted:  tracks/scratch.mid

--short (color letter prefix when stdout is a TTY)::

    M tracks/drums.mid
    A tracks/lead.mp3
    D tracks/scratch.mid

--porcelain (machine-readable, stable for scripting — no color ever)::

    ## main
     M tracks/drums.mid
     A tracks/lead.mp3
     D tracks/scratch.mid

Color convention
----------------
- yellow  modified  — file exists in both old and new snapshot, content changed
- green   new file  — file is new, not present in last commit
- red     deleted   — file was removed since last commit
"""

from __future__ import annotations

import argparse
import json
import logging
import pathlib
import sys

from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.store import get_head_snapshot_manifest, read_current_branch
from muse.domain import SnapshotManifest
from muse.plugins.registry import resolve_plugin_by_domain

logger = logging.getLogger(__name__)

_YELLOW = "\033[33m"
_GREEN  = "\033[32m"
_RED    = "\033[31m"
_BOLD   = "\033[1m"
_RESET  = "\033[0m"


def _color(text: str, ansi: str, is_tty: bool) -> str:
    return f"{_BOLD}{ansi}{text}{_RESET}" if is_tty else text


def _read_repo_meta(root: pathlib.Path) -> tuple[str, str]:
    """Read ``.muse/repo.json`` once and return ``(repo_id, domain)``.

        Returns sensible defaults on any read or parse failure rather than
        propagating an unhandled exception to the user.  The caller never needs
        to guard against a missing or corrupt ``repo.json`` — status degrades
        gracefully to an empty diff in the worst case.

    """
    repo_json = root / ".muse" / "repo.json"
    try:
        data = json.loads(repo_json.read_text(encoding="utf-8"))
        repo_id_raw = data.get("repo_id", "")
        repo_id = str(repo_id_raw) if isinstance(repo_id_raw, str) and repo_id_raw else ""
        domain_raw = data.get("domain", "")
        domain = str(domain_raw) if isinstance(domain_raw, str) and domain_raw else "midi"
        return repo_id, domain
    except (OSError, json.JSONDecodeError):
        return "", "midi"


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the status subcommand."""
    parser = subparsers.add_parser(
        "status",
        help="Show working-tree drift against HEAD.",
        description=__doc__,
    )
    parser.add_argument("--short", "-s", action="store_true", help="Condensed output.")
    parser.add_argument("--porcelain", action="store_true", help="Machine-readable output (no color).")
    parser.add_argument("--branch", "-b", action="store_true", dest="branch_only", help="Show branch info only.")
    parser.add_argument("--format", "-f", dest="fmt", default="text", metavar="FORMAT", help="Output format: text or json.")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> None:
    """Show working-tree drift against HEAD."""
    from muse.core.validation import sanitize_display as _sd

    fmt: str = args.fmt
    short: bool = args.short
    porcelain: bool = args.porcelain
    branch_only: bool = args.branch_only

    if fmt not in ("text", "json"):
        print(f"❌ Unknown --format '{_sd(fmt)}'. Choose text or json.", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)

    root = require_repo()
    try:
        branch = read_current_branch(root)
    except ValueError as exc:
        print(f"fatal: {exc}", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)

    repo_id, domain = _read_repo_meta(root)

    if fmt != "json":
        if porcelain:
            print(f"## {branch}")
        elif not short:
            print(f"On branch {branch}")

    if branch_only:
        if fmt == "json":
            print(json.dumps({"branch": branch}))
        return

    is_tty = sys.stdout.isatty() and not porcelain and fmt != "json"

    head_manifest = get_head_snapshot_manifest(root, repo_id, branch) or {}
    plugin = resolve_plugin_by_domain(domain)
    committed_snap = SnapshotManifest(files=head_manifest, domain=domain)
    report = plugin.drift(committed_snap, root)
    delta = report.delta

    added: set[str] = {op["address"] for op in delta["ops"] if op["op"] == "insert"}
    modified: set[str] = {op["address"] for op in delta["ops"] if op["op"] in ("replace", "patch")}
    deleted: set[str] = {op["address"] for op in delta["ops"] if op["op"] == "delete"}

    clean = not (added or modified or deleted)

    if fmt == "json":
        print(json.dumps({
            "branch": branch,
            "clean": clean,
            "added": sorted(added),
            "modified": sorted(modified),
            "deleted": sorted(deleted),
        }))
        return

    if clean:
        if not short and not porcelain:
            print("\nNothing to commit, working tree clean")
        return

    if porcelain:
        for p in sorted(modified):
            print(f" M {p}")
        for p in sorted(added):
            print(f" A {p}")
        for p in sorted(deleted):
            print(f" D {p}")
        return

    if short:
        for p in sorted(modified):
            print(f" {_color('M', _YELLOW, is_tty)} {p}")
        for p in sorted(added):
            print(f" {_color('A', _GREEN, is_tty)} {p}")
        for p in sorted(deleted):
            print(f" {_color('D', _RED, is_tty)} {p}")
        return

    print("\nChanges since last commit:")
    print('  (use "muse commit -m <msg>" to record changes)\n')
    for p in sorted(modified):
        print(f"\t{_color('    modified:', _YELLOW, is_tty)} {p}")
    for p in sorted(added):
        print(f"\t{_color('    new file:', _GREEN, is_tty)} {p}")
    for p in sorted(deleted):
        print(f"\t{_color('     deleted:', _RED, is_tty)} {p}")

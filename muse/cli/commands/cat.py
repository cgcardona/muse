"""muse cat — print the source of a specific symbol from HEAD or any commit.

Address format::

    muse cat cache.py::LRUCache.get
    muse cat cache.py::LRUCache.get --at abc123
    muse cat cache.py::LRUCache.get --at v0.1.4

The ``::`` separator is the same format used throughout Muse's symbol graph.
The right-hand side is matched against the symbol's ``qualified_name`` first,
then ``name`` (allowing short references like ``get`` when unambiguous).

Exit codes
----------
0   Symbol found and printed.
1   Address malformed, symbol not found, or file not tracked.
3   I/O error reading from the object store.
"""

from __future__ import annotations

import argparse
import json
import logging
import pathlib
import sys

from muse.core.errors import ExitCode
from muse.core.object_store import read_object
from muse.core.repo import require_repo
from muse.core.store import (
    get_commit_snapshot_manifest,
    get_head_snapshot_manifest,
    read_current_branch,
    resolve_commit_ref,
)
from muse.core.validation import sanitize_display
from muse.plugins.code.ast_parser import adapter_for_path
from muse.plugins.registry import read_domain

logger = logging.getLogger(__name__)


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the cat subcommand."""
    parser = subparsers.add_parser(
        "cat",
        help="Print the source code of a single symbol.",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "address",
        help="Symbol address: 'file.py::ClassName.method' or 'file.py::function_name'.",
    )
    parser.add_argument(
        "--at", default=None,
        help="Commit ref (SHA, branch, tag) to read from. Defaults to HEAD.",
    )
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> None:
    """Print the source code of a single symbol.

    Address format: ``file.py::ClassName.method`` — the same ``::`` separator
    used throughout Muse's symbol graph.  The right side is matched against
    ``qualified_name`` first, then ``name`` when unambiguous.
    """
    address: str = args.address
    at: str | None = args.at

    if "::" not in address:
        print(
            "❌ Address must contain '::' separator, e.g. cache.py::LRUCache.get",
            file=sys.stderr,
        )
        raise SystemExit(ExitCode.USER_ERROR)

    file_path, _, symbol_ref = address.partition("::")

    root = require_repo()
    repo_id = str(json.loads((root / ".muse" / "repo.json").read_text())["repo_id"])
    branch = read_current_branch(root)
    domain = read_domain(root)

    if domain != "code":
        print(f"❌ muse cat requires the code domain (current domain: {domain})", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)

    # Resolve snapshot manifest for the requested ref.
    manifest: dict[str, str]
    if at is None:
        manifest = get_head_snapshot_manifest(root, repo_id, branch) or {}
    else:
        resolved = resolve_commit_ref(root, repo_id, branch, at)
        if resolved is None:
            print(f"❌ Ref not found: {sanitize_display(at)}", file=sys.stderr)
            raise SystemExit(ExitCode.USER_ERROR)
        manifest = get_commit_snapshot_manifest(root, resolved.commit_id) or {}

    if file_path not in manifest:
        print(f"❌ File not tracked in snapshot: {sanitize_display(file_path)}", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)

    raw = read_object(root, manifest[file_path])
    if raw is None:
        print(f"❌ Blob not found in object store: {manifest[file_path][:12]}", file=sys.stderr)
        raise SystemExit(ExitCode.INTERNAL_ERROR)

    try:
        text = raw.decode("utf-8", errors="replace")
    except Exception:
        print("❌ File is not valid UTF-8.", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)

    # Parse symbol tree using the file-appropriate adapter.
    adapter = adapter_for_path(file_path)
    tree = adapter.parse_symbols(raw, file_path)

    if not tree:
        print(f"❌ No symbols found in {sanitize_display(file_path)}", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)

    # Match against qualified_name first, then fall back to plain name.
    match = next(
        (rec for rec in tree.values() if rec["qualified_name"] == symbol_ref),
        None,
    )
    if match is None:
        candidates = [rec for rec in tree.values() if rec["name"] == symbol_ref]
        if len(candidates) == 1:
            match = candidates[0]
        elif len(candidates) > 1:
            names = ", ".join(rec["qualified_name"] for rec in candidates)
            print(
                f"❌ Ambiguous symbol '{sanitize_display(symbol_ref)}'. "
                f"Qualify it: {names}",
                file=sys.stderr,
            )
            raise SystemExit(ExitCode.USER_ERROR)

    if match is None:
        available = ", ".join(sorted(rec["qualified_name"] for rec in tree.values()))
        print(
            f"❌ Symbol '{sanitize_display(symbol_ref)}' not found in "
            f"{sanitize_display(file_path)}.\n"
            f"   Available: {available}",
            file=sys.stderr,
        )
        raise SystemExit(ExitCode.USER_ERROR)

    # Slice source lines (SymbolRecord lineno is 1-indexed).
    lines = text.splitlines()
    start = max(0, match["lineno"] - 1)
    end = min(len(lines), match["end_lineno"])

    ref_label = sanitize_display(at) if at else "HEAD"
    print(
        f"# {file_path}::{match['qualified_name']}"
        f"  L{match['lineno']}–{match['end_lineno']}  ({ref_label})"
    )
    print("\n".join(lines[start:end]))

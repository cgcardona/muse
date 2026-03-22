"""muse checkout-symbol — restore a historical version of a specific symbol.

Extracts a single named symbol from a historical committed snapshot and writes
it back into the current working-tree file, replacing the current version of
that symbol.

This is a **surgical** operation: only the target symbol's lines change.
All surrounding code — other symbols, comments, imports, blank lines outside
the symbol boundary — is left untouched.

Why this matters
----------------
Git's ``checkout`` restores entire files.  If you need to roll back a single
function while keeping everything else current, you need to manually cherry-
pick lines.  ``muse checkout-symbol`` does this atomically against Muse's
content-addressed symbol index.

Security note: the file path component of ADDRESS is validated via
``contain_path()`` before any disk access.  Paths that escape the repo root
(e.g. ``../../etc/passwd::foo``) are rejected with exit 1.

Usage::

    muse checkout-symbol "src/billing.py::compute_invoice_total" --commit HEAD~3
    muse checkout-symbol "src/auth.py::validate_token" --commit abc12345 --dry-run
    muse checkout-symbol "src/billing.py::compute_invoice_total" --commit HEAD~3 --json

Output (without --dry-run)::

    Restoring: src/billing.py::compute_invoice_total
      from commit: abc12345 (2026-02-15)
      lines 42–67 → replaced with 31 historical lines
    ✅ Written to src/billing.py

Output (with --dry-run)::

    Dry run — no files will be written.

    Restoring: src/billing.py::compute_invoice_total
      from commit: abc12345 (2026-02-15)

    --- current
    +++ historical
    @@ -42,26 +42,20 @@
     def compute_invoice_total(...):
    -    ...current body...
    +    ...historical body...

JSON output (``--json``)::

    {
      "address": "src/billing.py::compute_invoice_total",
      "file": "src/billing.py",
      "restored_from": "abc12345",
      "dry_run": false
    }

Flags:

``--commit, -c REF``
    Required. Commit to restore from.

``--dry-run``
    Print the diff without writing anything.

``--json``
    Emit result as JSON for agent consumption.
"""

from __future__ import annotations

import argparse
import difflib
import json
import logging
import pathlib
import sys

from muse.core.errors import ExitCode
from muse.core.object_store import read_object
from muse.core.repo import require_repo
from muse.core.store import get_commit_snapshot_manifest, read_current_branch, resolve_commit_ref
from muse.core.validation import contain_path
from muse.plugins.code.ast_parser import parse_symbols

logger = logging.getLogger(__name__)


def _read_repo_id(root: pathlib.Path) -> str:
    return str(json.loads((root / ".muse" / "repo.json").read_text())["repo_id"])


def _read_branch(root: pathlib.Path) -> str:
    return read_current_branch(root)


def _extract_lines(source: bytes, lineno: int, end_lineno: int) -> list[str]:
    """Extract lines *lineno*..*end_lineno* (1-indexed, inclusive) as a list."""
    all_lines = source.decode("utf-8", errors="replace").splitlines(keepends=True)
    return all_lines[lineno - 1:end_lineno]


def _find_current_symbol_lines(
    working_tree_file: pathlib.Path,
    address: str,
) -> tuple[int, int] | None:
    """Return (lineno, end_lineno) for *address* in the current working-tree file.

    Returns ``None`` if the symbol is not found.
    """
    if not working_tree_file.exists():
        return None
    raw = working_tree_file.read_bytes()
    tree = parse_symbols(raw, str(working_tree_file))
    rec = tree.get(address)
    if rec is None:
        return None
    return rec["lineno"], rec["end_lineno"]


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the checkout-symbol subcommand."""
    parser = subparsers.add_parser(
        "checkout-symbol",
        help="Restore a historical version of a specific symbol into the working tree.",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "address", metavar="ADDRESS",
        help='Symbol address, e.g. "src/billing.py::compute_invoice_total".',
    )
    parser.add_argument(
        "--commit", "-c", required=True, metavar="REF", dest="ref",
        help="Commit to restore the symbol from (required).",
    )
    parser.add_argument(
        "--dry-run", action="store_true", dest="dry_run",
        help="Print the diff without writing anything.",
    )
    parser.add_argument(
        "--json", action="store_true", dest="as_json",
        help="Emit result as JSON for agent consumption.",
    )
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> None:
    """Restore a historical version of a specific symbol into the working tree.

    Extracts the symbol body from the given historical commit and splices it
    into the current working-tree file at the symbol's current location.
    Only the target symbol's lines change; everything else is left untouched.

    If the symbol does not exist at ``--commit``, the command exits with an
    error.  If the symbol does not exist in the current working tree (perhaps
    it was deleted), the historical version is appended to the end of the file.

    The file path component of ADDRESS is validated against the repo root —
    path-traversal addresses (e.g. ``../../etc/passwd::foo``) are rejected.
    """
    address: str = args.address
    ref: str = args.ref
    dry_run: bool = args.dry_run
    as_json: bool = args.as_json

    root = require_repo()
    repo_id = _read_repo_id(root)
    branch = _read_branch(root)

    if "::" not in address:
        print("❌ ADDRESS must be a symbol address like 'src/billing.py::func'.", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)

    file_rel, sym_qualified = address.split("::", 1)

    # Validate the file path stays inside the repo root.
    try:
        contain_path(root, file_rel)
    except ValueError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)

    commit = resolve_commit_ref(root, repo_id, branch, ref)
    if commit is None:
        print(f"❌ Commit '{ref}' not found.", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)

    # Read the historical blob.
    manifest = get_commit_snapshot_manifest(root, commit.commit_id) or {}
    obj_id = manifest.get(file_rel)
    if obj_id is None:
        print(
            f"❌ '{file_rel}' is not in snapshot {commit.commit_id[:8]}.", file=sys.stderr
        )
        raise SystemExit(ExitCode.USER_ERROR)

    historical_raw = read_object(root, obj_id)
    if historical_raw is None:
        print(f"❌ Blob {obj_id[:8]} missing from object store.", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)

    # Find the symbol in the historical blob.
    hist_tree = parse_symbols(historical_raw, file_rel)
    hist_rec = hist_tree.get(address)
    if hist_rec is None:
        print(
            f"❌ Symbol '{address}' not found in commit {commit.commit_id[:8]}.", file=sys.stderr
        )
        raise SystemExit(ExitCode.USER_ERROR)

    historical_lines = _extract_lines(
        historical_raw, hist_rec["lineno"], hist_rec["end_lineno"]
    )

    # working_file is already validated as within root by contain_path above.
    working_file = root / file_rel
    current_lines = working_file.read_bytes().decode("utf-8", errors="replace").splitlines(
        keepends=True
    ) if working_file.exists() else []

    current_sym_range = _find_current_symbol_lines(working_file, address)

    if current_sym_range is not None:
        cur_start, cur_end = current_sym_range
        new_lines = current_lines[:cur_start - 1] + historical_lines + current_lines[cur_end:]
    else:
        new_lines = current_lines + ["\n"] + historical_lines

    if dry_run:
        if as_json:
            print(json.dumps({
                "address": address,
                "file": file_rel,
                "restored_from": commit.commit_id[:8],
                "dry_run": True,
            }, indent=2))
            return
        print("Dry run — no files will be written.\n")
        print(f"Restoring: {address}")
        print(f"  from commit: {commit.commit_id[:8]} ({commit.committed_at.date()})")
        diff = difflib.unified_diff(
            current_lines,
            new_lines,
            fromfile="current",
            tofile="historical",
            lineterm="",
        )
        print("\n" + "".join(diff))
        return

    if not as_json:
        print(f"Restoring: {address}")
        print(f"  from commit: {commit.commit_id[:8]} ({commit.committed_at.date()})")
        if current_sym_range is not None:
            cur_start, cur_end = current_sym_range
            print(
                f"  lines {cur_start}–{cur_end} → replaced with "
                f"{len(historical_lines)} historical line(s)"
            )
        else:
            print("  symbol not found in working tree — appending at end of file")

    # Write the patched file.
    working_file.write_text("".join(new_lines), encoding="utf-8")

    if as_json:
        print(json.dumps({
            "address": address,
            "file": file_rel,
            "restored_from": commit.commit_id[:8],
            "dry_run": False,
        }, indent=2))
        return

    print(f"✅ Written to {file_rel}")

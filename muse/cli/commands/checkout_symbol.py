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

Usage::

    muse checkout-symbol "src/billing.py::compute_invoice_total" --commit HEAD~3
    muse checkout-symbol "src/auth.py::validate_token" --commit abc12345 --dry-run

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

Flags:

``--commit, -c REF``
    Required. Commit to restore from.

``--dry-run``
    Print the diff without writing anything.
"""
from __future__ import annotations

import difflib
import json
import logging
import pathlib

import typer

from muse.core.errors import ExitCode
from muse.core.object_store import read_object
from muse.core.repo import require_repo
from muse.core.store import get_commit_snapshot_manifest, resolve_commit_ref
from muse.plugins.code.ast_parser import parse_symbols

logger = logging.getLogger(__name__)

app = typer.Typer()


def _read_repo_id(root: pathlib.Path) -> str:
    return str(json.loads((root / ".muse" / "repo.json").read_text())["repo_id"])


def _read_branch(root: pathlib.Path) -> str:
    head_ref = (root / ".muse" / "HEAD").read_text().strip()
    return head_ref.removeprefix("refs/heads/").strip()


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


@app.callback(invoke_without_command=True)
def checkout_symbol(
    ctx: typer.Context,
    address: str = typer.Argument(
        ..., metavar="ADDRESS",
        help='Symbol address, e.g. "src/billing.py::compute_invoice_total".',
    ),
    ref: str = typer.Option(
        ..., "--commit", "-c", metavar="REF",
        help="Commit to restore the symbol from (required).",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run",
        help="Print the diff without writing anything.",
    ),
) -> None:
    """Restore a historical version of a specific symbol into the working tree.

    Extracts the symbol body from the given historical commit and splices it
    into the current working-tree file at the symbol's current location.
    Only the target symbol's lines change; everything else is left untouched.

    If the symbol does not exist at ``--commit``, the command exits with an
    error.  If the symbol does not exist in the current working tree (perhaps
    it was deleted), the historical version is appended to the end of the file.
    """
    root = require_repo()
    repo_id = _read_repo_id(root)
    branch = _read_branch(root)

    if "::" not in address:
        typer.echo("❌ ADDRESS must be a symbol address like 'src/billing.py::func'.", err=True)
        raise typer.Exit(code=ExitCode.USER_ERROR)

    file_rel, sym_qualified = address.split("::", 1)

    commit = resolve_commit_ref(root, repo_id, branch, ref)
    if commit is None:
        typer.echo(f"❌ Commit '{ref}' not found.", err=True)
        raise typer.Exit(code=ExitCode.USER_ERROR)

    # Read the historical blob.
    manifest = get_commit_snapshot_manifest(root, commit.commit_id) or {}
    obj_id = manifest.get(file_rel)
    if obj_id is None:
        typer.echo(
            f"❌ '{file_rel}' is not in snapshot {commit.commit_id[:8]}.", err=True
        )
        raise typer.Exit(code=ExitCode.USER_ERROR)

    historical_raw = read_object(root, obj_id)
    if historical_raw is None:
        typer.echo(f"❌ Blob {obj_id[:8]} missing from object store.", err=True)
        raise typer.Exit(code=ExitCode.USER_ERROR)

    # Find the symbol in the historical blob.
    hist_tree = parse_symbols(historical_raw, file_rel)
    hist_rec = hist_tree.get(address)
    if hist_rec is None:
        typer.echo(
            f"❌ Symbol '{address}' not found in commit {commit.commit_id[:8]}.", err=True
        )
        raise typer.Exit(code=ExitCode.USER_ERROR)

    historical_lines = _extract_lines(
        historical_raw, hist_rec["lineno"], hist_rec["end_lineno"]
    )

    # Find the symbol in the current working tree.
    working_file = root / file_rel
    current_lines = working_file.read_bytes().decode("utf-8", errors="replace").splitlines(
        keepends=True
    ) if working_file.exists() else []

    current_sym_range = _find_current_symbol_lines(working_file, address)

    if dry_run:
        typer.echo("Dry run — no files will be written.\n")

    typer.echo(f"Restoring: {address}")
    typer.echo(f"  from commit: {commit.commit_id[:8]} ({commit.committed_at.date()})")

    if current_sym_range is not None:
        cur_start, cur_end = current_sym_range
        typer.echo(
            f"  lines {cur_start}–{cur_end} → replaced with "
            f"{len(historical_lines)} historical line(s)"
        )
        new_lines = current_lines[:cur_start - 1] + historical_lines + current_lines[cur_end:]
    else:
        typer.echo(f"  symbol not found in working tree — appending at end of file")
        new_lines = current_lines + ["\n"] + historical_lines

    if dry_run:
        # Show unified diff.
        diff = difflib.unified_diff(
            current_lines,
            new_lines,
            fromfile="current",
            tofile="historical",
            lineterm="",
        )
        typer.echo("\n" + "".join(diff))
        return

    # Write the patched file.
    working_file.write_text("".join(new_lines), encoding="utf-8")
    typer.echo(f"✅ Written to {file_rel}")

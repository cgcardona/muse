"""muse semantic-cherry-pick — cherry-pick specific symbols, not files.

Extracts named symbols from a source commit and applies them to the current
working tree, replacing only those symbols.  All other code is left untouched.

This is the semantic counterpart to ``git cherry-pick``, which operates at the
file-hunk level.  ``muse semantic-cherry-pick`` operates at the symbol level:
you name the exact functions, classes, or methods you want to bring forward.

Multiple symbols can be cherry-picked in a single invocation.  They are
applied left-to-right.  If any symbol fails to apply, the remaining are
skipped and the error is reported.

Usage::

    muse semantic-cherry-pick "src/billing.py::compute_total" --from abc12345
    muse semantic-cherry-pick \\
        "src/auth.py::validate_token" \\
        "src/auth.py::refresh_token" \\
        --from feature-branch
    muse semantic-cherry-pick "src/core.py::hash_content" --from HEAD~5 --dry-run
    muse semantic-cherry-pick "src/billing.py::Invoice.pay" --from v1.0 --json

Output::

    Semantic cherry-pick from commit abc12345
    ──────────────────────────────────────────────────────────────

    ✅  src/auth.py::validate_token       applied  (lines 12–34 → 12–29)
    ✅  src/auth.py::refresh_token        applied  (lines 36–58 → 36–52)
    ❌  src/billing.py::compute_total     not found in source commit

    2 applied, 1 failed

Flags:

``--from REF``
    Required. Commit or branch to cherry-pick from.

``--dry-run``
    Print what would change without writing anything.

``--json``
    Emit per-symbol results as JSON.
"""

from __future__ import annotations

import json
import logging
import pathlib
from typing import Literal

import typer

from muse.core.errors import ExitCode
from muse.core.object_store import read_object
from muse.core.repo import require_repo
from muse.core.store import get_commit_snapshot_manifest, read_current_branch, resolve_commit_ref
from muse.plugins.code.ast_parser import parse_symbols

logger = logging.getLogger(__name__)

app = typer.Typer()

ApplyStatus = Literal["applied", "not_found", "file_missing", "parse_error", "already_current"]


def _read_repo_id(root: pathlib.Path) -> str:
    return str(json.loads((root / ".muse" / "repo.json").read_text())["repo_id"])


def _read_branch(root: pathlib.Path) -> str:
    return read_current_branch(root)


class _PickResult:
    def __init__(
        self,
        address: str,
        status: ApplyStatus,
        detail: str = "",
        old_lines: int = 0,
        new_lines: int = 0,
    ) -> None:
        self.address = address
        self.status = status
        self.detail = detail
        self.old_lines = old_lines
        self.new_lines = new_lines

    def to_dict(self) -> dict[str, str | int]:
        return {
            "address": self.address,
            "status": self.status,
            "detail": self.detail,
            "old_lines": self.old_lines,
            "new_lines": self.new_lines,
        }


def _apply_symbol(
    root: pathlib.Path,
    address: str,
    src_manifest: dict[str, str],
    dry_run: bool,
) -> _PickResult:
    """Apply one symbol from *src_manifest* to the working tree."""
    if "::" not in address:
        return _PickResult(address, "not_found", "address has no '::' separator")

    file_rel = address.split("::")[0]

    # Read historical blob.
    obj_id = src_manifest.get(file_rel)
    if obj_id is None:
        return _PickResult(address, "file_missing", f"'{file_rel}' not in source snapshot")

    src_raw = read_object(root, obj_id)
    if src_raw is None:
        return _PickResult(address, "file_missing", f"blob {obj_id[:8]} missing")

    try:
        src_tree = parse_symbols(src_raw, file_rel)
    except Exception as exc:
        return _PickResult(address, "parse_error", str(exc))

    src_rec = src_tree.get(address)
    if src_rec is None:
        return _PickResult(address, "not_found", f"symbol not found in source commit")

    src_lines_list = src_raw.decode("utf-8", errors="replace").splitlines(keepends=True)
    src_symbol_lines = src_lines_list[src_rec["lineno"] - 1:src_rec["end_lineno"]]

    # Read current working tree.
    working_file = root / file_rel
    if not working_file.exists():
        # File doesn't exist in working tree — create it with just the symbol.
        if not dry_run:
            working_file.parent.mkdir(parents=True, exist_ok=True)
            working_file.write_text("".join(src_symbol_lines), encoding="utf-8")
        return _PickResult(address, "applied", "created file", 0, len(src_symbol_lines))

    current_text = working_file.read_text(encoding="utf-8", errors="replace")
    current_lines = current_text.splitlines(keepends=True)

    # Find the symbol in the current working tree.
    current_raw = current_text.encode("utf-8")
    try:
        current_tree = parse_symbols(current_raw, file_rel)
    except Exception as exc:
        return _PickResult(address, "parse_error", f"current file: {exc}")

    current_rec = current_tree.get(address)

    if current_rec is not None:
        # Check if already current (content_id matches).
        if current_rec["content_id"] == src_rec["content_id"]:
            return _PickResult(address, "already_current", "content identical", 0, 0)
        old_start = current_rec["lineno"] - 1
        old_end = current_rec["end_lineno"]
        old_count = old_end - old_start
        new_lines = current_lines[:old_start] + src_symbol_lines + current_lines[old_end:]
        detail = f"lines {current_rec['lineno']}–{current_rec['end_lineno']} → {len(src_symbol_lines)} lines"
    else:
        # Symbol not in current tree — append at end.
        new_lines = current_lines + ["\n"] + src_symbol_lines
        old_count = 0
        detail = "appended at end (symbol not found in current tree)"

    if not dry_run:
        working_file.write_text("".join(new_lines), encoding="utf-8")

    return _PickResult(address, "applied", detail, old_count, len(src_symbol_lines))


@app.callback(invoke_without_command=True)
def semantic_cherry_pick(
    ctx: typer.Context,
    addresses: list[str] = typer.Argument(
        ..., metavar="ADDRESS...",
        help='Symbol addresses to cherry-pick, e.g. "src/auth.py::validate_token".',
    ),
    from_ref: str = typer.Option(
        ..., "--from", metavar="REF",
        help="Commit or branch to cherry-pick symbols from (required).",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run",
        help="Print what would change without writing anything.",
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit per-symbol results as JSON."),
) -> None:
    """Cherry-pick specific named symbols from a historical commit.

    Extracts each listed symbol from the source commit and splices it into
    the current working-tree file at the symbol's current location.  Only
    the target symbol's lines change; all surrounding code is preserved.

    If the symbol does not exist in the current working tree, the historical
    version is appended to the end of the file.

    ``--dry-run`` shows what would change without writing anything.
    ``--json`` emits per-symbol results for machine consumption.
    """
    root = require_repo()
    repo_id = _read_repo_id(root)
    branch = _read_branch(root)

    if not addresses:
        typer.echo("❌ At least one ADDRESS is required.", err=True)
        raise typer.Exit(code=ExitCode.USER_ERROR)

    from_commit = resolve_commit_ref(root, repo_id, branch, from_ref)
    if from_commit is None:
        typer.echo(f"❌ --from ref '{from_ref}' not found.", err=True)
        raise typer.Exit(code=ExitCode.USER_ERROR)

    src_manifest = get_commit_snapshot_manifest(root, from_commit.commit_id) or {}

    results: list[_PickResult] = []
    for address in addresses:
        result = _apply_symbol(root, address, src_manifest, dry_run)
        results.append(result)

    if as_json:
        typer.echo(json.dumps(
            {
                "from_commit": from_commit.commit_id[:8],
                "dry_run": dry_run,
                "results": [r.to_dict() for r in results],
                "applied": sum(1 for r in results if r.status == "applied"),
                "failed": sum(1 for r in results if r.status not in ("applied", "already_current")),
                "already_current": sum(1 for r in results if r.status == "already_current"),
            },
            indent=2,
        ))
        return

    action = "Dry-run" if dry_run else "Semantic cherry-pick"
    typer.echo(f"\n{action} from commit {from_commit.commit_id[:8]}")
    typer.echo("─" * 62)

    max_addr = max(len(r.address) for r in results)
    applied = 0
    failed = 0

    for r in results:
        if r.status == "applied":
            icon = "✅"
            label = f"applied  ({r.detail})"
            applied += 1
        elif r.status == "already_current":
            icon = "ℹ️ "
            label = "already current — no change needed"
        else:
            icon = "❌"
            label = f"{r.status}  ({r.detail})"
            failed += 1
        typer.echo(f"\n  {icon}  {r.address:<{max_addr}}  {label}")

    typer.echo(f"\n  {applied} applied, {failed} failed")
    if dry_run:
        typer.echo("  (dry run — no files were written)")

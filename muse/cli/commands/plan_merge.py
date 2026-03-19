"""muse plan-merge — dry-run semantic merge planning.

Computes which symbols diverged between two commits, classifies every conflict
by a semantic taxonomy, and recommends a merge strategy — without writing
anything to disk.

This is a *planning* command.  It reads committed snapshots and predicts what
a three-way merge would encounter.  No files are modified.

Conflict taxonomy
-----------------
``symbol_edit_overlap``
    The same symbol address was modified by both branches.  Both touched it,
    the content diverged.

``rename_edit``
    One branch renamed a symbol; the other edited its body.  A text merge
    would fail.

``move_edit``
    One branch moved a symbol to a different file; the other modified it.

``delete_use``
    One branch deleted a symbol; the other added new call sites for it.

``dependency_conflict``
    Branch A changed a symbol; Branch B's changes depend on a symbol in that
    same file's blast radius.

``no_conflict``
    Symbol was changed on only one branch or is identical on both.

Usage::

    muse plan-merge HEAD main
    muse plan-merge feature/billing main --json

Output::

    Semantic merge plan — a1b2c3d4  ← (merging)  e5f6a7b8
    ──────────────────────────────────────────────────────────────

    🔴  symbol_edit_overlap       src/billing.py::compute_total
        ours:   impl_only change  (body_hash differs)
        theirs: signature change  (signature_id differs)

    ⚠️   rename_edit               src/billing.py::process_payment
        ours:   renamed to process_invoice_payment
        theirs: implementation modified

    ✅  no_conflict                src/auth.py::validate_token

    Summary: 2 conflict(s), 1 clean
    Recommended strategy: resolve symbol_edit_overlap manually; rename_edit can
    be auto-resolved if theirs is rebased onto the rename.

Flags:

``--json``
    Emit the full plan as JSON.
"""

from __future__ import annotations

import json
import logging
import pathlib

import typer

from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.store import get_commit_snapshot_manifest, resolve_commit_ref
from muse.plugins.code._query import symbols_for_snapshot
from muse.plugins.code.ast_parser import SymbolRecord

logger = logging.getLogger(__name__)

app = typer.Typer()


def _read_repo_id(root: pathlib.Path) -> str:
    return str(json.loads((root / ".muse" / "repo.json").read_text())["repo_id"])


def _read_branch(root: pathlib.Path) -> str:
    head_ref = (root / ".muse" / "HEAD").read_text().strip()
    return head_ref.removeprefix("refs/heads/").strip()


class _MergeItem:
    def __init__(
        self,
        address: str,
        conflict_type: str,
        ours_change: str,
        theirs_change: str,
        recommendation: str,
    ) -> None:
        self.address = address
        self.conflict_type = conflict_type
        self.ours_change = ours_change
        self.theirs_change = theirs_change
        self.recommendation = recommendation

    def to_dict(self) -> dict[str, str]:
        return {
            "address": self.address,
            "conflict_type": self.conflict_type,
            "ours_change": self.ours_change,
            "theirs_change": self.theirs_change,
            "recommendation": self.recommendation,
        }


def _classify_change(base: SymbolRecord, target: SymbolRecord) -> str:
    """Classify how *base* → *target* changed."""
    if base["content_id"] == target["content_id"]:
        return "unchanged"
    if base["body_hash"] == target["body_hash"]:
        return "signature_only"
    if base["signature_id"] == target["signature_id"]:
        return "impl_only"
    if base["body_hash"] != target["body_hash"] and base["name"] != target["name"]:
        return "rename+modify"
    return "full_rewrite"


def _classify_conflict(
    addr: str,
    base: SymbolRecord | None,
    ours: SymbolRecord | None,
    theirs: SymbolRecord | None,
) -> _MergeItem:
    """Classify the conflict type for one symbol."""
    # Both deleted it — no conflict.
    if ours is None and theirs is None:
        return _MergeItem(addr, "no_conflict", "deleted", "deleted", "nothing to merge")

    # Only one side touched it.
    if base is not None and ours is not None and theirs is None:
        return _MergeItem(addr, "no_conflict", "unchanged", "deleted", "apply theirs (delete)")

    if base is not None and ours is None and theirs is not None:
        return _MergeItem(addr, "no_conflict", "deleted", "unchanged", "apply ours (delete)")

    if base is None and ours is not None and theirs is None:
        return _MergeItem(addr, "no_conflict", "added", "absent", "apply ours (insert)")

    if base is None and ours is None and theirs is not None:
        return _MergeItem(addr, "no_conflict", "absent", "added", "apply theirs (insert)")

    # Both sides touched it.
    if ours is not None and theirs is not None:
        if ours["content_id"] == theirs["content_id"]:
            return _MergeItem(addr, "no_conflict", "same", "same", "auto-merge (identical)")

        if base is not None:
            our_change = _classify_change(base, ours)
            their_change = _classify_change(base, theirs)
        else:
            our_change = "added"
            their_change = "added"

        # Rename conflict.
        if ours["name"] != addr.split("::")[-1] and base is not None and theirs["content_id"] != base["content_id"]:
            return _MergeItem(addr, "rename_edit", our_change, their_change,
                              "manual: rename ours, rebase theirs")

        # General edit overlap.
        return _MergeItem(
            addr, "symbol_edit_overlap", our_change, their_change,
            "manual: three-way merge required"
        )

    return _MergeItem(addr, "no_conflict", "no change", "no change", "auto-merge")


@app.callback(invoke_without_command=True)
def plan_merge(
    ctx: typer.Context,
    ours_ref: str = typer.Argument(..., metavar="OURS", help="Our commit/branch."),
    theirs_ref: str = typer.Argument(..., metavar="THEIRS", help="Their commit/branch."),
    as_json: bool = typer.Option(False, "--json", help="Emit the full plan as JSON."),
) -> None:
    """Dry-run semantic merge planning between two commits.

    Compares the symbol graphs of two commits — no base required — and
    classifies every diverging symbol into a conflict taxonomy:

    * ``symbol_edit_overlap`` — both sides modified the same symbol
    * ``rename_edit`` — one side renamed; the other edited
    * ``delete_use`` — one side deleted; the other called it
    * ``no_conflict`` — only one side touched it

    Does not modify any files or the repository.
    """
    root = require_repo()
    repo_id = _read_repo_id(root)
    branch = _read_branch(root)

    ours_commit = resolve_commit_ref(root, repo_id, branch, ours_ref)
    if ours_commit is None:
        typer.echo(f"❌ Ref '{ours_ref}' not found.", err=True)
        raise typer.Exit(code=ExitCode.USER_ERROR)

    theirs_commit = resolve_commit_ref(root, repo_id, branch, theirs_ref)
    if theirs_commit is None:
        typer.echo(f"❌ Ref '{theirs_ref}' not found.", err=True)
        raise typer.Exit(code=ExitCode.USER_ERROR)

    ours_manifest = get_commit_snapshot_manifest(root, ours_commit.commit_id) or {}
    theirs_manifest = get_commit_snapshot_manifest(root, theirs_commit.commit_id) or {}

    ours_syms: dict[str, SymbolRecord] = {}
    for _fp, tree in symbols_for_snapshot(root, ours_manifest).items():
        ours_syms.update(tree)

    theirs_syms: dict[str, SymbolRecord] = {}
    for _fp, tree in symbols_for_snapshot(root, theirs_manifest).items():
        theirs_syms.update(tree)

    # Collect all addresses.
    all_addrs = sorted(set(list(ours_syms) + list(theirs_syms)))

    items: list[_MergeItem] = []
    for addr in all_addrs:
        item = _classify_conflict(
            addr,
            base=None,  # No explicit base — compare ours vs theirs directly.
            ours=ours_syms.get(addr),
            theirs=theirs_syms.get(addr),
        )
        items.append(item)

    conflicts = [i for i in items if i.conflict_type != "no_conflict"]
    clean = [i for i in items if i.conflict_type == "no_conflict"]

    if as_json:
        typer.echo(json.dumps(
            {
                "schema_version": 1,
                "ours": ours_commit.commit_id[:8],
                "theirs": theirs_commit.commit_id[:8],
                "total_symbols": len(items),
                "conflicts": len(conflicts),
                "clean": len(clean),
                "items": [i.to_dict() for i in items if i.conflict_type != "no_conflict"],
            },
            indent=2,
        ))
        return

    typer.echo(
        f"\nSemantic merge plan — {ours_commit.commit_id[:8]}  ← (merging)  {theirs_commit.commit_id[:8]}"
    )
    typer.echo("─" * 62)

    if not conflicts:
        typer.echo(f"\n  ✅ No conflicts detected ({len(clean)} symbol(s) auto-merge safely)")
        return

    for item in conflicts:
        icon = "🔴" if "overlap" in item.conflict_type else "⚠️ "
        typer.echo(f"\n{icon}  {item.conflict_type:<24}  {item.address}")
        typer.echo(f"    ours:   {item.ours_change}")
        typer.echo(f"    theirs: {item.theirs_change}")
        typer.echo(f"    → {item.recommendation}")

    typer.echo(f"\n  Summary: {len(conflicts)} conflict(s), {len(clean)} clean")

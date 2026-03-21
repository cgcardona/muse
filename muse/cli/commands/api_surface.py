"""muse api-surface — public API surface tracking.

Shows which symbols in a snapshot are part of the public API, and how the
public API changed between two commits.

A symbol is **public** when all of the following hold:

* ``kind`` is one of: ``function``, ``async_function``, ``class``,
  ``method``, ``async_method``
* ``name`` does not start with ``_`` (Python convention for private/internal)
* ``kind`` is not ``import``

Git cannot answer "what changed in the public API between v1.0 and v1.1?"
without an external diffing tool.  Muse answers this in O(1) against committed
snapshots — no checkout required, no working-tree needed.

Usage::

    muse api-surface
    muse api-surface --commit HEAD~5
    muse api-surface --diff main
    muse api-surface --language Python
    muse api-surface --json

With ``--diff REF``, shows a three-section report::

    Public API surface — commit a1b2c3d4  vs  commit e5f6a7b8
    ──────────────────────────────────────────────────────────────

    Added (3):
      + src/billing.py::compute_tax      function
      + src/auth.py::refresh_token       function
      + src/models.py::User.to_json      method

    Removed (1):
      - src/billing.py::compute_total    function

    Changed (2):
      ~ src/billing.py::Invoice.pay      method  (signature_change)
      ~ src/auth.py::validate_token      function  (impl_only)

Flags:

``--commit, -c REF``
    Show or compare from this commit (default: HEAD).

``--diff REF``
    Compare the commit from ``--commit`` against this ref.

``--language LANG``
    Filter to symbols in files of this language.

``--json``
    Emit results as JSON with a ``schema_version`` wrapper.
"""

from __future__ import annotations

import json
import logging
import pathlib

import typer

from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.store import get_commit_snapshot_manifest, read_current_branch, resolve_commit_ref
from muse.plugins.code._query import language_of, symbols_for_snapshot
from muse.plugins.code.ast_parser import SymbolRecord

logger = logging.getLogger(__name__)

app = typer.Typer()

_PUBLIC_KINDS: frozenset[str] = frozenset({
    "function", "async_function", "class", "method", "async_method",
})


def _read_repo_id(root: pathlib.Path) -> str:
    return str(json.loads((root / ".muse" / "repo.json").read_text())["repo_id"])


def _read_branch(root: pathlib.Path) -> str:
    return read_current_branch(root)


def _is_public(name: str, kind: str) -> bool:
    return kind in _PUBLIC_KINDS and not name.split(".")[-1].startswith("_")


def _public_symbols(
    root: pathlib.Path,
    manifest: dict[str, str],
    language_filter: str | None,
) -> dict[str, SymbolRecord]:
    """Return all public symbols from *manifest* as a flat address → SymbolRecord dict."""
    result: dict[str, SymbolRecord] = {}
    sym_map = symbols_for_snapshot(root, manifest, language_filter=language_filter)
    for _file, tree in sym_map.items():
        for address, rec in tree.items():
            if _is_public(rec["name"], rec["kind"]):
                result[address] = rec
    return result


def _classify_change(old: SymbolRecord, new: SymbolRecord) -> str:
    """Return a human-readable classification of what changed."""
    if old["content_id"] == new["content_id"]:
        return "unchanged"
    if old["signature_id"] != new["signature_id"]:
        if old["body_hash"] != new["body_hash"]:
            return "signature+impl"
        return "signature_change"
    return "impl_only"


class _ApiEntry:
    def __init__(self, address: str, rec: SymbolRecord, language: str) -> None:
        self.address = address
        self.rec = rec
        self.language = language

    def to_dict(self) -> dict[str, str]:
        return {
            "address": self.address,
            "kind": self.rec["kind"],
            "name": self.rec["name"],
            "qualified_name": self.rec["qualified_name"],
            "language": self.language,
            "content_id": self.rec["content_id"][:8],
            "signature_id": self.rec["signature_id"][:8],
            "body_hash": self.rec["body_hash"][:8],
        }


@app.callback(invoke_without_command=True)
def api_surface(
    ctx: typer.Context,
    ref: str | None = typer.Option(
        None, "--commit", "-c", metavar="REF",
        help="Show surface at this commit (default: HEAD).",
    ),
    diff_ref: str | None = typer.Option(
        None, "--diff", metavar="REF",
        help="Compare HEAD (or --commit) against this ref.",
    ),
    language: str | None = typer.Option(
        None, "--language", "-l", metavar="LANG",
        help="Filter to this language (Python, Go, Rust, …).",
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit results as JSON."),
) -> None:
    """Show the public API surface and how it changed between two commits.

    A symbol is public when its kind is function/class/method (not import) and
    its bare name does not start with ``_``.

    With ``--diff REF``, shows three sections: Added, Removed, Changed.
    Without ``--diff``, lists all public symbols at the given commit.

    This command runs against committed snapshots only — no working-tree
    parsing, no test execution.
    """
    root = require_repo()
    repo_id = _read_repo_id(root)
    branch = _read_branch(root)

    commit = resolve_commit_ref(root, repo_id, branch, ref)
    if commit is None:
        typer.echo(f"❌ Commit '{ref or 'HEAD'}' not found.", err=True)
        raise typer.Exit(code=ExitCode.USER_ERROR)

    manifest = get_commit_snapshot_manifest(root, commit.commit_id) or {}
    current_surface = _public_symbols(root, manifest, language)

    if diff_ref is None:
        # Just list the current surface.
        entries = [
            _ApiEntry(addr, rec, language_of(addr.split("::")[0]))
            for addr, rec in sorted(current_surface.items())
        ]
        if as_json:
            typer.echo(json.dumps(
                {
                    "schema_version": 1,
                    "commit": commit.commit_id[:8],
                    "language_filter": language,
                    "total": len(entries),
                    "symbols": [e.to_dict() for e in entries],
                },
                indent=2,
            ))
            return

        typer.echo(f"\nPublic API surface — commit {commit.commit_id[:8]}")
        if language:
            typer.echo(f"  (language: {language})")
        typer.echo("─" * 62)
        if not entries:
            typer.echo("  (no public symbols found)")
            return
        max_addr = max(len(e.address) for e in entries)
        for e in entries:
            typer.echo(f"  {e.address:<{max_addr}}  {e.rec['kind']}")
        typer.echo(f"\n  {len(entries)} public symbol(s)")
        return

    # Diff mode.
    base_commit = resolve_commit_ref(root, repo_id, branch, diff_ref)
    if base_commit is None:
        typer.echo(f"❌ Diff ref '{diff_ref}' not found.", err=True)
        raise typer.Exit(code=ExitCode.USER_ERROR)

    base_manifest = get_commit_snapshot_manifest(root, base_commit.commit_id) or {}
    base_surface = _public_symbols(root, base_manifest, language)

    added = {a: r for a, r in current_surface.items() if a not in base_surface}
    removed = {a: r for a, r in base_surface.items() if a not in current_surface}
    changed: dict[str, tuple[SymbolRecord, SymbolRecord, str]] = {}
    for addr in current_surface:
        if addr in base_surface:
            cls = _classify_change(base_surface[addr], current_surface[addr])
            if cls != "unchanged":
                changed[addr] = (base_surface[addr], current_surface[addr], cls)

    if as_json:
        typer.echo(json.dumps(
            {
                "schema_version": 1,
                "commit": commit.commit_id[:8],
                "base_commit": base_commit.commit_id[:8],
                "language_filter": language,
                "added": [
                    _ApiEntry(a, r, language_of(a.split("::")[0])).to_dict()
                    for a, r in sorted(added.items())
                ],
                "removed": [
                    _ApiEntry(a, r, language_of(a.split("::")[0])).to_dict()
                    for a, r in sorted(removed.items())
                ],
                "changed": [
                    {**_ApiEntry(a, new, language_of(a.split("::")[0])).to_dict(),
                     "change": cls}
                    for a, (_, new, cls) in sorted(changed.items())
                ],
            },
            indent=2,
        ))
        return

    typer.echo(
        f"\nPublic API surface — commit {commit.commit_id[:8]}  vs  {base_commit.commit_id[:8]}"
    )
    if language:
        typer.echo(f"  (language: {language})")
    typer.echo("─" * 62)

    all_addrs = sorted(set(list(added) + list(removed) + list(changed)))
    max_addr = max((len(a) for a in all_addrs), default=40)

    if added:
        typer.echo(f"\nAdded ({len(added)}):")
        for addr, rec in sorted(added.items()):
            typer.echo(f"  + {addr:<{max_addr}}  {rec['kind']}")

    if removed:
        typer.echo(f"\nRemoved ({len(removed)}):")
        for addr, rec in sorted(removed.items()):
            typer.echo(f"  - {addr:<{max_addr}}  {rec['kind']}")

    if changed:
        typer.echo(f"\nChanged ({len(changed)}):")
        for addr, (_, new, cls) in sorted(changed.items()):
            typer.echo(f"  ~ {addr:<{max_addr}}  {new['kind']}  ({cls})")

    if not added and not removed and not changed:
        typer.echo("\n  ✅ No public API changes detected.")
    else:
        n = len(added) + len(removed) + len(changed)
        typer.echo(f"\n  {n} public API change(s)")

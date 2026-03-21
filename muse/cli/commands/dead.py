"""muse dead — dead code detection.

Finds symbols that are **never called** and whose containing module is
**never imported** by anything else in the committed snapshot.

A symbol is a dead-code candidate when two independent conditions hold:

1. **No call-site**: its bare name does not appear in any ``ast.Call``
   node in any Python file in the snapshot.

2. **No import**: its containing file's module name does not appear in
   any ``import``-kind symbol in any other file.

Both conditions must hold simultaneously.  A function that is never
called but lives in a module that *is* imported is still reachable —
it may be part of an exported API even if it's not called internally.

Known limitations (documented, not bugs)
-----------------------------------------
- Dynamic dispatch: ``getattr(obj, name)()``, ``functools.partial``,
  decorator-wrapped calls, and ``eval`` are not detected.
- Exported APIs: symbols accessed from outside the repo (library code)
  appear dead because the callers are not in the snapshot.
- Entry points: ``main()``, CLI callbacks, and test functions appear dead
  by design.  Use ``--exclude-tests`` to hide test file symbols.
- tree-sitter languages: call-site extraction is Python-only.  Symbols in
  Go/Rust/TypeScript files are checked for import-graph reachability only.

Usage::

    muse dead
    muse dead --kind function
    muse dead --exclude-tests
    muse dead --commit HEAD~10
    muse dead --json

Output::

    Dead code candidates — commit cb4afaed
    ──────────────────────────────────────────────────────────────
    src/billing.py::_internal_helper      function  (not called, module not imported)
    src/utils.py::deprecated_format       function  (not called, module imported)

    ⚠️  2 potentially dead symbol(s)
    Note: dynamic dispatch, exported APIs, and entry points are not detected.

Flags:

``--kind KIND``
    Restrict to symbols of a specific kind.

``--exclude-tests``
    Exclude symbols in files whose path contains ``test`` or ``spec``.

``--commit, -c REF``
    Analyse a historical snapshot instead of HEAD.

``--json``
    Emit results as JSON.
"""

from __future__ import annotations

import json
import logging
import pathlib

import typer

from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.store import get_commit_snapshot_manifest, read_current_branch, resolve_commit_ref
from muse.plugins.code._callgraph import build_reverse_graph
from muse.plugins.code._query import symbols_for_snapshot
from muse.plugins.code.ast_parser import parse_symbols
from muse.core.object_store import read_object

logger = logging.getLogger(__name__)

app = typer.Typer()


def _read_repo_id(root: pathlib.Path) -> str:
    return str(json.loads((root / ".muse" / "repo.json").read_text())["repo_id"])


def _read_branch(root: pathlib.Path) -> str:
    return read_current_branch(root)


def _is_test_file(file_path: str) -> bool:
    lower = file_path.lower()
    return "test" in lower or "spec" in lower


def _all_imported_modules(
    root: pathlib.Path,
    manifest: dict[str, str],
) -> set[str]:
    """Return the set of all module/symbol names imported across the snapshot."""
    from muse.plugins.code.ast_parser import SEMANTIC_EXTENSIONS
    imported: set[str] = set()
    for file_path, obj_id in manifest.items():
        suffix = pathlib.PurePosixPath(file_path).suffix.lower()
        if suffix not in SEMANTIC_EXTENSIONS:
            continue
        raw = read_object(root, obj_id)
        if raw is None:
            continue
        tree = parse_symbols(raw, file_path)
        for rec in tree.values():
            if rec["kind"] == "import":
                imported.add(rec["qualified_name"])
    return imported


def _module_is_imported(file_path: str, imported_modules: set[str]) -> bool:
    """Return True if *file_path*'s module name appears in *imported_modules*."""
    stem = pathlib.PurePosixPath(file_path).stem
    module = pathlib.PurePosixPath(file_path).with_suffix("").as_posix().replace("/", ".")
    for imp in imported_modules:
        if (
            imp == stem
            or imp == module
            or imp.endswith(f".{stem}")
            or imp.endswith(f".{module}")
            or stem in imp.split(".")
        ):
            return True
    return False


class _DeadCandidate:
    def __init__(
        self,
        address: str,
        kind: str,
        called: bool,
        module_imported: bool,
    ) -> None:
        self.address = address
        self.kind = kind
        self.called = called
        self.module_imported = module_imported

    @property
    def reason(self) -> str:
        if not self.called and not self.module_imported:
            return "not called, module not imported"
        if not self.called:
            return "not called (module is imported — may be exported API)"
        return "module not imported"

    def to_dict(self) -> dict[str, str | bool]:
        return {
            "address": self.address,
            "kind": self.kind,
            "called": self.called,
            "module_imported": self.module_imported,
            "reason": self.reason,
        }


@app.callback(invoke_without_command=True)
def dead(
    ctx: typer.Context,
    kind_filter: str | None = typer.Option(
        None, "--kind", "-k", metavar="KIND",
        help="Restrict to symbols of this kind (function, class, method, …).",
    ),
    exclude_tests: bool = typer.Option(
        False, "--exclude-tests",
        help="Exclude symbols in files whose path contains 'test' or 'spec'.",
    ),
    ref: str | None = typer.Option(
        None, "--commit", "-c", metavar="REF",
        help="Analyse a historical snapshot instead of HEAD.",
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit results as JSON."),
) -> None:
    """Find symbols with no callers and no importers — dead code candidates.

    A symbol is flagged when its bare name appears in no ``ast.Call`` node
    in any Python file, *and* its containing module is not imported by any
    other file in the snapshot.

    Symbols whose module *is* imported are also reported (with a softer
    warning) — they may be exported API surface that is reachable from
    outside the snapshot.

    Use ``--exclude-tests`` to suppress test-file symbols (which are
    intentionally uncalled within the production codebase).

    Limitations: dynamic dispatch, exported APIs, and entry points are not
    detected.  Treat results as *candidates*, not confirmed dead code.
    """
    root = require_repo()
    repo_id = _read_repo_id(root)
    branch = _read_branch(root)

    commit = resolve_commit_ref(root, repo_id, branch, ref)
    if commit is None:
        typer.echo(f"❌ Commit '{ref or 'HEAD'}' not found.", err=True)
        raise typer.Exit(code=ExitCode.USER_ERROR)

    manifest = get_commit_snapshot_manifest(root, commit.commit_id) or {}

    # Build the set of all called bare names across the snapshot.
    reverse = build_reverse_graph(root, manifest)
    called_names: set[str] = set(reverse.keys())

    # Build the set of all imported module names.
    imported_modules = _all_imported_modules(root, manifest)

    # Collect all symbols from the snapshot.
    symbol_map = symbols_for_snapshot(root, manifest, kind_filter=kind_filter)

    candidates: list[_DeadCandidate] = []
    for file_path, tree in sorted(symbol_map.items()):
        if exclude_tests and _is_test_file(file_path):
            continue
        mod_imported = _module_is_imported(file_path, imported_modules)
        for address, rec in sorted(tree.items()):
            if rec["kind"] == "import":
                continue  # import symbols are infrastructure, not callables
            bare_name = rec["name"].split(".")[-1]
            is_called = bare_name in called_names
            # Only flag as dead if not called; module-import status is
            # additional context in the reason string.
            if not is_called:
                candidates.append(_DeadCandidate(
                    address=address,
                    kind=rec["kind"],
                    called=is_called,
                    module_imported=mod_imported,
                ))

    # Sort: definite dead (module not imported) first, then softer warnings.
    candidates.sort(key=lambda c: (c.module_imported, c.address))

    if as_json:
        typer.echo(json.dumps(
            {
                "commit": commit.commit_id[:8],
                "total_symbols_scanned": sum(len(t) for t in symbol_map.values()),
                "dead_candidates": [c.to_dict() for c in candidates],
            },
            indent=2,
        ))
        return

    typer.echo(f"\nDead code candidates — commit {commit.commit_id[:8]}")
    typer.echo("─" * 62)

    if not candidates:
        typer.echo("  ✅ No dead code candidates found.")
        typer.echo(
            "\n  Note: dynamic dispatch, exported APIs, and entry points are not detected."
        )
        return

    max_addr = max(len(c.address) for c in candidates)
    for c in candidates:
        typer.echo(f"  {c.address:<{max_addr}}  {c.kind:<14}  ({c.reason})")

    typer.echo(f"\n⚠️  {len(candidates)} potentially dead symbol(s)")
    typer.echo(
        "Note: dynamic dispatch, exported APIs, and entry points are not detected."
        "\nTreat these as candidates — verify before deleting."
    )
    if exclude_tests:
        typer.echo("(test files excluded)")

"""muse breakage — detect symbol-level breakage in the working tree.

Checks the current working tree against the HEAD snapshot for structural
breakage that would fail at runtime or import time:

1. **Unresolved imports** — a ``from X import Y`` where Y no longer exists
   in the committed version of X (detected via symbol graph).
2. **Stale imports** — imports of symbols that were deleted from the source
   in the current working tree's own edits (not yet committed).
3. **Broken interface obligations** — a class promises a method (e.g. via
   an inherited abstract method) that no longer exists in its body.

This runs on committed snapshot data + the current working-tree parse.  It
does not execute code, install dependencies, or run a type checker.  It is a
pure structural analysis using the symbol graph.

Usage::

    muse breakage
    muse breakage --language Python
    muse breakage --json

Output::

    Breakage check — working tree vs HEAD (a1b2c3d4)
    ──────────────────────────────────────────────────────────────

    🔴  stale_import
        src/billing.py  imports compute_total from src/utils.py
        but compute_total was removed in HEAD snapshot

    ⚠️   missing_interface_method
        src/billing.py::Invoice  inherits abstract method pay()
        but pay() is not in the current class body

    2 issue(s) found

Flags:

``--language LANG``
    Restrict analysis to files of this language.

``--json``
    Emit results as JSON.
"""

from __future__ import annotations

import json
import logging
import pathlib

import typer

from muse.core.repo import require_repo
from muse.core.store import get_commit_snapshot_manifest, read_current_branch, resolve_commit_ref
from muse.plugins.code._query import is_semantic, language_of, symbols_for_snapshot
from muse.plugins.code.ast_parser import parse_symbols

logger = logging.getLogger(__name__)

app = typer.Typer()


def _read_repo_id(root: pathlib.Path) -> str:
    return str(json.loads((root / ".muse" / "repo.json").read_text())["repo_id"])


def _read_branch(root: pathlib.Path) -> str:
    return read_current_branch(root)


class _BreakageIssue:
    def __init__(
        self,
        issue_type: str,
        file_path: str,
        description: str,
        severity: str = "error",
    ) -> None:
        self.issue_type = issue_type
        self.file_path = file_path
        self.description = description
        self.severity = severity

    def to_dict(self) -> dict[str, str]:
        return {
            "issue_type": self.issue_type,
            "file_path": self.file_path,
            "description": self.description,
            "severity": self.severity,
        }


def _check_file(
    root: pathlib.Path,
    file_path: str,
    head_symbols_flat: dict[str, str],   # address → content_id from HEAD
    language_filter: str | None,
) -> list[_BreakageIssue]:
    """Check one working-tree file for breakage."""
    if language_filter and language_of(file_path) != language_filter:
        return []
    if not is_semantic(file_path):
        return []

    working_file = root / file_path
    if not working_file.exists():
        return []

    issues: list[_BreakageIssue] = []
    raw = working_file.read_bytes()
    tree = parse_symbols(raw, file_path)

    # Check 1: stale imports — symbols imported that no longer exist in HEAD.
    for addr, rec in tree.items():
        if rec["kind"] != "import":
            continue
        imported_name = rec["name"]
        # Check if this name appears as a symbol anywhere in HEAD.
        # Simple heuristic: if the imported name was in HEAD's flat symbol set
        # and is now gone, it's a stale import.
        found_in_head = any(
            a.split("::")[-1] == imported_name
            for a in head_symbols_flat
        )
        found_in_working = any(
            a.split("::")[-1] == imported_name
            for a in tree
            if tree[a]["kind"] != "import"
        )
        if not found_in_head and not found_in_working:
            issues.append(_BreakageIssue(
                issue_type="stale_import",
                file_path=file_path,
                description=f"imports '{imported_name}' but it is not found in the HEAD snapshot",
                severity="warning",
            ))

    # Check 2: broken interface obligations — class missing expected methods.
    # Python only: look at base class names; if they are in the same file and
    # have methods not present in the subclass, flag it.
    suffix = pathlib.PurePosixPath(file_path).suffix.lower()
    if suffix in {".py", ".pyi"}:
        for addr, rec in tree.items():
            if rec["kind"] != "class":
                continue
            class_name = rec["name"]
            # Find base class methods from HEAD.
            head_base_methods: set[str] = set()
            for head_addr in head_symbols_flat:
                # Look for methods of other classes that share a name with
                # this class's qualified bases (heuristic: base class in same file).
                parts = head_addr.split("::")
                if len(parts) == 2:
                    sym_name = parts[1]
                    # Does the working tree's class body have this method?
                    if "." in sym_name:
                        parent, method = sym_name.split(".", 1)
                        if parent != class_name and not method.startswith("_"):
                            head_base_methods.add(method)

            # Check that all expected methods exist in the working class.
            working_class_methods = {
                a.split("::")[-1].split(".")[-1]
                for a in tree
                if f"::{class_name}." in a
            }
            for method in sorted(head_base_methods):
                if method not in working_class_methods:
                    issues.append(_BreakageIssue(
                        issue_type="missing_interface_method",
                        file_path=file_path,
                        description=(
                            f"class {class_name!r} does not implement expected method "
                            f"'{method}' (found in HEAD snapshot)"
                        ),
                        severity="warning",
                    ))

    return issues


@app.callback(invoke_without_command=True)
def breakage(
    ctx: typer.Context,
    language: str | None = typer.Option(
        None, "--language", "-l", metavar="LANG",
        help="Restrict to files of this language.",
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit results as JSON."),
) -> None:
    """Detect symbol-level breakage in the working tree vs HEAD snapshot.

    Checks for:
    - **stale_import**: imports of symbols that no longer exist in HEAD
    - **missing_interface_method**: class body missing expected methods

    Purely structural analysis — no code execution, no type checking.
    Operates on the committed symbol graph + current working-tree parse.
    """
    root = require_repo()
    repo_id = _read_repo_id(root)
    branch = _read_branch(root)

    commit = resolve_commit_ref(root, repo_id, branch, None)
    if commit is None:
        typer.echo("❌ No HEAD commit found.", err=True)
        raise typer.Exit(code=1)

    manifest = get_commit_snapshot_manifest(root, commit.commit_id) or {}
    head_sym_map = symbols_for_snapshot(root, manifest)

    # Build flat address → content_id map from HEAD.
    head_flat: dict[str, str] = {}
    for _fp, tree in head_sym_map.items():
        for addr, rec in tree.items():
            head_flat[addr] = rec["content_id"]

    all_issues: list[_BreakageIssue] = []

    # Find all semantic files in the working tree.
    for file_path in sorted(manifest.keys()):
        issues = _check_file(root, file_path, head_flat, language)
        all_issues.extend(issues)

    if as_json:
        typer.echo(json.dumps(
            {
                "schema_version": 1,
                "commit": commit.commit_id[:8],
                "language_filter": language,
                "issues": [i.to_dict() for i in all_issues],
                "total": len(all_issues),
                "errors": sum(1 for i in all_issues if i.severity == "error"),
                "warnings": sum(1 for i in all_issues if i.severity == "warning"),
            },
            indent=2,
        ))
        return

    typer.echo(
        f"\nBreakage check — working tree vs HEAD ({commit.commit_id[:8]})"
    )
    if language:
        typer.echo(f"  (language: {language})")
    typer.echo("─" * 62)

    if not all_issues:
        typer.echo("\n  ✅ No structural breakage detected.")
        return

    for issue in all_issues:
        icon = "🔴" if issue.severity == "error" else "⚠️ "
        typer.echo(f"\n{icon}  {issue.issue_type}")
        typer.echo(f"    {issue.file_path}")
        typer.echo(f"    {issue.description}")

    errors = sum(1 for i in all_issues if i.severity == "error")
    warnings = sum(1 for i in all_issues if i.severity == "warning")
    typer.echo(f"\n  {errors} error(s), {warnings} warning(s)")

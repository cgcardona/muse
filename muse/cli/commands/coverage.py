"""muse coverage — class interface call-coverage.

Reports which methods of a class are actually called somewhere in the
committed snapshot and which are never reached.

This command answers the question: *"Is my API actually used?"*

Every ``class`` symbol with method children is a candidate interface.
``muse coverage`` builds the reverse call graph for the snapshot, then
checks each method's bare name against the set of called names.

Why this matters
----------------
Traditional coverage tools measure *test* coverage — how many lines are
executed during a test run.  That requires a running test suite.

Muse's *interface coverage* measures *call-site* coverage — how many of
a class's methods are invoked anywhere in the production codebase.  It
runs in O(snapshot_size) without executing any code.  It is ideal for:

* Auditing API surface before a deprecation.
* Finding method pairs where one is always called and the other never is.
* Verifying that a new interface is actually adopted after landing.

Usage::

    muse coverage "src/models.py::User"
    muse coverage "src/auth.py::TokenValidator" --commit HEAD~5
    muse coverage "src/billing.py::Invoice" --json

Output::

    Interface coverage: src/models.py::User
    ──────────────────────────────────────────────────────────────

    ✅  User.__init__        called by: src/api.py::create_user, src/api.py::update_user
    ✅  User.save            called by: src/api.py::create_user
    ❌  User.delete          (no callers detected)
    ❌  User.to_dict         (no callers detected)

    ──────────────────────────────────────────────────────────────
    Coverage: 2/4 methods called (50%)
    🟡 Partial coverage — 2 uncovered method(s) may be dead API surface.

Flags:

``--commit, -c REF``
    Analyse a historical snapshot instead of HEAD.

``--json``
    Emit results as JSON.

``--show-callers``
    Include the list of caller addresses next to each covered method
    (shown by default; use ``--no-show-callers`` to suppress).
"""

import json
import logging
import pathlib

import typer

from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.store import get_commit_snapshot_manifest, resolve_commit_ref
from muse.plugins.code._callgraph import build_reverse_graph
from muse.plugins.code._query import symbols_for_snapshot
from muse.plugins.code.ast_parser import SymbolRecord

logger = logging.getLogger(__name__)

app = typer.Typer()

_METHOD_KINDS: frozenset[str] = frozenset({"method", "async_method"})


def _read_repo_id(root: pathlib.Path) -> str:
    return str(json.loads((root / ".muse" / "repo.json").read_text())["repo_id"])


def _read_branch(root: pathlib.Path) -> str:
    head_ref = (root / ".muse" / "HEAD").read_text().strip()
    return head_ref.removeprefix("refs/heads/").strip()


def _class_methods(
    file_path: str,
    class_name: str,
    symbol_map: dict[str, dict[str, SymbolRecord]],
) -> list[tuple[str, str]]:
    """Return ``(address, bare_name)`` pairs for all methods under *class_name* in *file_path*.

    Addresses look like ``"src/models.py::User.__init__"``.
    Bare names look like ``"__init__"``.
    """
    methods: list[tuple[str, str]] = []
    prefix = f"{file_path}::{class_name}."
    for file, tree in symbol_map.items():
        if file != file_path:
            continue
        for address, rec in sorted(tree.items()):
            if rec["kind"] not in _METHOD_KINDS:
                continue
            if address.startswith(prefix):
                bare = rec["name"].split(".")[-1]
                methods.append((address, bare))
    return sorted(methods, key=lambda t: t[1])


@app.callback(invoke_without_command=True)
def coverage(
    ctx: typer.Context,
    address: str = typer.Argument(
        ..., metavar="CLASS_ADDRESS",
        help='Class symbol address, e.g. "src/models.py::User".',
    ),
    ref: str | None = typer.Option(
        None, "--commit", "-c", metavar="REF",
        help="Analyse a historical snapshot instead of HEAD.",
    ),
    show_callers: bool = typer.Option(
        True, "--show-callers/--no-show-callers",
        help="Include caller addresses next to each covered method.",
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit results as JSON."),
) -> None:
    """Show which methods of a class are called anywhere in the snapshot.

    Builds the reverse call graph, then checks each method's bare name
    against the set of called names.  Reports covered and uncovered methods,
    and a percentage coverage score.

    Useful for auditing API adoption, finding dead interface surface, and
    planning safe deprecations — without running a single test.

    Python only (call-graph analysis uses stdlib ``ast``).
    """
    root = require_repo()
    repo_id = _read_repo_id(root)
    branch = _read_branch(root)

    if "::" not in address:
        typer.echo(
            f"❌ ADDRESS must be a symbol address like 'src/models.py::User'.", err=True
        )
        raise typer.Exit(code=ExitCode.USER_ERROR)

    file_path, class_name = address.split("::", 1)

    commit = resolve_commit_ref(root, repo_id, branch, ref)
    if commit is None:
        typer.echo(f"❌ Commit '{ref or 'HEAD'}' not found.", err=True)
        raise typer.Exit(code=ExitCode.USER_ERROR)

    manifest = get_commit_snapshot_manifest(root, commit.commit_id) or {}
    symbol_map = symbols_for_snapshot(root, manifest, kind_filter=None)

    # Verify the class exists in the snapshot.
    class_addr = f"{file_path}::{class_name}"
    all_syms: dict[str, str] = {}
    for tree in symbol_map.values():
        for addr, rec in tree.items():
            all_syms[addr] = rec["kind"]
    if class_addr not in all_syms:
        typer.echo(
            f"❌ Class '{class_addr}' not found in snapshot {commit.commit_id[:8]}.",
            err=True,
        )
        raise typer.Exit(code=ExitCode.USER_ERROR)

    # Collect all methods.
    methods = _class_methods(file_path, class_name, symbol_map)
    if not methods:
        typer.echo(f"⚠️  No methods found for '{class_addr}'.", err=True)
        raise typer.Exit(code=ExitCode.USER_ERROR)

    # Build reverse call graph.
    reverse = build_reverse_graph(root, manifest)

    # Classify each method.
    covered: list[tuple[str, str, list[str]]] = []    # (address, bare_name, callers)
    uncovered: list[tuple[str, str]] = []             # (address, bare_name)
    for method_addr, bare_name in methods:
        callers = sorted(reverse.get(bare_name, []))
        if callers:
            covered.append((method_addr, bare_name, callers))
        else:
            uncovered.append((method_addr, bare_name))

    total = len(methods)
    n_covered = len(covered)
    pct = round(n_covered / total * 100) if total else 0

    if as_json:
        typer.echo(json.dumps(
            {
                "address": class_addr,
                "commit": commit.commit_id[:8],
                "total_methods": total,
                "covered": n_covered,
                "percent": pct,
                "methods": [
                    {
                        "address": addr,
                        "name": name,
                        "called": True,
                        "callers": callers,
                    }
                    for addr, name, callers in covered
                ] + [
                    {
                        "address": addr,
                        "name": name,
                        "called": False,
                        "callers": [],
                    }
                    for addr, name in uncovered
                ],
            },
            indent=2,
        ))
        return

    typer.echo(f"\nInterface coverage: {class_addr}")
    typer.echo("─" * 62)

    max_name = max(
        (len(f"{class_name}.{name}") for _, name in methods),
        default=0,
    )

    for addr, bare_name, callers in covered:
        display = f"{class_name}.{bare_name}"
        line = f"  ✅  {display:<{max_name}}"
        if show_callers:
            caller_str = ", ".join(callers[:3])
            if len(callers) > 3:
                caller_str += f" (+{len(callers) - 3} more)"
            line += f"  ← {caller_str}"
        typer.echo(line)

    for addr, bare_name in uncovered:
        display = f"{class_name}.{bare_name}"
        typer.echo(f"  ❌  {display:<{max_name}}  (no callers detected)")

    typer.echo("\n" + "─" * 62)
    typer.echo(f"Coverage: {n_covered}/{total} methods called ({pct}%)")

    if pct == 100:
        typer.echo("✅ Full coverage — all methods are called at least once.")
    elif pct >= 75:
        typer.echo(f"🟢 Good coverage — {total - n_covered} uncovered method(s).")
    elif pct >= 50:
        typer.echo(f"🟡 Partial coverage — {total - n_covered} uncovered method(s) may be dead API surface.")
    else:
        typer.echo(f"🔴 Low coverage — {total - n_covered} of {total} methods have no detected callers.")

    typer.echo(
        "\nNote: dynamic dispatch, subclass overrides, and external callers are not detected."
    )

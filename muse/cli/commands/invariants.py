"""muse invariants — enforce architectural rules from .muse/invariants.toml.

Loads invariant rules from ``.muse/invariants.toml`` and checks them against
the committed snapshot at HEAD (or a given commit).  Rules are declarative
architectural constraints — enforced by analysis, not by the runtime.

Supported rule types
--------------------
``no_cycles``
    The import graph must have no cycles.  Detects import cycle violations.

``forbidden_dependency``
    A file (or file pattern) must not import from another file (or pattern).
    Enforces layer boundaries (e.g. "core must not import from cli").

``required_test``
    Every public function in ``source_pattern`` must have a corresponding test
    in ``test_pattern`` (matched by function name).

``layer_boundary``
    Enforce import direction between layers: ``lower`` may not import from
    ``upper``.

Rule file format (``.muse/invariants.toml``)
--------------------------------------------

.. code-block:: toml

    [[rules]]
    type = "no_cycles"
    name = "no import cycles"

    [[rules]]
    type = "forbidden_dependency"
    name = "core must not import cli"
    source_pattern = "muse/core/"
    forbidden_pattern = "muse/cli/"

    [[rules]]
    type = "layer_boundary"
    name = "plugins must not import from cli"
    lower = "muse/plugins/"
    upper = "muse/cli/"

    [[rules]]
    type = "required_test"
    name = "all billing functions must have tests"
    source_pattern = "src/billing.py"
    test_pattern = "tests/test_billing.py"

Usage::

    muse invariants
    muse invariants --commit HEAD~5
    muse invariants --json

Output::

    Invariant check — commit a1b2c3d4
    ──────────────────────────────────────────────────────────────

    ✅  no import cycles                  passed
    🔴  core must not import cli          VIOLATED
        muse/core/snapshot.py imports muse/cli/app (1 violation)
    ✅  plugins must not import from cli  passed

    1 rule passed · 1 rule violated

Flags:

``--commit, -c REF``
    Check a historical snapshot instead of HEAD.

``--json``
    Emit results as JSON.
"""

from __future__ import annotations

import argparse
import json
import logging
import pathlib
import re
import sys

from muse._version import __version__
from muse.core.errors import ExitCode
from muse.core.object_store import read_object
from muse.core.repo import require_repo
from muse.core.store import get_commit_snapshot_manifest, read_current_branch, resolve_commit_ref
from muse.plugins.code._query import is_semantic, symbols_for_snapshot
from muse.plugins.code.ast_parser import parse_symbols

logger = logging.getLogger(__name__)

_INVARIANTS_FILE = pathlib.PurePosixPath(".muse") / "invariants.toml"


def _read_repo_id(root: pathlib.Path) -> str:
    return str(json.loads((root / ".muse" / "repo.json").read_text())["repo_id"])


def _read_branch(root: pathlib.Path) -> str:
    return read_current_branch(root)


class _RuleResult:
    def __init__(self, name: str, rule_type: str, passed: bool, violations: list[str]) -> None:
        self.name = name
        self.rule_type = rule_type
        self.passed = passed
        self.violations = violations

    def to_dict(self) -> dict[str, str | bool | list[str]]:
        return {
            "name": self.name,
            "rule_type": self.rule_type,
            "passed": self.passed,
            "violations": self.violations,
        }


def _parse_toml_rules(text: str) -> list[dict[str, str]]:
    """Minimal TOML parser for [[rules]] sections (no external dependencies).

    Parses key = "value" lines within [[rules]] blocks.  Does not support
    nested tables, arrays, or multi-line strings — the invariants format is
    intentionally simple.
    """
    rules: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for line in text.splitlines():
        line = line.strip()
        if line == "[[rules]]":
            if current is not None:
                rules.append(current)
            current = {}
            continue
        if current is not None and "=" in line and not line.startswith("#"):
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            current[key] = val
    if current is not None:
        rules.append(current)
    return rules


def _build_import_map(root: pathlib.Path, manifest: dict[str, str]) -> dict[str, list[str]]:
    """Return {file_path: [imported_file_paths]} from snapshot."""
    stem_to_file: dict[str, str] = {
        pathlib.PurePosixPath(fp).stem: fp for fp in manifest
    }
    imports: dict[str, list[str]] = {fp: [] for fp in manifest}
    for file_path, obj_id in sorted(manifest.items()):
        raw = read_object(root, obj_id)
        if raw is None:
            continue
        tree = parse_symbols(raw, file_path)
        for rec in tree.values():
            if rec["kind"] != "import":
                continue
            imported = rec["qualified_name"].split(".")[-1].replace("import::", "")
            target = stem_to_file.get(imported)
            if target and target != file_path:
                imports[file_path].append(target)
    return imports


def _find_cycles(imports: dict[str, list[str]]) -> list[list[str]]:
    """Iterative DFS cycle detection.

    Uses an explicit stack instead of recursion so that deeply nested import
    graphs (thousands of files in a chain) cannot exhaust Python's call stack.
    O(V+E) — every node is visited at most once.
    """
    cycles: list[list[str]] = []
    visited: set[str] = set()

    for start in imports:
        if start in visited:
            continue
        # Each stack frame: (node, path-so-far, in-stack set for this path)
        stack: list[tuple[str, list[str], set[str]]] = [(start, [], set())]
        while stack:
            node, path, in_stack = stack.pop()
            if node in in_stack:
                idx = path.index(node)
                cycles.append(path[idx:] + [node])
                continue
            if node in visited:
                continue
            visited.add(node)
            new_in_stack = in_stack | {node}
            for neighbour in imports.get(node, []):
                stack.append((neighbour, path + [node], new_in_stack))

    return cycles


def _check_rule(
    rule: dict[str, str],
    manifest: dict[str, str],
    import_map: dict[str, list[str]],
    root: pathlib.Path,
) -> _RuleResult:
    name = rule.get("name", "unnamed")
    rule_type = rule.get("type", "")
    violations: list[str] = []

    if rule_type == "no_cycles":
        cycles = _find_cycles(import_map)
        for cycle in cycles:
            violations.append(" → ".join(cycle))
        return _RuleResult(name, rule_type, not violations, violations)

    if rule_type == "forbidden_dependency":
        src_pat = rule.get("source_pattern", "")
        forb_pat = rule.get("forbidden_pattern", "")
        for fp, deps in sorted(import_map.items()):
            if src_pat and src_pat not in fp:
                continue
            for dep in deps:
                if forb_pat and forb_pat in dep:
                    violations.append(f"{fp} imports {dep}")
        return _RuleResult(name, rule_type, not violations, violations)

    if rule_type == "layer_boundary":
        lower = rule.get("lower", "")
        upper = rule.get("upper", "")
        for fp, deps in sorted(import_map.items()):
            if lower and lower not in fp:
                continue
            for dep in deps:
                if upper and upper in dep:
                    violations.append(f"{fp} (lower layer) imports {dep} (upper layer)")
        return _RuleResult(name, rule_type, not violations, violations)

    if rule_type == "required_test":
        src_pat = rule.get("source_pattern", "")
        test_pat = rule.get("test_pattern", "")
        # Collect public function names from source files.
        src_funcs: set[str] = set()
        for fp in manifest:
            if src_pat and src_pat not in fp:
                continue
            raw = read_object(root, manifest[fp])
            if raw is None:
                continue
            tree = parse_symbols(raw, fp)
            for addr, rec in tree.items():
                if rec["kind"] in ("function", "async_function") and not rec["name"].startswith("_"):
                    src_funcs.add(rec["name"])
        # Collect test function names from test files.
        test_funcs: set[str] = set()
        for fp in manifest:
            if test_pat and test_pat not in fp:
                continue
            raw = read_object(root, manifest[fp])
            if raw is None:
                continue
            tree = parse_symbols(raw, fp)
            for rec in tree.values():
                if rec["kind"] in ("function", "async_function"):
                    test_funcs.add(rec["name"])
        # Check that every src_func has a corresponding test_<name> or <name> in test.
        for func in sorted(src_funcs):
            has_test = f"test_{func}" in test_funcs or func in test_funcs
            if not has_test:
                violations.append(f"no test found for function '{func}'")
        return _RuleResult(name, rule_type, not violations, violations)

    # Unknown rule type.
    return _RuleResult(name, rule_type, False, [f"unknown rule type: {rule_type!r}"])


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the invariants subcommand."""
    parser = subparsers.add_parser(
        "invariants",
        help="Check architectural invariants from .muse/invariants.toml.",
        description=__doc__,
    )
    parser.add_argument(
        "--commit", "-c",
        dest="ref",
        default=None,
        metavar="REF",
        help="Check a historical snapshot instead of HEAD.",
    )
    parser.add_argument("--json", dest="as_json", action="store_true", help="Emit results as JSON.")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> None:
    """Check architectural invariants from .muse/invariants.toml.

    Loads declarative rules and verifies them against the committed snapshot:

    * **no_cycles** — the import graph must be acyclic
    * **forbidden_dependency** — enforces layer boundaries
    * **layer_boundary** — lower layers must not import from upper layers
    * **required_test** — public functions must have corresponding tests

    Create ``.muse/invariants.toml`` with ``[[rules]]`` blocks to define your
    architectural constraints.  All rules run against the committed snapshot;
    no working-tree parsing or code execution required.
    """
    ref: str | None = args.ref
    as_json: bool = args.as_json

    root = require_repo()
    repo_id = _read_repo_id(root)
    branch = _read_branch(root)

    invariants_path = root / ".muse" / "invariants.toml"
    if not invariants_path.exists():
        print(
            "⚠️  .muse/invariants.toml not found.\n"
            "Create it with [[rules]] blocks to define architectural constraints.\n"
            "See: muse invariants --help for the rule format."
        )
        return

    rules = _parse_toml_rules(invariants_path.read_text())
    if not rules:
        print("  (no rules defined in .muse/invariants.toml)")
        return

    commit = resolve_commit_ref(root, repo_id, branch, ref)
    if commit is None:
        print(f"❌ Commit '{ref or 'HEAD'}' not found.", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)

    manifest = get_commit_snapshot_manifest(root, commit.commit_id) or {}
    import_map = _build_import_map(root, manifest)

    results: list[_RuleResult] = []
    for rule in rules:
        result = _check_rule(rule, manifest, import_map, root)
        results.append(result)

    if as_json:
        print(json.dumps(
            {
                "schema_version": __version__,
                "commit": commit.commit_id[:8],
                "rules_checked": len(results),
                "passed": sum(1 for r in results if r.passed),
                "violated": sum(1 for r in results if not r.passed),
                "results": [r.to_dict() for r in results],
            },
            indent=2,
        ))
        return

    print(f"\nInvariant check — commit {commit.commit_id[:8]}")
    print("─" * 62)

    for result in results:
        icon = "✅" if result.passed else "🔴"
        status = "passed" if result.passed else "VIOLATED"
        print(f"\n{icon}  {result.name:<40}  {status}")
        if not result.passed:
            for v in result.violations[:5]:
                print(f"    {v}")
            if len(result.violations) > 5:
                print(f"    … and {len(result.violations) - 5} more")

    passed = sum(1 for r in results if r.passed)
    violated = sum(1 for r in results if not r.passed)
    print(f"\n  {passed} rule(s) passed · {violated} rule(s) violated")

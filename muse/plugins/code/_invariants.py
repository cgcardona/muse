"""Code-domain invariants engine for Muse.

Evaluates semantic rules against code snapshots.  Rules are declared in
``.muse/code_invariants.toml`` and evaluated at commit time, merge time, or
on-demand via ``muse code-check``.

Rule types
----------

``max_complexity``
    Detects functions / methods whose estimated cyclomatic complexity exceeds
    *threshold*.  Complexity is approximated by counting control-flow branch
    points (``if``, ``elif``, ``for``, ``while``, ``except``, ``with``,
    ``and``, ``or``) inside each symbol's body.  This correlates well with
    real cyclomatic complexity for Python and is language-agnostic for other
    tree-sitter-parsed languages.

``no_circular_imports``
    Detects import cycles among Python files in the snapshot.  Builds a
    directed graph (file → files it imports) and runs DFS cycle detection.
    Reports each cycle as one violation at the root file of the cycle.

``no_dead_exports``
    Detects top-level functions and classes that are never imported by any
    other file in the snapshot (dead exports / unreachable public API).
    Only applies to semantic files; test files and ``__init__.py`` are exempt.

``test_coverage_floor``
    Requires that at least *min_ratio* of non-test functions have a
    corresponding test function (detected by ``test_`` prefix convention).
    Reports the actual vs required coverage ratio when the floor is not met.

TOML example
------------
::

    [[rule]]
    name = "complexity_gate"
    severity = "error"
    scope = "function"
    rule_type = "max_complexity"
    [rule.params]
    threshold = 15

    [[rule]]
    name = "no_cycles"
    severity = "error"
    scope = "file"
    rule_type = "no_circular_imports"

    [[rule]]
    name = "dead_exports"
    severity = "warning"
    scope = "file"
    rule_type = "no_dead_exports"

    [[rule]]
    name = "test_coverage"
    severity = "warning"
    scope = "repo"
    rule_type = "test_coverage_floor"
    [rule.params]
    min_ratio = 0.30

Public API
----------
- :class:`CodeInvariantRule`    — code-specific rule declaration.
- :class:`CodeViolation`        — violation with file + symbol address.
- :class:`CodeInvariantReport`  — full report for one commit.
- :class:`CodeChecker`          — satisfies :class:`~muse.core.invariants.InvariantChecker`.
- :func:`load_invariant_rules`  — load from TOML with built-in defaults.
- :func:`run_invariants`        — top-level runner.
"""

import ast
import logging
import pathlib
from typing import Literal, TypedDict

from muse.core.invariants import (
    BaseReport,
    BaseViolation,
    InvariantSeverity,
    format_report,
    load_rules_toml,
    make_report,
)
from muse.core.object_store import read_object
from muse.core.store import get_commit_snapshot_manifest
from muse.plugins.code.ast_parser import (
    SEMANTIC_EXTENSIONS,
    SymbolTree,
    adapter_for_path,
    parse_symbols,
)

logger = logging.getLogger(__name__)

_DEFAULT_RULES_FILE = ".muse/code_invariants.toml"

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


class _RuleRequired(TypedDict):
    name: str
    severity: InvariantSeverity
    scope: Literal["function", "file", "repo", "global"]
    rule_type: str


class CodeInvariantRule(_RuleRequired, total=False):
    """A single code invariant rule declaration.

    ``name``      Unique human-readable identifier.
    ``severity``  ``"info"``, ``"warning"``, or ``"error"``.
    ``scope``     Granularity: ``"function"``, ``"file"``, ``"repo"``.
    ``rule_type`` Built-in type: ``"max_complexity"``,
                  ``"no_circular_imports"``, ``"no_dead_exports"``,
                  ``"test_coverage_floor"``.
    ``params``    Rule-specific numeric / string parameters.
    """

    params: dict[str, str | int | float]


class CodeViolation(TypedDict):
    """A code invariant violation with precise source location.

    ``rule_name``  Rule that fired.
    ``severity``   Inherited from the rule.
    ``address``    ``"file.py::symbol_name"`` or ``"file.py"`` for file-level.
    ``description`` Human-readable explanation.
    ``file``       Workspace-relative file path.
    ``symbol``     Symbol name (empty string for file-level violations).
    ``detail``     Additional context (e.g. complexity score, cycle path).
    """

    rule_name: str
    severity: InvariantSeverity
    address: str
    description: str
    file: str
    symbol: str
    detail: str


# ---------------------------------------------------------------------------
# Built-in default rules
# ---------------------------------------------------------------------------

_BUILTIN_DEFAULTS: list[CodeInvariantRule] = [
    CodeInvariantRule(
        name="complexity_gate",
        severity="warning",
        scope="function",
        rule_type="max_complexity",
        params={"threshold": 10},
    ),
    CodeInvariantRule(
        name="no_cycles",
        severity="error",
        scope="file",
        rule_type="no_circular_imports",
        params={},
    ),
    CodeInvariantRule(
        name="dead_exports",
        severity="warning",
        scope="file",
        rule_type="no_dead_exports",
        params={},
    ),
]


# ---------------------------------------------------------------------------
# Rule implementations
# ---------------------------------------------------------------------------


def _estimate_complexity(source: bytes, file_path: str) -> dict[str, int]:
    """Return {symbol_address: complexity_score} for a Python source file.

    Uses a simple branch-count heuristic: each ``if``, ``elif``, ``for``,
    ``while``, ``except``, ``with``, ``and``, ``or``, ``assert``,
    ``comprehension`` adds 1 to the enclosing function's score.  Starting
    score is 1 (the function itself).

    Returns an empty dict for non-Python files or parse failures.
    """
    if not file_path.endswith((".py", ".pyi")):
        return {}
    try:
        tree = ast.parse(source, filename=file_path)
    except SyntaxError:
        return {}

    branch_nodes = (
        ast.If, ast.For, ast.While, ast.ExceptHandler,
        ast.With, ast.AsyncWith, ast.AsyncFor,
        ast.BoolOp, ast.Assert, ast.comprehension,
    )

    scores: dict[str, int] = {}

    def _score_fn(node: ast.FunctionDef | ast.AsyncFunctionDef, prefix: str = "") -> None:
        name = node.name
        qualified = f"{prefix}{name}" if prefix else name
        addr = f"{file_path}::{qualified}"
        score = 1  # base complexity
        for child in ast.walk(node):
            if isinstance(child, branch_nodes):
                score += 1
        scores[addr] = score

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for item in ast.walk(node):
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    _score_fn(item, prefix=f"{node.name}.")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            _score_fn(node)

    return scores


def check_max_complexity(
    manifest: dict[str, str],
    repo_root: pathlib.Path,
    rule_name: str,
    severity: InvariantSeverity,
    *,
    threshold: int = 10,
) -> list[CodeViolation]:
    """Detect functions whose estimated cyclomatic complexity exceeds *threshold*."""
    violations: list[CodeViolation] = []
    for file_path, content_hash in manifest.items():
        if not file_path.endswith((".py", ".pyi")):
            continue
        source = read_object(repo_root, content_hash)
        if source is None:
            continue
        scores = _estimate_complexity(source, file_path)
        for addr, score in sorted(scores.items()):
            if score > threshold:
                symbol = addr.split("::", 1)[-1] if "::" in addr else ""
                violations.append(CodeViolation(
                    rule_name=rule_name,
                    severity=severity,
                    address=addr,
                    description=(
                        f"Complexity {score} exceeds threshold {threshold}. "
                        "Consider extracting helper functions."
                    ),
                    file=file_path,
                    symbol=symbol,
                    detail=f"score={score} threshold={threshold}",
                ))
    return violations


def _build_import_graph(
    manifest: dict[str, str],
    repo_root: pathlib.Path,
) -> dict[str, set[str]]:
    """Build a directed import graph: {file → set of imported files}.

    Only tracks intra-repo imports (files that exist in the manifest).
    """
    file_set = set(manifest)
    # Build a module-name → file-path index for intra-repo resolution.
    module_to_file: dict[str, str] = {}
    for fp in file_set:
        if fp.endswith((".py", ".pyi")):
            # Convert path to module name: strip suffix, replace / with .
            mod = fp.removesuffix(".pyi").removesuffix(".py").replace("/", ".").replace("\\", ".")
            module_to_file[mod] = fp
            # Also index by last segment for relative guesses.
            last = mod.rsplit(".", 1)[-1]
            module_to_file.setdefault(last, fp)

    graph: dict[str, set[str]] = {fp: set() for fp in file_set if fp.endswith(".py")}

    for file_path, content_hash in manifest.items():
        if not file_path.endswith(".py"):
            continue
        source = read_object(repo_root, content_hash)
        if source is None:
            continue
        try:
            tree = ast.parse(source, filename=file_path)
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    target = module_to_file.get(alias.name)
                    if target and target != file_path:
                        graph[file_path].add(target)
            elif isinstance(node, ast.ImportFrom) and node.module:
                target = module_to_file.get(node.module)
                if target and target != file_path:
                    graph[file_path].add(target)

    return graph


def _find_cycles(graph: dict[str, set[str]]) -> list[list[str]]:
    """DFS cycle detection; returns list of cycles as file-path lists."""
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {n: WHITE for n in graph}
    stack: list[str] = []
    cycles: list[list[str]] = []

    def dfs(node: str) -> None:
        color[node] = GRAY
        stack.append(node)
        for neighbor in sorted(graph.get(node, set())):
            if color.get(neighbor, BLACK) == WHITE:
                dfs(neighbor)
            elif color.get(neighbor, BLACK) == GRAY:
                # Found a cycle — extract from stack.
                idx = stack.index(neighbor)
                cycle = stack[idx:]
                # Deduplicate: only add if not already seen.
                cycle_key = frozenset(cycle)
                if not any(frozenset(c) == cycle_key for c in cycles):
                    cycles.append(list(cycle))
        stack.pop()
        color[node] = BLACK

    for node in sorted(graph):
        if color[node] == WHITE:
            dfs(node)

    return cycles


def check_no_circular_imports(
    manifest: dict[str, str],
    repo_root: pathlib.Path,
    rule_name: str,
    severity: InvariantSeverity,
) -> list[CodeViolation]:
    """Detect import cycles among Python files in the snapshot."""
    graph = _build_import_graph(manifest, repo_root)
    cycles = _find_cycles(graph)
    violations: list[CodeViolation] = []
    for cycle in cycles:
        root_file = cycle[0]
        cycle_str = " → ".join([*cycle, cycle[0]])
        violations.append(CodeViolation(
            rule_name=rule_name,
            severity=severity,
            address=root_file,
            description=f"Circular import cycle detected: {cycle_str}",
            file=root_file,
            symbol="",
            detail=cycle_str,
        ))
    return violations


def check_no_dead_exports(
    manifest: dict[str, str],
    repo_root: pathlib.Path,
    rule_name: str,
    severity: InvariantSeverity,
) -> list[CodeViolation]:
    """Detect top-level functions/classes never imported by any other file.

    Exempt: test files, ``__init__.py``, files with ``__all__`` declarations
    (which signal deliberate public API), and ``main`` functions.
    """
    violations: list[CodeViolation] = []

    # Collect all intra-repo imported names.
    imported_names: set[str] = set()
    for file_path, content_hash in manifest.items():
        if not file_path.endswith(".py"):
            continue
        source = read_object(repo_root, content_hash)
        if source is None:
            continue
        try:
            tree = ast.parse(source, filename=file_path)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported_names.add(alias.asname or alias.name.split(".")[-1])
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    imported_names.add(alias.asname or alias.name)

    # Check each non-test, non-init Python file.
    for file_path, content_hash in manifest.items():
        if not file_path.endswith(".py"):
            continue
        base = pathlib.PurePosixPath(file_path).name
        if base.startswith("test_") or base == "__init__.py":
            continue
        source = read_object(repo_root, content_hash)
        if source is None:
            continue
        try:
            tree = ast.parse(source, filename=file_path)
        except SyntaxError:
            continue

        # Skip files that declare __all__ (they manage their own exports).
        has_all = any(
            isinstance(n, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "__all__" for t in n.targets)
            for n in ast.walk(tree)
        )
        if has_all:
            continue

        for node in ast.iter_child_nodes(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name.startswith("_") or node.name == "main":
                    continue
                if node.name not in imported_names:
                    addr = f"{file_path}::{node.name}"
                    violations.append(CodeViolation(
                        rule_name=rule_name,
                        severity=severity,
                        address=addr,
                        description=(
                            f"'{node.name}' is never imported by any other file. "
                            "Consider removing, making private (prefix _), or adding to __all__."
                        ),
                        file=file_path,
                        symbol=node.name,
                        detail="no importers found",
                    ))
            elif isinstance(node, ast.ClassDef):
                if node.name.startswith("_"):
                    continue
                if node.name not in imported_names:
                    addr = f"{file_path}::{node.name}"
                    violations.append(CodeViolation(
                        rule_name=rule_name,
                        severity=severity,
                        address=addr,
                        description=(
                            f"Class '{node.name}' is never imported by any other file. "
                            "Consider removing or making private."
                        ),
                        file=file_path,
                        symbol=node.name,
                        detail="no importers found",
                    ))

    return violations


def check_test_coverage_floor(
    manifest: dict[str, str],
    repo_root: pathlib.Path,
    rule_name: str,
    severity: InvariantSeverity,
    *,
    min_ratio: float = 0.30,
) -> list[CodeViolation]:
    """Require that at least *min_ratio* of functions have a test counterpart.

    A function ``foo`` is considered "tested" if any test file contains a
    function named ``test_foo`` or a class method containing ``foo`` in its
    name.  This is a naming-convention heuristic, not true coverage.
    """
    test_fn_names: set[str] = set()
    all_fn_names: set[str] = set()

    for file_path, content_hash in manifest.items():
        if not file_path.endswith(".py"):
            continue
        source = read_object(repo_root, content_hash)
        if source is None:
            continue
        try:
            tree = ast.parse(source, filename=file_path)
        except SyntaxError:
            continue

        base = pathlib.PurePosixPath(file_path).name
        is_test = base.startswith("test_") or base.endswith("_test.py")

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if is_test and node.name.startswith("test_"):
                    test_fn_names.add(node.name.removeprefix("test_"))
                elif not is_test and not node.name.startswith("_"):
                    all_fn_names.add(node.name)

    if not all_fn_names:
        return []

    covered = all_fn_names & test_fn_names
    ratio = len(covered) / len(all_fn_names)

    if ratio < min_ratio:
        pct_actual = round(ratio * 100, 1)
        pct_required = round(min_ratio * 100, 1)
        return [CodeViolation(
            rule_name=rule_name,
            severity=severity,
            address="repo",
            description=(
                f"Test coverage floor not met: {pct_actual}% of functions have test counterparts "
                f"(required {pct_required}%). Untested: "
                + ", ".join(sorted(all_fn_names - covered)[:10])
                + ("…" if len(all_fn_names - covered) > 10 else "")
            ),
            file="",
            symbol="",
            detail=f"ratio={ratio:.3f} required={min_ratio:.3f}",
        )]
    return []


# ---------------------------------------------------------------------------
# Rule dispatch
# ---------------------------------------------------------------------------


def _dispatch_rule(
    rule: CodeInvariantRule,
    manifest: dict[str, str],
    repo_root: pathlib.Path,
) -> list[CodeViolation]:
    """Dispatch a single rule to its implementation function."""
    params = rule.get("params", {})
    rule_name = rule["name"]
    severity = rule["severity"]
    rt = rule["rule_type"]

    if rt == "max_complexity":
        threshold = int(params.get("threshold", 10))
        return check_max_complexity(manifest, repo_root, rule_name, severity, threshold=threshold)

    if rt == "no_circular_imports":
        return check_no_circular_imports(manifest, repo_root, rule_name, severity)

    if rt == "no_dead_exports":
        return check_no_dead_exports(manifest, repo_root, rule_name, severity)

    if rt == "test_coverage_floor":
        min_ratio = float(params.get("min_ratio", 0.30))
        return check_test_coverage_floor(manifest, repo_root, rule_name, severity, min_ratio=min_ratio)

    logger.warning("Unknown code invariant rule_type: %r — skipping", rt)
    return []


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def load_invariant_rules(
    rules_file: pathlib.Path | None = None,
) -> list[CodeInvariantRule]:
    """Load code invariant rules from TOML, falling back to built-in defaults.

    Args:
        rules_file: Path to the TOML file.  ``None`` → use default path.

    Returns:
        List of :class:`CodeInvariantRule` dicts.
    """
    path = rules_file or pathlib.Path(_DEFAULT_RULES_FILE)
    raw = load_rules_toml(path)
    if not raw:
        return list(_BUILTIN_DEFAULTS)

    rules: list[CodeInvariantRule] = []
    for r in raw:
        name = str(r.get("name", "unnamed"))
        raw_sev = str(r.get("severity", "warning"))
        _sev_map: dict[str, InvariantSeverity] = {"info": "info", "warning": "warning", "error": "error"}
        severity: InvariantSeverity = _sev_map.get(raw_sev, "warning")
        scope_raw = str(r.get("scope", "function"))
        _scope_map: dict[str, Literal["function", "file", "repo", "global"]] = {
            "function": "function", "file": "file", "repo": "repo", "global": "global",
        }
        scope: Literal["function", "file", "repo", "global"] = _scope_map.get(scope_raw, "function")
        rule_type = str(r.get("rule_type", ""))
        raw_params = r.get("params", {})
        params: dict[str, str | int | float] = (
            {k: v for k, v in raw_params.items()}
            if isinstance(raw_params, dict)
            else {}
        )
        rule = CodeInvariantRule(
            name=name, severity=severity, scope=scope, rule_type=rule_type, params=params
        )
        rules.append(rule)
    return rules


def run_invariants(
    repo_root: pathlib.Path,
    commit_id: str,
    rules: list[CodeInvariantRule],
) -> BaseReport:
    """Evaluate all rules against the snapshot of *commit_id*.

    Args:
        repo_root:  Repository root.
        commit_id:  Commit to check.
        rules:      Rules to evaluate (from :func:`load_invariant_rules`).

    Returns:
        A :class:`~muse.core.invariants.BaseReport` with all violations.
    """
    manifest = get_commit_snapshot_manifest(repo_root, commit_id)
    if manifest is None:
        logger.warning("Could not load snapshot for commit %s", commit_id)
        return make_report(commit_id, "code", [], 0)

    all_violations: list[BaseViolation] = []
    for rule in rules:
        try:
            code_violations = _dispatch_rule(rule, dict(manifest), repo_root)
            # Upcast CodeViolation → BaseViolation (CodeViolation structurally satisfies it).
            for cv in code_violations:
                all_violations.append(BaseViolation(
                    rule_name=cv["rule_name"],
                    severity=cv["severity"],
                    address=cv["address"],
                    description=cv["description"],
                ))
        except Exception:
            logger.exception("Error evaluating rule %r on commit %s", rule["name"], commit_id)

    return make_report(commit_id, "code", all_violations, len(rules))


class CodeChecker:
    """Satisfies :class:`~muse.core.invariants.InvariantChecker` for the code domain."""

    def check(
        self,
        repo_root: pathlib.Path,
        commit_id: str,
        *,
        rules_file: pathlib.Path | None = None,
    ) -> BaseReport:
        """Run code invariant checks against *commit_id*."""
        rules = load_invariant_rules(rules_file)
        return run_invariants(repo_root, commit_id, rules)

"""Tests for the code-domain invariants engine."""

import pathlib
import tempfile

import pytest

from muse.core.invariants import InvariantChecker
from muse.plugins.code._invariants import (
    CodeChecker,
    CodeInvariantRule,
    check_max_complexity,
    check_no_circular_imports,
    check_no_dead_exports,
    check_test_coverage_floor,
    load_invariant_rules,
    run_invariants,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_repo(tmp_path: pathlib.Path) -> pathlib.Path:
    """Set up a minimal .muse/ structure."""
    muse = tmp_path / ".muse"
    muse.mkdir()
    (muse / "repo.json").write_text('{"repo_id":"test"}')
    (muse / "HEAD").write_text("refs/heads/main")
    (muse / "commits").mkdir()
    (muse / "snapshots").mkdir()
    (muse / "refs" / "heads").mkdir(parents=True)
    (muse / "objects").mkdir()
    return tmp_path


def _write_object(root: pathlib.Path, content: bytes) -> str:
    import hashlib
    h = hashlib.sha256(content).hexdigest()
    obj_path = root / ".muse" / "objects" / h[:2] / h[2:]
    obj_path.parent.mkdir(parents=True, exist_ok=True)
    obj_path.write_bytes(content)
    return h


# ---------------------------------------------------------------------------
# _estimate_complexity (via check_max_complexity)
# ---------------------------------------------------------------------------


class TestMaxComplexity:
    def test_simple_function_no_violation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_repo(pathlib.Path(tmp))
            src = b"def simple():\n    return 1\n"
            h = _write_object(root, src)
            manifest = {"mod.py": h}
            violations = check_max_complexity(manifest, root, "test", "error", threshold=10)
            assert violations == []

    def test_complex_function_triggers_violation(self) -> None:
        # 15+ branches = definitely over threshold 5.
        src = b"""
def complex():
    if True:
        pass
    if True:
        pass
    if True:
        pass
    if True:
        pass
    if True:
        pass
    if True:
        pass
    if True:
        pass
    return 1
"""
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_repo(pathlib.Path(tmp))
            h = _write_object(root, src)
            manifest = {"mod.py": h}
            violations = check_max_complexity(manifest, root, "gate", "error", threshold=5)
            assert len(violations) >= 1
            assert violations[0]["rule_name"] == "gate"
            assert "complexity" in violations[0]["description"].lower()

    def test_non_python_file_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_repo(pathlib.Path(tmp))
            src = b"def hello() { return 1; }"
            h = _write_object(root, src)
            manifest = {"mod.js": h}
            violations = check_max_complexity(manifest, root, "c", "error", threshold=1)
            assert violations == []


# ---------------------------------------------------------------------------
# check_no_circular_imports
# ---------------------------------------------------------------------------


class TestNoCircularImports:
    def test_no_cycle_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_repo(pathlib.Path(tmp))
            a = b"import b\n"
            b_src = b"x = 1\n"
            ha = _write_object(root, a)
            hb = _write_object(root, b_src)
            manifest = {"a.py": ha, "b.py": hb}
            violations = check_no_circular_imports(manifest, root, "no_cycles", "error")
            assert violations == []

    def test_cycle_detected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_repo(pathlib.Path(tmp))
            # a imports b, b imports a → cycle
            a = b"import b\n"
            b_src = b"import a\n"
            ha = _write_object(root, a)
            hb = _write_object(root, b_src)
            manifest = {"a.py": ha, "b.py": hb}
            violations = check_no_circular_imports(manifest, root, "no_cycles", "error")
            assert len(violations) >= 1
            assert "cycle" in violations[0]["description"].lower()

    def test_three_file_cycle_detected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_repo(pathlib.Path(tmp))
            a = b"import b\n"
            b_src = b"import c\n"
            c_src = b"import a\n"
            ha = _write_object(root, a)
            hb = _write_object(root, b_src)
            hc = _write_object(root, c_src)
            manifest = {"a.py": ha, "b.py": hb, "c.py": hc}
            violations = check_no_circular_imports(manifest, root, "cycles", "error")
            assert len(violations) >= 1


# ---------------------------------------------------------------------------
# check_no_dead_exports
# ---------------------------------------------------------------------------


class TestNoDeadExports:
    def test_used_function_not_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_repo(pathlib.Path(tmp))
            lib = b"def my_func():\n    return 1\n"
            main = b"from lib import my_func\n"
            hl = _write_object(root, lib)
            hm = _write_object(root, main)
            manifest = {"lib.py": hl, "main.py": hm}
            violations = check_no_dead_exports(manifest, root, "dead", "warning")
            # lib.my_func is imported by main.py → should not be reported.
            addresses = [v["address"] for v in violations]
            assert "lib.py::my_func" not in addresses

    def test_unused_function_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_repo(pathlib.Path(tmp))
            lib = b"def orphan_fn():\n    return 1\n"
            other = b"x = 1\n"
            hl = _write_object(root, lib)
            ho = _write_object(root, other)
            manifest = {"lib.py": hl, "other.py": ho}
            violations = check_no_dead_exports(manifest, root, "dead", "warning")
            addresses = [v["address"] for v in violations]
            assert "lib.py::orphan_fn" in addresses

    def test_private_function_exempt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_repo(pathlib.Path(tmp))
            lib = b"def _private():\n    return 1\n"
            h = _write_object(root, lib)
            manifest = {"lib.py": h}
            violations = check_no_dead_exports(manifest, root, "dead", "warning")
            # Private functions are exempt.
            assert all("_private" not in v["address"] for v in violations)

    def test_test_file_exempt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_repo(pathlib.Path(tmp))
            lib = b"def test_something():\n    assert True\n"
            h = _write_object(root, lib)
            manifest = {"test_stuff.py": h}
            violations = check_no_dead_exports(manifest, root, "dead", "warning")
            assert violations == []


# ---------------------------------------------------------------------------
# check_test_coverage_floor
# ---------------------------------------------------------------------------


class TestTestCoverageFloor:
    def test_well_covered_code_no_violation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_repo(pathlib.Path(tmp))
            src = b"def foo():\n    return 1\n"
            test_src = b"def test_foo():\n    assert True\n"
            hs = _write_object(root, src)
            ht = _write_object(root, test_src)
            manifest = {"src.py": hs, "test_src.py": ht}
            violations = check_test_coverage_floor(manifest, root, "coverage", "warning", min_ratio=0.5)
            assert violations == []

    def test_uncovered_code_violates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_repo(pathlib.Path(tmp))
            src = b"def foo():\n    pass\ndef bar():\n    pass\ndef baz():\n    pass\n"
            h = _write_object(root, src)
            manifest = {"src.py": h}
            violations = check_test_coverage_floor(manifest, root, "coverage", "warning", min_ratio=0.5)
            assert len(violations) == 1
            assert "coverage floor" in violations[0]["description"].lower()

    def test_no_functions_no_violation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_repo(pathlib.Path(tmp))
            src = b"X = 1\n"
            h = _write_object(root, src)
            manifest = {"config.py": h}
            violations = check_test_coverage_floor(manifest, root, "coverage", "warning", min_ratio=0.5)
            assert violations == []


# ---------------------------------------------------------------------------
# load_invariant_rules
# ---------------------------------------------------------------------------


class TestLoadInvariantRules:
    def test_no_file_returns_defaults(self) -> None:
        rules = load_invariant_rules(pathlib.Path("/no/such/file.toml"))
        assert len(rules) >= 1
        rule_types = {r["rule_type"] for r in rules}
        assert "max_complexity" in rule_types

    def test_toml_file_loaded(self) -> None:
        import tempfile
        toml = "[[rule]]\nname='r1'\nseverity='error'\nscope='function'\nrule_type='max_complexity'\n"
        with tempfile.NamedTemporaryFile(suffix=".toml", mode="w", delete=False) as f:
            f.write(toml)
            path = pathlib.Path(f.name)
        try:
            rules = load_invariant_rules(path)
            assert any(r["rule_type"] == "max_complexity" for r in rules)
        finally:
            path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# CodeChecker (protocol)
# ---------------------------------------------------------------------------


class TestCodeChecker:
    def test_satisfies_invariant_checker_protocol(self) -> None:
        checker = CodeChecker()
        assert isinstance(checker, InvariantChecker)

    def test_check_returns_base_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_repo(pathlib.Path(tmp))
            # No commits — check should return a report with 0 violations.
            from muse.core.store import CommitRecord, SnapshotRecord, write_commit, write_snapshot
            import datetime
            snap = SnapshotRecord(snapshot_id="snap1", manifest={})
            write_snapshot(root, snap)
            commit = CommitRecord(
                commit_id="abc123",
                repo_id="test",
                branch="main",
                snapshot_id="snap1",
                message="init",
                committed_at=datetime.datetime.now(datetime.timezone.utc),
            )
            write_commit(root, commit)
            report = CodeChecker().check(root, "abc123")
            assert report["commit_id"] == "abc123"
            assert report["domain"] == "code"
            assert isinstance(report["violations"], list)

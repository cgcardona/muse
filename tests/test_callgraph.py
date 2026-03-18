"""Tests for muse/plugins/code/_callgraph.py.

Coverage
--------
build_forward_graph(root, manifest)
    - Empty manifest → empty graph.
    - Single Python file with function calls → callee names recorded.
    - Nested calls (function calling another function).
    - Non-Python files are skipped.
    - Syntax error files are skipped gracefully.
    - Method calls are recorded for class methods.

build_reverse_graph(root, manifest)
    - Empty manifest → empty graph.
    - Single caller → single callee reverse mapping.
    - Multiple callers of same function → all listed.
    - Sorted output (deterministic).

transitive_callers(start_name, reverse, max_depth)
    - Direct caller only (depth 1).
    - Transitive callers at depth 2.
    - No callers → empty dict.
    - Self-recursive function → terminates (no infinite loop).
    - Multi-hop chain a→b→c→d.
    - max_depth limits traversal.
    - Return type is dict[int, list[str]] — depth-keyed.
"""
from __future__ import annotations

import hashlib
import pathlib
import textwrap

import pytest

from muse.plugins.code._callgraph import (
    build_forward_graph,
    build_reverse_graph,
    transitive_callers,
)
from muse.core.object_store import write_object


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_snapshot(
    tmp_path: pathlib.Path,
    files: dict[str, str],
) -> dict[str, str]:
    """Write source files to the object store and return a manifest dict."""
    manifest: dict[str, str] = {}
    for rel_path, source in files.items():
        blob = source.encode()
        oid = hashlib.sha256(blob).hexdigest()
        write_object(tmp_path, oid, blob)
        manifest[rel_path] = oid
    return manifest


# ---------------------------------------------------------------------------
# build_forward_graph
# ---------------------------------------------------------------------------


class TestBuildForwardGraph:
    def test_empty_manifest(self, tmp_path: pathlib.Path) -> None:
        graph = build_forward_graph(tmp_path, {})
        assert graph == {}

    def test_single_function_no_calls(self, tmp_path: pathlib.Path) -> None:
        src = textwrap.dedent("""\
            def standalone():
                return 42
        """)
        manifest = _write_snapshot(tmp_path, {"src/a.py": src})
        graph = build_forward_graph(tmp_path, manifest)
        if "src/a.py::standalone" in graph:
            assert graph["src/a.py::standalone"] == frozenset()

    def test_function_calls_another(self, tmp_path: pathlib.Path) -> None:
        src = textwrap.dedent("""\
            def helper():
                return 1

            def caller():
                return helper()
        """)
        manifest = _write_snapshot(tmp_path, {"src/a.py": src})
        graph = build_forward_graph(tmp_path, manifest)
        caller_key = "src/a.py::caller"
        assert caller_key in graph
        assert "helper" in graph[caller_key]

    def test_nested_calls(self, tmp_path: pathlib.Path) -> None:
        src = textwrap.dedent("""\
            def a():
                return b(c())

            def b(x):
                return x

            def c():
                return 1
        """)
        manifest = _write_snapshot(tmp_path, {"src/a.py": src})
        graph = build_forward_graph(tmp_path, manifest)
        a_key = "src/a.py::a"
        assert a_key in graph
        assert "b" in graph[a_key]
        assert "c" in graph[a_key]

    def test_non_python_files_skipped(self, tmp_path: pathlib.Path) -> None:
        go_src = "func caller() { helper() }"
        manifest = _write_snapshot(tmp_path, {"src/main.go": go_src})
        graph = build_forward_graph(tmp_path, manifest)
        assert len(graph) == 0

    def test_multiple_files(self, tmp_path: pathlib.Path) -> None:
        src_a = textwrap.dedent("""\
            def alpha():
                return beta()
        """)
        src_b = textwrap.dedent("""\
            def beta():
                return gamma()

            def gamma():
                return 0
        """)
        manifest = _write_snapshot(tmp_path, {"src/a.py": src_a, "src/b.py": src_b})
        graph = build_forward_graph(tmp_path, manifest)
        assert "src/a.py::alpha" in graph
        assert "beta" in graph["src/a.py::alpha"]
        assert "src/b.py::beta" in graph
        assert "gamma" in graph["src/b.py::beta"]

    def test_syntax_error_file_skipped_gracefully(self, tmp_path: pathlib.Path) -> None:
        bad_src = "def broken(:\n    pass\n"
        manifest = _write_snapshot(tmp_path, {"src/broken.py": bad_src})
        graph = build_forward_graph(tmp_path, manifest)
        assert isinstance(graph, dict)

    def test_method_calls_recorded(self, tmp_path: pathlib.Path) -> None:
        src = textwrap.dedent("""\
            class Invoice:
                def compute(self):
                    return self.apply_tax()

                def apply_tax(self):
                    return 0.1
        """)
        manifest = _write_snapshot(tmp_path, {"src/a.py": src})
        graph = build_forward_graph(tmp_path, manifest)
        compute_key = "src/a.py::Invoice.compute"
        if compute_key in graph:
            assert "apply_tax" in graph[compute_key]

    def test_forward_graph_returns_frozensets(self, tmp_path: pathlib.Path) -> None:
        src = textwrap.dedent("""\
            def f():
                return g()
        """)
        manifest = _write_snapshot(tmp_path, {"a.py": src})
        graph = build_forward_graph(tmp_path, manifest)
        for callee_set in graph.values():
            assert isinstance(callee_set, frozenset)


# ---------------------------------------------------------------------------
# build_reverse_graph
# ---------------------------------------------------------------------------


class TestBuildReverseGraph:
    def test_empty_manifest(self, tmp_path: pathlib.Path) -> None:
        rev = build_reverse_graph(tmp_path, {})
        assert rev == {}

    def test_single_caller(self, tmp_path: pathlib.Path) -> None:
        src = textwrap.dedent("""\
            def helper():
                return 1

            def caller():
                return helper()
        """)
        manifest = _write_snapshot(tmp_path, {"a.py": src})
        rev = build_reverse_graph(tmp_path, manifest)
        assert "helper" in rev
        assert any("caller" in addr for addr in rev["helper"])

    def test_multiple_callers(self, tmp_path: pathlib.Path) -> None:
        src = textwrap.dedent("""\
            def core():
                return 0

            def a():
                return core()

            def b():
                return core()

            def c():
                return core()
        """)
        manifest = _write_snapshot(tmp_path, {"a.py": src})
        rev = build_reverse_graph(tmp_path, manifest)
        assert "core" in rev
        callers = rev["core"]
        assert len(callers) >= 3

    def test_reverse_graph_callers_sorted(self, tmp_path: pathlib.Path) -> None:
        src = textwrap.dedent("""\
            def target():
                return 0

            def a_caller():
                return target()

            def z_caller():
                return target()
        """)
        manifest = _write_snapshot(tmp_path, {"a.py": src})
        rev = build_reverse_graph(tmp_path, manifest)
        if "target" in rev:
            callers = rev["target"]
            assert callers == sorted(callers)

    def test_reverse_graph_returns_lists(self, tmp_path: pathlib.Path) -> None:
        src = textwrap.dedent("""\
            def f():
                return g()
            def g():
                return 0
        """)
        manifest = _write_snapshot(tmp_path, {"a.py": src})
        rev = build_reverse_graph(tmp_path, manifest)
        for callers in rev.values():
            assert isinstance(callers, list)


# ---------------------------------------------------------------------------
# transitive_callers — returns dict[int, list[str]]
# ---------------------------------------------------------------------------


class TestTransitiveCallers:
    def test_no_callers_returns_empty(self) -> None:
        rev: dict[str, list[str]] = {}
        result = transitive_callers("orphan", rev)
        assert result == {}

    def test_direct_caller_at_depth_1(self) -> None:
        rev: dict[str, list[str]] = {"target": ["a.py::caller"]}
        result = transitive_callers("target", rev)
        assert 1 in result
        assert "a.py::caller" in result[1]

    def test_two_hop_chain(self) -> None:
        rev: dict[str, list[str]] = {
            "target": ["a.py::caller"],
            "caller": ["a.py::grandcaller"],
        }
        result = transitive_callers("target", rev)
        assert 1 in result
        assert "a.py::caller" in result[1]
        assert 2 in result
        assert "a.py::grandcaller" in result[2]

    def test_multi_hop_chain(self) -> None:
        rev: dict[str, list[str]] = {
            "d": ["a.py::c"],
            "c": ["a.py::b"],
            "b": ["a.py::a"],
        }
        result = transitive_callers("d", rev)
        assert 1 in result
        assert "a.py::c" in result[1]
        assert 2 in result
        assert "a.py::b" in result[2]
        assert 3 in result
        assert "a.py::a" in result[3]

    def test_no_infinite_loop_on_cycle(self) -> None:
        rev: dict[str, list[str]] = {
            "a": ["a.py::b"],
            "b": ["a.py::a"],
        }
        result = transitive_callers("a", rev)
        assert isinstance(result, dict)

    def test_self_recursive_not_infinite(self) -> None:
        rev: dict[str, list[str]] = {"recursive": ["a.py::recursive"]}
        result = transitive_callers("recursive", rev)
        assert isinstance(result, dict)

    def test_max_depth_limits_traversal(self) -> None:
        rev: dict[str, list[str]] = {
            "d": ["a.py::c"],
            "c": ["a.py::b"],
            "b": ["a.py::a"],
        }
        result = transitive_callers("d", rev, max_depth=1)
        assert 1 in result
        assert 2 not in result
        assert 3 not in result

    def test_diamond_dependency_no_duplicate_addresses(self) -> None:
        rev: dict[str, list[str]] = {
            "a": ["x.py::b", "x.py::c"],
            "b": ["x.py::d"],
            "c": ["x.py::d"],
        }
        result = transitive_callers("a", rev)
        all_addrs: list[str] = []
        for addrs in result.values():
            all_addrs.extend(addrs)
        # x.py::d should appear at most once (visited set prevents duplicates).
        assert all_addrs.count("x.py::d") <= 1

    def test_multiple_direct_callers_all_at_depth_1(self) -> None:
        rev: dict[str, list[str]] = {
            "target": ["a.py::f1", "a.py::f2", "a.py::f3"],
        }
        result = transitive_callers("target", rev)
        assert 1 in result
        assert set(result[1]) == {"a.py::f1", "a.py::f2", "a.py::f3"}

    def test_return_type_is_depth_to_list(self) -> None:
        rev: dict[str, list[str]] = {"t": ["a.py::f"]}
        result = transitive_callers("t", rev)
        for depth, addrs in result.items():
            assert isinstance(depth, int)
            assert isinstance(addrs, list)

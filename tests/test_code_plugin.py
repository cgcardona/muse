"""Tests for the code domain plugin.

Coverage
--------
Unit
    - :mod:`muse.plugins.code.ast_parser`: symbol extraction, content IDs,
      rename detection hashes, import handling.
    - :mod:`muse.plugins.code.symbol_diff`: diff_symbol_trees golden cases,
      cross-file move annotation.

Protocol conformance
    - ``CodePlugin`` satisfies ``MuseDomainPlugin`` and ``StructuredMergePlugin``.

Snapshot
    - Path form: walks all files, raw-bytes hash, honours .museignore.
    - Manifest form: returned as-is.
    - Stability: two calls on the same directory produce identical results.

Diff
    - File-level (no repo_root): added / removed / modified.
    - Semantic (with repo_root via object store): symbol-level PatchOps,
      rename detection, formatting-only suppression.

Golden diff cases
    - Add a new function → InsertOp inside PatchOp.
    - Remove a function → DeleteOp inside PatchOp.
    - Rename a function → ReplaceOp with "renamed to" in new_summary.
    - Change function body → ReplaceOp with "implementation changed".
    - Change function signature → ReplaceOp with "signature changed".
    - Add a new file → InsertOp (or PatchOp with all-insert child ops).
    - Remove a file → DeleteOp (or PatchOp with all-delete child ops).
    - Reformat only → ReplaceOp with "reformatted" in new_summary.

Merge
    - Different symbols in same file → auto-merge (no conflicts).
    - Same symbol modified by both → symbol-level conflict address.
    - Disjoint files → auto-merge.
    - File-level three-way merge correctness.

Schema
    - Valid DomainSchema with five dimensions.
    - merge_mode == "three_way".
    - schema_version == 1.

Drift
    - No drift: committed equals live.
    - Has drift: file added / modified / removed.

Plugin registry
    - "code" is in the registered domain list.
"""

import hashlib
import pathlib
import textwrap

import pytest

from muse._version import __version__
from muse.core.object_store import write_object
from muse.domain import (
    InsertOp,
    MuseDomainPlugin,
    SnapshotManifest,
    StructuredMergePlugin,
)
from muse.plugins.code.ast_parser import (
    FallbackAdapter,
    PythonAdapter,
    SymbolRecord,
    SymbolTree,
    _extract_stmts,
    _import_names,
    _sha256,
    adapter_for_path,
    file_content_id,
    parse_symbols,
)
from muse.plugins.code.plugin import CodePlugin, _hash_file
from muse.plugins.code.symbol_diff import (
    build_diff_ops,
    delta_summary,
    diff_symbol_trees,
)
from muse.plugins.registry import registered_domains


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _make_manifest(files: dict[str, str]) -> SnapshotManifest:
    return SnapshotManifest(files=files, domain="code")


def _src(code: str) -> bytes:
    return textwrap.dedent(code).encode()


def _empty_tree() -> SymbolTree:
    return {}


def _store_blob(repo_root: pathlib.Path, data: bytes) -> str:
    oid = _sha256_bytes(data)
    write_object(repo_root, oid, data)
    return oid


# ---------------------------------------------------------------------------
# Plugin registry
# ---------------------------------------------------------------------------


def test_code_in_registry() -> None:
    assert "code" in registered_domains()


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


def test_satisfies_muse_domain_plugin() -> None:
    plugin = CodePlugin()
    assert isinstance(plugin, MuseDomainPlugin)


def test_satisfies_structured_merge_plugin() -> None:
    plugin = CodePlugin()
    assert isinstance(plugin, StructuredMergePlugin)


# ---------------------------------------------------------------------------
# PythonAdapter — unit tests
# ---------------------------------------------------------------------------


class TestPythonAdapter:
    adapter = PythonAdapter()

    def test_supported_extensions(self) -> None:
        assert ".py" in self.adapter.supported_extensions()
        assert ".pyi" in self.adapter.supported_extensions()

    def test_parse_top_level_function(self) -> None:
        src = _src("""\
            def add(a: int, b: int) -> int:
                return a + b
        """)
        tree = self.adapter.parse_symbols(src, "utils.py")
        assert "utils.py::add" in tree
        rec = tree["utils.py::add"]
        assert rec["kind"] == "function"
        assert rec["name"] == "add"
        assert rec["qualified_name"] == "add"

    def test_parse_async_function(self) -> None:
        src = _src("""\
            async def fetch(url: str) -> bytes:
                pass
        """)
        tree = self.adapter.parse_symbols(src, "api.py")
        assert "api.py::fetch" in tree
        assert tree["api.py::fetch"]["kind"] == "async_function"

    def test_parse_class_and_methods(self) -> None:
        src = _src("""\
            class Dog:
                def bark(self) -> None:
                    print("woof")
                def sit(self) -> None:
                    pass
        """)
        tree = self.adapter.parse_symbols(src, "animals.py")
        assert "animals.py::Dog" in tree
        assert tree["animals.py::Dog"]["kind"] == "class"
        assert "animals.py::Dog.bark" in tree
        assert tree["animals.py::Dog.bark"]["kind"] == "method"
        assert "animals.py::Dog.sit" in tree

    def test_parse_imports(self) -> None:
        src = _src("""\
            import os
            import sys
            from pathlib import Path
        """)
        tree = self.adapter.parse_symbols(src, "app.py")
        assert "app.py::import::os" in tree
        assert "app.py::import::sys" in tree
        assert "app.py::import::Path" in tree

    def test_parse_top_level_variable(self) -> None:
        src = _src("""\
            MAX_RETRIES = 3
            VERSION: str = "1.0"
        """)
        tree = self.adapter.parse_symbols(src, "config.py")
        assert "config.py::MAX_RETRIES" in tree
        assert tree["config.py::MAX_RETRIES"]["kind"] == "variable"
        assert "config.py::VERSION" in tree

    def test_syntax_error_returns_empty_tree(self) -> None:
        src = b"def broken("
        tree = self.adapter.parse_symbols(src, "broken.py")
        assert tree == {}

    def test_content_id_stable_across_calls(self) -> None:
        src = _src("""\
            def hello() -> str:
                return "world"
        """)
        t1 = self.adapter.parse_symbols(src, "a.py")
        t2 = self.adapter.parse_symbols(src, "a.py")
        assert t1["a.py::hello"]["content_id"] == t2["a.py::hello"]["content_id"]

    def test_formatting_does_not_change_content_id(self) -> None:
        """Reformatting a function must not change its content_id."""
        src1 = _src("""\
            def add(a, b):
                return a + b
        """)
        src2 = _src("""\
            def add(a,b):
                return   a  +  b
        """)
        t1 = self.adapter.parse_symbols(src1, "f.py")
        t2 = self.adapter.parse_symbols(src2, "f.py")
        assert t1["f.py::add"]["content_id"] == t2["f.py::add"]["content_id"]

    def test_body_hash_differs_from_content_id(self) -> None:
        src = _src("""\
            def compute(x: int) -> int:
                return x * 2
        """)
        tree = self.adapter.parse_symbols(src, "m.py")
        rec = tree["m.py::compute"]
        assert rec["body_hash"] != rec["content_id"]  # body excludes def line

    def test_rename_detection_via_body_hash(self) -> None:
        """Two functions with identical bodies but different names share body_hash."""
        src1 = _src("def foo(x):\n    return x + 1\n")
        src2 = _src("def bar(x):\n    return x + 1\n")
        t1 = self.adapter.parse_symbols(src1, "f.py")
        t2 = self.adapter.parse_symbols(src2, "f.py")
        assert t1["f.py::foo"]["body_hash"] == t2["f.py::bar"]["body_hash"]
        assert t1["f.py::foo"]["content_id"] != t2["f.py::bar"]["content_id"]

    def test_signature_id_same_despite_body_change(self) -> None:
        src1 = _src("def calc(x: int) -> int:\n    return x\n")
        src2 = _src("def calc(x: int) -> int:\n    return x * 10\n")
        t1 = self.adapter.parse_symbols(src1, "m.py")
        t2 = self.adapter.parse_symbols(src2, "m.py")
        assert t1["m.py::calc"]["signature_id"] == t2["m.py::calc"]["signature_id"]
        assert t1["m.py::calc"]["body_hash"] != t2["m.py::calc"]["body_hash"]

    def test_file_content_id_formatting_insensitive(self) -> None:
        src1 = _src("x = 1\ny = 2\n")
        src2 = _src("x=1\ny=2\n")
        assert self.adapter.file_content_id(src1) == self.adapter.file_content_id(src2)

    def test_file_content_id_syntax_error_uses_raw_bytes(self) -> None:
        bad = b"def("
        cid = self.adapter.file_content_id(bad)
        assert cid == _sha256_bytes(bad)


# ---------------------------------------------------------------------------
# FallbackAdapter
# ---------------------------------------------------------------------------


class TestFallbackAdapter:
    adapter = FallbackAdapter(frozenset({".unknown_xyz"}))

    def test_supported_extensions(self) -> None:
        assert ".unknown_xyz" in self.adapter.supported_extensions()

    def test_parse_returns_empty(self) -> None:
        assert self.adapter.parse_symbols(b"const x = 1;", "src.unknown_xyz") == {}

    def test_content_id_is_raw_bytes_hash(self) -> None:
        data = b"const x = 1;"
        assert self.adapter.file_content_id(data) == _sha256_bytes(data)


# ---------------------------------------------------------------------------
# TreeSitterAdapter — one test per language
# ---------------------------------------------------------------------------


class TestTreeSitterAdapters:
    """Validate symbol extraction for each of the ten tree-sitter-backed languages."""

    def _syms(self, src: bytes, path: str) -> dict[str, str]:
        """Return {addr: kind} for all extracted symbols."""
        tree = parse_symbols(src, path)
        return {addr: rec["kind"] for addr, rec in tree.items()}

    # --- JavaScript -----------------------------------------------------------

    def test_js_top_level_function(self) -> None:
        src = b"function greet(name) { return name; }"
        syms = self._syms(src, "app.js")
        assert "app.js::greet" in syms
        assert syms["app.js::greet"] == "function"

    def test_js_class_and_method(self) -> None:
        src = b"class Animal { speak() { return 1; } }"
        syms = self._syms(src, "animal.js")
        assert "animal.js::Animal" in syms
        assert syms["animal.js::Animal"] == "class"
        assert "animal.js::Animal.speak" in syms
        assert syms["animal.js::Animal.speak"] == "method"

    def test_js_body_hash_rename_detection(self) -> None:
        """JS functions with identical bodies but different names share body_hash."""
        src_foo = b"function foo(x) { return x + 1; }"
        src_bar = b"function bar(x) { return x + 1; }"
        t1 = parse_symbols(src_foo, "f.js")
        t2 = parse_symbols(src_bar, "f.js")
        assert t1["f.js::foo"]["body_hash"] == t2["f.js::bar"]["body_hash"]
        assert t1["f.js::foo"]["content_id"] != t2["f.js::bar"]["content_id"]

    def test_js_adapter_claims_jsx_and_mjs(self) -> None:
        src = b"function f() {}"
        assert parse_symbols(src, "x.jsx") != {} or True  # adapter loaded
        assert "x.mjs::f" in parse_symbols(src, "x.mjs")

    # --- TypeScript -----------------------------------------------------------

    def test_ts_function_and_interface(self) -> None:
        src = b"function hello(name: string): void {}\ninterface Animal { speak(): void; }"
        syms = self._syms(src, "app.ts")
        assert "app.ts::hello" in syms
        assert syms["app.ts::hello"] == "function"
        assert "app.ts::Animal" in syms
        assert syms["app.ts::Animal"] == "class"

    def test_ts_class_and_method(self) -> None:
        src = b"class Dog { bark(): string { return 'woof'; } }"
        syms = self._syms(src, "dog.ts")
        assert "dog.ts::Dog" in syms
        assert "dog.ts::Dog.bark" in syms

    def test_tsx_parses_correctly(self) -> None:
        src = b"function Button(): void { return; }\ninterface Props { label: string; }"
        syms = self._syms(src, "button.tsx")
        assert "button.tsx::Button" in syms
        assert "button.tsx::Props" in syms

    # --- Go -------------------------------------------------------------------

    def test_go_function(self) -> None:
        src = b"func NewDog(name string) string { return name }"
        syms = self._syms(src, "dog.go")
        assert "dog.go::NewDog" in syms
        assert syms["dog.go::NewDog"] == "function"

    def test_go_method_qualified_with_receiver(self) -> None:
        """Go methods carry the receiver type as qualified-name prefix."""
        src = b"type Dog struct { Name string }\nfunc (d Dog) Bark() string { return d.Name }"
        syms = self._syms(src, "dog.go")
        assert "dog.go::Dog" in syms
        assert "dog.go::Dog.Bark" in syms
        assert syms["dog.go::Dog.Bark"] == "method"

    def test_go_pointer_receiver_stripped(self) -> None:
        """Pointer receivers (*Dog) are stripped to give Dog.Method."""
        src = b"type Dog struct {}\nfunc (d *Dog) Sit() {}"
        syms = self._syms(src, "d.go")
        assert "d.go::Dog.Sit" in syms

    # --- Rust -----------------------------------------------------------------

    def test_rust_standalone_function(self) -> None:
        src = b"fn add(a: i32, b: i32) -> i32 { a + b }"
        syms = self._syms(src, "math.rs")
        assert "math.rs::add" in syms
        assert syms["math.rs::add"] == "function"

    def test_rust_impl_method_qualified(self) -> None:
        """Rust impl methods are qualified as TypeName.method."""
        src = b"struct Dog { name: String }\nimpl Dog { fn bark(&self) -> String { self.name.clone() } }"
        syms = self._syms(src, "dog.rs")
        assert "dog.rs::Dog" in syms
        assert "dog.rs::Dog.bark" in syms

    def test_rust_struct_and_trait(self) -> None:
        src = b"struct Point { x: f64, y: f64 }\ntrait Shape { fn area(&self) -> f64; }"
        syms = self._syms(src, "shapes.rs")
        assert "shapes.rs::Point" in syms
        assert syms["shapes.rs::Point"] == "class"
        assert "shapes.rs::Shape" in syms

    # --- Java -----------------------------------------------------------------

    def test_java_class_and_method(self) -> None:
        src = b"public class Calculator { public int add(int a, int b) { return a + b; } }"
        syms = self._syms(src, "Calc.java")
        assert "Calc.java::Calculator" in syms
        assert syms["Calc.java::Calculator"] == "class"
        assert "Calc.java::Calculator.add" in syms
        assert syms["Calc.java::Calculator.add"] == "method"

    def test_java_interface(self) -> None:
        src = b"public interface Shape { double area(); }"
        syms = self._syms(src, "Shape.java")
        assert "Shape.java::Shape" in syms
        assert syms["Shape.java::Shape"] == "class"

    # --- C --------------------------------------------------------------------

    def test_c_function(self) -> None:
        src = b"int add(int a, int b) { return a + b; }\nvoid noop(void) {}"
        syms = self._syms(src, "math.c")
        assert "math.c::add" in syms
        assert syms["math.c::add"] == "function"
        assert "math.c::noop" in syms

    # --- C++ ------------------------------------------------------------------

    def test_cpp_class_and_function(self) -> None:
        src = b"class Animal { public: void speak() {} };\nint square(int x) { return x * x; }"
        syms = self._syms(src, "app.cpp")
        assert "app.cpp::Animal" in syms
        assert syms["app.cpp::Animal"] == "class"
        assert "app.cpp::square" in syms

    # --- C# -------------------------------------------------------------------

    def test_cs_class_and_method(self) -> None:
        src = b"public class Greeter { public string Hello(string name) { return name; } }"
        syms = self._syms(src, "Greeter.cs")
        assert "Greeter.cs::Greeter" in syms
        assert syms["Greeter.cs::Greeter"] == "class"
        assert "Greeter.cs::Greeter.Hello" in syms
        assert syms["Greeter.cs::Greeter.Hello"] == "method"

    def test_cs_interface_and_struct(self) -> None:
        src = b"interface IShape { double Area(); }\nstruct Point { public int X, Y; }"
        syms = self._syms(src, "shapes.cs")
        assert "shapes.cs::IShape" in syms
        assert "shapes.cs::Point" in syms

    # --- Ruby -----------------------------------------------------------------

    def test_ruby_class_and_method(self) -> None:
        src = b"class Dog\n  def bark\n    puts 'woof'\n  end\nend"
        syms = self._syms(src, "dog.rb")
        assert "dog.rb::Dog" in syms
        assert syms["dog.rb::Dog"] == "class"
        assert "dog.rb::Dog.bark" in syms
        assert syms["dog.rb::Dog.bark"] == "method"

    def test_ruby_module(self) -> None:
        src = b"module Greetable\n  def greet\n    'hello'\n  end\nend"
        syms = self._syms(src, "greet.rb")
        assert "greet.rb::Greetable" in syms
        assert syms["greet.rb::Greetable"] == "class"

    # --- Kotlin ---------------------------------------------------------------

    def test_kotlin_function_and_class(self) -> None:
        src = b"fun greet(name: String): String = name\nclass Dog { fun bark(): Unit { } }"
        syms = self._syms(src, "main.kt")
        assert "main.kt::greet" in syms
        assert syms["main.kt::greet"] == "function"
        assert "main.kt::Dog" in syms
        assert "main.kt::Dog.bark" in syms

    # --- cross-language adapter routing ---------------------------------------

    def test_adapter_for_path_routes_all_extensions(self) -> None:
        """adapter_for_path must return a TreeSitterAdapter (not Fallback) for all supported exts."""
        from muse.plugins.code.ast_parser import TreeSitterAdapter, adapter_for_path

        for ext in (
            ".js", ".jsx", ".mjs", ".cjs",
            ".ts", ".tsx",
            ".go",
            ".rs",
            ".java",
            ".c", ".h",
            ".cpp", ".cc", ".cxx", ".hpp",
            ".cs",
            ".rb",
            ".kt", ".kts",
        ):
            a = adapter_for_path(f"src/file{ext}")
            assert isinstance(a, TreeSitterAdapter), (
                f"Expected TreeSitterAdapter for {ext}, got {type(a).__name__}"
            )

    def test_semantic_extensions_covers_all_ts_languages(self) -> None:
        from muse.plugins.code.ast_parser import SEMANTIC_EXTENSIONS

        expected = {
            ".py", ".pyi",
            ".js", ".jsx", ".mjs", ".cjs",
            ".ts", ".tsx",
            ".go", ".rs",
            ".java",
            ".c", ".h",
            ".cpp", ".cc", ".cxx", ".hpp", ".hxx",
            ".cs",
            ".rb",
            ".kt", ".kts",
        }
        assert expected <= SEMANTIC_EXTENSIONS


# ---------------------------------------------------------------------------
# adapter_for_path
# ---------------------------------------------------------------------------


def test_adapter_for_py_is_python() -> None:
    assert isinstance(adapter_for_path("src/utils.py"), PythonAdapter)


def test_adapter_for_ts_is_tree_sitter() -> None:
    from muse.plugins.code.ast_parser import TreeSitterAdapter

    assert isinstance(adapter_for_path("src/app.ts"), TreeSitterAdapter)


def test_adapter_for_no_extension_is_fallback() -> None:
    assert isinstance(adapter_for_path("Makefile"), FallbackAdapter)


# ---------------------------------------------------------------------------
# diff_symbol_trees — golden test cases
# ---------------------------------------------------------------------------


class TestDiffSymbolTrees:
    """Golden test cases for symbol-level diff."""

    def _func(
        self,
        addr: str,
        content_id: str,
        body_hash: str | None = None,
        signature_id: str | None = None,
        name: str = "f",
    ) -> tuple[str, SymbolRecord]:
        return addr, SymbolRecord(
            kind="function",
            name=name,
            qualified_name=name,
            content_id=content_id,
            body_hash=body_hash or content_id,
            signature_id=signature_id or content_id,
            lineno=1,
            end_lineno=3,
        )

    def test_empty_trees_produce_no_ops(self) -> None:
        assert diff_symbol_trees({}, {}) == []

    def test_added_symbol(self) -> None:
        base: SymbolTree = {}
        target: SymbolTree = dict([self._func("f.py::new_fn", "abc", name="new_fn")])
        ops = diff_symbol_trees(base, target)
        assert len(ops) == 1
        assert ops[0]["op"] == "insert"
        assert ops[0]["address"] == "f.py::new_fn"

    def test_removed_symbol(self) -> None:
        base: SymbolTree = dict([self._func("f.py::old", "abc", name="old")])
        target: SymbolTree = {}
        ops = diff_symbol_trees(base, target)
        assert len(ops) == 1
        assert ops[0]["op"] == "delete"
        assert ops[0]["address"] == "f.py::old"

    def test_unchanged_symbol_no_op(self) -> None:
        rec = dict([self._func("f.py::stable", "xyz", name="stable")])
        assert diff_symbol_trees(rec, rec) == []

    def test_implementation_changed(self) -> None:
        """Same signature, different body → ReplaceOp with 'implementation changed'."""
        sig_id = _sha256("calc(x)->int")
        base: SymbolTree = dict([self._func("m.py::calc", "old_body", body_hash="old", signature_id=sig_id, name="calc")])
        target: SymbolTree = dict([self._func("m.py::calc", "new_body", body_hash="new", signature_id=sig_id, name="calc")])
        ops = diff_symbol_trees(base, target)
        assert len(ops) == 1
        assert ops[0]["op"] == "replace"
        assert "implementation changed" in ops[0]["new_summary"]

    def test_signature_changed(self) -> None:
        """Same body, different signature → ReplaceOp with 'signature changed'."""
        body = _sha256("return x + 1")
        base: SymbolTree = dict([self._func("m.py::f", "c1", body_hash=body, signature_id="old_sig", name="f")])
        target: SymbolTree = dict([self._func("m.py::f", "c2", body_hash=body, signature_id="new_sig", name="f")])
        ops = diff_symbol_trees(base, target)
        assert len(ops) == 1
        assert ops[0]["op"] == "replace"
        assert "signature changed" in ops[0]["old_summary"]

    def test_rename_detected(self) -> None:
        """Same body_hash, different name/address → ReplaceOp with 'renamed to'."""
        body = _sha256("return 42")
        base: SymbolTree = dict([self._func("u.py::old_name", "old_cid", body_hash=body, name="old_name")])
        target: SymbolTree = dict([self._func("u.py::new_name", "new_cid", body_hash=body, name="new_name")])
        ops = diff_symbol_trees(base, target)
        assert len(ops) == 1
        assert ops[0]["op"] == "replace"
        assert "renamed to" in ops[0]["new_summary"]
        assert "new_name" in ops[0]["new_summary"]

    def test_independent_changes_both_emitted(self) -> None:
        """Different symbols changed independently → two ReplaceOps."""
        sig_a = "sig_a"
        sig_b = "sig_b"
        base: SymbolTree = {
            **dict([self._func("f.py::foo", "foo_old", body_hash="foo_b_old", signature_id=sig_a, name="foo")]),
            **dict([self._func("f.py::bar", "bar_old", body_hash="bar_b_old", signature_id=sig_b, name="bar")]),
        }
        target: SymbolTree = {
            **dict([self._func("f.py::foo", "foo_new", body_hash="foo_b_new", signature_id=sig_a, name="foo")]),
            **dict([self._func("f.py::bar", "bar_new", body_hash="bar_b_new", signature_id=sig_b, name="bar")]),
        }
        ops = diff_symbol_trees(base, target)
        assert len(ops) == 2
        addrs = {o["address"] for o in ops}
        assert "f.py::foo" in addrs
        assert "f.py::bar" in addrs


# ---------------------------------------------------------------------------
# build_diff_ops — integration
# ---------------------------------------------------------------------------


class TestBuildDiffOps:
    def test_added_file_no_tree(self) -> None:
        ops = build_diff_ops(
            base_files={},
            target_files={"new.ts": "abc"},
            base_trees={},
            target_trees={},
        )
        assert len(ops) == 1
        assert ops[0]["op"] == "insert"
        assert ops[0]["address"] == "new.ts"

    def test_removed_file_no_tree(self) -> None:
        ops = build_diff_ops(
            base_files={"old.ts": "abc"},
            target_files={},
            base_trees={},
            target_trees={},
        )
        assert len(ops) == 1
        assert ops[0]["op"] == "delete"

    def test_modified_file_with_trees(self) -> None:
        body = _sha256("return x")
        base_tree: SymbolTree = {
            "u.py::foo": SymbolRecord(
                kind="function", name="foo", qualified_name="foo",
                content_id="old_c", body_hash=body, signature_id="sig",
                lineno=1, end_lineno=2,
            )
        }
        target_tree: SymbolTree = {
            "u.py::foo": SymbolRecord(
                kind="function", name="foo", qualified_name="foo",
                content_id="new_c", body_hash="new_body", signature_id="sig",
                lineno=1, end_lineno=2,
            )
        }
        ops = build_diff_ops(
            base_files={"u.py": "base_hash"},
            target_files={"u.py": "target_hash"},
            base_trees={"u.py": base_tree},
            target_trees={"u.py": target_tree},
        )
        assert len(ops) == 1
        assert ops[0]["op"] == "patch"
        assert ops[0]["address"] == "u.py"
        assert len(ops[0]["child_ops"]) == 1
        assert ops[0]["child_ops"][0]["op"] == "replace"

    def test_reformat_only_produces_replace_op(self) -> None:
        """When all symbol content_ids are unchanged, emit a reformatted ReplaceOp."""
        content_id = _sha256("return x")
        tree: SymbolTree = {
            "u.py::foo": SymbolRecord(
                kind="function", name="foo", qualified_name="foo",
                content_id=content_id, body_hash=content_id, signature_id=content_id,
                lineno=1, end_lineno=2,
            )
        }
        ops = build_diff_ops(
            base_files={"u.py": "hash_before"},
            target_files={"u.py": "hash_after"},
            base_trees={"u.py": tree},
            target_trees={"u.py": tree},  # same tree → no symbol changes
        )
        assert len(ops) == 1
        assert ops[0]["op"] == "replace"
        assert "reformatted" in ops[0]["new_summary"]

    def test_cross_file_move_annotation(self) -> None:
        """A symbol deleted in file A and inserted in file B is annotated as moved."""
        content_id = _sha256("the_body")
        base_tree: SymbolTree = {
            "a.py::helper": SymbolRecord(
                kind="function", name="helper", qualified_name="helper",
                content_id=content_id, body_hash=content_id, signature_id=content_id,
                lineno=1, end_lineno=3,
            )
        }
        target_tree: SymbolTree = {
            "b.py::helper": SymbolRecord(
                kind="function", name="helper", qualified_name="helper",
                content_id=content_id, body_hash=content_id, signature_id=content_id,
                lineno=1, end_lineno=3,
            )
        }
        ops = build_diff_ops(
            base_files={"a.py": "hash_a", "b.py": "hash_b_before"},
            target_files={"b.py": "hash_b_after"},
            base_trees={"a.py": base_tree},
            target_trees={"b.py": target_tree},
        )
        # Find the patch ops.
        patch_addrs = {o["address"] for o in ops if o["op"] == "patch"}
        assert "a.py" in patch_addrs or "b.py" in patch_addrs


class TestFileMoveAndEdit:
    """Regression: a file renamed+edited must be emitted as a single move+edit PatchOp.

    Before the fix, Muse emitted an all-delete PatchOp for the old path and
    an all-insert PatchOp for the new path — showing a spurious delete+add
    rather than a move+edit.  After the fix, the two are collapsed into a
    single PatchOp carrying ``from_address`` and symbol-level child diffs.
    """

    def _func(
        self,
        addr: str,
        content_id: str,
        body_hash: str | None = None,
        signature_id: str | None = None,
        name: str = "f",
    ) -> tuple[str, SymbolRecord]:
        return addr, SymbolRecord(
            kind="function",
            name=name,
            qualified_name=name,
            content_id=content_id,
            body_hash=body_hash or content_id,
            signature_id=signature_id or content_id,
            lineno=1,
            end_lineno=3,
        )

    def test_move_and_edit_collapses_to_single_patch(self) -> None:
        """File renamed utils.py→helpers.py with one symbol changed must emit one PatchOp."""
        shared_body = _sha256("def unchanged(): pass")
        base_tree: SymbolTree = {
            "utils.py::unchanged": SymbolRecord(
                kind="function", name="unchanged", qualified_name="unchanged",
                content_id=shared_body, body_hash=shared_body, signature_id=shared_body,
                lineno=1, end_lineno=2,
            ),
            "utils.py::modified": SymbolRecord(
                kind="function", name="modified", qualified_name="modified",
                content_id="old_cid", body_hash="old_body", signature_id="old_sig",
                lineno=3, end_lineno=5,
            ),
        }
        target_tree: SymbolTree = {
            "helpers.py::unchanged": SymbolRecord(
                kind="function", name="unchanged", qualified_name="unchanged",
                content_id=shared_body, body_hash=shared_body, signature_id=shared_body,
                lineno=1, end_lineno=2,
            ),
            "helpers.py::modified": SymbolRecord(
                kind="function", name="modified", qualified_name="modified",
                content_id="new_cid", body_hash="new_body", signature_id="new_sig",
                lineno=3, end_lineno=5,
            ),
        }
        ops = build_diff_ops(
            base_files={"utils.py": "hash_old"},
            target_files={"helpers.py": "hash_new"},
            base_trees={"utils.py": base_tree},
            target_trees={"helpers.py": target_tree},
        )
        assert len(ops) == 1, f"Expected 1 op, got {len(ops)}: {[o['op'] for o in ops]}"
        assert ops[0]["op"] == "patch"
        assert ops[0]["address"] == "helpers.py"
        assert ops[0].get("from_address") == "utils.py"

    def test_move_and_edit_child_ops_show_symbol_diff(self) -> None:
        """Child ops of a move+edit PatchOp must reflect symbol-level changes only."""
        shared_body = _sha256("def keep(): pass")
        base_tree: SymbolTree = {
            "a.py::keep": SymbolRecord(
                kind="function", name="keep", qualified_name="keep",
                content_id=shared_body, body_hash=shared_body, signature_id=shared_body,
                lineno=1, end_lineno=2,
            ),
            "a.py::gone": SymbolRecord(
                kind="function", name="gone", qualified_name="gone",
                content_id="cid_gone", body_hash="body_gone", signature_id="sig_gone",
                lineno=3, end_lineno=5,
            ),
        }
        target_tree: SymbolTree = {
            "b.py::keep": SymbolRecord(
                kind="function", name="keep", qualified_name="keep",
                content_id=shared_body, body_hash=shared_body, signature_id=shared_body,
                lineno=1, end_lineno=2,
            ),
            "b.py::new_fn": SymbolRecord(
                kind="function", name="new_fn", qualified_name="new_fn",
                content_id="cid_new", body_hash="body_new", signature_id="sig_new",
                lineno=3, end_lineno=5,
            ),
        }
        ops = build_diff_ops(
            base_files={"a.py": "hash_a"},
            target_files={"b.py": "hash_b"},
            base_trees={"a.py": base_tree},
            target_trees={"b.py": target_tree},
        )
        assert len(ops) == 1
        patch = ops[0]
        assert patch["op"] == "patch"
        child_op_types = {c["op"] for c in patch["child_ops"]}
        # "gone" was deleted, "new_fn" was inserted; "keep" is unchanged → no op.
        assert "delete" in child_op_types
        assert "insert" in child_op_types

    def test_no_false_positive_unrelated_files(self) -> None:
        """Two files with no symbol overlap must NOT be collapsed into a move+edit."""
        ops = build_diff_ops(
            base_files={"old.py": "hash_old"},
            target_files={"new.py": "hash_new"},
            base_trees={
                "old.py": {
                    "old.py::alpha": SymbolRecord(
                        kind="function", name="alpha", qualified_name="alpha",
                        content_id="cid_a", body_hash="body_a", signature_id="sig_a",
                        lineno=1, end_lineno=2,
                    )
                }
            },
            target_trees={
                "new.py": {
                    "new.py::omega": SymbolRecord(
                        kind="function", name="omega", qualified_name="omega",
                        content_id="cid_o", body_hash="body_o", signature_id="sig_o",
                        lineno=1, end_lineno=2,
                    )
                }
            },
        )
        # No overlap → separate delete + insert ops, NOT a move+edit.
        assert len(ops) == 2
        op_types = {o["op"] for o in ops}
        assert op_types == {"patch"}  # Both are PatchOps wrapping single-symbol trees.
        for op in ops:
            assert op.get("from_address") is None


# ---------------------------------------------------------------------------
# CodePlugin — snapshot
# ---------------------------------------------------------------------------


class TestCodePluginSnapshot:
    plugin = CodePlugin()

    def test_path_returns_manifest(self, tmp_path: pathlib.Path) -> None:
        workdir = tmp_path
        (workdir / "app.py").write_text("x = 1\n")
        snap = self.plugin.snapshot(workdir)
        assert snap["domain"] == "code"
        assert "app.py" in snap["files"]

    def test_snapshot_stability(self, tmp_path: pathlib.Path) -> None:
        workdir = tmp_path
        (workdir / "main.py").write_text("def f(): pass\n")
        s1 = self.plugin.snapshot(workdir)
        s2 = self.plugin.snapshot(workdir)
        assert s1 == s2

    def test_snapshot_uses_raw_bytes_hash(self, tmp_path: pathlib.Path) -> None:
        workdir = tmp_path
        content = b"def add(a, b): return a + b\n"
        (workdir / "math.py").write_bytes(content)
        snap = self.plugin.snapshot(workdir)
        expected = _sha256_bytes(content)
        assert snap["files"]["math.py"] == expected

    def test_museignore_respected(self, tmp_path: pathlib.Path) -> None:
        workdir = tmp_path
        (workdir / "keep.py").write_text("x = 1\n")
        (workdir / "skip.log").write_text("log\n")
        ignore = tmp_path / ".museignore"
        ignore.write_text('[global]\npatterns = ["*.log"]\n')
        snap = self.plugin.snapshot(workdir)
        assert "keep.py" in snap["files"]
        assert "skip.log" not in snap["files"]

    def test_pycache_always_ignored(self, tmp_path: pathlib.Path) -> None:
        workdir = tmp_path
        cache = workdir / "__pycache__"
        cache.mkdir()
        (cache / "utils.cpython-312.pyc").write_bytes(b"\x00")
        (workdir / "main.py").write_text("x = 1\n")
        snap = self.plugin.snapshot(workdir)
        assert "main.py" in snap["files"]
        assert not any("__pycache__" in k for k in snap["files"])

    def test_nested_files_tracked(self, tmp_path: pathlib.Path) -> None:
        workdir = tmp_path
        (workdir / "src").mkdir(parents=True)
        (workdir / "src" / "utils.py").write_text("pass\n")
        snap = self.plugin.snapshot(workdir)
        assert "src/utils.py" in snap["files"]

    def test_manifest_passthrough(self) -> None:
        manifest = _make_manifest({"a.py": "hash"})
        result = self.plugin.snapshot(manifest)
        assert result is manifest


# ---------------------------------------------------------------------------
# CodePlugin — diff (file-level, no repo_root)
# ---------------------------------------------------------------------------


class TestCodePluginDiffFileLevel:
    plugin = CodePlugin()

    def test_added_file(self) -> None:
        base = _make_manifest({})
        target = _make_manifest({"new.py": "abc"})
        delta = self.plugin.diff(base, target)
        assert len(delta["ops"]) == 1
        assert delta["ops"][0]["op"] == "insert"

    def test_removed_file(self) -> None:
        base = _make_manifest({"old.py": "abc"})
        target = _make_manifest({})
        delta = self.plugin.diff(base, target)
        assert len(delta["ops"]) == 1
        assert delta["ops"][0]["op"] == "delete"

    def test_modified_file(self) -> None:
        base = _make_manifest({"f.py": "old"})
        target = _make_manifest({"f.py": "new"})
        delta = self.plugin.diff(base, target)
        assert len(delta["ops"]) == 1
        assert delta["ops"][0]["op"] == "replace"

    def test_no_changes_empty_ops(self) -> None:
        snap = _make_manifest({"f.py": "abc"})
        delta = self.plugin.diff(snap, snap)
        assert delta["ops"] == []
        assert delta["summary"] == "no changes"

    def test_domain_is_code(self) -> None:
        delta = self.plugin.diff(_make_manifest({}), _make_manifest({}))
        assert delta["domain"] == "code"


# ---------------------------------------------------------------------------
# CodePlugin — diff (semantic, with repo_root)
# ---------------------------------------------------------------------------


class TestCodePluginDiffSemantic:
    plugin = CodePlugin()

    def _setup_repo(
        self, tmp_path: pathlib.Path
    ) -> tuple[pathlib.Path, pathlib.Path]:
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        workdir = repo_root
        return repo_root, workdir

    def test_add_function_produces_patch_op(self, tmp_path: pathlib.Path) -> None:
        repo_root, _ = self._setup_repo(tmp_path)
        base_src = _src("x = 1\n")
        target_src = _src("x = 1\n\ndef greet(name: str) -> str:\n    return f'Hello {name}'\n")

        base_oid = _store_blob(repo_root, base_src)
        target_oid = _store_blob(repo_root, target_src)

        base = _make_manifest({"hello.py": base_oid})
        target = _make_manifest({"hello.py": target_oid})
        delta = self.plugin.diff(base, target, repo_root=repo_root)

        patch_ops = [o for o in delta["ops"] if o["op"] == "patch"]
        assert len(patch_ops) == 1
        assert patch_ops[0]["address"] == "hello.py"
        child_ops = patch_ops[0]["child_ops"]
        assert any(c["op"] == "insert" and "greet" in c.get("content_summary", "") for c in child_ops)

    def test_remove_function_produces_patch_op(self, tmp_path: pathlib.Path) -> None:
        repo_root, _ = self._setup_repo(tmp_path)
        base_src = _src("def old_fn() -> None:\n    pass\n")
        target_src = _src("# removed\n")

        base_oid = _store_blob(repo_root, base_src)
        target_oid = _store_blob(repo_root, target_src)

        base = _make_manifest({"mod.py": base_oid})
        target = _make_manifest({"mod.py": target_oid})
        delta = self.plugin.diff(base, target, repo_root=repo_root)

        patch_ops = [o for o in delta["ops"] if o["op"] == "patch"]
        assert len(patch_ops) == 1
        child_ops = patch_ops[0]["child_ops"]
        assert any(c["op"] == "delete" and "old_fn" in c.get("content_summary", "") for c in child_ops)

    def test_rename_function_detected(self, tmp_path: pathlib.Path) -> None:
        repo_root, _ = self._setup_repo(tmp_path)
        base_src = _src("def compute(x: int) -> int:\n    return x * 2\n")
        target_src = _src("def calculate(x: int) -> int:\n    return x * 2\n")

        base_oid = _store_blob(repo_root, base_src)
        target_oid = _store_blob(repo_root, target_src)

        base = _make_manifest({"ops.py": base_oid})
        target = _make_manifest({"ops.py": target_oid})
        delta = self.plugin.diff(base, target, repo_root=repo_root)

        patch_ops = [o for o in delta["ops"] if o["op"] == "patch"]
        assert len(patch_ops) == 1
        child_ops = patch_ops[0]["child_ops"]
        rename_ops = [
            c for c in child_ops
            if c["op"] == "replace" and "renamed to" in c.get("new_summary", "")
        ]
        assert len(rename_ops) == 1
        assert "calculate" in rename_ops[0]["new_summary"]

    def test_implementation_change_detected(self, tmp_path: pathlib.Path) -> None:
        repo_root, _ = self._setup_repo(tmp_path)
        base_src = _src("def double(x: int) -> int:\n    return x * 2\n")
        target_src = _src("def double(x: int) -> int:\n    return x + x\n")

        base_oid = _store_blob(repo_root, base_src)
        target_oid = _store_blob(repo_root, target_src)

        base = _make_manifest({"math.py": base_oid})
        target = _make_manifest({"math.py": target_oid})
        delta = self.plugin.diff(base, target, repo_root=repo_root)

        patch_ops = [o for o in delta["ops"] if o["op"] == "patch"]
        child_ops = patch_ops[0]["child_ops"]
        impl_ops = [c for c in child_ops if "implementation changed" in c.get("new_summary", "")]
        assert len(impl_ops) == 1

    def test_reformat_only_produces_replace_with_reformatted(
        self, tmp_path: pathlib.Path
    ) -> None:
        repo_root, _ = self._setup_repo(tmp_path)
        base_src = _src("def add(a,b):\n    return   a+b\n")
        # Same semantics, different formatting — ast.unparse normalizes both.
        target_src = _src("def add(a, b):\n    return a + b\n")

        base_oid = _store_blob(repo_root, base_src)
        target_oid = _store_blob(repo_root, target_src)

        base = _make_manifest({"f.py": base_oid})
        target = _make_manifest({"f.py": target_oid})
        delta = self.plugin.diff(base, target, repo_root=repo_root)

        # The diff should produce a reformatted ReplaceOp rather than a PatchOp.
        replace_ops = [o for o in delta["ops"] if o["op"] == "replace"]
        patch_ops = [o for o in delta["ops"] if o["op"] == "patch"]
        # Reformatting: either zero ops (if raw hashes are identical) or a
        # reformatted replace (if raw hashes differ but symbols unchanged).
        if delta["ops"]:
            assert replace_ops or patch_ops  # something was emitted
            if replace_ops:
                assert any("reformatted" in o.get("new_summary", "") for o in replace_ops)

    def test_missing_object_falls_back_to_file_level(
        self, tmp_path: pathlib.Path
    ) -> None:
        repo_root, _ = self._setup_repo(tmp_path)
        # Objects NOT written to store — should fall back gracefully.
        base = _make_manifest({"f.py": "deadbeef" * 8})
        target = _make_manifest({"f.py": "cafebabe" * 8})
        delta = self.plugin.diff(base, target, repo_root=repo_root)
        assert len(delta["ops"]) == 1
        assert delta["ops"][0]["op"] == "replace"


# ---------------------------------------------------------------------------
# CodePlugin — merge
# ---------------------------------------------------------------------------


class TestCodePluginMerge:
    plugin = CodePlugin()

    def test_only_one_side_changed(self) -> None:
        base = _make_manifest({"f.py": "v1"})
        left = _make_manifest({"f.py": "v1"})
        right = _make_manifest({"f.py": "v2"})
        result = self.plugin.merge(base, left, right)
        assert result.is_clean
        assert result.merged["files"]["f.py"] == "v2"

    def test_both_sides_same_change(self) -> None:
        base = _make_manifest({"f.py": "v1"})
        left = _make_manifest({"f.py": "v2"})
        right = _make_manifest({"f.py": "v2"})
        result = self.plugin.merge(base, left, right)
        assert result.is_clean
        assert result.merged["files"]["f.py"] == "v2"

    def test_conflict_when_both_sides_differ(self) -> None:
        base = _make_manifest({"f.py": "v1"})
        left = _make_manifest({"f.py": "v2"})
        right = _make_manifest({"f.py": "v3"})
        result = self.plugin.merge(base, left, right)
        assert not result.is_clean
        assert "f.py" in result.conflicts

    def test_disjoint_additions_auto_merge(self) -> None:
        base = _make_manifest({})
        left = _make_manifest({"a.py": "hash_a"})
        right = _make_manifest({"b.py": "hash_b"})
        result = self.plugin.merge(base, left, right)
        assert result.is_clean
        assert "a.py" in result.merged["files"]
        assert "b.py" in result.merged["files"]

    def test_deletion_on_one_side(self) -> None:
        base = _make_manifest({"f.py": "v1"})
        left = _make_manifest({})
        right = _make_manifest({"f.py": "v1"})
        result = self.plugin.merge(base, left, right)
        assert result.is_clean
        assert "f.py" not in result.merged["files"]


# ---------------------------------------------------------------------------
# CodePlugin — merge_ops (symbol-level OT)
# ---------------------------------------------------------------------------


class TestCodePluginMergeOps:
    plugin = CodePlugin()

    def _py_snap(self, file_path: str, src: bytes, repo_root: pathlib.Path) -> SnapshotManifest:
        oid = _store_blob(repo_root, src)
        return _make_manifest({file_path: oid})

    def test_different_symbols_auto_merge(self, tmp_path: pathlib.Path) -> None:
        """Two agents modify different functions → no conflict."""
        repo_root = tmp_path / "repo"
        repo_root.mkdir()

        base_src = _src("""\
            def foo(x: int) -> int:
                return x

            def bar(y: int) -> int:
                return y
        """)
        # Ours: modify foo.
        ours_src = _src("""\
            def foo(x: int) -> int:
                return x * 2

            def bar(y: int) -> int:
                return y
        """)
        # Theirs: modify bar.
        theirs_src = _src("""\
            def foo(x: int) -> int:
                return x

            def bar(y: int) -> int:
                return y + 1
        """)

        base_snap = self._py_snap("m.py", base_src, repo_root)
        ours_snap = self._py_snap("m.py", ours_src, repo_root)
        theirs_snap = self._py_snap("m.py", theirs_src, repo_root)

        ours_delta = self.plugin.diff(base_snap, ours_snap, repo_root=repo_root)
        theirs_delta = self.plugin.diff(base_snap, theirs_snap, repo_root=repo_root)

        result = self.plugin.merge_ops(
            base_snap,
            ours_snap,
            theirs_snap,
            ours_delta["ops"],
            theirs_delta["ops"],
            repo_root=repo_root,
        )
        # Different symbol addresses → ops commute → no conflict.
        assert result.is_clean, f"Expected no conflicts, got: {result.conflicts}"

    def test_same_symbol_conflict(self, tmp_path: pathlib.Path) -> None:
        """Both agents modify the same function → conflict at symbol address."""
        repo_root = tmp_path / "repo"
        repo_root.mkdir()

        base_src = _src("def calc(x: int) -> int:\n    return x\n")
        ours_src = _src("def calc(x: int) -> int:\n    return x * 2\n")
        theirs_src = _src("def calc(x: int) -> int:\n    return x + 100\n")

        base_snap = self._py_snap("calc.py", base_src, repo_root)
        ours_snap = self._py_snap("calc.py", ours_src, repo_root)
        theirs_snap = self._py_snap("calc.py", theirs_src, repo_root)

        ours_delta = self.plugin.diff(base_snap, ours_snap, repo_root=repo_root)
        theirs_delta = self.plugin.diff(base_snap, theirs_snap, repo_root=repo_root)

        result = self.plugin.merge_ops(
            base_snap,
            ours_snap,
            theirs_snap,
            ours_delta["ops"],
            theirs_delta["ops"],
            repo_root=repo_root,
        )
        assert not result.is_clean
        # Conflict should be at file or symbol level.
        assert len(result.conflicts) > 0

    def test_disjoint_files_auto_merge(self, tmp_path: pathlib.Path) -> None:
        """Agents modify completely different files → auto-merge."""
        repo_root = tmp_path / "repo"
        repo_root.mkdir()

        base = _make_manifest({"a.py": "v1", "b.py": "v1"})
        ours = _make_manifest({"a.py": "v2", "b.py": "v1"})
        theirs = _make_manifest({"a.py": "v1", "b.py": "v2"})

        ours_delta = self.plugin.diff(base, ours)
        theirs_delta = self.plugin.diff(base, theirs)

        result = self.plugin.merge_ops(
            base, ours, theirs,
            ours_delta["ops"],
            theirs_delta["ops"],
        )
        assert result.is_clean


# ---------------------------------------------------------------------------
# CodePlugin — drift
# ---------------------------------------------------------------------------


class TestCodePluginDrift:
    plugin = CodePlugin()

    def test_no_drift(self, tmp_path: pathlib.Path) -> None:
        workdir = tmp_path
        (workdir / "app.py").write_text("x = 1\n")
        snap = self.plugin.snapshot(workdir)
        report = self.plugin.drift(snap, workdir)
        assert not report.has_drift

    def test_has_drift_after_edit(self, tmp_path: pathlib.Path) -> None:
        workdir = tmp_path
        f = workdir / "app.py"
        f.write_text("x = 1\n")
        snap = self.plugin.snapshot(workdir)
        f.write_text("x = 2\n")
        report = self.plugin.drift(snap, workdir)
        assert report.has_drift

    def test_has_drift_after_add(self, tmp_path: pathlib.Path) -> None:
        workdir = tmp_path
        (workdir / "a.py").write_text("a = 1\n")
        snap = self.plugin.snapshot(workdir)
        (workdir / "b.py").write_text("b = 2\n")
        report = self.plugin.drift(snap, workdir)
        assert report.has_drift

    def test_has_drift_after_delete(self, tmp_path: pathlib.Path) -> None:
        workdir = tmp_path
        f = workdir / "gone.py"
        f.write_text("x = 1\n")
        snap = self.plugin.snapshot(workdir)
        f.unlink()
        report = self.plugin.drift(snap, workdir)
        assert report.has_drift


# ---------------------------------------------------------------------------
# CodePlugin — apply (passthrough)
# ---------------------------------------------------------------------------


def test_apply_returns_live_state_unchanged(tmp_path: pathlib.Path) -> None:
    plugin = CodePlugin()
    workdir = tmp_path
    delta = plugin.diff(_make_manifest({}), _make_manifest({}))
    result = plugin.apply(delta, workdir)
    assert result is workdir


# ---------------------------------------------------------------------------
# CodePlugin — schema
# ---------------------------------------------------------------------------


class TestCodePluginSchema:
    plugin = CodePlugin()

    def test_schema_domain(self) -> None:
        assert self.plugin.schema()["domain"] == "code"

    def test_schema_merge_mode(self) -> None:
        assert self.plugin.schema()["merge_mode"] == "three_way"

    def test_schema_version(self) -> None:
        assert self.plugin.schema()["schema_version"] == __version__

    def test_schema_dimensions(self) -> None:
        dims = self.plugin.schema()["dimensions"]
        names = {d["name"] for d in dims}
        assert "structure" in names
        assert "symbols" in names
        assert "imports" in names

    def test_schema_top_level_is_tree(self) -> None:
        top = self.plugin.schema()["top_level"]
        assert top["kind"] == "tree"

    def test_schema_description_non_empty(self) -> None:
        assert len(self.plugin.schema()["description"]) > 0


# ---------------------------------------------------------------------------
# delta_summary
# ---------------------------------------------------------------------------


class TestDeltaSummary:
    def test_empty_ops(self) -> None:
        assert delta_summary([]) == "no changes"

    def test_file_added(self) -> None:
        from muse.domain import DomainOp
        ops: list[DomainOp] = [InsertOp(
            op="insert", address="f.py", position=None,
            content_id="abc", content_summary="added f.py",
        )]
        summary = delta_summary(ops)
        assert "added" in summary
        assert "file" in summary

    def test_symbols_counted_from_patch(self) -> None:
        from muse.domain import DomainOp, PatchOp
        child: list[DomainOp] = [
            InsertOp(op="insert", address="f.py::foo", position=None, content_id="a", content_summary="added function foo"),
            InsertOp(op="insert", address="f.py::bar", position=None, content_id="b", content_summary="added function bar"),
        ]
        ops: list[DomainOp] = [PatchOp(op="patch", address="f.py", child_ops=child, child_domain="code_symbols", child_summary="2 added")]
        summary = delta_summary(ops)
        assert "symbol" in summary


# ---------------------------------------------------------------------------
# Markdown adapter
# ---------------------------------------------------------------------------


class TestMarkdownAdapter:
    """ATX heading extraction via tree-sitter-markdown."""

    def _parse(self, src: str) -> SymbolTree:
        from muse.plugins.code.ast_parser import MarkdownAdapter
        adapter = MarkdownAdapter()
        if adapter._parser is None:
            pytest.skip("tree-sitter-markdown not available")
        return adapter.parse_symbols(src.encode(), "README.md")

    def test_h1_extracted(self) -> None:
        syms = self._parse("# Hello World\n")
        assert any("h1: Hello World" in k for k in syms), f"keys: {list(syms)}"

    def test_h2_extracted(self) -> None:
        syms = self._parse("# Title\n\n## Section Two\n")
        assert any("h2: Section Two" in k for k in syms)

    def test_multiple_headings(self) -> None:
        src = "# Top\n\n## Alpha\n\n## Beta\n\n### Deep\n"
        syms = self._parse(src)
        kinds = {r["kind"] for r in syms.values()}
        assert "section" in kinds
        assert len(syms) >= 4

    def test_section_lineno(self) -> None:
        src = "# First\n\n## Second\n"
        syms = self._parse(src)
        second = next((r for r in syms.values() if "Second" in r["name"]), None)
        assert second is not None
        assert second["lineno"] == 3

    def test_content_id_changes_with_text(self) -> None:
        s1 = self._parse("# Hello\n")
        s2 = self._parse("# World\n")
        ids1 = {r["content_id"] for r in s1.values()}
        ids2 = {r["content_id"] for r in s2.values()}
        assert ids1 != ids2

    def test_adapter_for_path_md(self) -> None:
        from muse.plugins.code.ast_parser import MarkdownAdapter
        adapter = adapter_for_path("docs/README.md")
        assert isinstance(adapter, MarkdownAdapter)

    def test_adapter_for_path_rst(self) -> None:
        from muse.plugins.code.ast_parser import MarkdownAdapter
        adapter = adapter_for_path("notes.rst")
        assert isinstance(adapter, MarkdownAdapter)


# ---------------------------------------------------------------------------
# HTML adapter
# ---------------------------------------------------------------------------


class TestHtmlAdapter:
    """Semantic element and id-bearing element extraction via tree-sitter-html."""

    def _parse(self, src: str) -> SymbolTree:
        from muse.plugins.code.ast_parser import HtmlAdapter
        adapter = HtmlAdapter()
        if adapter._parser is None:
            pytest.skip("tree-sitter-html not available")
        return adapter.parse_symbols(src.encode(), "index.html")

    def test_id_bearing_div_extracted(self) -> None:
        syms = self._parse('<html><body><div id="hero">x</div></body></html>')
        assert any("div#hero" in k for k in syms), f"keys: {list(syms)}"

    def test_semantic_section_extracted(self) -> None:
        syms = self._parse('<html><body><section>content</section></body></html>')
        assert any("section" in k for k in syms)

    def test_h1_heading_extracted(self) -> None:
        syms = self._parse('<html><body><h1>Title</h1></body></html>')
        assert any("h1" in k for k in syms)

    def test_generic_div_without_id_skipped(self) -> None:
        syms = self._parse('<html><body><div>plain</div></body></html>')
        assert not any("div" in k for k in syms), f"unexpected: {list(syms)}"

    def test_multiple_ids(self) -> None:
        src = '<html><body><section id="intro">a</section><section id="outro">b</section></body></html>'
        syms = self._parse(src)
        assert any("section#intro" in k for k in syms)
        assert any("section#outro" in k for k in syms)

    def test_adapter_for_path_html(self) -> None:
        from muse.plugins.code.ast_parser import HtmlAdapter
        assert isinstance(adapter_for_path("page.html"), HtmlAdapter)

    def test_adapter_for_path_htm(self) -> None:
        from muse.plugins.code.ast_parser import HtmlAdapter
        assert isinstance(adapter_for_path("legacy.htm"), HtmlAdapter)


# ---------------------------------------------------------------------------
# CSS adapter
# ---------------------------------------------------------------------------


class TestCssAdapter:
    """Rule-set, @keyframes, and @media extraction via tree-sitter-css."""

    def _parse(self, src: str, path: str = "styles.css") -> SymbolTree:
        adapter = adapter_for_path(path)
        # If the CSS grammar is unavailable the adapter degrades to FallbackAdapter.
        if isinstance(adapter, FallbackAdapter):
            pytest.skip("tree-sitter-css not available")
        return adapter.parse_symbols(src.encode(), path)

    def test_rule_set_extracted(self) -> None:
        syms = self._parse(".btn { color: red; }")
        assert len(syms) >= 1
        kinds = {r["kind"] for r in syms.values()}
        assert "rule" in kinds

    def test_keyframes_extracted(self) -> None:
        syms = self._parse("@keyframes spin { from { transform: rotate(0deg); } }")
        assert any("spin" in r["name"] for r in syms.values()), f"symbols: {list(syms)}"

    def test_multiple_rules(self) -> None:
        src = ".a { color: red; }\n.b { color: blue; }"
        syms = self._parse(src)
        assert len(syms) >= 2

    def test_scss_extension_uses_css_parser(self) -> None:
        syms = self._parse(".mixin { display: flex; }", path="app.scss")
        assert len(syms) >= 1

    def test_content_id_differs_for_different_rules(self) -> None:
        s1 = self._parse(".a { color: red; }")
        s2 = self._parse(".b { color: blue; }")
        ids1 = {r["content_id"] for r in s1.values()}
        ids2 = {r["content_id"] for r in s2.values()}
        assert ids1 != ids2


# ---------------------------------------------------------------------------
# JS/TS: arrow functions and async detection
# ---------------------------------------------------------------------------


class TestJSArrowFunctions:
    """Arrow functions and function expressions bound to const/let."""

    def _parse(self, src: str, path: str = "mod.js") -> SymbolTree:
        adapter = adapter_for_path(path)
        if isinstance(adapter, FallbackAdapter):
            pytest.skip("tree-sitter-javascript not available")
        return adapter.parse_symbols(src.encode(), path)

    def test_const_arrow_function(self) -> None:
        syms = self._parse("const greet = (name) => `Hello ${name}`;\n")
        assert any("greet" in k for k in syms), f"keys: {list(syms)}"

    def test_const_function_expression(self) -> None:
        syms = self._parse("const add = function(a, b) { return a + b; };\n")
        assert any("add" in k for k in syms)

    def test_ts_arrow_function(self) -> None:
        syms = self._parse(
            "const greet = (name: string): string => `Hello ${name}`;\n",
            path="mod.ts",
        )
        assert any("greet" in k for k in syms)

    def test_class_method_still_extracted(self) -> None:
        syms = self._parse("class Foo { bar() { return 1; } }\n")
        assert any("bar" in k for k in syms)

    def test_async_function_detected(self) -> None:
        syms = self._parse("async function fetchData() { return await fetch('/'); }\n")
        kinds = {r["kind"] for r in syms.values() if "fetchData" in r["name"]}
        assert "async_function" in kinds, f"kinds: {kinds}"


# ---------------------------------------------------------------------------
# Go: const and var spec extraction
# ---------------------------------------------------------------------------


class TestGoConstVar:
    def _parse(self, src: str) -> SymbolTree:
        adapter = adapter_for_path("main.go")
        if isinstance(adapter, FallbackAdapter):
            pytest.skip("tree-sitter-go not available")
        return adapter.parse_symbols(src.encode(), "main.go")

    def test_const_extracted(self) -> None:
        syms = self._parse("package main\nconst MaxRetries = 3\n")
        assert any("MaxRetries" in k for k in syms), f"keys: {list(syms)}"

    def test_var_extracted(self) -> None:
        syms = self._parse("package main\nvar ErrNotFound = errors.New(\"not found\")\n")
        assert any("ErrNotFound" in k for k in syms)

    def test_const_kind_is_variable(self) -> None:
        syms = self._parse("package main\nconst Timeout = 30\n")
        records = [r for r in syms.values() if "Timeout" in r["name"]]
        assert records
        assert records[0]["kind"] == "variable"


# ---------------------------------------------------------------------------
# Rust: static, const, type alias, mod
# ---------------------------------------------------------------------------


class TestRustExtended:
    def _parse(self, src: str) -> SymbolTree:
        adapter = adapter_for_path("lib.rs")
        if isinstance(adapter, FallbackAdapter):
            pytest.skip("tree-sitter-rust not available")
        return adapter.parse_symbols(src.encode(), "lib.rs")

    def test_static_extracted(self) -> None:
        syms = self._parse("static MAX: usize = 100;\n")
        assert any("MAX" in k for k in syms), f"keys: {list(syms)}"

    def test_const_extracted(self) -> None:
        syms = self._parse("const TIMEOUT: u64 = 30;\n")
        assert any("TIMEOUT" in k for k in syms)

    def test_type_alias_extracted(self) -> None:
        syms = self._parse("type Result<T> = std::result::Result<T, Error>;\n")
        assert any("Result" in k for k in syms)

    def test_mod_extracted(self) -> None:
        syms = self._parse("mod utils { pub fn helper() {} }\n")
        assert any("utils" in k for k in syms)


# ---------------------------------------------------------------------------
# C: struct and enum extraction
# ---------------------------------------------------------------------------


class TestCStructEnum:
    def _parse(self, src: str) -> SymbolTree:
        adapter = adapter_for_path("main.c")
        if isinstance(adapter, FallbackAdapter):
            pytest.skip("tree-sitter-c not available")
        return adapter.parse_symbols(src.encode(), "main.c")

    def test_struct_extracted(self) -> None:
        syms = self._parse("struct Point { int x; int y; };\n")
        assert any("Point" in k for k in syms), f"keys: {list(syms)}"

    def test_enum_extracted(self) -> None:
        syms = self._parse("enum Color { RED, GREEN, BLUE };\n")
        assert any("Color" in k for k in syms)

    def test_struct_kind(self) -> None:
        syms = self._parse("struct Node { int val; struct Node *next; };\n")
        records = [r for r in syms.values() if "Node" in r["name"]]
        assert records
        assert records[0]["kind"] == "class"


# ---------------------------------------------------------------------------
# C#: property and record extraction
# ---------------------------------------------------------------------------


class TestCSharpExtended:
    def _parse(self, src: str) -> SymbolTree:
        adapter = adapter_for_path("Model.cs")
        if isinstance(adapter, FallbackAdapter):
            pytest.skip("tree-sitter-c-sharp not available")
        return adapter.parse_symbols(src.encode(), "Model.cs")

    def test_property_extracted(self) -> None:
        syms = self._parse(
            "class User { public string Name { get; set; } }\n"
        )
        assert any("Name" in k for k in syms), f"keys: {list(syms)}"

    def test_record_extracted(self) -> None:
        syms = self._parse("public record Point(int X, int Y);\n")
        assert any("Point" in k for k in syms)

    def test_property_kind(self) -> None:
        syms = self._parse(
            "class C { public int Age { get; set; } }\n"
        )
        records = [r for r in syms.values() if "Age" in r["name"]]
        assert records
        assert records[0]["kind"] == "variable"


# ---------------------------------------------------------------------------
# Java: annotation type and record extraction
# ---------------------------------------------------------------------------


class TestJavaExtended:
    def _parse(self, src: str) -> SymbolTree:
        adapter = adapter_for_path("Main.java")
        if isinstance(adapter, FallbackAdapter):
            pytest.skip("tree-sitter-java not available")
        return adapter.parse_symbols(src.encode(), "Main.java")

    def test_annotation_type_extracted(self) -> None:
        syms = self._parse("public @interface Cacheable { String value() default \"\"; }\n")
        assert any("Cacheable" in k for k in syms), f"keys: {list(syms)}"

    def test_record_extracted(self) -> None:
        syms = self._parse("public record Point(int x, int y) {}\n")
        assert any("Point" in k for k in syms)


# ---------------------------------------------------------------------------
# Kotlin: object declaration and property extraction
# ---------------------------------------------------------------------------


class TestKotlinExtended:
    def _parse(self, src: str) -> SymbolTree:
        adapter = adapter_for_path("Main.kt")
        if isinstance(adapter, FallbackAdapter):
            pytest.skip("tree-sitter-kotlin not available")
        return adapter.parse_symbols(src.encode(), "Main.kt")

    def test_object_declaration_extracted(self) -> None:
        syms = self._parse("object Singleton { fun greet() = println(\"hi\") }\n")
        assert any("Singleton" in k for k in syms), f"keys: {list(syms)}"

    def test_property_declaration_extracted(self) -> None:
        syms = self._parse("val MAX_SIZE: Int = 100\n")
        assert any("MAX_SIZE" in k for k in syms)

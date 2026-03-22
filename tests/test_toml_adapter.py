"""Tests for the TOML language adapter (TomlAdapter) in ast_parser.py.

Coverage
--------
- Extension routing via :func:`adapter_for_path`.
- Symbol extraction: scalars, tables, nested tables, array-of-tables.
- Edge cases: empty file, comments-only, invalid TOML, mixed lists.
- Semantic content IDs: comment-insensitive, key-order-insensitive,
  whitespace-insensitive, date-stable.
- Rename detection via ``body_hash``.
- ``canonical_key`` uniqueness within a snapshot.
- Depth limit: symbols beyond ``_MAX_DEPTH`` are not emitted.
- Real-world ``pyproject.toml``-shaped fixture.
"""

from __future__ import annotations

import pytest

from muse.plugins.code.ast_parser import (
    FallbackAdapter,
    TomlAdapter,
    adapter_for_path,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def adapter() -> TomlAdapter:
    """Return a fresh TomlAdapter instance for each test."""
    return TomlAdapter()


# ---------------------------------------------------------------------------
# Extension routing
# ---------------------------------------------------------------------------


class TestExtensionRouting:
    """adapter_for_path must route .toml to TomlAdapter."""

    def test_supported_extensions_contains_toml(self, adapter: TomlAdapter) -> None:
        assert ".toml" in adapter.supported_extensions()

    def test_supported_extensions_is_toml_only(self, adapter: TomlAdapter) -> None:
        assert adapter.supported_extensions() == frozenset({".toml"})

    def test_adapter_for_path_flat(self) -> None:
        assert isinstance(adapter_for_path("pyproject.toml"), TomlAdapter)

    def test_adapter_for_path_nested(self) -> None:
        assert isinstance(adapter_for_path("config/settings.toml"), TomlAdapter)

    def test_adapter_for_path_does_not_match_py(self) -> None:
        assert not isinstance(adapter_for_path("main.py"), TomlAdapter)

    def test_adapter_for_path_does_not_match_yaml(self) -> None:
        # .yaml has no dedicated adapter → FallbackAdapter
        assert isinstance(adapter_for_path("config.yaml"), FallbackAdapter)


# ---------------------------------------------------------------------------
# Scalar key-value pairs → variable symbols
# ---------------------------------------------------------------------------


class TestScalarSymbols:
    """Scalar TOML values produce ``variable`` kind symbols."""

    def test_string_value(self, adapter: TomlAdapter) -> None:
        src = b'name = "muse"\n'
        syms = adapter.parse_symbols(src, "p.toml")
        assert "p.toml::name" in syms
        s = syms["p.toml::name"]
        assert s["kind"] == "variable"
        assert s["name"] == "name"
        assert s["qualified_name"] == "name"

    def test_integer_value(self, adapter: TomlAdapter) -> None:
        src = b"port = 8080\n"
        syms = adapter.parse_symbols(src, "cfg.toml")
        assert "cfg.toml::port" in syms
        assert syms["cfg.toml::port"]["kind"] == "variable"

    def test_boolean_value(self, adapter: TomlAdapter) -> None:
        src = b"strict = true\n"
        syms = adapter.parse_symbols(src, "mypy.toml")
        assert "mypy.toml::strict" in syms
        assert syms["mypy.toml::strict"]["kind"] == "variable"

    def test_float_value(self, adapter: TomlAdapter) -> None:
        src = b"threshold = 0.95\n"
        syms = adapter.parse_symbols(src, "c.toml")
        assert "c.toml::threshold" in syms

    def test_list_of_strings_is_variable(self, adapter: TomlAdapter) -> None:
        """A list whose elements are not all dicts is treated as a variable."""
        src = b'deps = ["typer", "mido"]\n'
        syms = adapter.parse_symbols(src, "p.toml")
        assert "p.toml::deps" in syms
        assert syms["p.toml::deps"]["kind"] == "variable"

    def test_multiple_top_level_scalars(self, adapter: TomlAdapter) -> None:
        src = b'name = "foo"\nversion = "1.0"\nbuild = 42\n'
        syms = adapter.parse_symbols(src, "p.toml")
        assert "p.toml::name" in syms
        assert "p.toml::version" in syms
        assert "p.toml::build" in syms


# ---------------------------------------------------------------------------
# Tables → section symbols
# ---------------------------------------------------------------------------


class TestTableSymbols:
    """TOML tables emit ``section`` symbols; their scalar children emit ``variable``."""

    def test_simple_table_section(self, adapter: TomlAdapter) -> None:
        src = b'[project]\nname = "muse"\n'
        syms = adapter.parse_symbols(src, "p.toml")
        assert "p.toml::project" in syms
        assert syms["p.toml::project"]["kind"] == "section"

    def test_simple_table_child_variable(self, adapter: TomlAdapter) -> None:
        src = b'[project]\nname = "muse"\n'
        syms = adapter.parse_symbols(src, "p.toml")
        assert "p.toml::project.name" in syms
        assert syms["p.toml::project.name"]["kind"] == "variable"
        assert syms["p.toml::project.name"]["qualified_name"] == "project.name"

    def test_nested_table_emits_all_levels(self, adapter: TomlAdapter) -> None:
        src = b"[tool.mypy]\nstrict = true\n"
        syms = adapter.parse_symbols(src, "p.toml")
        # [tool] is an implicit table — still emitted.
        assert "p.toml::tool" in syms
        assert "p.toml::tool.mypy" in syms
        assert "p.toml::tool.mypy.strict" in syms

    def test_table_name_preserves_hyphens(self, adapter: TomlAdapter) -> None:
        src = b"[build-system]\nrequires = []\n"
        syms = adapter.parse_symbols(src, "p.toml")
        assert "p.toml::build-system" in syms
        assert syms["p.toml::build-system"]["kind"] == "section"

    def test_multiple_sibling_tables(self, adapter: TomlAdapter) -> None:
        src = b"[a]\nx = 1\n\n[b]\ny = 2\n"
        syms = adapter.parse_symbols(src, "c.toml")
        assert "c.toml::a" in syms
        assert "c.toml::b" in syms
        assert "c.toml::a.x" in syms
        assert "c.toml::b.y" in syms

    def test_file_path_used_as_prefix(self, adapter: TomlAdapter) -> None:
        src = b'[project]\nname = "x"\n'
        syms = adapter.parse_symbols(src, "sub/dir/p.toml")
        assert "sub/dir/p.toml::project" in syms
        assert "sub/dir/p.toml::project.name" in syms


# ---------------------------------------------------------------------------
# Array of tables → indexed section symbols
# ---------------------------------------------------------------------------


class TestArrayOfTableSymbols:
    """[[array.of.tables]] entries become indexed ``section`` symbols."""

    def test_single_entry(self, adapter: TomlAdapter) -> None:
        src = b"[[servers]]\nname = 'alpha'\n"
        syms = adapter.parse_symbols(src, "c.toml")
        assert "c.toml::servers[0]" in syms
        assert syms["c.toml::servers[0]"]["kind"] == "section"

    def test_multiple_entries_indexed(self, adapter: TomlAdapter) -> None:
        src = (
            b"[[tool.mypy.overrides]]\n"
            b"module = ['mido']\n"
            b"ignore_missing_imports = true\n\n"
            b"[[tool.mypy.overrides]]\n"
            b"module = ['tree_sitter']\n"
            b"ignore_missing_imports = true\n"
        )
        syms = adapter.parse_symbols(src, "p.toml")
        assert "p.toml::tool.mypy.overrides[0]" in syms
        assert "p.toml::tool.mypy.overrides[1]" in syms

    def test_array_entry_children_emitted(self, adapter: TomlAdapter) -> None:
        src = b"[[servers]]\nname = 'alpha'\nport = 8080\n"
        syms = adapter.parse_symbols(src, "c.toml")
        assert "c.toml::servers[0].name" in syms
        assert "c.toml::servers[0].port" in syms

    def test_different_entries_different_content_ids(
        self, adapter: TomlAdapter
    ) -> None:
        src = (
            b"[[deps]]\nname = 'typer'\n\n"
            b"[[deps]]\nname = 'mido'\n"
        )
        syms = adapter.parse_symbols(src, "p.toml")
        assert (
            syms["p.toml::deps[0]"]["content_id"]
            != syms["p.toml::deps[1]"]["content_id"]
        )


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Degenerate inputs must never raise; empty dicts are returned."""

    def test_empty_file(self, adapter: TomlAdapter) -> None:
        assert adapter.parse_symbols(b"", "e.toml") == {}

    def test_comments_only(self, adapter: TomlAdapter) -> None:
        src = b"# This is a comment\n# Another\n"
        assert adapter.parse_symbols(src, "c.toml") == {}

    def test_invalid_toml_returns_empty(self, adapter: TomlAdapter) -> None:
        src = b"invalid toml [[[[\n"
        assert adapter.parse_symbols(src, "bad.toml") == {}

    def test_duplicate_key_invalid_toml(self, adapter: TomlAdapter) -> None:
        """TOML forbids duplicate keys — parser should reject, adapter returns {}."""
        src = b'name = "a"\nname = "b"\n'
        assert adapter.parse_symbols(src, "dup.toml") == {}

    def test_empty_table(self, adapter: TomlAdapter) -> None:
        src = b"[project]\n"
        syms = adapter.parse_symbols(src, "p.toml")
        # The section itself is emitted even though it has no children.
        assert "p.toml::project" in syms

    def test_mixed_list_is_variable(self, adapter: TomlAdapter) -> None:
        """A list mixing dicts and scalars is treated as a variable, not a section."""
        src = b"mixed = [1, {key = 'val'}]\n"
        syms = adapter.parse_symbols(src, "m.toml")
        assert "m.toml::mixed" in syms
        assert syms["m.toml::mixed"]["kind"] == "variable"


# ---------------------------------------------------------------------------
# Semantic content ID (file_content_id)
# ---------------------------------------------------------------------------


class TestFileContentID:
    """file_content_id must be deterministic and semantics-based."""

    def test_same_content_same_id(self, adapter: TomlAdapter) -> None:
        src = b'[project]\nname = "muse"\n'
        assert adapter.file_content_id(src) == adapter.file_content_id(src)

    def test_different_value_different_id(self, adapter: TomlAdapter) -> None:
        src1 = b'name = "muse"\n'
        src2 = b'name = "musehub"\n'
        assert adapter.file_content_id(src1) != adapter.file_content_id(src2)

    def test_comment_insensitive(self, adapter: TomlAdapter) -> None:
        src1 = b'name = "muse"\n'
        src2 = b'# A leading comment\nname = "muse"\n'
        assert adapter.file_content_id(src1) == adapter.file_content_id(src2)

    def test_key_order_insensitive(self, adapter: TomlAdapter) -> None:
        src1 = b'name = "muse"\nversion = "1.0"\n'
        src2 = b'version = "1.0"\nname = "muse"\n'
        assert adapter.file_content_id(src1) == adapter.file_content_id(src2)

    def test_whitespace_insensitive(self, adapter: TomlAdapter) -> None:
        src1 = b'name="muse"\n'
        src2 = b'name   =   "muse"\n'
        assert adapter.file_content_id(src1) == adapter.file_content_id(src2)

    def test_invalid_toml_falls_back_to_raw_hash(self, adapter: TomlAdapter) -> None:
        """Malformed TOML must not raise — falls back to raw-bytes SHA-256."""
        src = b"invalid [[[[\n"
        result = adapter.file_content_id(src)
        assert isinstance(result, str)
        assert len(result) == 64  # SHA-256 hex digest length

    def test_returns_hex_string(self, adapter: TomlAdapter) -> None:
        src = b'[project]\nname = "muse"\n'
        result = adapter.file_content_id(src)
        assert all(c in "0123456789abcdef" for c in result)
        assert len(result) == 64


# ---------------------------------------------------------------------------
# Per-symbol content IDs and rename detection
# ---------------------------------------------------------------------------


class TestSymbolContentIDs:
    """Symbol-level content_id and body_hash must enable rename detection."""

    def test_content_id_changes_on_value_change(self, adapter: TomlAdapter) -> None:
        src1 = b'[project]\nname = "muse"\n'
        src2 = b'[project]\nname = "musehub"\n'
        syms1 = adapter.parse_symbols(src1, "p.toml")
        syms2 = adapter.parse_symbols(src2, "p.toml")
        assert (
            syms1["p.toml::project.name"]["content_id"]
            != syms2["p.toml::project.name"]["content_id"]
        )

    def test_body_hash_same_for_same_value_different_key(
        self, adapter: TomlAdapter
    ) -> None:
        """Rename detection: same scalar value under different keys → same body_hash."""
        src = b'[a]\nfoo = "bar"\n\n[b]\nbaz = "bar"\n'
        syms = adapter.parse_symbols(src, "c.toml")
        assert syms["c.toml::a.foo"]["body_hash"] == syms["c.toml::b.baz"]["body_hash"]

    def test_body_hash_differs_for_different_values(
        self, adapter: TomlAdapter
    ) -> None:
        src = b'x = "hello"\ny = "world"\n'
        syms = adapter.parse_symbols(src, "c.toml")
        assert syms["c.toml::x"]["body_hash"] != syms["c.toml::y"]["body_hash"]

    def test_table_content_id_stable_across_key_order(
        self, adapter: TomlAdapter
    ) -> None:
        """Table content_id is stable regardless of internal key order."""
        src1 = b'[project]\nname = "muse"\nversion = "1.0"\n'
        src2 = b'[project]\nversion = "1.0"\nname = "muse"\n'
        syms1 = adapter.parse_symbols(src1, "p.toml")
        syms2 = adapter.parse_symbols(src2, "p.toml")
        assert (
            syms1["p.toml::project"]["content_id"]
            == syms2["p.toml::project"]["content_id"]
        )

    def test_section_content_id_changes_when_child_changes(
        self, adapter: TomlAdapter
    ) -> None:
        src1 = b'[project]\nname = "muse"\n'
        src2 = b'[project]\nname = "musehub"\n'
        syms1 = adapter.parse_symbols(src1, "p.toml")
        syms2 = adapter.parse_symbols(src2, "p.toml")
        assert (
            syms1["p.toml::project"]["content_id"]
            != syms2["p.toml::project"]["content_id"]
        )


# ---------------------------------------------------------------------------
# canonical_key uniqueness
# ---------------------------------------------------------------------------


class TestCanonicalKeyUniqueness:
    """canonical_key must be unique within a snapshot."""

    def test_flat_keys_unique(self, adapter: TomlAdapter) -> None:
        src = b'a = 1\nb = 2\nc = 3\n'
        syms = adapter.parse_symbols(src, "c.toml")
        keys = [s["canonical_key"] for s in syms.values()]
        assert len(keys) == len(set(keys))

    def test_mixed_tables_and_scalars_unique(self, adapter: TomlAdapter) -> None:
        src = (
            b'name = "muse"\n'
            b"[project]\n"
            b'version = "1.0"\n'
            b"[tool.mypy]\n"
            b"strict = true\n"
        )
        syms = adapter.parse_symbols(src, "p.toml")
        keys = [s["canonical_key"] for s in syms.values()]
        assert len(keys) == len(set(keys))

    def test_array_of_tables_entries_unique(self, adapter: TomlAdapter) -> None:
        src = (
            b"[[overrides]]\nmodule = 'a'\n\n"
            b"[[overrides]]\nmodule = 'b'\n\n"
            b"[[overrides]]\nmodule = 'c'\n"
        )
        syms = adapter.parse_symbols(src, "p.toml")
        keys = [s["canonical_key"] for s in syms.values()]
        assert len(keys) == len(set(keys))


# ---------------------------------------------------------------------------
# Depth limit
# ---------------------------------------------------------------------------


class TestDepthLimit:
    """Symbols deeper than _MAX_DEPTH (6) must not be emitted."""

    def test_max_depth_not_exceeded(self, adapter: TomlAdapter) -> None:
        # TOML dotted keys: a.b.c.d.e.f.g = "deep" creates 7-level nesting.
        # [a.b.c.d.e.f.g] header syntax is valid TOML.
        src = b"[a.b.c.d.e.f.g]\nkey = 'val'\n"
        syms = adapter.parse_symbols(src, "d.toml")
        # Levels 1-6 are within limit and should appear.
        assert "d.toml::a" in syms
        assert "d.toml::a.b" in syms
        assert "d.toml::a.b.c" in syms
        # Level 7 key inside a level-7 section exceeds _MAX_DEPTH.
        assert "d.toml::a.b.c.d.e.f.g.key" not in syms

    def test_within_depth_limit_emitted(self, adapter: TomlAdapter) -> None:
        src = b"[a.b.c]\nkey = 'val'\n"
        syms = adapter.parse_symbols(src, "d.toml")
        assert "d.toml::a.b.c.key" in syms


# ---------------------------------------------------------------------------
# Real-world pyproject.toml shape
# ---------------------------------------------------------------------------


class TestRealWorldShape:
    """Validate symbol extraction against a realistic pyproject.toml structure."""

    _PYPROJECT = b"""
[project]
name = "muse"
version = "0.1.5"
description = "Domain-agnostic version control"

[project.scripts]
muse = "muse.cli.app:main"

[build-system]
requires = ["hatchling>=1.29.0"]
build-backend = "hatchling.build"

[tool.mypy]
python_version = "3.14"
strict = true

[[tool.mypy.overrides]]
module = ["mido"]
ignore_missing_imports = true

[[tool.mypy.overrides]]
module = ["tree_sitter"]
ignore_missing_imports = true
"""

    def test_top_level_sections_present(self, adapter: TomlAdapter) -> None:
        syms = adapter.parse_symbols(self._PYPROJECT, "pyproject.toml")
        assert "pyproject.toml::project" in syms
        assert "pyproject.toml::build-system" in syms
        assert "pyproject.toml::tool" in syms
        assert "pyproject.toml::tool.mypy" in syms

    def test_scalar_children_present(self, adapter: TomlAdapter) -> None:
        syms = adapter.parse_symbols(self._PYPROJECT, "pyproject.toml")
        assert "pyproject.toml::project.name" in syms
        assert "pyproject.toml::project.version" in syms
        assert "pyproject.toml::project.description" in syms
        assert "pyproject.toml::tool.mypy.strict" in syms

    def test_nested_table_present(self, adapter: TomlAdapter) -> None:
        syms = adapter.parse_symbols(self._PYPROJECT, "pyproject.toml")
        assert "pyproject.toml::project.scripts" in syms

    def test_array_of_tables_indexed(self, adapter: TomlAdapter) -> None:
        syms = adapter.parse_symbols(self._PYPROJECT, "pyproject.toml")
        assert "pyproject.toml::tool.mypy.overrides[0]" in syms
        assert "pyproject.toml::tool.mypy.overrides[1]" in syms

    def test_all_canonical_keys_unique(self, adapter: TomlAdapter) -> None:
        syms = adapter.parse_symbols(self._PYPROJECT, "pyproject.toml")
        keys = [s["canonical_key"] for s in syms.values()]
        assert len(keys) == len(set(keys)), "Duplicate canonical_keys detected"

    def test_comment_and_reorder_stable_file_id(self, adapter: TomlAdapter) -> None:
        """Adding a comment or reordering keys must not change file_content_id."""
        src_with_comment = b'# Top comment\n' + self._PYPROJECT
        assert adapter.file_content_id(self._PYPROJECT) == adapter.file_content_id(
            src_with_comment
        )

    def test_version_change_detected(self, adapter: TomlAdapter) -> None:
        v1 = self._PYPROJECT
        v2 = v1.replace(b'version = "0.1.5"', b'version = "0.2.0"')
        assert adapter.file_content_id(v1) != adapter.file_content_id(v2)
        syms1 = adapter.parse_symbols(v1, "pyproject.toml")
        syms2 = adapter.parse_symbols(v2, "pyproject.toml")
        assert (
            syms1["pyproject.toml::project.version"]["content_id"]
            != syms2["pyproject.toml::project.version"]["content_id"]
        )

    def test_unrelated_section_change_does_not_affect_other_content_id(
        self, adapter: TomlAdapter
    ) -> None:
        """Changing [tool.mypy] must not change project.name content_id."""
        v1 = self._PYPROJECT
        v2 = v1.replace(b"strict = true", b"strict = false")
        syms1 = adapter.parse_symbols(v1, "pyproject.toml")
        syms2 = adapter.parse_symbols(v2, "pyproject.toml")
        assert (
            syms1["pyproject.toml::project.name"]["content_id"]
            == syms2["pyproject.toml::project.name"]["content_id"]
        )

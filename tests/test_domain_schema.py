"""Tests for Phase 2 domain schema declaration and plugin registry lookup.

Verifies that:
- ``MusicPlugin.schema()`` returns a fully-typed ``DomainSchema``.
- The four dimensions have the correct element schema types.
- The schema is JSON round-trippable (all values are JSON-serialisable).
- ``schema_for()`` in the plugin registry performs the correct lookup.
- The protocol assertion still holds after adding ``schema()``.
"""
from __future__ import annotations

import json

import pytest

from muse.core.schema import (
    DomainSchema,
    SequenceSchema,
    SetSchema,
    TensorSchema,
    TreeSchema,
)
from muse.domain import MuseDomainPlugin
from muse.plugins.music.plugin import MusicPlugin
from muse.plugins.registry import registered_domains, schema_for


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture()
def music_plugin() -> MusicPlugin:
    return MusicPlugin()


@pytest.fixture()
def music_schema(music_plugin: MusicPlugin) -> DomainSchema:
    return music_plugin.schema()


# ===========================================================================
# MusicPlugin.schema() structure
# ===========================================================================


class TestMusicPluginSchema:
    def test_schema_returns_domain_schema(self, music_schema: DomainSchema) -> None:
        assert isinstance(music_schema, dict)
        assert music_schema["domain"] == "music"

    def test_schema_version_is_1(self, music_schema: DomainSchema) -> None:
        assert music_schema["schema_version"] == 1

    def test_schema_has_four_dimensions(self, music_schema: DomainSchema) -> None:
        assert len(music_schema["dimensions"]) == 4

    def test_dimension_names(self, music_schema: DomainSchema) -> None:
        names = [d["name"] for d in music_schema["dimensions"]]
        assert names == ["melodic", "harmonic", "dynamic", "structural"]

    def test_top_level_is_set_schema(self, music_schema: DomainSchema) -> None:
        top = music_schema["top_level"]
        assert top["kind"] == "set"
        assert isinstance(top, dict)

    def test_top_level_set_schema_fields(self, music_schema: DomainSchema) -> None:
        top = music_schema["top_level"]
        assert top["kind"] == "set"
        # Narrow to SetSchema for field access
        if top["kind"] == "set":
            assert top["element_type"] == "audio_file"
            assert top["identity"] == "by_content"

    def test_melodic_dimension_is_sequence(self, music_schema: DomainSchema) -> None:
        melodic = next(d for d in music_schema["dimensions"] if d["name"] == "melodic")
        schema = melodic["schema"]
        assert schema["kind"] == "sequence"

    def test_melodic_dimension_element_type(self, music_schema: DomainSchema) -> None:
        melodic = next(d for d in music_schema["dimensions"] if d["name"] == "melodic")
        schema = melodic["schema"]
        if schema["kind"] == "sequence":
            assert schema["element_type"] == "note_event"
            assert schema["diff_algorithm"] == "lcs"

    def test_harmonic_dimension_is_sequence(self, music_schema: DomainSchema) -> None:
        harmonic = next(d for d in music_schema["dimensions"] if d["name"] == "harmonic")
        schema = harmonic["schema"]
        assert schema["kind"] == "sequence"

    def test_dynamic_dimension_is_tensor(self, music_schema: DomainSchema) -> None:
        dynamic = next(d for d in music_schema["dimensions"] if d["name"] == "dynamic")
        schema = dynamic["schema"]
        assert schema["kind"] == "tensor"

    def test_dynamic_tensor_schema_fields(self, music_schema: DomainSchema) -> None:
        dynamic = next(d for d in music_schema["dimensions"] if d["name"] == "dynamic")
        schema = dynamic["schema"]
        if schema["kind"] == "tensor":
            assert schema["dtype"] == "float32"
            assert schema["rank"] == 1
            assert schema["epsilon"] == 1.0
            assert schema["diff_mode"] == "sparse"

    def test_structural_dimension_is_tree(self, music_schema: DomainSchema) -> None:
        structural = next(d for d in music_schema["dimensions"] if d["name"] == "structural")
        schema = structural["schema"]
        assert schema["kind"] == "tree"

    def test_structural_tree_schema_fields(self, music_schema: DomainSchema) -> None:
        structural = next(d for d in music_schema["dimensions"] if d["name"] == "structural")
        schema = structural["schema"]
        if schema["kind"] == "tree":
            assert schema["node_type"] == "track_node"
            assert schema["diff_algorithm"] == "zhang_shasha"

    def test_melodic_independent_merge_is_true(self, music_schema: DomainSchema) -> None:
        melodic = next(d for d in music_schema["dimensions"] if d["name"] == "melodic")
        assert melodic["independent_merge"] is True

    def test_structural_independent_merge_is_false(self, music_schema: DomainSchema) -> None:
        structural = next(d for d in music_schema["dimensions"] if d["name"] == "structural")
        assert structural["independent_merge"] is False

    def test_merge_mode_is_three_way(self, music_schema: DomainSchema) -> None:
        assert music_schema["merge_mode"] == "three_way"

    def test_schema_round_trips_json(self, music_schema: DomainSchema) -> None:
        serialised = json.dumps(music_schema)
        restored = json.loads(serialised)
        assert restored["domain"] == music_schema["domain"]
        assert restored["schema_version"] == music_schema["schema_version"]
        assert len(restored["dimensions"]) == len(music_schema["dimensions"])
        # top_level round-trips
        assert restored["top_level"]["kind"] == music_schema["top_level"]["kind"]

    def test_schema_description_is_non_empty(self, music_schema: DomainSchema) -> None:
        assert isinstance(music_schema["description"], str)
        assert len(music_schema["description"]) > 0

    def test_all_dimension_schemas_have_kind(self, music_schema: DomainSchema) -> None:
        for dim in music_schema["dimensions"]:
            assert "kind" in dim["schema"]


# ===========================================================================
# Plugin registry schema lookup
# ===========================================================================


class TestPluginRegistrySchemaLookup:
    def test_schema_for_music_returns_domain_schema(self) -> None:
        result = schema_for("music")
        assert result is not None
        assert result["domain"] == "music"

    def test_schema_for_unknown_domain_returns_none(self) -> None:
        result = schema_for("nonexistent_domain_xyz")
        assert result is None

    def test_schema_for_returns_same_type_as_plugin_schema(self) -> None:
        plugin = MusicPlugin()
        direct = plugin.schema()
        via_registry = schema_for("music")
        assert via_registry is not None
        assert via_registry["domain"] == direct["domain"]
        assert via_registry["schema_version"] == direct["schema_version"]

    def test_registered_domains_still_contains_music(self) -> None:
        assert "music" in registered_domains()

    def test_schema_for_all_registered_domains_returns_non_none(self) -> None:
        for domain in registered_domains():
            result = schema_for(domain)
            assert result is not None, f"schema_for({domain!r}) returned None"


# ===========================================================================
# Protocol conformance
# ===========================================================================


class TestProtocolConformance:
    def test_music_plugin_satisfies_protocol(self) -> None:
        plugin = MusicPlugin()
        assert isinstance(plugin, MuseDomainPlugin)

    def test_schema_method_is_callable(self) -> None:
        plugin = MusicPlugin()
        assert callable(plugin.schema)

    def test_schema_returns_dict(self) -> None:
        plugin = MusicPlugin()
        result = plugin.schema()
        assert isinstance(result, dict)

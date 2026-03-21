"""Tests for domain schema declaration and plugin registry lookup.

Verifies that:
- ``MidiPlugin.schema()`` returns a fully-typed ``DomainSchema``.
- All 21 MIDI dimensions are declared with the correct schema types.
- Independence flags match the semantic MIDI merge model.
- The schema is JSON round-trippable (all values are JSON-serialisable).
- ``schema_for()`` in the plugin registry performs the correct lookup.
- The protocol assertion still holds after adding ``schema()``.
"""

import json

import pytest

from muse._version import __version__
from muse.core.schema import DomainSchema
from muse.domain import MuseDomainPlugin
from muse.plugins.midi.plugin import MidiPlugin
from muse.plugins.registry import registered_domains, schema_for

# ---------------------------------------------------------------------------
# Expected dimension layout for the 21-dimension MIDI schema
# ---------------------------------------------------------------------------

# (name, independent_merge, schema_kind)
_EXPECTED_DIMS: list[tuple[str, bool, str]] = [
    # Expressive note content
    ("notes",            True,  "sequence"),
    ("pitch_bend",       True,  "tensor"),
    ("channel_pressure", True,  "tensor"),
    ("poly_pressure",    True,  "tensor"),
    # Named CC controllers
    ("cc_modulation",    True,  "tensor"),
    ("cc_volume",        True,  "tensor"),
    ("cc_pan",           True,  "tensor"),
    ("cc_expression",    True,  "tensor"),
    ("cc_sustain",       True,  "tensor"),
    ("cc_portamento",    True,  "tensor"),
    ("cc_sostenuto",     True,  "tensor"),
    ("cc_soft_pedal",    True,  "tensor"),
    ("cc_reverb",        True,  "tensor"),
    ("cc_chorus",        True,  "tensor"),
    ("cc_other",         True,  "tensor"),
    # Patch selection
    ("program_change",   True,  "sequence"),
    # Non-independent timeline metadata
    ("tempo_map",        False, "sequence"),
    ("time_signatures",  False, "sequence"),
    # Tonal / annotation metadata
    ("key_signatures",   True,  "sequence"),
    ("markers",          True,  "sequence"),
    # Track structure (non-independent)
    ("track_structure",  False, "tree"),
]

_NON_INDEPENDENT = frozenset(
    name for name, independent, _ in _EXPECTED_DIMS if not independent
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def midi_plugin() -> MidiPlugin:
    return MidiPlugin()


@pytest.fixture()
def midi_schema(midi_plugin: MidiPlugin) -> DomainSchema:
    return midi_plugin.schema()


# ===========================================================================
# MidiPlugin.schema() — top-level structure
# ===========================================================================


class TestMidiPluginSchema:
    def test_schema_returns_dict(self, midi_schema: DomainSchema) -> None:
        assert isinstance(midi_schema, dict)

    def test_domain_is_midi(self, midi_schema: DomainSchema) -> None:
        assert midi_schema["domain"] == "midi"

    def test_schema_version_matches_package(self, midi_schema: DomainSchema) -> None:
        assert midi_schema["schema_version"] == __version__

    def test_merge_mode_is_three_way(self, midi_schema: DomainSchema) -> None:
        assert midi_schema["merge_mode"] == "three_way"

    def test_description_is_non_empty(self, midi_schema: DomainSchema) -> None:
        assert isinstance(midi_schema["description"], str)
        assert len(midi_schema["description"]) > 0

    def test_top_level_is_set_schema(self, midi_schema: DomainSchema) -> None:
        top = midi_schema["top_level"]
        assert top["kind"] == "set"

    def test_top_level_element_type(self, midi_schema: DomainSchema) -> None:
        top = midi_schema["top_level"]
        assert top["kind"] == "set"
        assert top["element_type"] == "audio_file"
        assert top["identity"] == "by_content"


# ===========================================================================
# 21-dimension layout
# ===========================================================================


class TestMidiDimensions:
    def test_exactly_21_dimensions(self, midi_schema: DomainSchema) -> None:
        assert len(midi_schema["dimensions"]) == 21

    def test_all_expected_dimension_names_present(self, midi_schema: DomainSchema) -> None:
        names = {d["name"] for d in midi_schema["dimensions"]}
        expected = {name for name, _, _ in _EXPECTED_DIMS}
        assert names == expected

    def test_dimension_order_matches_spec(self, midi_schema: DomainSchema) -> None:
        names = [d["name"] for d in midi_schema["dimensions"]]
        expected = [name for name, _, _ in _EXPECTED_DIMS]
        assert names == expected

    @pytest.mark.parametrize("name,independent,kind", _EXPECTED_DIMS)
    def test_dimension_independence(
        self, midi_schema: DomainSchema, name: str, independent: bool, kind: str
    ) -> None:
        dim = next(d for d in midi_schema["dimensions"] if d["name"] == name)
        assert dim["independent_merge"] is independent, (
            f"Dimension '{name}': expected independent_merge={independent}"
        )

    @pytest.mark.parametrize("name,independent,kind", _EXPECTED_DIMS)
    def test_dimension_schema_kind(
        self, midi_schema: DomainSchema, name: str, independent: bool, kind: str
    ) -> None:
        dim = next(d for d in midi_schema["dimensions"] if d["name"] == name)
        assert dim["schema"]["kind"] == kind, (
            f"Dimension '{name}': expected schema kind '{kind}', got '{dim['schema']['kind']}'"
        )

    def test_all_dimensions_have_description(self, midi_schema: DomainSchema) -> None:
        for dim in midi_schema["dimensions"]:
            assert isinstance(dim.get("description"), str), (
                f"Dimension '{dim['name']}' missing description"
            )
            assert len(dim["description"]) > 0

    def test_non_independent_set(self, midi_schema: DomainSchema) -> None:
        non_indep = {
            d["name"] for d in midi_schema["dimensions"] if not d["independent_merge"]
        }
        assert non_indep == _NON_INDEPENDENT

    def test_notes_dimension_sequence_fields(self, midi_schema: DomainSchema) -> None:
        notes = next(d for d in midi_schema["dimensions"] if d["name"] == "notes")
        schema = notes["schema"]
        assert schema["kind"] == "sequence"
        assert schema["element_type"] == "note_event"
        assert schema["diff_algorithm"] == "lcs"

    def test_cc_dimensions_are_tensor_float32(self, midi_schema: DomainSchema) -> None:
        cc_names = {name for name, _, kind in _EXPECTED_DIMS if kind == "tensor"}
        for dim in midi_schema["dimensions"]:
            if dim["name"] in cc_names:
                s = dim["schema"]
                assert s["kind"] == "tensor"
                assert s["dtype"] == "float32"
                assert s["diff_mode"] == "sparse"

    def test_track_structure_is_tree(self, midi_schema: DomainSchema) -> None:
        ts = next(d for d in midi_schema["dimensions"] if d["name"] == "track_structure")
        schema = ts["schema"]
        assert schema["kind"] == "tree"
        assert schema["node_type"] == "track_node"
        assert schema["diff_algorithm"] == "zhang_shasha"


# ===========================================================================
# JSON round-trip
# ===========================================================================


class TestSchemaJsonRoundtrip:
    def test_schema_is_json_serialisable(self, midi_schema: DomainSchema) -> None:
        serialised = json.dumps(midi_schema)
        restored = json.loads(serialised)
        assert restored["domain"] == midi_schema["domain"]
        assert restored["schema_version"] == midi_schema["schema_version"]
        assert len(restored["dimensions"]) == len(midi_schema["dimensions"])
        assert restored["top_level"]["kind"] == midi_schema["top_level"]["kind"]

    def test_all_dimension_schemas_survive_roundtrip(self, midi_schema: DomainSchema) -> None:
        serialised = json.dumps(midi_schema)
        restored = json.loads(serialised)
        original_kinds = {d["name"]: d["schema"]["kind"] for d in midi_schema["dimensions"]}
        restored_kinds = {d["name"]: d["schema"]["kind"] for d in restored["dimensions"]}
        assert original_kinds == restored_kinds


# ===========================================================================
# Plugin registry schema lookup
# ===========================================================================


class TestPluginRegistrySchemaLookup:
    def test_schema_for_midi_returns_domain_schema(self) -> None:
        result = schema_for("midi")
        assert result is not None
        assert result["domain"] == "midi"

    def test_schema_for_unknown_domain_returns_none(self) -> None:
        result = schema_for("nonexistent_domain_xyz")
        assert result is None

    def test_schema_for_matches_direct_plugin_call(self) -> None:
        plugin = MidiPlugin()
        direct = plugin.schema()
        via_registry = schema_for("midi")
        assert via_registry is not None
        assert via_registry["domain"] == direct["domain"]
        assert via_registry["schema_version"] == direct["schema_version"]
        assert len(via_registry["dimensions"]) == len(direct["dimensions"])

    def test_registered_domains_contains_midi(self) -> None:
        assert "midi" in registered_domains()

    def test_music_key_not_in_registry(self) -> None:
        """Ensure the old 'music' key was fully removed."""
        assert "music" not in registered_domains()

    def test_schema_for_all_registered_domains_returns_non_none(self) -> None:
        for domain in registered_domains():
            result = schema_for(domain)
            assert result is not None, f"schema_for({domain!r}) returned None"


# ===========================================================================
# Protocol conformance
# ===========================================================================


class TestProtocolConformance:
    def test_midi_plugin_satisfies_protocol(self) -> None:
        plugin = MidiPlugin()
        assert isinstance(plugin, MuseDomainPlugin)

    def test_schema_method_is_callable(self) -> None:
        plugin = MidiPlugin()
        assert callable(plugin.schema)

    def test_schema_returns_domain_schema(self) -> None:
        plugin = MidiPlugin()
        result = plugin.schema()
        assert isinstance(result, dict)
        assert "domain" in result
        assert "dimensions" in result

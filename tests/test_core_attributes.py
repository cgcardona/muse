"""Tests for muse/core/attributes.py — .museattributes TOML parser and resolver."""

import logging
import pathlib

import pytest

from muse.core.attributes import (
    VALID_STRATEGIES,
    AttributeRule,
    load_attributes,
    read_attributes_meta,
    resolve_strategy,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_attrs(tmp_path: pathlib.Path, content: str) -> None:
    (tmp_path / ".museattributes").write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# load_attributes
# ---------------------------------------------------------------------------


class TestLoadAttributes:
    def test_missing_file_returns_empty(self, tmp_path: pathlib.Path) -> None:
        assert load_attributes(tmp_path) == []

    def test_empty_file_returns_empty(self, tmp_path: pathlib.Path) -> None:
        _write_attrs(tmp_path, "")
        assert load_attributes(tmp_path) == []

    def test_comment_only_returns_empty(self, tmp_path: pathlib.Path) -> None:
        _write_attrs(tmp_path, "# just a comment\n\n")
        assert load_attributes(tmp_path) == []

    def test_meta_only_returns_empty_rules(self, tmp_path: pathlib.Path) -> None:
        _write_attrs(tmp_path, '[meta]\ndomain = "midi"\n')
        assert load_attributes(tmp_path) == []

    def test_parses_single_rule(self, tmp_path: pathlib.Path) -> None:
        _write_attrs(
            tmp_path,
            '[meta]\ndomain = "midi"\n\n[[rules]]\npath = "drums/*"\ndimension = "*"\nstrategy = "ours"\n',
        )
        rules = load_attributes(tmp_path)
        assert len(rules) == 1
        assert rules[0].path_pattern == "drums/*"
        assert rules[0].dimension == "*"
        assert rules[0].strategy == "ours"
        assert rules[0].source_index == 0

    def test_parses_multiple_rules(self, tmp_path: pathlib.Path) -> None:
        content = (
            '[[rules]]\npath = "drums/*"\ndimension = "*"\nstrategy = "ours"\n\n'
            '[[rules]]\npath = "keys/*"\ndimension = "pitch_bend"\nstrategy = "theirs"\n'
        )
        _write_attrs(tmp_path, content)
        rules = load_attributes(tmp_path)
        assert len(rules) == 2
        assert rules[0].path_pattern == "drums/*"
        assert rules[1].path_pattern == "keys/*"

    def test_preserves_source_index(self, tmp_path: pathlib.Path) -> None:
        content = (
            '[[rules]]\npath = "drums/*"\ndimension = "*"\nstrategy = "ours"\n\n'
            '[[rules]]\npath = "keys/*"\ndimension = "pitch_bend"\nstrategy = "theirs"\n'
        )
        _write_attrs(tmp_path, content)
        rules = load_attributes(tmp_path)
        assert rules[0].source_index == 0
        assert rules[1].source_index == 1

    def test_all_valid_strategies_accepted(self, tmp_path: pathlib.Path) -> None:
        lines = "\n".join(
            f'[[rules]]\npath = "path{i}/*"\ndimension = "*"\nstrategy = "{s}"\n'
            for i, s in enumerate(sorted(VALID_STRATEGIES))
        )
        _write_attrs(tmp_path, lines)
        rules = load_attributes(tmp_path)
        assert {r.strategy for r in rules} == VALID_STRATEGIES

    def test_invalid_strategy_raises(self, tmp_path: pathlib.Path) -> None:
        _write_attrs(
            tmp_path,
            '[[rules]]\npath = "drums/*"\ndimension = "*"\nstrategy = "badstrategy"\n',
        )
        with pytest.raises(ValueError, match="badstrategy"):
            load_attributes(tmp_path)

    def test_missing_required_field_raises(self, tmp_path: pathlib.Path) -> None:
        # Rule missing "strategy"
        _write_attrs(
            tmp_path,
            '[[rules]]\npath = "drums/*"\ndimension = "*"\n',
        )
        with pytest.raises(ValueError, match="strategy"):
            load_attributes(tmp_path)

    def test_all_dimension_names_accepted(self, tmp_path: pathlib.Path) -> None:
        dims = ["notes", "pitch_bend", "cc_volume", "cc_sustain", "track_structure", "*", "custom"]
        lines = "\n".join(
            f'[[rules]]\npath = "path/*"\ndimension = "{d}"\nstrategy = "auto"\n'
            for d in dims
        )
        _write_attrs(tmp_path, lines)
        rules = load_attributes(tmp_path)
        assert [r.dimension for r in rules] == dims

    def test_toml_parse_error_raises(self, tmp_path: pathlib.Path) -> None:
        _write_attrs(tmp_path, "this is not valid toml [\n")
        with pytest.raises(ValueError, match="TOML parse error"):
            load_attributes(tmp_path)

    def test_domain_kwarg_mismatch_warns(
        self, tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        _write_attrs(tmp_path, '[meta]\ndomain = "midi"\n')
        with caplog.at_level(logging.WARNING, logger="muse.core.attributes"):
            load_attributes(tmp_path, domain="genomics")
        assert "genomics" in caplog.text

    def test_domain_kwarg_match_no_warning(
        self, tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        _write_attrs(tmp_path, '[meta]\ndomain = "midi"\n')
        with caplog.at_level(logging.WARNING, logger="muse.core.attributes"):
            load_attributes(tmp_path, domain="midi")
        assert caplog.text == ""

    def test_no_domain_kwarg_no_warning(
        self, tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        _write_attrs(tmp_path, '[meta]\ndomain = "midi"\n')
        with caplog.at_level(logging.WARNING, logger="muse.core.attributes"):
            load_attributes(tmp_path)
        assert caplog.text == ""


# ---------------------------------------------------------------------------
# read_attributes_meta
# ---------------------------------------------------------------------------


class TestReadAttributesMeta:
    def test_missing_file_returns_empty(self, tmp_path: pathlib.Path) -> None:
        assert read_attributes_meta(tmp_path) == {}

    def test_no_meta_section_returns_empty(self, tmp_path: pathlib.Path) -> None:
        _write_attrs(
            tmp_path,
            '[[rules]]\npath = "*"\ndimension = "*"\nstrategy = "auto"\n',
        )
        assert read_attributes_meta(tmp_path) == {}

    def test_meta_domain_returned(self, tmp_path: pathlib.Path) -> None:
        _write_attrs(tmp_path, '[meta]\ndomain = "midi"\n')
        meta = read_attributes_meta(tmp_path)
        assert meta.get("domain") == "midi"

    def test_invalid_toml_returns_empty(self, tmp_path: pathlib.Path) -> None:
        _write_attrs(tmp_path, "not valid toml [\n")
        assert read_attributes_meta(tmp_path) == {}


# ---------------------------------------------------------------------------
# resolve_strategy
# ---------------------------------------------------------------------------


class TestResolveStrategy:
    def test_empty_rules_returns_auto(self) -> None:
        assert resolve_strategy([], "drums/kick.mid") == "auto"

    def test_wildcard_dimension_matches_any(self) -> None:
        rules = [AttributeRule("drums/*", "*", "ours", 0)]
        assert resolve_strategy(rules, "drums/kick.mid") == "ours"
        assert resolve_strategy(rules, "drums/kick.mid", "notes") == "ours"
        assert resolve_strategy(rules, "drums/kick.mid", "pitch_bend") == "ours"

    def test_specific_dimension_matches_exact(self) -> None:
        rules = [AttributeRule("keys/*", "pitch_bend", "theirs", 0)]
        assert resolve_strategy(rules, "keys/piano.mid", "pitch_bend") == "theirs"

    def test_specific_dimension_no_match_on_other(self) -> None:
        rules = [AttributeRule("keys/*", "pitch_bend", "theirs", 0)]
        assert resolve_strategy(rules, "keys/piano.mid", "notes") == "auto"

    def test_first_match_wins(self) -> None:
        rules = [
            AttributeRule("*", "*", "ours", 0),
            AttributeRule("*", "*", "theirs", 1),
        ]
        assert resolve_strategy(rules, "any/file.mid") == "ours"

    def test_more_specific_rule_wins_when_first(self) -> None:
        rules = [
            AttributeRule("drums/*", "*", "ours", 0),
            AttributeRule("*", "*", "auto", 1),
        ]
        assert resolve_strategy(rules, "drums/kick.mid") == "ours"
        assert resolve_strategy(rules, "keys/piano.mid") == "auto"

    def test_no_path_match_returns_auto(self) -> None:
        rules = [AttributeRule("drums/*", "*", "ours", 0)]
        assert resolve_strategy(rules, "keys/piano.mid") == "auto"

    def test_glob_star_star(self) -> None:
        rules = [AttributeRule("src/**/*.mid", "*", "manual", 0)]
        assert resolve_strategy(rules, "src/tracks/beat.mid") == "manual"

    def test_wildcard_dimension_in_query_matches_any_rule_dim(self) -> None:
        """When caller passes dimension='*', any rule dimension matches."""
        rules = [AttributeRule("drums/*", "track_structure", "manual", 0)]
        assert resolve_strategy(rules, "drums/kick.mid", "*") == "manual"

    def test_fallback_rule_order(self) -> None:
        rules = [
            AttributeRule("keys/*", "pitch_bend", "theirs", 0),
            AttributeRule("*", "*", "manual", 1),
        ]
        assert resolve_strategy(rules, "keys/piano.mid", "pitch_bend") == "theirs"
        assert resolve_strategy(rules, "keys/piano.mid", "cc_volume") == "manual"
        assert resolve_strategy(rules, "drums/kick.mid") == "manual"

    def test_default_dimension_is_wildcard(self) -> None:
        """Omitting dimension argument should match wildcard rules."""
        rules = [AttributeRule("*", "*", "ours", 0)]
        assert resolve_strategy(rules, "any.mid") == "ours"

    def test_manual_strategy_returned(self) -> None:
        rules = [AttributeRule("*", "track_structure", "manual")]
        assert resolve_strategy(rules, "song.mid", "track_structure") == "manual"


# ---------------------------------------------------------------------------
# New strategies: base and union
# ---------------------------------------------------------------------------


class TestNewStrategies:
    def test_base_strategy_in_valid_set(self) -> None:
        assert "base" in VALID_STRATEGIES

    def test_union_strategy_in_valid_set(self) -> None:
        assert "union" in VALID_STRATEGIES

    def test_base_strategy_accepted_by_load(self, tmp_path: pathlib.Path) -> None:
        (tmp_path / ".museattributes").write_text(
            '[[rules]]\npath = "lock.json"\ndimension = "*"\nstrategy = "base"\n'
        )
        rules = load_attributes(tmp_path)
        assert len(rules) == 1
        assert rules[0].strategy == "base"

    def test_union_strategy_accepted_by_load(self, tmp_path: pathlib.Path) -> None:
        (tmp_path / ".museattributes").write_text(
            '[[rules]]\npath = "docs/*"\ndimension = "*"\nstrategy = "union"\n'
        )
        rules = load_attributes(tmp_path)
        assert rules[0].strategy == "union"

    def test_base_strategy_resolved(self) -> None:
        rules = [AttributeRule("lock.json", "*", "base")]
        assert resolve_strategy(rules, "lock.json") == "base"

    def test_union_strategy_resolved(self) -> None:
        rules = [AttributeRule("docs/*", "*", "union")]
        assert resolve_strategy(rules, "docs/api.md") == "union"


# ---------------------------------------------------------------------------
# comment field
# ---------------------------------------------------------------------------


class TestCommentField:
    def test_comment_field_parsed(self, tmp_path: pathlib.Path) -> None:
        (tmp_path / ".museattributes").write_text(
            '[[rules]]\n'
            'path = "drums/*"\n'
            'dimension = "*"\n'
            'strategy = "ours"\n'
            'comment = "Drums are always authored on this branch."\n'
        )
        rules = load_attributes(tmp_path)
        assert rules[0].comment == "Drums are always authored on this branch."

    def test_comment_field_defaults_to_empty(self, tmp_path: pathlib.Path) -> None:
        (tmp_path / ".museattributes").write_text(
            '[[rules]]\npath = "*"\ndimension = "*"\nstrategy = "auto"\n'
        )
        rules = load_attributes(tmp_path)
        assert rules[0].comment == ""

    def test_comment_field_ignored_in_resolution(self) -> None:
        rules = [AttributeRule("*", "*", "ours", comment="ignored at runtime")]
        assert resolve_strategy(rules, "any/file.mid") == "ours"


# ---------------------------------------------------------------------------
# priority field
# ---------------------------------------------------------------------------


class TestPriorityField:
    def test_priority_field_parsed(self, tmp_path: pathlib.Path) -> None:
        (tmp_path / ".museattributes").write_text(
            '[[rules]]\npath = "*"\ndimension = "*"\nstrategy = "ours"\npriority = 10\n'
        )
        rules = load_attributes(tmp_path)
        assert rules[0].priority == 10

    def test_priority_defaults_to_zero(self, tmp_path: pathlib.Path) -> None:
        (tmp_path / ".museattributes").write_text(
            '[[rules]]\npath = "*"\ndimension = "*"\nstrategy = "auto"\n'
        )
        rules = load_attributes(tmp_path)
        assert rules[0].priority == 0

    def test_higher_priority_overrides_lower_despite_file_order(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A low-priority catch-all declared first must not beat a high-priority rule."""
        (tmp_path / ".museattributes").write_text(
            '[[rules]]\n'
            'path = "*"\ndimension = "*"\nstrategy = "theirs"\npriority = 0\n\n'
            '[[rules]]\n'
            'path = "drums/*"\ndimension = "*"\nstrategy = "ours"\npriority = 10\n'
        )
        rules = load_attributes(tmp_path)
        # High-priority rule appears first after sort.
        assert rules[0].path_pattern == "drums/*"
        assert rules[0].strategy == "ours"
        assert resolve_strategy(rules, "drums/kick.mid") == "ours"

    def test_equal_priority_preserves_declaration_order(
        self, tmp_path: pathlib.Path
    ) -> None:
        (tmp_path / ".museattributes").write_text(
            '[[rules]]\n'
            'path = "*"\ndimension = "*"\nstrategy = "ours"\npriority = 5\n\n'
            '[[rules]]\n'
            'path = "*"\ndimension = "*"\nstrategy = "theirs"\npriority = 5\n'
        )
        rules = load_attributes(tmp_path)
        # Same priority → declaration order preserved; first match wins.
        assert resolve_strategy(rules, "any/file.mid") == "ours"

    def test_priority_negative_values_allowed(self, tmp_path: pathlib.Path) -> None:
        (tmp_path / ".museattributes").write_text(
            '[[rules]]\n'
            'path = "*"\ndimension = "*"\nstrategy = "auto"\npriority = -5\n\n'
            '[[rules]]\n'
            'path = "src/*"\ndimension = "*"\nstrategy = "ours"\npriority = 0\n'
        )
        rules = load_attributes(tmp_path)
        # priority=0 rule is higher than priority=-5, so it sorts first.
        assert rules[0].path_pattern == "src/*"

    def test_priority_affects_all_valid_strategies(
        self, tmp_path: pathlib.Path
    ) -> None:
        (tmp_path / ".museattributes").write_text(
            '[[rules]]\n'
            'path = "a/*"\ndimension = "*"\nstrategy = "base"\npriority = 1\n\n'
            '[[rules]]\n'
            'path = "a/*"\ndimension = "*"\nstrategy = "union"\npriority = 100\n'
        )
        rules = load_attributes(tmp_path)
        # "union" has higher priority, is resolved first for "a/*".
        assert resolve_strategy(rules, "a/file.txt") == "union"


# ---------------------------------------------------------------------------
# Full-stack: comment + priority + new strategies together
# ---------------------------------------------------------------------------


class TestFullRuleComposition:
    def test_all_new_fields_round_trip(self, tmp_path: pathlib.Path) -> None:
        (tmp_path / ".museattributes").write_text(
            '[meta]\ndomain = "code"\n\n'
            '[[rules]]\n'
            'path = "src/generated/**"\n'
            'dimension = "*"\n'
            'strategy = "base"\n'
            'comment = "Generated — always revert to ancestor."\n'
            'priority = 30\n\n'
            '[[rules]]\n'
            'path = "tests/**"\n'
            'dimension = "symbols"\n'
            'strategy = "union"\n'
            'comment = "Test additions from both branches are safe."\n'
            'priority = 10\n'
        )
        rules = load_attributes(tmp_path, domain="code")
        assert len(rules) == 2
        # Higher priority rule first.
        assert rules[0].path_pattern == "src/generated/**"
        assert rules[0].strategy == "base"
        assert rules[0].comment == "Generated — always revert to ancestor."
        assert rules[0].priority == 30
        assert rules[1].path_pattern == "tests/**"
        assert rules[1].strategy == "union"
        assert rules[1].priority == 10

    def test_priority_sorts_midi_rules(self, tmp_path: pathlib.Path) -> None:
        (tmp_path / ".museattributes").write_text(
            '[meta]\ndomain = "midi"\n\n'
            '[[rules]]\n'
            'path = "*"\ndimension = "*"\nstrategy = "auto"\npriority = 0\n\n'
            '[[rules]]\n'
            'path = "master.mid"\ndimension = "*"\nstrategy = "manual"\npriority = 100\n\n'
            '[[rules]]\n'
            'path = "stems/*"\ndimension = "notes"\nstrategy = "union"\npriority = 20\n'
        )
        rules = load_attributes(tmp_path, domain="midi")
        assert rules[0].path_pattern == "master.mid"   # priority 100
        assert rules[1].path_pattern == "stems/*"       # priority 20
        assert rules[2].path_pattern == "*"             # priority 0

        assert resolve_strategy(rules, "master.mid") == "manual"
        assert resolve_strategy(rules, "stems/bass.mid", "notes") == "union"
        assert resolve_strategy(rules, "other/file.mid") == "auto"

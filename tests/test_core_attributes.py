"""Tests for muse/core/attributes.py — .museattributes parser and resolver."""
from __future__ import annotations

import pathlib

import pytest

from muse.core.attributes import (
    VALID_STRATEGIES,
    AttributeRule,
    load_attributes,
    resolve_strategy,
)


# ---------------------------------------------------------------------------
# load_attributes
# ---------------------------------------------------------------------------


class TestLoadAttributes:
    def test_missing_file_returns_empty(self, tmp_path: pathlib.Path) -> None:
        assert load_attributes(tmp_path) == []

    def test_empty_file_returns_empty(self, tmp_path: pathlib.Path) -> None:
        (tmp_path / ".museattributes").write_text("")
        assert load_attributes(tmp_path) == []

    def test_comment_only_returns_empty(self, tmp_path: pathlib.Path) -> None:
        (tmp_path / ".museattributes").write_text("# just a comment\n\n")
        assert load_attributes(tmp_path) == []

    def test_parses_single_rule(self, tmp_path: pathlib.Path) -> None:
        (tmp_path / ".museattributes").write_text("drums/*  *  ours\n")
        rules = load_attributes(tmp_path)
        assert len(rules) == 1
        assert rules[0].path_pattern == "drums/*"
        assert rules[0].dimension == "*"
        assert rules[0].strategy == "ours"
        assert rules[0].source_line == 1

    def test_parses_multiple_rules(self, tmp_path: pathlib.Path) -> None:
        content = "drums/*  *  ours\nkeys/*  harmonic  theirs\n"
        (tmp_path / ".museattributes").write_text(content)
        rules = load_attributes(tmp_path)
        assert len(rules) == 2
        assert rules[0].path_pattern == "drums/*"
        assert rules[1].path_pattern == "keys/*"

    def test_skips_blank_lines(self, tmp_path: pathlib.Path) -> None:
        content = "\ndrum/*  *  ours\n\nkeys/*  *  theirs\n\n"
        (tmp_path / ".museattributes").write_text(content)
        rules = load_attributes(tmp_path)
        assert len(rules) == 2

    def test_skips_comment_lines(self, tmp_path: pathlib.Path) -> None:
        content = "# drums\ndrums/*  *  ours\n# keys\nkeys/*  *  theirs\n"
        (tmp_path / ".museattributes").write_text(content)
        rules = load_attributes(tmp_path)
        assert len(rules) == 2

    def test_preserves_source_line_numbers(self, tmp_path: pathlib.Path) -> None:
        content = "# comment\n\ndrums/*  *  ours\n\nkeys/*  harmonic  theirs\n"
        (tmp_path / ".museattributes").write_text(content)
        rules = load_attributes(tmp_path)
        assert rules[0].source_line == 3
        assert rules[1].source_line == 5

    def test_all_valid_strategies_accepted(self, tmp_path: pathlib.Path) -> None:
        lines = "\n".join(
            f"path{i}/*  *  {s}" for i, s in enumerate(sorted(VALID_STRATEGIES))
        )
        (tmp_path / ".museattributes").write_text(lines)
        rules = load_attributes(tmp_path)
        assert {r.strategy for r in rules} == VALID_STRATEGIES

    def test_invalid_strategy_raises(self, tmp_path: pathlib.Path) -> None:
        (tmp_path / ".museattributes").write_text("drums/*  *  badstrategy\n")
        with pytest.raises(ValueError, match="badstrategy"):
            load_attributes(tmp_path)

    def test_too_few_fields_raises(self, tmp_path: pathlib.Path) -> None:
        (tmp_path / ".museattributes").write_text("drums/*  ours\n")
        with pytest.raises(ValueError, match="3 fields"):
            load_attributes(tmp_path)

    def test_too_many_fields_raises(self, tmp_path: pathlib.Path) -> None:
        (tmp_path / ".museattributes").write_text("drums/*  *  ours  extra\n")
        with pytest.raises(ValueError, match="3 fields"):
            load_attributes(tmp_path)

    def test_all_dimension_names_accepted(self, tmp_path: pathlib.Path) -> None:
        dims = ["melodic", "rhythmic", "harmonic", "dynamic", "structural", "*", "custom"]
        lines = "\n".join(f"path/*  {d}  auto" for d in dims)
        (tmp_path / ".museattributes").write_text(lines)
        rules = load_attributes(tmp_path)
        assert [r.dimension for r in rules] == dims


# ---------------------------------------------------------------------------
# resolve_strategy
# ---------------------------------------------------------------------------


class TestResolveStrategy:
    def test_empty_rules_returns_auto(self) -> None:
        assert resolve_strategy([], "drums/kick.mid") == "auto"

    def test_wildcard_dimension_matches_any(self) -> None:
        rules = [AttributeRule("drums/*", "*", "ours", 1)]
        assert resolve_strategy(rules, "drums/kick.mid") == "ours"
        assert resolve_strategy(rules, "drums/kick.mid", "melodic") == "ours"
        assert resolve_strategy(rules, "drums/kick.mid", "harmonic") == "ours"

    def test_specific_dimension_matches_exact(self) -> None:
        rules = [AttributeRule("keys/*", "harmonic", "theirs", 1)]
        assert resolve_strategy(rules, "keys/piano.mid", "harmonic") == "theirs"

    def test_specific_dimension_no_match_on_other(self) -> None:
        rules = [AttributeRule("keys/*", "harmonic", "theirs", 1)]
        assert resolve_strategy(rules, "keys/piano.mid", "melodic") == "auto"

    def test_first_match_wins(self) -> None:
        rules = [
            AttributeRule("*", "*", "ours", 1),
            AttributeRule("*", "*", "theirs", 2),
        ]
        assert resolve_strategy(rules, "any/file.mid") == "ours"

    def test_more_specific_rule_wins_when_first(self) -> None:
        rules = [
            AttributeRule("drums/*", "*", "ours", 1),
            AttributeRule("*", "*", "auto", 2),
        ]
        assert resolve_strategy(rules, "drums/kick.mid") == "ours"
        assert resolve_strategy(rules, "keys/piano.mid") == "auto"

    def test_no_path_match_returns_auto(self) -> None:
        rules = [AttributeRule("drums/*", "*", "ours", 1)]
        assert resolve_strategy(rules, "keys/piano.mid") == "auto"

    def test_glob_star_star(self) -> None:
        rules = [AttributeRule("src/**/*.mid", "*", "manual", 1)]
        assert resolve_strategy(rules, "src/tracks/beat.mid") == "manual"

    def test_wildcard_dimension_in_query_matches_any_rule_dim(self) -> None:
        """When caller passes dimension='*', any rule dimension matches."""
        rules = [AttributeRule("drums/*", "structural", "manual", 1)]
        # Calling with dimension="*" means "give me any matching rule"
        assert resolve_strategy(rules, "drums/kick.mid", "*") == "manual"

    def test_fallback_rule_order(self) -> None:
        rules = [
            AttributeRule("keys/*", "harmonic", "theirs", 1),
            AttributeRule("*", "*", "manual", 2),
        ]
        # keys/ file, harmonic → matched by first rule
        assert resolve_strategy(rules, "keys/piano.mid", "harmonic") == "theirs"
        # keys/ file, other dim → falls through to catch-all
        assert resolve_strategy(rules, "keys/piano.mid", "dynamic") == "manual"
        # unrelated path → catch-all
        assert resolve_strategy(rules, "drums/kick.mid") == "manual"

    def test_default_dimension_is_wildcard(self) -> None:
        """Omitting dimension argument should match wildcard rules."""
        rules = [AttributeRule("*", "*", "ours", 1)]
        assert resolve_strategy(rules, "any.mid") == "ours"

    def test_manual_strategy_returned(self) -> None:
        rules = [AttributeRule("*", "structural", "manual", 1)]
        assert resolve_strategy(rules, "song.mid", "structural") == "manual"

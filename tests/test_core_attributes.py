"""Tests for muse/core/attributes.py — .museattributes TOML parser and resolver."""
from __future__ import annotations

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
        _write_attrs(tmp_path, '[meta]\ndomain = "music"\n')
        assert load_attributes(tmp_path) == []

    def test_parses_single_rule(self, tmp_path: pathlib.Path) -> None:
        _write_attrs(
            tmp_path,
            '[meta]\ndomain = "music"\n\n[[rules]]\npath = "drums/*"\ndimension = "*"\nstrategy = "ours"\n',
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
            '[[rules]]\npath = "keys/*"\ndimension = "harmonic"\nstrategy = "theirs"\n'
        )
        _write_attrs(tmp_path, content)
        rules = load_attributes(tmp_path)
        assert len(rules) == 2
        assert rules[0].path_pattern == "drums/*"
        assert rules[1].path_pattern == "keys/*"

    def test_preserves_source_index(self, tmp_path: pathlib.Path) -> None:
        content = (
            '[[rules]]\npath = "drums/*"\ndimension = "*"\nstrategy = "ours"\n\n'
            '[[rules]]\npath = "keys/*"\ndimension = "harmonic"\nstrategy = "theirs"\n'
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
        dims = ["melodic", "rhythmic", "harmonic", "dynamic", "structural", "*", "custom"]
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
        _write_attrs(tmp_path, '[meta]\ndomain = "music"\n')
        with caplog.at_level(logging.WARNING, logger="muse.core.attributes"):
            load_attributes(tmp_path, domain="genomics")
        assert "genomics" in caplog.text

    def test_domain_kwarg_match_no_warning(
        self, tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        _write_attrs(tmp_path, '[meta]\ndomain = "music"\n')
        with caplog.at_level(logging.WARNING, logger="muse.core.attributes"):
            load_attributes(tmp_path, domain="music")
        assert caplog.text == ""

    def test_no_domain_kwarg_no_warning(
        self, tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        _write_attrs(tmp_path, '[meta]\ndomain = "music"\n')
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
        _write_attrs(tmp_path, '[meta]\ndomain = "music"\n')
        meta = read_attributes_meta(tmp_path)
        assert meta.get("domain") == "music"

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
        assert resolve_strategy(rules, "drums/kick.mid", "melodic") == "ours"
        assert resolve_strategy(rules, "drums/kick.mid", "harmonic") == "ours"

    def test_specific_dimension_matches_exact(self) -> None:
        rules = [AttributeRule("keys/*", "harmonic", "theirs", 0)]
        assert resolve_strategy(rules, "keys/piano.mid", "harmonic") == "theirs"

    def test_specific_dimension_no_match_on_other(self) -> None:
        rules = [AttributeRule("keys/*", "harmonic", "theirs", 0)]
        assert resolve_strategy(rules, "keys/piano.mid", "melodic") == "auto"

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
        rules = [AttributeRule("drums/*", "structural", "manual", 0)]
        assert resolve_strategy(rules, "drums/kick.mid", "*") == "manual"

    def test_fallback_rule_order(self) -> None:
        rules = [
            AttributeRule("keys/*", "harmonic", "theirs", 0),
            AttributeRule("*", "*", "manual", 1),
        ]
        assert resolve_strategy(rules, "keys/piano.mid", "harmonic") == "theirs"
        assert resolve_strategy(rules, "keys/piano.mid", "dynamic") == "manual"
        assert resolve_strategy(rules, "drums/kick.mid") == "manual"

    def test_default_dimension_is_wildcard(self) -> None:
        """Omitting dimension argument should match wildcard rules."""
        rules = [AttributeRule("*", "*", "ours", 0)]
        assert resolve_strategy(rules, "any.mid") == "ours"

    def test_manual_strategy_returned(self) -> None:
        rules = [AttributeRule("*", "structural", "manual", 0)]
        assert resolve_strategy(rules, "song.mid", "structural") == "manual"

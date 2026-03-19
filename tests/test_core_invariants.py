"""Tests for the generic invariants engine in muse/core/invariants.py."""

import pathlib
import tempfile

import pytest

from muse.core.invariants import (
    BaseReport,
    BaseViolation,
    InvariantChecker,
    InvariantSeverity,
    format_report,
    load_rules_toml,
    make_report,
)


# ---------------------------------------------------------------------------
# make_report
# ---------------------------------------------------------------------------


def _make_violation(
    rule_name: str = "test_rule",
    severity: InvariantSeverity = "error",
    address: str = "src/foo.py",
    description: str = "test violation",
) -> BaseViolation:
    return BaseViolation(
        rule_name=rule_name,
        severity=severity,
        address=address,
        description=description,
    )


class TestMakeReport:
    def test_empty_violations(self) -> None:
        report = make_report("abc123", "code", [], 3)
        assert report["commit_id"] == "abc123"
        assert report["domain"] == "code"
        assert report["violations"] == []
        assert report["rules_checked"] == 3
        assert not report["has_errors"]
        assert not report["has_warnings"]

    def test_error_sets_has_errors(self) -> None:
        v = _make_violation(severity="error")
        report = make_report("abc", "code", [v], 1)
        assert report["has_errors"] is True
        assert report["has_warnings"] is False

    def test_warning_sets_has_warnings(self) -> None:
        v = _make_violation(severity="warning")
        report = make_report("abc", "code", [v], 1)
        assert report["has_errors"] is False
        assert report["has_warnings"] is True

    def test_violations_sorted_by_address(self) -> None:
        v1 = _make_violation(address="z.py")
        v2 = _make_violation(address="a.py")
        report = make_report("abc", "code", [v1, v2], 2)
        assert report["violations"][0]["address"] == "a.py"
        assert report["violations"][1]["address"] == "z.py"

    def test_info_does_not_set_flags(self) -> None:
        v = _make_violation(severity="info")
        report = make_report("abc", "code", [v], 1)
        assert not report["has_errors"]
        assert not report["has_warnings"]


# ---------------------------------------------------------------------------
# format_report
# ---------------------------------------------------------------------------


class TestFormatReport:
    def test_no_violations_shows_green(self) -> None:
        report = make_report("abc", "code", [], 5)
        out = format_report(report)
        assert "✅" in out
        assert "5 rules" in out

    def test_error_shows_red_cross(self) -> None:
        v = _make_violation(severity="error", address="src/foo.py::bar")
        report = make_report("abc", "code", [v], 1)
        out = format_report(report)
        assert "❌" in out
        assert "src/foo.py::bar" in out

    def test_warning_shows_warning_emoji(self) -> None:
        v = _make_violation(severity="warning", address="src/baz.py")
        report = make_report("abc", "code", [v], 1)
        out = format_report(report)
        assert "⚠️" in out

    def test_no_color_mode(self) -> None:
        v = _make_violation(severity="error")
        report = make_report("abc", "code", [v], 1)
        out = format_report(report, color=False)
        assert "[error]" in out
        assert "❌" not in out


# ---------------------------------------------------------------------------
# load_rules_toml
# ---------------------------------------------------------------------------


class TestLoadRulesToml:
    def test_missing_file_returns_empty(self) -> None:
        path = pathlib.Path("/nonexistent/path/rules.toml")
        result = load_rules_toml(path)
        assert result == []

    def test_valid_toml_parsed(self) -> None:
        toml_content = """
[[rule]]
name = "my_rule"
severity = "error"
scope = "file"
rule_type = "max_complexity"

[rule.params]
threshold = 10
"""
        with tempfile.NamedTemporaryFile(suffix=".toml", mode="w", delete=False) as f:
            f.write(toml_content)
            path = pathlib.Path(f.name)

        try:
            rules = load_rules_toml(path)
            assert len(rules) == 1
            assert rules[0]["name"] == "my_rule"
            assert rules[0]["severity"] == "error"
        finally:
            path.unlink(missing_ok=True)

    def test_empty_toml_returns_empty(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".toml", mode="w", delete=False) as f:
            f.write("")
            path = pathlib.Path(f.name)
        try:
            result = load_rules_toml(path)
            assert result == []
        finally:
            path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# InvariantChecker protocol
# ---------------------------------------------------------------------------


class TestInvariantCheckerProtocol:
    def test_concrete_checker_satisfies_protocol(self) -> None:
        """A class with a check() method satisfies the InvariantChecker protocol."""

        class MyChecker:
            def check(
                self,
                repo_root: pathlib.Path,
                commit_id: str,
                *,
                rules_file: pathlib.Path | None = None,
            ) -> BaseReport:
                return make_report(commit_id, "test", [], 0)

        checker = MyChecker()
        assert isinstance(checker, InvariantChecker)

    def test_missing_check_method_fails_protocol(self) -> None:
        class NotAChecker:
            def run(self) -> None:
                pass

        assert not isinstance(NotAChecker(), InvariantChecker)

"""Tests for muse.core.validation — all trust-boundary primitives.

Every function in the validation module operates on untrusted input and must
either return a safe value or raise ValueError / TypeError with a descriptive
message.  These tests verify correctness of the allow-lists, reject-lists, and
edge cases for each guard.
"""

from __future__ import annotations

import math
import pathlib

import pytest

from muse.core.validation import (
    MAX_FILE_BYTES,
    MAX_RESPONSE_BYTES,
    MAX_SYSEX_BYTES,
    clamp_int,
    contain_path,
    finite_float,
    sanitize_display,
    sanitize_glob_prefix,
    validate_branch_name,
    validate_domain_name,
    validate_object_id,
    validate_ref_id,
    validate_repo_id,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


class TestConstants:
    def test_max_file_bytes_is_256mb(self) -> None:
        assert MAX_FILE_BYTES == 256 * 1024 * 1024

    def test_max_response_bytes_is_64mb(self) -> None:
        assert MAX_RESPONSE_BYTES == 64 * 1024 * 1024

    def test_max_sysex_bytes_is_64kib(self) -> None:
        assert MAX_SYSEX_BYTES == 65_536


# ---------------------------------------------------------------------------
# validate_object_id
# ---------------------------------------------------------------------------


class TestValidateObjectId:
    """validate_object_id must accept valid 64-char hex and reject everything else."""

    def test_valid_all_zeros(self) -> None:
        oid = "0" * 64
        assert validate_object_id(oid) == oid

    def test_valid_all_lowercase_hex(self) -> None:
        oid = "a" * 64
        assert validate_object_id(oid) == oid

    def test_valid_mixed_hex(self) -> None:
        oid = "deadbeef" * 8
        assert validate_object_id(oid) == oid

    def test_returns_same_string(self) -> None:
        oid = "f" * 64
        result = validate_object_id(oid)
        assert result is oid  # identity, not a copy

    def test_rejects_uppercase(self) -> None:
        with pytest.raises(ValueError, match="64 lowercase hex"):
            validate_object_id("A" * 64)

    def test_rejects_63_chars(self) -> None:
        with pytest.raises(ValueError):
            validate_object_id("a" * 63)

    def test_rejects_65_chars(self) -> None:
        with pytest.raises(ValueError):
            validate_object_id("a" * 65)

    def test_rejects_empty_string(self) -> None:
        with pytest.raises(ValueError):
            validate_object_id("")

    def test_rejects_non_hex_chars(self) -> None:
        oid = "g" + "a" * 63  # 'g' is not hex
        with pytest.raises(ValueError):
            validate_object_id(oid)

    def test_rejects_path_traversal_string(self) -> None:
        with pytest.raises(ValueError):
            validate_object_id("../evil/../path/" + "a" * 48)

    def test_rejects_null_byte_in_id(self) -> None:
        with pytest.raises(ValueError):
            validate_object_id("\x00" * 64)



# ---------------------------------------------------------------------------
# validate_ref_id
# ---------------------------------------------------------------------------


class TestValidateRefId:
    """validate_ref_id is an alias for the same 64-char hex rule."""

    def test_valid_commit_id(self) -> None:
        rid = "b" * 64
        assert validate_ref_id(rid) == rid

    def test_rejects_short_id(self) -> None:
        with pytest.raises(ValueError):
            validate_ref_id("abc123")

    def test_rejects_uppercase(self) -> None:
        with pytest.raises(ValueError):
            validate_ref_id("B" * 64)

    def test_error_message_mentions_ref_id(self) -> None:
        with pytest.raises(ValueError, match="ref ID"):
            validate_ref_id("short")


# ---------------------------------------------------------------------------
# validate_branch_name
# ---------------------------------------------------------------------------


class TestValidateBranchName:
    """Branch names follow Git conventions — forward slashes allowed,
    backslashes and null bytes are not."""

    # --- valid names ---

    def test_simple_name(self) -> None:
        assert validate_branch_name("main") == "main"

    def test_dev_branch(self) -> None:
        assert validate_branch_name("dev") == "dev"

    def test_feature_slash_style(self) -> None:
        assert validate_branch_name("feature/my-branch") == "feature/my-branch"

    def test_fix_slash_style(self) -> None:
        assert validate_branch_name("fix/auth-token-exposure") == "fix/auth-token-exposure"

    def test_nested_path(self) -> None:
        assert validate_branch_name("feat/v2/core") == "feat/v2/core"

    def test_max_length_255(self) -> None:
        name = "a" * 255
        assert validate_branch_name(name) == name

    def test_digits_hyphens_underscores(self) -> None:
        assert validate_branch_name("branch-123_test") == "branch-123_test"

    # --- rejected names ---

    def test_rejects_empty(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            validate_branch_name("")

    def test_rejects_too_long(self) -> None:
        with pytest.raises(ValueError, match="too long"):
            validate_branch_name("a" * 256)

    def test_rejects_backslash(self) -> None:
        with pytest.raises(ValueError, match="forbidden"):
            validate_branch_name("evil\\branch")

    def test_rejects_null_byte(self) -> None:
        with pytest.raises(ValueError):
            validate_branch_name("branch\x00name")

    def test_rejects_carriage_return(self) -> None:
        with pytest.raises(ValueError):
            validate_branch_name("branch\rname")

    def test_rejects_linefeed(self) -> None:
        with pytest.raises(ValueError):
            validate_branch_name("branch\nname")

    def test_rejects_tab(self) -> None:
        with pytest.raises(ValueError):
            validate_branch_name("branch\tname")

    def test_rejects_leading_dot(self) -> None:
        with pytest.raises(ValueError):
            validate_branch_name(".hidden")

    def test_rejects_trailing_dot(self) -> None:
        with pytest.raises(ValueError):
            validate_branch_name("branch.")

    def test_rejects_consecutive_dots(self) -> None:
        with pytest.raises(ValueError):
            validate_branch_name("branch..name")

    def test_rejects_triple_dot(self) -> None:
        with pytest.raises(ValueError):
            validate_branch_name("branch...name")

    def test_rejects_consecutive_slashes(self) -> None:
        with pytest.raises(ValueError):
            validate_branch_name("feat//branch")

    def test_rejects_leading_slash(self) -> None:
        with pytest.raises(ValueError):
            validate_branch_name("/branch")

    def test_rejects_trailing_slash(self) -> None:
        with pytest.raises(ValueError):
            validate_branch_name("branch/")



# ---------------------------------------------------------------------------
# validate_repo_id
# ---------------------------------------------------------------------------


class TestValidateRepoId:
    def test_valid_uuid_style(self) -> None:
        rid = "abc123-def456-ghi789"
        assert validate_repo_id(rid) == rid

    def test_valid_simple_id(self) -> None:
        assert validate_repo_id("myrepo") == "myrepo"

    def test_rejects_empty(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            validate_repo_id("")

    def test_rejects_too_long(self) -> None:
        with pytest.raises(ValueError, match="too long"):
            validate_repo_id("x" * 256)

    def test_rejects_dotdot_component(self) -> None:
        with pytest.raises(ValueError):
            validate_repo_id("repo..evil")

    def test_rejects_null_byte(self) -> None:
        with pytest.raises(ValueError):
            validate_repo_id("repo\x00id")



# ---------------------------------------------------------------------------
# validate_domain_name
# ---------------------------------------------------------------------------


class TestValidateDomainName:
    def test_midi(self) -> None:
        assert validate_domain_name("midi") == "midi"

    def test_code(self) -> None:
        assert validate_domain_name("code") == "code"

    def test_scaffold(self) -> None:
        assert validate_domain_name("scaffold") == "scaffold"

    def test_with_hyphen(self) -> None:
        assert validate_domain_name("my-domain") == "my-domain"

    def test_with_underscore(self) -> None:
        assert validate_domain_name("my_domain") == "my_domain"

    def test_with_digits(self) -> None:
        assert validate_domain_name("domain2") == "domain2"

    def test_rejects_empty(self) -> None:
        with pytest.raises(ValueError):
            validate_domain_name("")

    def test_rejects_leading_digit(self) -> None:
        with pytest.raises(ValueError):
            validate_domain_name("2domain")

    def test_rejects_uppercase(self) -> None:
        with pytest.raises(ValueError):
            validate_domain_name("MIDI")

    def test_rejects_space(self) -> None:
        with pytest.raises(ValueError):
            validate_domain_name("my domain")

    def test_rejects_slash(self) -> None:
        with pytest.raises(ValueError):
            validate_domain_name("midi/ext")

    def test_rejects_dot(self) -> None:
        with pytest.raises(ValueError):
            validate_domain_name("midi.ext")

    def test_rejects_too_long(self) -> None:
        with pytest.raises(ValueError):
            # > 63 chars (the regex allows a start letter + up to 62 more)
            validate_domain_name("a" + "b" * 63)


# ---------------------------------------------------------------------------
# contain_path
# ---------------------------------------------------------------------------


class TestContainPath:
    def test_simple_subpath(self, tmp_path: pathlib.Path) -> None:
        result = contain_path(tmp_path, "file.txt")
        assert result == (tmp_path / "file.txt").resolve()

    def test_nested_subpath(self, tmp_path: pathlib.Path) -> None:
        result = contain_path(tmp_path, "sub/dir/file.txt")
        assert result == (tmp_path / "sub" / "dir" / "file.txt").resolve()

    def test_returns_resolved_path(self, tmp_path: pathlib.Path) -> None:
        result = contain_path(tmp_path, "a/./b")
        assert "./" not in str(result)

    def test_rejects_dotdot_traversal(self, tmp_path: pathlib.Path) -> None:
        with pytest.raises(ValueError, match="traversal"):
            contain_path(tmp_path, "../escape")

    def test_rejects_double_dotdot(self, tmp_path: pathlib.Path) -> None:
        with pytest.raises(ValueError):
            contain_path(tmp_path, "sub/../../etc/passwd")

    def test_rejects_absolute_path(self, tmp_path: pathlib.Path) -> None:
        with pytest.raises(ValueError):
            contain_path(tmp_path, "/etc/passwd")

    def test_rejects_empty_rel(self, tmp_path: pathlib.Path) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            contain_path(tmp_path, "")


    def test_path_equal_to_child_is_fine(self, tmp_path: pathlib.Path) -> None:
        # A path that resolves exactly to a direct child should pass.
        result = contain_path(tmp_path, "direct_child")
        assert result.parent == tmp_path.resolve()

    def test_rejects_symlink_escaping_base(self, tmp_path: pathlib.Path) -> None:
        # Create a symlink inside base that points outside.
        outside = tmp_path.parent / "outside.txt"
        outside.write_text("secret")
        link = tmp_path / "link.txt"
        link.symlink_to(outside)
        # contain_path resolves the path — symlink target is outside base.
        with pytest.raises(ValueError, match="traversal"):
            contain_path(tmp_path, "link.txt")


# ---------------------------------------------------------------------------
# sanitize_glob_prefix
# ---------------------------------------------------------------------------


class TestSanitizeGlobPrefix:
    def test_clean_prefix_unchanged(self) -> None:
        assert sanitize_glob_prefix("abcdef") == "abcdef"

    def test_strips_asterisk(self) -> None:
        assert sanitize_glob_prefix("abc*def") == "abcdef"

    def test_strips_question_mark(self) -> None:
        assert sanitize_glob_prefix("abc?def") == "abcdef"

    def test_strips_open_bracket(self) -> None:
        assert sanitize_glob_prefix("abc[def") == "abcdef"

    def test_strips_close_bracket(self) -> None:
        assert sanitize_glob_prefix("abc]def") == "abcdef"

    def test_strips_open_brace(self) -> None:
        assert sanitize_glob_prefix("abc{def") == "abcdef"

    def test_strips_close_brace(self) -> None:
        assert sanitize_glob_prefix("abc}def") == "abcdef"

    def test_strips_all_metacharacters(self) -> None:
        assert sanitize_glob_prefix("*?[]{} abc") == " abc"

    def test_empty_string(self) -> None:
        assert sanitize_glob_prefix("") == ""

    def test_hex_prefix_unaffected(self) -> None:
        prefix = "deadbeef01"
        assert sanitize_glob_prefix(prefix) == prefix


# ---------------------------------------------------------------------------
# sanitize_display
# ---------------------------------------------------------------------------


class TestSanitizeDisplay:
    def test_clean_ascii_unchanged(self) -> None:
        assert sanitize_display("Hello, World!") == "Hello, World!"

    def test_newline_preserved(self) -> None:
        s = "line1\nline2"
        assert sanitize_display(s) == s

    def test_tab_preserved(self) -> None:
        s = "col1\tcol2"
        assert sanitize_display(s) == s

    def test_strips_ansi_escape_sequence(self) -> None:
        ansi = "\x1b[31mred text\x1b[0m"
        result = sanitize_display(ansi)
        assert "\x1b" not in result
        assert "red text" in result

    def test_strips_bel(self) -> None:
        assert sanitize_display("ring\x07bell") == "ringbell"

    def test_strips_null_byte(self) -> None:
        assert sanitize_display("no\x00null") == "nonull"

    def test_strips_osc_sequence(self) -> None:
        # OSC sequences start with \x9b (C1 CSI) or ESC [
        osc = "\x9bmalicious"
        result = sanitize_display(osc)
        assert "\x9b" not in result

    def test_strips_cr(self) -> None:
        assert sanitize_display("text\r") == "text"

    def test_strips_vertical_tab(self) -> None:
        assert sanitize_display("text\x0bmore") == "textmore"

    def test_strips_form_feed(self) -> None:
        assert sanitize_display("text\x0cmore") == "textmore"

    def test_strips_del(self) -> None:
        assert sanitize_display("text\x7fmore") == "textmore"

    def test_multiline_message_sanitized(self) -> None:
        msg = "commit: \x1b[1mAdd feature\x1b[0m\nSigned-off-by: Alice"
        result = sanitize_display(msg)
        assert "\x1b" not in result
        assert "Add feature" in result
        assert "Signed-off-by: Alice" in result

    def test_empty_string(self) -> None:
        assert sanitize_display("") == ""

    def test_unicode_letters_preserved(self) -> None:
        s = "Héllo Wörld — 日本語"
        assert sanitize_display(s) == s


# ---------------------------------------------------------------------------
# clamp_int
# ---------------------------------------------------------------------------


class TestClampInt:
    def test_value_in_range_returned_unchanged(self) -> None:
        assert clamp_int(5, 1, 10) == 5

    def test_value_at_lower_bound(self) -> None:
        assert clamp_int(1, 1, 10) == 1

    def test_value_at_upper_bound(self) -> None:
        assert clamp_int(10, 1, 10) == 10

    def test_below_min_raises(self) -> None:
        with pytest.raises(ValueError, match="between"):
            clamp_int(0, 1, 10)

    def test_above_max_raises(self) -> None:
        with pytest.raises(ValueError, match="between"):
            clamp_int(11, 1, 10)

    def test_name_in_error_message(self) -> None:
        with pytest.raises(ValueError, match="depth"):
            clamp_int(-1, 0, 100, name="depth")

    def test_negative_range(self) -> None:
        assert clamp_int(-5, -10, 0) == -5

    def test_equal_lo_hi(self) -> None:
        assert clamp_int(42, 42, 42) == 42


# ---------------------------------------------------------------------------
# finite_float
# ---------------------------------------------------------------------------


class TestFiniteFloat:
    def test_finite_value_returned_unchanged(self) -> None:
        assert finite_float(120.0, 120.0) == 120.0

    def test_zero_is_finite(self) -> None:
        assert finite_float(0.0, 1.0) == 0.0

    def test_negative_finite_returned(self) -> None:
        assert finite_float(-5.5, 0.0) == -5.5

    def test_positive_inf_returns_fallback(self) -> None:
        assert finite_float(math.inf, 120.0) == 120.0

    def test_negative_inf_returns_fallback(self) -> None:
        assert finite_float(-math.inf, 120.0) == 120.0

    def test_nan_returns_fallback(self) -> None:
        assert finite_float(math.nan, 120.0) == 120.0

    def test_large_finite_returned(self) -> None:
        big = 1e300
        assert finite_float(big, 0.0) == big


# ---------------------------------------------------------------------------
# Stress: contain_path with many adversarial inputs
# ---------------------------------------------------------------------------


class TestContainPathStress:
    """Fuzz-style test — generate many adversarial path strings and verify
    that contain_path rejects all traversal attempts."""

    TRAVERSAL_ATTEMPTS: list[str] = [
        "..",
        "../etc/passwd",
        "../../etc/shadow",
        "sub/../../../etc/passwd",
        "/absolute/path",
        "/",
        "//double-slash",
        # Note: URL-encoded dots (%2e%2e) are NOT traversal from a filesystem
        # perspective — contain_path is a filesystem guard, not an HTTP parser.
        # Null bytes cause an OS-level ValueError, which we also accept.
        "\x00null",
        "sub/\x00null",
    ]

    def test_all_traversal_attempts_rejected(self, tmp_path: pathlib.Path) -> None:
        for attempt in self.TRAVERSAL_ATTEMPTS:
            with pytest.raises((ValueError, TypeError)):
                contain_path(tmp_path, attempt)

    def test_large_number_of_valid_paths_accepted(self, tmp_path: pathlib.Path) -> None:
        for i in range(200):
            rel = f"subdir/track_{i:04d}.mid"
            result = contain_path(tmp_path, rel)
            assert str(result).startswith(str(tmp_path.resolve()))

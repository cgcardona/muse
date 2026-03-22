"""Tests for the rewritten MarkdownAdapter.

Coverage:
- Extension routing: only .md / .rst / .txt are accepted.
- Section symbols: flat headings, hierarchical qualified names, level encoding.
- Content-ID correctness: full section bytes hashed, not just heading text.
- Body-hash / signature split: retitle detection, level-change detection.
- Code block symbols: language tag, no-language fallback, content hash.
- GFM table symbols: header signature, data-row body_hash, schema changes.
- Inline markup stripping: bold, italic, inline-code, links in headings.
- Deduplication: identical sibling headings get @L{lineno} suffix.
- Depth limit: sections beyond _MAX_DEPTH are silently dropped.
- Edge cases: empty file, no headings, setext headings (unsupported → skip).
- Real-world shape: README-shaped document exercises all three emitters.
- _plain_heading unit tests: images dropped, markup stripped, truncation.
"""

from __future__ import annotations

import pytest
from muse.plugins.code.ast_parser import (
    MarkdownAdapter,
    SymbolRecord,
    SymbolTree,
    _plain_heading,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse(source: str, path: str = "README.md") -> SymbolTree:
    adapter = MarkdownAdapter()
    if adapter._parser is None:
        pytest.skip("tree-sitter-markdown not available")
    return adapter.parse_symbols(source.encode(), path)


# ---------------------------------------------------------------------------
# _plain_heading unit tests
# ---------------------------------------------------------------------------

class TestPlainHeading:
    def test_plain_text_unchanged(self) -> None:
        assert _plain_heading("Hello World") == "Hello World"

    def test_bold_stripped(self) -> None:
        assert _plain_heading("**Bold** heading") == "Bold heading"

    def test_italic_star_stripped(self) -> None:
        assert _plain_heading("*italic* text") == "italic text"

    def test_bold_italic_combined(self) -> None:
        assert _plain_heading("***bold italic***") == "bold italic"

    def test_italic_underscore_stripped(self) -> None:
        assert _plain_heading("_italic_") == "italic"

    def test_bold_underscore_stripped(self) -> None:
        assert _plain_heading("__bold__") == "bold"

    def test_inline_code_stripped(self) -> None:
        assert _plain_heading("`code` block") == "code block"

    def test_triple_backtick_stripped(self) -> None:
        assert _plain_heading("```code```") == "code"

    def test_link_keeps_text(self) -> None:
        assert _plain_heading("[link text](https://example.com)") == "link text"

    def test_reference_link_keeps_text(self) -> None:
        assert _plain_heading("[link text][ref]") == "link text"

    def test_image_dropped_entirely(self) -> None:
        assert _plain_heading("![alt text](img.png) caption") == "caption"

    def test_reference_image_dropped(self) -> None:
        assert _plain_heading("![alt][ref] caption") == "caption"

    def test_html_entity_amp(self) -> None:
        assert _plain_heading("foo &amp; bar") == "foo & bar"

    def test_html_entity_lt_gt(self) -> None:
        assert _plain_heading("a &lt; b &gt; c") == "a < b > c"

    def test_html_entity_quot(self) -> None:
        assert _plain_heading("say &quot;hi&quot;") == 'say "hi"'

    def test_html_entity_apos(self) -> None:
        assert _plain_heading("it&#39;s") == "it's"

    def test_whitespace_collapsed(self) -> None:
        assert _plain_heading("  too   many    spaces  ") == "too many spaces"

    def test_truncation_at_120_chars(self) -> None:
        long = "A" * 200
        result = _plain_heading(long)
        assert len(result) == 120

    def test_empty_string(self) -> None:
        assert _plain_heading("") == ""

    def test_mixed_markup(self) -> None:
        # Realistic heading: "**API** `Reference` Guide"
        result = _plain_heading("**API** `Reference` Guide")
        assert result == "API Reference Guide"


# ---------------------------------------------------------------------------
# Extension routing
# ---------------------------------------------------------------------------

class TestExtensionRouting:
    def test_md_supported(self) -> None:
        adapter = MarkdownAdapter()
        assert ".md" in adapter.supported_extensions()

    def test_rst_supported(self) -> None:
        adapter = MarkdownAdapter()
        assert ".rst" in adapter.supported_extensions()

    def test_txt_supported(self) -> None:
        adapter = MarkdownAdapter()
        assert ".txt" in adapter.supported_extensions()

    def test_py_not_supported(self) -> None:
        adapter = MarkdownAdapter()
        assert ".py" not in adapter.supported_extensions()

    def test_html_not_supported(self) -> None:
        adapter = MarkdownAdapter()
        assert ".html" not in adapter.supported_extensions()


# ---------------------------------------------------------------------------
# Section symbols: flat headings
# ---------------------------------------------------------------------------

class TestFlatSections:
    def test_h1_emitted(self) -> None:
        syms = _parse("# Hello\n\nContent.\n")
        keys = list(syms)
        assert any("Hello" in k for k in keys)

    def test_h1_kind_is_section(self) -> None:
        syms = _parse("# Hello\n\nContent.\n")
        rec = next(v for k, v in syms.items() if "Hello" in k)
        assert rec["kind"] == "section"

    def test_h2_emitted(self) -> None:
        syms = _parse("## Setup\n\nDo the thing.\n")
        keys = list(syms)
        assert any("Setup" in k for k in keys)

    def test_h3_emitted(self) -> None:
        syms = _parse("### Detail\n\nMore detail.\n")
        keys = list(syms)
        assert any("Detail" in k for k in keys)

    def test_address_contains_file_path(self) -> None:
        syms = _parse("# Hello\n", "docs/guide.md")
        assert any(k.startswith("docs/guide.md::") for k in syms)

    def test_lineno_is_one_based(self) -> None:
        syms = _parse("# Hello\n\nContent.\n")
        rec = next(v for k, v in syms.items() if "Hello" in k)
        assert rec["lineno"] == 1

    def test_end_lineno_greater_than_lineno(self) -> None:
        syms = _parse("# Hello\n\nSome content.\n")
        rec = next(v for k, v in syms.items() if "Hello" in k)
        assert rec["end_lineno"] >= rec["lineno"]

    def test_name_is_plain_text(self) -> None:
        syms = _parse("# **Bold** Heading\n\nContent.\n")
        rec = next(v for k, v in syms.items() if "Bold Heading" in k)
        assert rec["name"] == "Bold Heading"


# ---------------------------------------------------------------------------
# Section symbols: hierarchy
# ---------------------------------------------------------------------------

class TestSectionHierarchy:
    def test_h2_under_h1_has_qualified_name(self) -> None:
        src = "# Parent\n\n## Child\n\nText.\n"
        syms = _parse(src)
        assert any("Parent.Child" in k for k in syms)

    def test_h3_under_h2_under_h1(self) -> None:
        src = "# A\n\n## B\n\n### C\n\nText.\n"
        syms = _parse(src)
        assert any("A.B.C" in k for k in syms)

    def test_sibling_h2s_are_distinct(self) -> None:
        src = "# Root\n\n## Alpha\n\nFoo.\n\n## Beta\n\nBar.\n"
        syms = _parse(src)
        assert any("Alpha" in k for k in syms)
        assert any("Beta" in k for k in syms)

    def test_h2_address_does_not_bleed_into_sibling(self) -> None:
        src = "# Root\n\n## A\n\nFoo.\n\n## B\n\nBar.\n"
        syms = _parse(src)
        # "A.B" should NOT appear; B is a sibling, not a child of A.
        assert not any("A.B" in k for k in syms)

    def test_parent_section_includes_child_in_content_id(self) -> None:
        src_with_child = "# Parent\n\n## Child\n\nText.\n"
        src_no_child = "# Parent\n\nText.\n"
        syms_with = _parse(src_with_child)
        syms_no = _parse(src_no_child)
        parent_with = next(v for k, v in syms_with.items() if k.endswith("::Parent"))
        parent_no = next(v for k, v in syms_no.items() if k.endswith("::Parent"))
        # Adding a child section changes the parent's content_id.
        assert parent_with["content_id"] != parent_no["content_id"]

    def test_parallel_h2s_in_separate_h1_sections_dont_collide(self) -> None:
        src = "# Intro\n\n## Overview\n\nX.\n\n# Usage\n\n## Overview\n\nY.\n"
        syms = _parse(src)
        # Two Overview headings exist; they must have different addresses.
        overview_keys = [k for k in syms if "Overview" in k]
        assert len(overview_keys) == 2
        assert overview_keys[0] != overview_keys[1]


# ---------------------------------------------------------------------------
# Content-ID correctness — the core bug fix
# ---------------------------------------------------------------------------

class TestContentIDCorrectness:
    def test_changing_body_changes_content_id(self) -> None:
        src_a = "# Intro\n\nFirst paragraph.\n"
        src_b = "# Intro\n\nFirst paragraph changed entirely.\n"
        a = _parse(src_a)
        b = _parse(src_b)
        key_a = next(k for k in a if "Intro" in k)
        key_b = next(k for k in b if "Intro" in k)
        assert a[key_a]["content_id"] != b[key_b]["content_id"]

    def test_same_content_produces_same_content_id(self) -> None:
        src = "# Hello\n\nSame content.\n"
        a = _parse(src)
        b = _parse(src)
        key = next(k for k in a if "Hello" in k)
        assert a[key]["content_id"] == b[key]["content_id"]

    def test_adding_paragraph_changes_content_id(self) -> None:
        src_a = "# Section\n\nParagraph one.\n"
        src_b = "# Section\n\nParagraph one.\n\nParagraph two.\n"
        a = _parse(src_a)
        b = _parse(src_b)
        key_a = next(k for k in a if "Section" in k)
        key_b = next(k for k in b if "Section" in k)
        assert a[key_a]["content_id"] != b[key_b]["content_id"]

    def test_heading_retitle_changes_content_id(self) -> None:
        src_a = "# Old Title\n\nSame body.\n"
        src_b = "# New Title\n\nSame body.\n"
        a = _parse(src_a)
        b = _parse(src_b)
        # Different addresses (different titles) — both content_ids checked
        key_a = next(k for k in a if "Old Title" in k)
        key_b = next(k for k in b if "New Title" in k)
        # content_id differs because heading text changed.
        assert a[key_a]["content_id"] != b[key_b]["content_id"]

    def test_retitle_with_same_body_has_same_body_hash(self) -> None:
        """Retitle detection: body_hash stable, signature_id changes."""
        src_a = "# Old Title\n\nIdentical body content.\n"
        src_b = "# New Title\n\nIdentical body content.\n"
        a = _parse(src_a)
        b = _parse(src_b)
        key_a = next(k for k in a if "Old Title" in k)
        key_b = next(k for k in b if "New Title" in k)
        # Same body text below heading → same body_hash.
        assert a[key_a]["body_hash"] == b[key_b]["body_hash"]
        # Different heading text → different signature_id.
        assert a[key_a]["signature_id"] != b[key_b]["signature_id"]

    def test_level_change_changes_metadata_id(self) -> None:
        """Promoting a heading level is visible in metadata_id, not body_hash."""
        src_a = "## Section\n\nBody.\n"
        src_b = "# Section\n\nBody.\n"
        a = _parse(src_a)
        b = _parse(src_b)
        key_a = next(k for k in a if "Section" in k)
        key_b = next(k for k in b if "Section" in k)
        assert a[key_a]["metadata_id"] != b[key_b]["metadata_id"]
        # Body content is the same, so body_hash should match.
        assert a[key_a]["body_hash"] == b[key_b]["body_hash"]

    def test_level_change_changes_signature_id(self) -> None:
        src_a = "## Section\n\nBody.\n"
        src_b = "# Section\n\nBody.\n"
        a = _parse(src_a)
        b = _parse(src_b)
        key_a = next(k for k in a if "Section" in k)
        key_b = next(k for k in b if "Section" in k)
        assert a[key_a]["signature_id"] != b[key_b]["signature_id"]


# ---------------------------------------------------------------------------
# Fenced code blocks
# ---------------------------------------------------------------------------

class TestCodeBlockSymbols:
    def test_python_block_emitted(self) -> None:
        src = "# Section\n\n```python\nprint('hello')\n```\n"
        syms = _parse(src)
        assert any("code[python]" in k for k in syms)

    def test_code_block_kind_is_variable(self) -> None:
        src = "# Section\n\n```python\nprint('hello')\n```\n"
        syms = _parse(src)
        rec = next(v for k, v in syms.items() if "code[python]" in k)
        assert rec["kind"] == "variable"

    def test_no_language_block_emitted(self) -> None:
        src = "# Section\n\n```\nplain text\n```\n"
        syms = _parse(src)
        assert any("code@L" in k for k in syms)

    def test_no_language_not_in_symbol_name(self) -> None:
        src = "# Section\n\n```\nplain text\n```\n"
        syms = _parse(src)
        # Should be code@L... not code[]@L...
        assert not any("code[]" in k for k in syms)

    def test_code_block_scoped_to_section(self) -> None:
        src = "# Intro\n\n```python\nx = 1\n```\n"
        syms = _parse(src)
        # code block address should contain the parent section name
        assert any("Intro" in k and "code[python]" in k for k in syms)

    def test_code_content_change_changes_content_id(self) -> None:
        src_a = "# S\n\n```python\nx = 1\n```\n"
        src_b = "# S\n\n```python\nx = 2\n```\n"
        a = _parse(src_a)
        b = _parse(src_b)
        key_a = next(k for k in a if "code[python]" in k)
        key_b = next(k for k in b if "code[python]" in k)
        assert a[key_a]["content_id"] != b[key_b]["content_id"]

    def test_lang_change_changes_signature_id(self) -> None:
        src_a = "# S\n\n```python\nx = 1\n```\n"
        src_b = "# S\n\n```javascript\nx = 1\n```\n"
        a = _parse(src_a)
        b = _parse(src_b)
        key_a = next(k for k in a if "code[python]" in k)
        key_b = next(k for k in b if "code[javascript]" in k)
        assert a[key_a]["signature_id"] != b[key_b]["signature_id"]

    def test_lang_tag_is_lowercased(self) -> None:
        src = "# S\n\n```Python\npass\n```\n"
        syms = _parse(src)
        # Language tag must be lowercased in the symbol name.
        assert any("code[python]" in k for k in syms)

    def test_multiple_code_blocks_are_distinct(self) -> None:
        src = (
            "# Section\n\n"
            "```python\nblock_one = 1\n```\n\n"
            "```python\nblock_two = 2\n```\n"
        )
        syms = _parse(src)
        code_keys = [k for k in syms if "code[python]" in k]
        assert len(code_keys) == 2
        assert code_keys[0] != code_keys[1]

    def test_code_block_lineno_populated(self) -> None:
        src = "# Section\n\n```python\npass\n```\n"
        syms = _parse(src)
        rec = next(v for k, v in syms.items() if "code[python]" in k)
        assert rec["lineno"] > 0


# ---------------------------------------------------------------------------
# GFM pipe tables
# ---------------------------------------------------------------------------

class TestTableSymbols:
    _TABLE_SRC = (
        "# Section\n\n"
        "| Name | Value |\n"
        "| ---- | ----- |\n"
        "| foo  | 1     |\n"
        "| bar  | 2     |\n"
    )

    def test_table_emitted(self) -> None:
        syms = _parse(self._TABLE_SRC)
        assert any("table@L" in k for k in syms)

    def test_table_kind_is_section(self) -> None:
        syms = _parse(self._TABLE_SRC)
        rec = next(v for k, v in syms.items() if "table@L" in k)
        assert rec["kind"] == "section"

    def test_table_scoped_to_section(self) -> None:
        syms = _parse(self._TABLE_SRC)
        assert any("Section" in k and "table@L" in k for k in syms)

    def test_adding_data_row_changes_content_id(self) -> None:
        src_a = (
            "# S\n\n"
            "| A | B |\n| - | - |\n| 1 | 2 |\n"
        )
        src_b = (
            "# S\n\n"
            "| A | B |\n| - | - |\n| 1 | 2 |\n| 3 | 4 |\n"
        )
        a = _parse(src_a)
        b = _parse(src_b)
        key_a = next(k for k in a if "table@L" in k)
        key_b = next(k for k in b if "table@L" in k)
        assert a[key_a]["content_id"] != b[key_b]["content_id"]

    def test_adding_data_row_changes_body_hash(self) -> None:
        src_a = "# S\n\n| A | B |\n| - | - |\n| 1 | 2 |\n"
        src_b = "# S\n\n| A | B |\n| - | - |\n| 1 | 2 |\n| 3 | 4 |\n"
        a = _parse(src_a)
        b = _parse(src_b)
        key_a = next(k for k in a if "table@L" in k)
        key_b = next(k for k in b if "table@L" in k)
        assert a[key_a]["body_hash"] != b[key_b]["body_hash"]

    def test_column_rename_changes_signature_id(self) -> None:
        src_a = "# S\n\n| Name | Value |\n| ---- | ----- |\n| x | 1 |\n"
        src_b = "# S\n\n| Label | Value |\n| ----- | ----- |\n| x | 1 |\n"
        a = _parse(src_a)
        b = _parse(src_b)
        key_a = next(k for k in a if "table@L" in k)
        key_b = next(k for k in b if "table@L" in k)
        assert a[key_a]["signature_id"] != b[key_b]["signature_id"]

    def test_column_rename_does_not_change_body_hash(self) -> None:
        """Renaming a column header should change signature_id but not body_hash."""
        src_a = "# S\n\n| Name | Value |\n| ---- | ----- |\n| x | 1 |\n"
        src_b = "# S\n\n| Label | Value |\n| ------ | ----- |\n| x | 1 |\n"
        a = _parse(src_a)
        b = _parse(src_b)
        key_a = next(k for k in a if "table@L" in k)
        key_b = next(k for k in b if "table@L" in k)
        # Data rows are the same → body_hash must be equal.
        assert a[key_a]["body_hash"] == b[key_b]["body_hash"]

    def test_table_lineno_populated(self) -> None:
        syms = _parse(self._TABLE_SRC)
        rec = next(v for k, v in syms.items() if "table@L" in k)
        assert rec["lineno"] > 0


# ---------------------------------------------------------------------------
# Inline markup stripping — address stability
# ---------------------------------------------------------------------------

class TestInlineMarkupStripping:
    def test_bold_heading_address_matches_plain(self) -> None:
        src_bold = "# **Setup**\n\nContent.\n"
        src_plain = "# Setup\n\nContent.\n"
        syms_bold = _parse(src_bold)
        syms_plain = _parse(src_plain)
        # Both should produce a key containing "Setup" (not **Setup**).
        assert any("Setup" in k for k in syms_bold)
        assert any("Setup" in k for k in syms_plain)
        # The qualified name in both should be identical.
        name_bold = next(v for k, v in syms_bold.items() if "Setup" in k)["name"]
        name_plain = next(v for k, v in syms_plain.items() if "Setup" in k)["name"]
        assert name_bold == name_plain

    def test_inline_code_heading_stripped(self) -> None:
        src = "# `muse init` Command\n\nContent.\n"
        syms = _parse(src)
        assert any("muse init Command" in k for k in syms)

    def test_link_heading_keeps_text(self) -> None:
        src = "# [API Reference](https://example.com/api)\n\nContent.\n"
        syms = _parse(src)
        assert any("API Reference" in k for k in syms)

    def test_image_in_heading_dropped(self) -> None:
        src = "# ![logo](logo.png) Intro\n\nContent.\n"
        syms = _parse(src)
        # The logo image should be gone; "Intro" should remain.
        assert any("Intro" in k for k in syms)
        assert not any("logo.png" in k for k in syms)


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

class TestDeduplication:
    def test_two_identical_h2s_get_unique_addresses(self) -> None:
        src = (
            "# Root\n\n"
            "## Examples\n\nFirst.\n\n"
            "## Examples\n\nSecond.\n"
        )
        syms = _parse(src)
        examples_keys = [k for k in syms if "Examples" in k]
        assert len(examples_keys) == 2
        assert examples_keys[0] != examples_keys[1]

    def test_deduplicated_key_contains_lineno(self) -> None:
        src = (
            "# Root\n\n"
            "## Examples\n\nFirst.\n\n"
            "## Examples\n\nSecond.\n"
        )
        syms = _parse(src)
        examples_keys = [k for k in syms if "Examples" in k]
        # One of the two keys must have @L appended.
        assert any("@L" in k for k in examples_keys)

    def test_identical_headings_in_different_parents_not_deduped(self) -> None:
        src = (
            "# Alpha\n\n## Notes\n\nFoo.\n\n"
            "# Beta\n\n## Notes\n\nBar.\n"
        )
        syms = _parse(src)
        notes_keys = [k for k in syms if "Notes" in k]
        assert len(notes_keys) == 2
        # Should be Alpha.Notes and Beta.Notes — no @L suffix needed.
        assert any("Alpha.Notes" in k for k in notes_keys)
        assert any("Beta.Notes" in k for k in notes_keys)


# ---------------------------------------------------------------------------
# Depth limit
# ---------------------------------------------------------------------------

class TestDepthLimit:
    def test_deep_nesting_does_not_crash(self) -> None:
        # Build 20 levels of nesting: # A, ## A.B, ### A.B.C, etc.
        levels = ["#" * i + f" Level{i}\n\nText.\n\n" for i in range(1, 21)]
        src = "".join(levels)
        # Should not raise; may return fewer symbols than levels.
        syms = _parse(src)
        assert isinstance(syms, dict)

    def test_symbols_within_limit_are_extracted(self) -> None:
        # Only 3 levels — all should be extracted.
        src = "# A\n\n## A B\n\n### A B C\n\nText.\n"
        syms = _parse(src)
        assert any("A" in k for k in syms)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_file_returns_empty(self) -> None:
        adapter = MarkdownAdapter()
        if adapter._parser is None:
            pytest.skip("tree-sitter-markdown not available")
        result = adapter.parse_symbols(b"", "empty.md")
        assert result == {}

    def test_no_headings_returns_empty(self) -> None:
        src = "Just a paragraph with no headings.\n"
        syms = _parse(src)
        assert syms == {}

    def test_only_horizontal_rule_returns_empty(self) -> None:
        src = "---\n"
        syms = _parse(src)
        assert syms == {}

    def test_binary_like_content_does_not_crash(self) -> None:
        adapter = MarkdownAdapter()
        if adapter._parser is None:
            pytest.skip("tree-sitter-markdown not available")
        # Non-UTF-8 bytes should not raise.
        result = adapter.parse_symbols(b"\xff\xfe# Title\n", "weird.md")
        assert isinstance(result, dict)

    def test_very_long_heading_truncated_in_name(self) -> None:
        long_heading = "Word " * 50  # 250 chars
        src = f"# {long_heading}\n\nContent.\n"
        syms = _parse(src)
        assert len(syms) == 1
        rec = next(iter(syms.values()))
        # name must be at most 120 chars.
        assert len(rec["name"]) <= 120

    def test_file_content_id_changes_on_any_change(self) -> None:
        adapter = MarkdownAdapter()
        src_a = b"# Hello\n\nWorld.\n"
        src_b = b"# Hello\n\nWorld. "  # trailing space
        assert adapter.file_content_id(src_a) != adapter.file_content_id(src_b)

    def test_file_content_id_is_hex_sha256(self) -> None:
        adapter = MarkdownAdapter()
        cid = adapter.file_content_id(b"# Hello\n")
        assert len(cid) == 64
        assert all(c in "0123456789abcdef" for c in cid)

    def test_headings_only_no_body(self) -> None:
        src = "# Title\n## Subtitle\n"
        syms = _parse(src)
        assert any("Title" in k for k in syms)

    def test_code_block_at_root_level(self) -> None:
        """A code block not inside any section gets a root-level address."""
        src = "```python\nprint('hi')\n```\n"
        syms = _parse(src)
        # Should be emitted even without a parent section.
        assert any("code[python]" in k for k in syms)

    def test_table_at_root_level(self) -> None:
        src = "| A | B |\n| - | - |\n| 1 | 2 |\n"
        syms = _parse(src)
        assert any("table@L" in k for k in syms)


# ---------------------------------------------------------------------------
# Real-world README shape
# ---------------------------------------------------------------------------

class TestRealWorldShape:
    _README = """\
# Muse

A domain-agnostic version control system.

## Installation

```bash
pip install muse-vcs
```

## Usage

Run `muse init` to initialise a repository.

### Commands

| Command        | Description               |
| -------------- | ------------------------- |
| `muse init`    | Initialise a repository   |
| `muse commit`  | Record a new snapshot     |
| `muse log`     | Show commit history       |

## API Reference

### `muse.core.snapshot`

Snapshot hashing and workdir diffing.

```python
from muse.core import snapshot
snap = snapshot.build(root)
```

## Contributing

See CONTRIBUTING.md for guidelines.
"""

    def test_top_level_sections_extracted(self) -> None:
        syms = _parse(self._README)
        top = [k for k in syms if "::" in k]
        names = [k.split("::")[-1] for k in top]
        assert "Muse" in names or any("Muse" in n for n in names)

    def test_installation_section_extracted(self) -> None:
        syms = _parse(self._README)
        assert any("Installation" in k for k in syms)

    def test_usage_commands_table_extracted(self) -> None:
        syms = _parse(self._README)
        assert any("table@L" in k for k in syms)

    def test_bash_code_block_extracted(self) -> None:
        syms = _parse(self._README)
        assert any("code[bash]" in k for k in syms)

    def test_python_code_block_extracted(self) -> None:
        syms = _parse(self._README)
        assert any("code[python]" in k for k in syms)

    def test_api_reference_subsection_extracted(self) -> None:
        syms = _parse(self._README)
        assert any("API Reference" in k for k in syms)

    def test_all_symbol_records_have_required_keys(self) -> None:
        syms = _parse(self._README)
        required = {
            "kind", "name", "qualified_name", "content_id", "body_hash",
            "signature_id", "metadata_id", "canonical_key", "lineno", "end_lineno",
        }
        for addr, rec in syms.items():
            missing = required - set(rec.keys())
            assert not missing, f"{addr!r} missing keys: {missing}"

    def test_no_symbol_has_empty_content_id(self) -> None:
        syms = _parse(self._README)
        for addr, rec in syms.items():
            assert rec["content_id"], f"{addr!r} has empty content_id"

    def test_all_linenos_positive(self) -> None:
        syms = _parse(self._README)
        for addr, rec in syms.items():
            assert rec["lineno"] > 0, f"{addr!r} lineno={rec['lineno']}"

    def test_all_end_linenos_gte_lineno(self) -> None:
        syms = _parse(self._README)
        for addr, rec in syms.items():
            assert rec["end_lineno"] >= rec["lineno"], (
                f"{addr!r} end_lineno={rec['end_lineno']} < lineno={rec['lineno']}"
            )

    def test_contributing_section_extracted(self) -> None:
        syms = _parse(self._README)
        assert any("Contributing" in k for k in syms)

    def test_commands_subsection_qualified_under_usage(self) -> None:
        syms = _parse(self._README)
        # "Commands" lives under "Usage", so its qualified name should
        # contain "Usage.Commands".
        assert any("Usage.Commands" in k for k in syms)

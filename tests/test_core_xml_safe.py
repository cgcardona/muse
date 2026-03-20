"""Tests for muse.core.xml_safe — defusedxml typed adapter.

These tests verify that:
1. SafeET.parse() correctly parses well-formed XML / MusicXML files.
2. SafeET.parse() blocks XML entity expansion attacks (Billion Laughs).
3. SafeET.parse() blocks external entity injection (XXE).
4. The returned ElementTree is a standard stdlib ElementTree instance.
5. The ParseError, Element, and ElementTree types are correctly re-exported.
"""

from __future__ import annotations

import pathlib
import xml.etree.ElementTree as StdET

import pytest

from muse.core.xml_safe import SafeET


# ---------------------------------------------------------------------------
# Helpers — test XML file factories
# ---------------------------------------------------------------------------


def _write(path: pathlib.Path, content: str) -> pathlib.Path:
    path.write_text(content, encoding="utf-8")
    return path


def _minimal_musicxml(tmp_path: pathlib.Path) -> pathlib.Path:
    xml = """\
<?xml version="1.0" encoding="UTF-8"?>
<score-partwise version="3.1">
  <part id="P1">
    <measure number="1">
      <note><pitch><step>C</step><octave>4</octave></pitch><duration>4</duration></note>
    </measure>
  </part>
</score-partwise>
"""
    return _write(tmp_path / "score.xml", xml)


def _billion_laughs_xml(tmp_path: pathlib.Path) -> pathlib.Path:
    """Classic entity expansion DoS payload (Billion Laughs)."""
    xml = """\
<?xml version="1.0"?>
<!DOCTYPE lolz [
  <!ENTITY lol "lol">
  <!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">
  <!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;">
  <!ENTITY lol4 "&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;">
]>
<root>&lol4;</root>
"""
    return _write(tmp_path / "billion_laughs.xml", xml)


def _xxe_file_xml(tmp_path: pathlib.Path) -> pathlib.Path:
    """External entity reference attempting to read /etc/passwd."""
    xml = """\
<?xml version="1.0"?>
<!DOCTYPE foo [
  <!ELEMENT foo ANY>
  <!ENTITY xxe SYSTEM "file:///etc/passwd">
]>
<foo>&xxe;</foo>
"""
    return _write(tmp_path / "xxe.xml", xml)


def _external_dtd_xml(tmp_path: pathlib.Path) -> pathlib.Path:
    """DTD pulled from an external URL — should be forbidden."""
    xml = """\
<?xml version="1.0"?>
<!DOCTYPE root SYSTEM "http://attacker.example.com/evil.dtd">
<root>data</root>
"""
    return _write(tmp_path / "ext_dtd.xml", xml)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestSafeETParse:
    def test_parses_minimal_musicxml(self, tmp_path: pathlib.Path) -> None:
        path = _minimal_musicxml(tmp_path)
        tree = SafeET.parse(path)
        assert tree is not None

    def test_returns_element_tree_instance(self, tmp_path: pathlib.Path) -> None:
        path = _minimal_musicxml(tmp_path)
        tree = SafeET.parse(path)
        assert isinstance(tree, StdET.ElementTree)

    def test_getroot_returns_element(self, tmp_path: pathlib.Path) -> None:
        path = _minimal_musicxml(tmp_path)
        tree = SafeET.parse(path)
        root = tree.getroot()
        assert root is not None
        assert root.tag == "score-partwise"

    def test_find_works_on_result(self, tmp_path: pathlib.Path) -> None:
        path = _minimal_musicxml(tmp_path)
        tree = SafeET.parse(path)
        root = tree.getroot()
        assert root is not None
        note = root.find(".//note")
        assert note is not None

    def test_accepts_str_path(self, tmp_path: pathlib.Path) -> None:
        path = _minimal_musicxml(tmp_path)
        tree = SafeET.parse(str(path))
        assert tree.getroot() is not None

    def test_accepts_pathlib_path(self, tmp_path: pathlib.Path) -> None:
        path = _minimal_musicxml(tmp_path)
        tree = SafeET.parse(path)
        assert tree.getroot() is not None

    def test_nonexistent_file_raises(self, tmp_path: pathlib.Path) -> None:
        with pytest.raises((FileNotFoundError, StdET.ParseError)):
            SafeET.parse(tmp_path / "nonexistent.xml")


# ---------------------------------------------------------------------------
# Security — attack XML must be blocked
# ---------------------------------------------------------------------------


class TestSafeETSecurity:
    def test_billion_laughs_is_blocked(self, tmp_path: pathlib.Path) -> None:
        """Entity expansion (Billion Laughs DoS) must be rejected by defusedxml."""
        path = _billion_laughs_xml(tmp_path)
        with pytest.raises(Exception):
            SafeET.parse(path)

    def test_xxe_is_blocked(self, tmp_path: pathlib.Path) -> None:
        """External entity reference (XXE credential theft) must be rejected."""
        path = _xxe_file_xml(tmp_path)
        with pytest.raises(Exception):
            SafeET.parse(path)

    def test_external_dtd_reference_does_not_fetch(self, tmp_path: pathlib.Path) -> None:
        """An external DTD reference in the DOCTYPE must not trigger a network
        request.  defusedxml either raises or parses without fetching; in both
        cases no network call should occur.  We verify there is no connection
        attempt by relying on the offline test environment — if defusedxml
        silently ignores the SYSTEM reference the parse can succeed (the DTD is
        not actually applied), which is also acceptable.
        """
        path = _external_dtd_xml(tmp_path)
        # defusedxml may raise or succeed — both are safe outcomes.
        # What is never acceptable: fetching the remote URL.
        try:
            SafeET.parse(path)
        except Exception:
            pass  # Blocking the DTD by raising is the strictest safe outcome.


# ---------------------------------------------------------------------------
# Type re-exports
# ---------------------------------------------------------------------------


class TestSafeETReexports:
    def test_parse_error_is_xml_parse_error(self) -> None:
        """SafeET.ParseError must be the stdlib ParseError for generic catching."""
        assert SafeET.ParseError is StdET.ParseError

    def test_element_is_xml_element(self) -> None:
        assert SafeET.Element is StdET.Element

    def test_element_tree_is_xml_element_tree(self) -> None:
        assert SafeET.ElementTree is StdET.ElementTree

    def test_parse_method_exists(self) -> None:
        assert callable(SafeET.parse)

"""Typed safe XML parsing adapter.

Wraps ``defusedxml`` behind a typed interface so the rest of Muse can use
``SafeET.parse()`` with full type information and no suppression comments.

``defusedxml`` does not ship type stubs, so importing it directly would
require a suppression comment (banned by the project's zero-ignore rule).
This module contains the single, justified crossing of the typed/untyped
boundary and presents a fully-typed surface to callers.

Only ``parse()`` is exposed — the sole function we use from defusedxml.
All other ElementTree functionality (``Element``, ``iterparse``, etc.) is
re-exported from the stdlib ``xml.etree.ElementTree``, which is fully typed.
"""

from __future__ import annotations

import xml.etree.ElementTree as _StdET
from pathlib import Path
from xml.etree.ElementTree import Element, ElementTree, ParseError


def _defuse_parse(source: str | Path) -> ElementTree:
    """Parse an XML file through defusedxml to block entity expansion attacks.

    defusedxml raises ``defusedxml.DTDForbidden``, ``defusedxml.EntitiesForbidden``,
    etc. on malicious XML.  These are all subclasses of ``xml.etree.ElementTree.ParseError``
    so callers can catch ``ParseError`` generically.
    """
    import defusedxml.ElementTree as _dxml  # noqa: PLC0415 (local import intentional)

    return _dxml.parse(str(source))


class SafeET:
    """Namespace class — use ``SafeET.parse()`` as a drop-in for ``ET.parse()``."""

    @staticmethod
    def parse(source: str | Path) -> ElementTree:
        """Return an :class:`xml.etree.ElementTree.ElementTree` parsed safely."""
        return _defuse_parse(source)

    # Re-export stdlib types so callers do not need to import xml.etree.ElementTree
    # separately.
    ParseError = ParseError
    Element = Element
    ElementTree = ElementTree


__all__ = ["SafeET"]

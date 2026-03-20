"""Muse ignore — ``.museignore`` TOML parser and workspace path filter.

``.museignore`` uses TOML with two kinds of sections:

``[global]``
    Patterns applied to every domain.  Evaluated first, in array order.

``[domain.<name>]``
    Patterns applied only when the active domain is *<name>*.  Appended
    after global patterns and evaluated in array order.

Pattern syntax (gitignore-compatible):

- A trailing ``/`` marks a directory pattern; it is never matched against
  individual files (Muse VCS tracks files, not directories).
- A leading ``/`` **anchors** the pattern to the repository root, so
  ``/tmp/*.mid`` matches only ``tmp/drums.mid`` and not ``cache/tmp/drums.mid``.
- A leading ``!`` **negates** a pattern: a path previously matched by an ignore
  rule is un-ignored when it matches a subsequent negation rule.
- ``*`` matches any sequence of characters **except** a path separator (``/``).
- ``**`` matches any sequence of characters **including** path separators.
- All other characters are matched literally.

Rule evaluation
---------------
Patterns are evaluated in the order they appear (global first, then
domain-specific).  The **last matching rule wins**, mirroring gitignore
behaviour.  A later ``!important.tmp`` overrides an earlier ``*.tmp`` for
that specific path.

Public API
----------
- :func:`load_ignore_config` — parse ``.museignore`` → :data:`MuseIgnoreConfig`
- :func:`resolve_patterns`   — flatten config to ``list[str]`` for a domain
- :func:`is_ignored`         — test a relative POSIX path against a pattern list
"""

from __future__ import annotations

import fnmatch
import pathlib
import tomllib
from typing import TypedDict

_FILENAME = ".museignore"


class DomainSection(TypedDict, total=False):
    """Patterns for one ignore section (global or a named domain)."""

    patterns: list[str]


# ``global`` is a Python keyword, so we use the functional TypedDict form.
MuseIgnoreConfig = TypedDict(
    "MuseIgnoreConfig",
    {
        "global": DomainSection,
        "domain": dict[str, DomainSection],
    },
    total=False,
)


def load_ignore_config(root: pathlib.Path) -> MuseIgnoreConfig:
    """Read ``.museignore`` from *root* and return the parsed configuration.

    Builds :data:`MuseIgnoreConfig` from the raw TOML dict using explicit
    ``isinstance`` checks — no ``Any`` propagated into the return value.

    Args:
        root: Repository root directory (the directory that contains ``.muse/``
              and ``state/``).  The ``.museignore`` file, if present, lives
              directly inside *root*.

    Returns:
        A :data:`MuseIgnoreConfig` mapping.  Both the ``"global"`` key and the
        ``"domain"`` key are optional; use :func:`resolve_patterns` which
        handles all missing-key cases.  Returns an empty mapping when
        ``.museignore`` is absent.

    Raises:
        ValueError: When ``.museignore`` exists but contains invalid TOML.
    """
    ignore_file = root / _FILENAME
    if not ignore_file.exists():
        return {}

    raw_bytes = ignore_file.read_bytes()
    try:
        raw = tomllib.loads(raw_bytes.decode("utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"{_FILENAME}: TOML parse error — {exc}") from exc

    result: MuseIgnoreConfig = {}

    # [global] section
    global_raw = raw.get("global")
    if isinstance(global_raw, dict):
        global_section: DomainSection = {}
        global_patterns_val = global_raw.get("patterns")
        if isinstance(global_patterns_val, list):
            global_section["patterns"] = [
                p for p in global_patterns_val if isinstance(p, str)
            ]
        result["global"] = global_section

    # [domain.*] sections — each key under [domain] is a domain name.
    domain_raw = raw.get("domain")
    if isinstance(domain_raw, dict):
        domain_map: dict[str, DomainSection] = {}
        for domain_name, domain_val in domain_raw.items():
            if isinstance(domain_name, str) and isinstance(domain_val, dict):
                section: DomainSection = {}
                domain_patterns_val = domain_val.get("patterns")
                if isinstance(domain_patterns_val, list):
                    section["patterns"] = [
                        p for p in domain_patterns_val if isinstance(p, str)
                    ]
                domain_map[domain_name] = section
        result["domain"] = domain_map

    return result


def resolve_patterns(config: MuseIgnoreConfig, domain: str) -> list[str]:
    """Flatten *config* into an ordered pattern list for *domain*.

    Global patterns come first (in array order), followed by domain-specific
    patterns.  Patterns declared under any other domain are never included.

    Args:
        config: Parsed ignore configuration from :func:`load_ignore_config`.
        domain: The active domain name, e.g. ``"music"`` or ``"code"``.

    Returns:
        Ordered ``list[str]`` of raw glob pattern strings.  Returns an empty
        list when *config* is empty or neither section contains patterns.
    """
    global_patterns: list[str] = []
    if "global" in config:
        global_section = config["global"]
        if "patterns" in global_section:
            global_patterns = global_section["patterns"]

    domain_patterns: list[str] = []
    if "domain" in config:
        domain_map = config["domain"]
        if domain in domain_map:
            domain_section = domain_map[domain]
            if "patterns" in domain_section:
                domain_patterns = domain_section["patterns"]

    return global_patterns + domain_patterns


def is_ignored(rel_posix: str, patterns: list[str]) -> bool:
    """Return ``True`` if *rel_posix* should be excluded from the snapshot.

    Args:
        rel_posix: Workspace-relative POSIX path, e.g. ``"tracks/drums.mid"``.
        patterns:  Ordered pattern list from :func:`resolve_patterns`.

    Returns:
        ``True`` when the path is ignored, ``False`` otherwise.  An empty
        *patterns* list means nothing is ignored.

    The last matching rule wins.  A negation rule (``!pattern``) can un-ignore
    a path that was matched by an earlier rule.

    Directory patterns (trailing ``/``) match any file whose path starts with
    that directory prefix — e.g. ``artifacts/`` ignores ``artifacts/demo.html``.
    """
    p = pathlib.PurePosixPath(rel_posix)
    ignored = False
    for pattern in patterns:
        negate = pattern.startswith("!")
        pat = pattern[1:] if negate else pattern

        if pat.endswith("/"):
            # Directory pattern: match any file inside that directory.
            dir_prefix = pat  # e.g. "artifacts/"
            if rel_posix.startswith(dir_prefix) or _matches(p, pat.rstrip("/") + "/**"):
                ignored = not negate
        elif _matches(p, pat):
            ignored = not negate
    return ignored


def _matches(p: pathlib.PurePosixPath, pattern: str) -> bool:
    """Test whether the path *p* matches *pattern*.

    Implements gitignore path-matching semantics:

    - **Anchored** (leading ``/``): the pattern is matched against the full
      path from the root using :func:`fnmatch.fnmatch`.  The leading slash is
      stripped before matching.
    - **Pattern with embedded ``/``**: matched against the full relative path
      from the right using :meth:`pathlib.PurePosixPath.match`.
    - **Pattern without ``/``**: matched against every trailing suffix of the
      path (i.e. the filename, the filename plus its parent, etc.) so that
      ``*.tmp`` matches ``drums.tmp`` *and* ``tracks/drums.tmp``.
    """
    # Anchored pattern: must match the full path from the root.
    if pattern.startswith("/"):
        return fnmatch.fnmatch(str(p), pattern[1:])

    # Non-anchored pattern with an embedded slash: match from the right.
    # PurePosixPath.match() handles ** natively in Python 3.12+, but a
    # leading **/ does not always match zero path components in CPython 3.13
    # (implementation gap).  When the direct match fails, strip the leading
    # **/ and retry — this makes "**/cache/*.dat" match "cache/index.dat".
    if "/" in pattern:
        if p.match(pattern):
            return True
        if pattern.startswith("**/"):
            return p.match(pattern[3:])
        return False

    # Pattern without any slash: match against the filename or any suffix.
    # e.g. "*.tmp" must match "drums.tmp" (top-level) and "tracks/drums.tmp".
    for start in range(len(p.parts)):
        sub = pathlib.PurePosixPath(*p.parts[start:])
        if sub.match(pattern):
            return True
    return False

"""Muse ignore — ``.museignore`` parser and workspace path filter.

``.museignore`` uses the same syntax as ``.gitignore``:

- Lines beginning with ``#`` are comments and are ignored.
- Blank lines are ignored.
- A trailing ``/`` marks a directory pattern; it is never matched against
  individual files (Muse VCS tracks files, not directories).
- A leading ``/`` **anchors** the pattern to the repository root, so
  ``/tmp/*.mid`` matches only ``tmp/drums.mid`` and not ``cache/tmp/drums.mid``.
- A leading ``!`` **negates** a pattern: a path that was previously matched by
  an ignore rule is un-ignored if it matches a subsequent negation rule.
- ``*`` matches any sequence of characters **except** a path separator (``/``).
- ``**`` matches any sequence of characters **including** path separators.
- All other characters are matched literally.

Rule evaluation
---------------
Rules are evaluated top-to-bottom.  The **last matching rule wins**.  This
mirrors gitignore behaviour: a later ``!important.tmp`` overrides an earlier
``*.tmp`` for that specific path.

Public API
----------
- :func:`load_patterns` — parse ``.museignore`` → ``list[str]``
- :func:`is_ignored`    — test a relative POSIX path against a pattern list
"""

import fnmatch
import pathlib


def load_patterns(root: pathlib.Path) -> list[str]:
    """Read ``.museignore`` from *root* and return the non-empty, non-comment lines.

    Args:
        root: Repository root directory (the directory that contains ``.muse/``
              and ``muse-work/``).  The ``.museignore`` file, if present, lives
              directly inside *root*.

    Returns:
        A list of raw pattern strings in file order.  Blank lines and lines
        starting with ``#`` are excluded.  Returns an empty list when
        ``.museignore`` is absent.
    """
    ignore_file = root / ".museignore"
    if not ignore_file.exists():
        return []
    patterns: list[str] = []
    for line in ignore_file.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            patterns.append(stripped)
    return patterns


def is_ignored(rel_posix: str, patterns: list[str]) -> bool:
    """Return ``True`` if *rel_posix* should be excluded from the snapshot.

    Args:
        rel_posix: Workspace-relative POSIX path, e.g. ``"tracks/drums.mid"``.
        patterns:  Pattern list returned by :func:`load_patterns`.

    Returns:
        ``True`` when the path is ignored, ``False`` otherwise.  An empty
        *patterns* list means nothing is ignored.

    The last matching rule wins.  A negation rule (``!pattern``) can un-ignore
    a path that was matched by an earlier rule.

    Directory-only patterns (trailing ``/``) are silently skipped because Muse
    tracks files, not directories.
    """
    p = pathlib.PurePosixPath(rel_posix)
    ignored = False
    for pattern in patterns:
        negate = pattern.startswith("!")
        pat = pattern[1:] if negate else pattern

        # Directory-only patterns never match files.
        if pat.endswith("/"):
            continue

        if _matches(p, pat):
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

"""Muse attributes — ``.museattributes`` parser and per-path strategy resolver.

``.museattributes`` lives in the repository root (next to ``.muse/`` and
``muse-work/``) and declares merge strategies for specific paths and
dimensions.  It uses the same ``fnmatch`` glob syntax as ``.gitignore`` for
path patterns, plus a dimension column for domain-specific orthogonal axes.

Format
------

::

    <path-pattern>  <dimension>  <strategy>

- **path-pattern** — an ``fnmatch`` glob matched against workspace-relative
  POSIX paths.
- **dimension** — a domain-defined axis name (e.g. ``melodic``, ``harmonic``)
  or ``*`` to match any dimension.
- **strategy** — one of ``ours | theirs | union | auto | manual``.

Lines beginning with ``#`` and blank lines are ignored.  **First matching rule
wins.**

Public API
----------

- :class:`AttributeRule` — a single parsed rule.
- :func:`load_attributes` — read ``.museattributes`` from a repo root.
- :func:`resolve_strategy` — first-match strategy lookup.
"""
from __future__ import annotations

import fnmatch
import pathlib
from dataclasses import dataclass

VALID_STRATEGIES: frozenset[str] = frozenset(
    {"ours", "theirs", "union", "auto", "manual"}
)

_FILENAME = ".museattributes"


@dataclass(frozen=True)
class AttributeRule:
    """A single rule from ``.museattributes``.

    Attributes:
        path_pattern: ``fnmatch`` glob matched against workspace-relative paths.
        dimension:    Domain axis name (e.g. ``"melodic"``) or ``"*"``.
        strategy:     Resolution strategy: ``ours | theirs | union | auto | manual``.
        source_line:  1-based line number in the file (for diagnostics).
    """

    path_pattern: str
    dimension: str
    strategy: str
    source_line: int = 0


def load_attributes(root: pathlib.Path) -> list[AttributeRule]:
    """Parse ``.museattributes`` from *root* and return the ordered rule list.

    Args:
        root: Repository root directory (the directory that contains ``.muse/``
              and ``muse-work/``).

    Returns:
        A list of :class:`AttributeRule` in file order.  Returns an empty list
        when ``.museattributes`` is absent or contains no valid rules.

    Raises:
        ValueError: If a line has an invalid strategy or wrong number of fields.
    """
    attr_file = root / _FILENAME
    if not attr_file.exists():
        return []

    rules: list[AttributeRule] = []
    for lineno, raw_line in enumerate(
        attr_file.read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        parts = line.split()
        if len(parts) != 3:
            raise ValueError(
                f"{_FILENAME}:{lineno}: expected 3 fields "
                f"(path-pattern dimension strategy), got {len(parts)}: {line!r}"
            )

        path_pattern, dimension, strategy = parts
        if strategy not in VALID_STRATEGIES:
            raise ValueError(
                f"{_FILENAME}:{lineno}: unknown strategy {strategy!r}. "
                f"Valid strategies: {sorted(VALID_STRATEGIES)}"
            )

        rules.append(
            AttributeRule(
                path_pattern=path_pattern,
                dimension=dimension,
                strategy=strategy,
                source_line=lineno,
            )
        )
    return rules


def resolve_strategy(
    rules: list[AttributeRule],
    path: str,
    dimension: str = "*",
) -> str:
    """Return the first matching strategy for *path* and *dimension*.

    Matching rules:

    - **path**: ``fnmatch.fnmatch(path, rule.path_pattern)`` must be ``True``.
    - **dimension**: ``rule.dimension`` must be ``"*"`` (matches anything) **or**
      equal *dimension*.

    First-match wins.  Returns ``"auto"`` when no rule matches.

    Args:
        rules:     Rule list from :func:`load_attributes`.
        path:      Workspace-relative POSIX path (e.g. ``"tracks/drums.mid"``).
        dimension: Domain axis name or ``"*"`` to match any rule dimension.

    Returns:
        A strategy string: ``"ours"``, ``"theirs"``, ``"union"``, ``"auto"``,
        or ``"manual"``.
    """
    for rule in rules:
        path_match = fnmatch.fnmatch(path, rule.path_pattern)
        dim_match = rule.dimension == "*" or rule.dimension == dimension or dimension == "*"
        if path_match and dim_match:
            return rule.strategy
    return "auto"

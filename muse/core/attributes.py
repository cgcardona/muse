"""Muse attributes — ``.museattributes`` TOML parser and per-path strategy resolver.

``.museattributes`` lives in the repository root (next to ``.muse/`` and
``state/``) and declares merge strategies for specific paths and
dimensions.  It uses TOML syntax with an optional ``[meta]`` section for
domain declaration and an ordered ``[[rules]]`` array.

Format
------

.. code-block:: toml

    # .museattributes
    # Merge strategy overrides for this repository.

    [meta]
    domain = "midi"          # optional — validated against .muse/repo.json

    [[rules]]
    path      = "drums/*"   # fnmatch glob against workspace-relative POSIX paths
    dimension = "*"          # domain axis name, or "*" to match any dimension
    strategy  = "ours"       # resolution strategy (see below)
    comment   = "Drums are always authored by branch A — always prefer ours."
    priority  = 10           # optional; higher priority rules are tried first

    [[rules]]
    path      = "keys/*"
    dimension = "pitch_bend"
    strategy  = "theirs"
    comment   = "Remote always has the better pitch-bend automation."

    [[rules]]
    path      = "*"
    dimension = "*"
    strategy  = "auto"

Strategies
----------

``ours``
    Take the left / current-branch version; the path is removed from the
    conflict list.

``theirs``
    Take the right / incoming-branch version; the path is removed from the
    conflict list.

``union``
    Include **all** additions from both sides.  Deletions are honoured only
    when **both** sides agree.  For independent element sets (MIDI notes,
    code symbol additions, import sets) this produces a combined result with
    no conflicts.  For opaque binary blobs where full unification is
    impossible, the left / current-branch blob is preferred and the path is
    removed from the conflict list.

``base``
    Revert to the common merge-base version — discard changes from *both*
    branches.  Useful for generated files, lock files, or any path that
    should always stay at a known-good state during a merge.

``auto``
    Default behaviour.  Defer to the engine's three-way algorithm.

``manual``
    Force the path into the conflict list even if the engine would
    auto-resolve it.  Use this to guarantee human review on safety-critical
    paths.

Rule fields
-----------

``path``      (required) — ``fnmatch`` glob matched against workspace-relative
              POSIX paths (e.g. ``"tracks/*.mid"``, ``"src/**/*.py"``).

``dimension`` (required) — domain axis name (e.g. ``"notes"``,
              ``"pitch_bend"``, ``"symbols"``) or ``"*"`` to match any
              dimension.

``strategy``  (required) — one of the six strategies listed above.

``comment``   (optional) — free-form documentation string; ignored at
              runtime.  Use it to explain *why* the rule exists.

``priority``  (optional, default 0) — integer used to order rules before
              file order.  Higher-priority rules are evaluated first.  Rules
              with equal priority preserve their declaration order.

**First matching rule wins** after sorting by priority (descending) then
file order (ascending).

``[meta]`` is optional; its absence has no effect on merge correctness.
When both ``[meta] domain`` and a repo ``domain`` are known, a mismatch
logs a warning.

Public API
----------

- :class:`AttributesMeta` — TypedDict for the ``[meta]`` section.
- :class:`AttributesRuleDict` — TypedDict for a single ``[[rules]]`` entry.
- :class:`MuseAttributesFile` — TypedDict for the full parsed file.
- :class:`AttributeRule` — a single resolved rule (dataclass).
- :func:`read_attributes_meta` — read only the ``[meta]`` section.
- :func:`load_attributes` — read ``.museattributes`` from a repo root.
- :func:`resolve_strategy` — first-match strategy lookup.
"""

from __future__ import annotations

import fnmatch
import logging
import pathlib
import tomllib
from dataclasses import dataclass, field
from typing import TypedDict

_logger = logging.getLogger(__name__)

VALID_STRATEGIES: frozenset[str] = frozenset(
    {"ours", "theirs", "union", "base", "auto", "manual"}
)

_FILENAME = ".museattributes"


class AttributesMeta(TypedDict, total=False):
    """Typed representation of the ``[meta]`` section in ``.museattributes``."""

    domain: str


class AttributesRuleDict(TypedDict, total=False):
    """Typed representation of a single ``[[rules]]`` entry.

    ``path``, ``dimension``, and ``strategy`` are required at parse time.
    ``comment`` and ``priority`` are optional.
    """

    path: str
    dimension: str
    strategy: str
    comment: str
    priority: int


class MuseAttributesFile(TypedDict, total=False):
    """Typed representation of the complete ``.museattributes`` file."""

    meta: AttributesMeta
    rules: list[AttributesRuleDict]


@dataclass(frozen=True)
class AttributeRule:
    """A single rule resolved from ``.museattributes``.

    Attributes:
        path_pattern: ``fnmatch`` glob matched against workspace-relative paths.
        dimension:    Domain axis name (e.g. ``"notes"``) or ``"*"``.
        strategy:     Resolution strategy: one of ``ours | theirs | union |
                      base | auto | manual``.
        comment:      Human-readable annotation explaining the rule's purpose.
                      Ignored at runtime.
        priority:     Ordering weight.  Higher values are evaluated before
                      lower values.  Rules with equal priority preserve
                      declaration order.
        source_index: 0-based index of the rule in the ``[[rules]]`` array.
    """

    path_pattern: str
    dimension: str
    strategy: str
    comment: str = ""
    priority: int = 0
    source_index: int = 0


def _parse_raw(root: pathlib.Path) -> MuseAttributesFile:
    """Read and TOML-parse ``.museattributes``, returning a typed file structure.

    Builds ``MuseAttributesFile`` from the raw TOML dict using explicit
    ``isinstance`` checks — no ``Any`` propagated into the return value.

    Raises:
        ValueError: On TOML syntax errors.
    """
    attr_file = root / _FILENAME
    raw_bytes = attr_file.read_bytes()
    try:
        raw = tomllib.loads(raw_bytes.decode("utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"{_FILENAME}: TOML parse error — {exc}") from exc

    result: MuseAttributesFile = {}

    # [meta] section
    meta_raw = raw.get("meta")
    if isinstance(meta_raw, dict):
        meta: AttributesMeta = {}
        domain_val = meta_raw.get("domain")
        if isinstance(domain_val, str):
            meta["domain"] = domain_val
        result["meta"] = meta

    # [[rules]] array
    rules_raw = raw.get("rules")
    if isinstance(rules_raw, list):
        rules: list[AttributesRuleDict] = []
        for idx, entry in enumerate(rules_raw):
            if not isinstance(entry, dict):
                continue
            path_val = entry.get("path")
            dim_val = entry.get("dimension")
            strat_val = entry.get("strategy")
            if (
                isinstance(path_val, str)
                and isinstance(dim_val, str)
                and isinstance(strat_val, str)
            ):
                rule: AttributesRuleDict = {
                    "path": path_val,
                    "dimension": dim_val,
                    "strategy": strat_val,
                }
                comment_val = entry.get("comment")
                if isinstance(comment_val, str):
                    rule["comment"] = comment_val
                priority_val = entry.get("priority")
                if isinstance(priority_val, int):
                    rule["priority"] = priority_val
                rules.append(rule)
            else:
                missing = [
                    f
                    for f, v in (
                        ("path", path_val),
                        ("dimension", dim_val),
                        ("strategy", strat_val),
                    )
                    if not isinstance(v, str)
                ]
                raise ValueError(
                    f"{_FILENAME}: rule[{idx}] is missing required field(s): "
                    + ", ".join(missing)
                )
        result["rules"] = rules

    return result


def read_attributes_meta(root: pathlib.Path) -> AttributesMeta:
    """Return the ``[meta]`` section of ``.museattributes``, or an empty dict.

    Does not validate or resolve rules — use this to inspect metadata only.

    Args:
        root: Repository root directory.

    Returns:
        The ``[meta]`` TypedDict, which may be empty if the section is absent
        or the file does not exist.
    """
    attr_file = root / _FILENAME
    if not attr_file.exists():
        return {}
    try:
        parsed = _parse_raw(root)
    except ValueError:
        return {}
    meta = parsed.get("meta")
    if meta is None:
        return {}
    return meta


def load_attributes(
    root: pathlib.Path,
    *,
    domain: str | None = None,
) -> list[AttributeRule]:
    """Parse ``.museattributes`` from *root* and return the ordered rule list.

    Rules are sorted by ``priority`` (descending) then by declaration order
    (ascending), so higher-priority rules are evaluated first.

    Args:
        root:   Repository root directory (the directory that contains ``.muse/``
                and ``state/``).
        domain: Optional domain name from the active repository.  When provided
                and the file contains ``[meta] domain``, a mismatch logs a
                warning.  Pass ``None`` to skip domain validation.

    Returns:
        A list of :class:`AttributeRule` sorted by priority then file order.
        Returns an empty list when ``.museattributes`` is absent or contains
        no valid rules.

    Raises:
        ValueError: If a rule entry is missing required fields, or contains an
                    invalid strategy.
    """
    attr_file = root / _FILENAME
    if not attr_file.exists():
        return []

    data = _parse_raw(root)

    # Domain validation
    meta = data.get("meta", {})
    file_domain = meta.get("domain") if meta else None
    if file_domain and domain and file_domain != domain:
        _logger.warning(
            "⚠️  %s: [meta] domain %r does not match active repo domain %r — "
            "rules may target a different domain",
            _FILENAME,
            file_domain,
            domain,
        )

    raw_rules = data.get("rules", [])

    rules: list[AttributeRule] = []
    for idx, entry in enumerate(raw_rules):
        strategy = entry["strategy"]
        if strategy not in VALID_STRATEGIES:
            raise ValueError(
                f"{_FILENAME}: rule[{idx}]: unknown strategy {strategy!r}. "
                f"Valid strategies: {sorted(VALID_STRATEGIES)}"
            )

        rules.append(
            AttributeRule(
                path_pattern=entry["path"],
                dimension=entry["dimension"],
                strategy=strategy,
                comment=entry.get("comment", ""),
                priority=entry.get("priority", 0),
                source_index=idx,
            )
        )

    # Stable sort: higher priority first, ties preserve declaration order.
    rules.sort(key=lambda r: -r.priority)
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

    First-match wins after priority ordering applied by :func:`load_attributes`.
    Returns ``"auto"`` when no rule matches.

    Args:
        rules:     Rule list from :func:`load_attributes`.
        path:      Workspace-relative POSIX path (e.g. ``"tracks/drums.mid"``).
        dimension: Domain axis name or ``"*"`` to match any rule dimension.

    Returns:
        A strategy string: ``"ours"``, ``"theirs"``, ``"union"``, ``"base"``,
        ``"auto"``, or ``"manual"``.
    """
    for rule in rules:
        path_match = fnmatch.fnmatch(path, rule.path_pattern)
        dim_match = (
            rule.dimension == "*"
            or rule.dimension == dimension
            or dimension == "*"
        )
        if path_match and dim_match:
            return rule.strategy
    return "auto"

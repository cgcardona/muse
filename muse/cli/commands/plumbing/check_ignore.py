"""muse plumbing check-ignore — test whether paths are ignored by ``.museignore``.

Reads the ``.museignore`` file (if present), resolves patterns for the active
domain, and evaluates each supplied path against the compiled rule list.
Reports whether each path is ignored and — when verbose — which pattern matched
it.

Output (JSON, default)::

    {
      "domain": "midi",
      "patterns_loaded": 4,
      "results": [
        {
          "path":             "build/output.bin",
          "ignored":          true,
          "matching_pattern": "build/"
        },
        {
          "path":             "tracks/drums.mid",
          "ignored":          false,
          "matching_pattern": null
        }
      ]
    }

Text output (``--format text``)::

    ignored   build/output.bin    [build/]
    ok        tracks/drums.mid

With ``--quiet`` (exits 0 if *all* paths are ignored, exits 1 otherwise, no
other output):

    (empty stdout)

Plumbing contract
-----------------

- Exit 0: all evaluated paths are ignored (success for ``--quiet`` mode), or
          all results emitted normally.
- Exit 1: one or more paths are not ignored (``--quiet`` mode only); bad args
          or bad ``--format``.
- Exit 3: I/O or TOML parse error reading ``.museignore``.
"""

from __future__ import annotations

import argparse
import fnmatch as _fnmatch
import json
import logging
import pathlib
import sys
from typing import TypedDict

from muse.core.errors import ExitCode
from muse.core.ignore import load_ignore_config, resolve_patterns
from muse.core.repo import require_repo
from muse.plugins.registry import read_domain

logger = logging.getLogger(__name__)

_FORMAT_CHOICES = ("json", "text")


class _PathResult(TypedDict):
    path: str
    ignored: bool
    matching_pattern: str | None


def _check_path(rel_posix: str, patterns: list[str]) -> _PathResult:
    """Evaluate a single path and return its ignore status plus matching pattern.

    Reimplements the last-match-wins traversal from :func:`muse.core.ignore.is_ignored`
    while also capturing which pattern last matched so we can surface it in
    verbose output.
    """
    p = pathlib.PurePosixPath(rel_posix)
    ignored = False
    matching: str | None = None

    for pattern in patterns:
        negate = pattern.startswith("!")
        pat = pattern[1:] if negate else pattern

        matched = False
        if pat.endswith("/"):
            dir_prefix = pat
            if rel_posix.startswith(dir_prefix) or _posix_match(p, pat.rstrip("/") + "/**"):
                matched = True
        elif _posix_match(p, pat):
            matched = True

        if matched:
            ignored = not negate
            matching = pattern if not negate else None

    return {"path": rel_posix, "ignored": ignored, "matching_pattern": matching}


def _posix_match(p: pathlib.PurePosixPath, pattern: str) -> bool:
    """Test whether *p* matches *pattern* using gitignore-style semantics."""
    if pattern.startswith("/"):
        return _fnmatch.fnmatch(str(p), pattern[1:])

    if "/" in pattern:
        if p.match(pattern):
            return True
        if pattern.startswith("**/"):
            return p.match(pattern[3:])
        return False

    for start in range(len(p.parts)):
        sub = pathlib.PurePosixPath(*p.parts[start:])
        if sub.match(pattern):
            return True
    return False


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the check-ignore subcommand."""
    parser = subparsers.add_parser(
        "check-ignore",
        help="Test paths against .museignore rules.",
        description=__doc__,
    )
    parser.add_argument(
        "paths",
        nargs="+",
        help="Workspace-relative paths to test.",
    )
    parser.add_argument(
        "--quiet", "-q",
        action="store_true",
        help="No output. Exit 0 if all paths are ignored, exit 1 otherwise.",
    )
    parser.add_argument(
        "--verbose", "-V",
        action="store_true",
        help="Include the matching pattern in text output.",
    )
    parser.add_argument(
        "--format", "-f",
        dest="fmt",
        default="json",
        metavar="FORMAT",
        help="Output format: json or text. (default: json)",
    )
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> None:
    """Test whether paths are excluded by ``.museignore`` rules.

    Evaluates each supplied path against the global and domain-specific
    patterns loaded from ``.museignore``.  Domain context is read automatically
    from ``.muse/repo.json``.

    Paths should be workspace-relative POSIX paths, e.g. ``tracks/drums.mid``
    or ``build/render.bin``.
    """
    fmt: str = args.fmt
    paths: list[str] = args.paths
    quiet: bool = args.quiet
    verbose: bool = args.verbose

    if fmt not in _FORMAT_CHOICES:
        print(
            json.dumps(
                {"error": f"Unknown format {fmt!r}. Valid: {', '.join(_FORMAT_CHOICES)}"}
            )
        )
        raise SystemExit(ExitCode.USER_ERROR)

    if not paths:
        print(json.dumps({"error": "At least one path argument is required."}))
        raise SystemExit(ExitCode.USER_ERROR)

    root = require_repo()
    domain = read_domain(root)

    try:
        config = load_ignore_config(root)
    except ValueError as exc:
        print(json.dumps({"error": str(exc)}))
        raise SystemExit(ExitCode.INTERNAL_ERROR)

    patterns = resolve_patterns(config, domain)
    results: list[_PathResult] = [_check_path(p, patterns) for p in paths]

    if quiet:
        all_ignored = all(r["ignored"] for r in results)
        raise SystemExit(0 if all_ignored else ExitCode.USER_ERROR)

    if fmt == "text":
        for r in results:
            status = "ignored" if r["ignored"] else "ok     "
            if verbose and r["matching_pattern"]:
                print(f"{status}  {r['path']}    [{r['matching_pattern']}]")
            else:
                print(f"{status}  {r['path']}")
        return

    print(
        json.dumps(
            {
                "domain": domain,
                "patterns_loaded": len(patterns),
                "results": [dict(r) for r in results],
            }
        )
    )

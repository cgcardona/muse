"""Shell tab-completion helpers for the muse CLI (argcomplete integration).

These completers are attached to argparse arguments so that argcomplete
can suggest branch names, remote names, and commit refs from the live
repository state without making any network calls.

Usage in a command module::

    from muse.cli._completers import branch_completer, remote_completer

    parser.add_argument("branch").completer = branch_completer
    parser.add_argument("remote").completer = remote_completer
"""

from __future__ import annotations

import pathlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import argparse


def _find_repo_root() -> pathlib.Path | None:
    """Walk up from cwd looking for a .muse directory."""
    cwd = pathlib.Path.cwd()
    for candidate in [cwd, *cwd.parents]:
        if (candidate / ".muse").is_dir():
            return candidate
    return None


def branch_completer(
    prefix: str,
    parsed_args: "argparse.Namespace",
    **kwargs: object,
) -> list[str]:
    """Return local branch names that start with *prefix*."""
    root = _find_repo_root()
    if root is None:
        return []
    refs_dir = root / ".muse" / "refs" / "heads"
    if not refs_dir.is_dir():
        return []
    return [
        p.name
        for p in refs_dir.iterdir()
        if p.is_file() and p.name.startswith(prefix)
    ]


def remote_completer(
    prefix: str,
    parsed_args: "argparse.Namespace",
    **kwargs: object,
) -> list[str]:
    """Return configured remote names that start with *prefix*."""
    root = _find_repo_root()
    if root is None:
        return []
    try:
        import tomllib
        config_path = root / ".muse" / "config.toml"
        if not config_path.exists():
            return []
        data = tomllib.loads(config_path.read_text())
        remotes: dict[str, object] = data.get("remote", {})
        return [name for name in remotes if name.startswith(prefix)]
    except Exception:
        return []


def ref_completer(
    prefix: str,
    parsed_args: "argparse.Namespace",
    **kwargs: object,
) -> list[str]:
    """Return branch names and short commit IDs that start with *prefix*."""
    branches = branch_completer(prefix, parsed_args, **kwargs)

    root = _find_repo_root()
    if root is None:
        return branches

    # Add short commit IDs from the commit store.
    commits_dir = root / ".muse" / "commits"
    if not commits_dir.is_dir():
        return branches

    short_ids = [
        p.stem[:8]
        for p in commits_dir.glob("*.json")
        if p.stem.startswith(prefix)
    ]
    return branches + short_ids

"""Pure filesystem snapshot logic for ``muse commit``.

All functions here are side-effect-free (no DB, no I/O besides reading
files under ``workdir``). They are kept separate so they can be
unit-tested without a database.

ID derivation contract (deterministic, no random/UUID components):

    object_id   = sha256(file_bytes).hexdigest()

    snapshot_id = sha256(
                      NUL.join(sorted(f"{path}NUL{oid}" for path, oid in manifest.items()))
                  ).hexdigest()

    commit_id   = sha256(
                      NUL.join([NUL.join(sorted(parent_ids)),
                                snapshot_id, message, committed_at_iso])
                  ).hexdigest()

The null byte (\\x00) is used as the field separator because it is:
  - Illegal in POSIX filenames (preventing separator-injection attacks from
    crafted file paths).
  - Absent from SHA-256 hex strings (preventing injection via object IDs).
  - Absent from ISO-8601 timestamps and typical message text.

This replaces the previous ``|`` / ``:`` separator scheme which allowed two
distinct manifests or commit inputs to produce the same hash if filenames
contained those characters.

Symlinks in the working tree are excluded from snapshots. Following a
symlink that points outside state/ would silently commit the contents
of arbitrary filesystem paths.

Exclusion policy
----------------
Dotfiles and dot-directories are **tracked by default** — ``.cursorrules``,
``.editorconfig``, ``.eslintrc`` are intentional project configuration that
collaborators need.  Exclusion is driven entirely by ``.museignore`` plus the
built-in secrets blocklist below.  The only hard-coded directory skip is
``.muse/`` itself (internal VCS storage) and a performance-only list of
directories that are universally noise (``node_modules/``, ``__pycache__/``,
``.venv/`` etc.).
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import stat as _stat

from muse.core.ignore import is_ignored, load_ignore_config, resolve_patterns
from muse.core.stat_cache import load_cache

# Directories that are always pruned before os.walk descends into them.
# These are either internal VCS storage (.muse) or universally-noisy
# directories whose contents are never meaningful project source.
# Kept as a frozenset for O(1) lookup inside the hot walk loop.
_ALWAYS_PRUNE_DIRS: frozenset[str] = frozenset(
    {
        ".muse",
        ".git",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        ".tox",
        ".nox",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        ".coverage",
        "htmlcov",
        "dist",
        "build",
    }
)

# Built-in secrets blocklist — applied even when .museignore is absent.
# This is the last line of defence: these files must never appear in a
# snapshot regardless of what a user configures in .museignore.
_BUILTIN_SECRET_PATTERNS: list[str] = [
    ".env",
    ".env.*",
    ".envrc",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    ".DS_Store",
    "Thumbs.db",
]


def _load_ignore_patterns(workdir: pathlib.Path) -> list[str]:
    """Return the combined ignore pattern list for *workdir*.

    Reads ``.museignore`` from *workdir* and detects the active domain from
    ``.muse/repo.json``.  Falls back to ``"code"`` when either file is absent.
    The built-in secrets blocklist is always prepended so it cannot be
    overridden by user configuration.
    """
    domain = "code"
    repo_json = workdir / ".muse" / "repo.json"
    if repo_json.exists():
        try:
            raw = json.loads(repo_json.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and isinstance(raw.get("domain"), str):
                domain = raw["domain"]
        except (OSError, json.JSONDecodeError):
            pass

    config = load_ignore_config(workdir)
    user_patterns = resolve_patterns(config, domain)
    return _BUILTIN_SECRET_PATTERNS + user_patterns

_SEP = "\x00"


def hash_file(path: pathlib.Path) -> str:
    """Return the sha256 hex digest of a file's raw bytes.

    This is the ``object_id`` for the given file. Reading in chunks
    keeps memory usage constant regardless of file size.
    """
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def build_snapshot_manifest(workdir: pathlib.Path) -> dict[str, str]:
    """Return ``{rel_path: object_id}`` for every tracked file in *workdir*.

    Preferred public name; delegates to :func:`walk_workdir`.
    """
    return walk_workdir(workdir)


def walk_workdir(workdir: pathlib.Path) -> dict[str, str]:
    """Walk *workdir* recursively and return ``{rel_path: object_id}``.

    Exclusions (all silent, no warning emitted):
    - Symlinks — following them could commit content from outside the repo.
    - Non-regular files — only regular files are included.
    - Paths matched by ``.museignore`` or the built-in secrets blocklist.
    - Directories in ``_ALWAYS_PRUNE_DIRS`` — internal VCS storage and
      universally-noisy directories (node_modules, __pycache__, .venv, …).

    Dotfiles and dot-directories are tracked unless excluded by the above
    rules.  ``.cursorrules``, ``.editorconfig``, ``.eslintrc`` etc. are
    intentional project configuration; the blanket dot-skip that Git-adjacent
    tools inherited is not carried forward here.

    Paths use POSIX separators regardless of host OS for cross-platform
    reproducibility.

    Performance note: ``os.walk`` with in-place ``dirnames`` pruning is used
    instead of ``pathlib.rglob`` so that large noisy directories are never
    descended into.  The stat cache further skips re-hashing files whose
    ``(mtime, size)`` is unchanged since the last walk.
    """
    ignore_patterns = _load_ignore_patterns(workdir)
    cache = load_cache(workdir)
    manifest: dict[str, str] = {}
    root_str = str(workdir)
    prefix_len = len(root_str) + 1  # +1 for the path separator

    for dirpath, dirnames, filenames in os.walk(root_str, followlinks=False):
        # Prune always-ignored directories in-place before os.walk descends.
        dirnames[:] = sorted(d for d in dirnames if d not in _ALWAYS_PRUNE_DIRS)

        for fname in sorted(filenames):
            abs_str = os.path.join(dirpath, fname)
            try:
                st = os.lstat(abs_str)
            except OSError:
                continue
            # os.lstat lets us check for symlinks and regular files with one
            # syscall, replacing the separate is_symlink() + is_file() pair.
            if not _stat.S_ISREG(st.st_mode):
                continue
            rel = abs_str[prefix_len:]
            if os.sep != "/":
                rel = rel.replace(os.sep, "/")
            if is_ignored(rel, ignore_patterns):
                continue
            manifest[rel] = cache.get_cached(rel, abs_str, st.st_mtime, st.st_size)

    cache.prune(set(manifest))
    cache.save()
    return manifest


def compute_snapshot_id(manifest: dict[str, str]) -> str:
    """Return sha256 of the sorted ``path NUL object_id`` pairs.

    The null-byte separator prevents collisions from filenames or object IDs
    that contain the previous ``|`` / ``:`` separators.

    Sorting ensures two identical working trees always produce the same
    snapshot_id, regardless of filesystem traversal order.
    """
    parts = sorted(f"{path}{_SEP}{oid}" for path, oid in manifest.items())
    payload = _SEP.join(parts).encode()
    return hashlib.sha256(payload).hexdigest()


def diff_workdir_vs_snapshot(
    workdir: pathlib.Path,
    last_manifest: dict[str, str],
) -> tuple[set[str], set[str], set[str], set[str]]:
    """Compare *workdir* against *last_manifest* from the previous commit.

    Returns a tuple of four disjoint path sets:

    - ``added`` — files in *workdir* absent from *last_manifest*
                      (new files since the last commit).
    - ``modified`` — files present in both but with a differing sha256 hash.
    - ``deleted`` — files in *last_manifest* absent from *workdir*.
    - ``untracked`` — non-empty only when *last_manifest* is empty (i.e. the
                      branch has no commits yet): every file in *workdir* is
                      treated as untracked rather than as newly-added.

    All paths use POSIX separators for cross-platform reproducibility.
    """
    if not workdir.exists():
        # Nothing on disk — every previously committed path is deleted.
        return set(), set(), set(last_manifest.keys()), set()

    current_manifest = walk_workdir(workdir)
    current_paths = set(current_manifest.keys())
    last_paths = set(last_manifest.keys())

    if not last_paths:
        # No prior snapshot — all working-tree files are untracked.
        return set(), set(), set(), current_paths

    added = current_paths - last_paths
    deleted = last_paths - current_paths
    common = current_paths & last_paths
    modified = {p for p in common if current_manifest[p] != last_manifest[p]}
    return added, modified, deleted, set()


def compute_commit_id(
    parent_ids: list[str],
    snapshot_id: str,
    message: str,
    committed_at_iso: str,
) -> str:
    """Return sha256 of the commit's canonical inputs.

    Uses null bytes as field separators to prevent separator-injection
    attacks from commit messages, author names, or branch names containing
    ``|`` characters.

    Given the same arguments on two machines the result is identical.
    ``parent_ids`` is sorted before hashing so insertion order does not
    affect determinism.
    """
    parts = [
        _SEP.join(sorted(parent_ids)),
        snapshot_id,
        message,
        committed_at_iso,
    ]
    payload = _SEP.join(parts).encode()
    return hashlib.sha256(payload).hexdigest()


def compute_commit_tree_id(
    parent_ids: list[str],
    snapshot_id: str,
    message: str,
    author: str,
) -> str:
    """Return a deterministic commit ID for a raw plumbing commit (no timestamp).

    Unlike ``compute_commit_id``, this function omits ``committed_at`` so that
    the same (parent_ids, snapshot_id, message, author) tuple always produces
    the same commit_id. This guarantees idempotency for ``muse commit-tree``:
    re-running with identical inputs returns the same ID without inserting a
    duplicate row.

    Args:
        parent_ids: Zero or more parent commit IDs. Sorted before hashing.
        snapshot_id: The sha256 ID of the snapshot this commit points to.
        message: The commit message.
        author: The author name string.

    Returns:
        A 64-character lowercase hex SHA-256 digest.
    """
    parts = [
        _SEP.join(sorted(parent_ids)),
        snapshot_id,
        message,
        author,
    ]
    payload = _SEP.join(parts).encode()
    return hashlib.sha256(payload).hexdigest()

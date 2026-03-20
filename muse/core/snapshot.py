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

Hidden directories (any path component starting with ``.``) are also excluded
to prevent accidental inclusion of ``.git/``, ``.env``, and similar.
"""

from __future__ import annotations

import hashlib
import pathlib

from muse.core.stat_cache import StatCache

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
    """Alias for ``walk_workdir`` — preferred name in public API."""
    return walk_workdir(workdir)


def walk_workdir(workdir: pathlib.Path) -> dict[str, str]:
    """Walk *workdir* recursively and return ``{rel_path: object_id}``.

    Exclusions (all silent, no warning emitted):
    - Symlinks — following them could commit content from outside state/.
    - Directories — only regular files are included.
    - Hidden files — names starting with ``.``.
    - Hidden directories — any path component starting with ``.``.

    Paths use POSIX separators regardless of host OS for cross-platform
    reproducibility.

    If a ``.muse/`` directory exists inside *workdir*, the walk uses
    :class:`~muse.core.stat_cache.StatCache` to avoid re-hashing unchanged
    files.  The cache is saved atomically after the walk.
    """
    muse_dir = workdir / ".muse"
    cache: StatCache | None = None
    if muse_dir.is_dir():
        cache = StatCache.load(muse_dir)

    manifest: dict[str, str] = {}
    for file_path in sorted(workdir.rglob("*")):
        if file_path.is_symlink():
            continue
        if not file_path.is_file():
            continue
        rel = file_path.relative_to(workdir)
        # Skip hidden files and files inside hidden directories.
        if any(part.startswith(".") for part in rel.parts):
            continue
        if cache is not None:
            obj_hash = cache.get_object_hash(workdir, file_path)
        else:
            obj_hash = hash_file(file_path)
        manifest[rel.as_posix()] = obj_hash

    if cache is not None:
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

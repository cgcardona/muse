"""Stat-based file hash cache — fast snapshot computation for all domains.

Architecture
------------
Every ``plugin.snapshot()`` call must hash every tracked file to detect
changes.  On a repository with hundreds of files this is the dominant cost of
``muse status``, ``muse diff``, and any command that calls ``snapshot()``.

``StatCache`` eliminates redundant I/O by persisting two classes of hash per
file between invocations:

Object hash
    SHA-256 of raw bytes.  Used by the content-addressed object store.
    Recomputed only when ``(mtime, size)`` changes.

Dimension hashes
    Domain-specific semantic hashes.  For the code domain these might be the
    SHA-256 of the AST symbol set, the import set, and so on.  For the MIDI
    domain they might be the hash of parsed note events, tempo map, and
    harmony analysis.  Populated by domain plugins after parsing; consumed by
    ``diff()`` and ``merge()`` to skip re-parsing unchanged files entirely.

    An empty ``dimensions`` dict means no semantic hashes are cached yet —
    this is the baseline state and is always safe.

Cache validity
--------------
A cache entry is valid when the file's current ``st_mtime`` and ``st_size``
exactly match the stored values — the same contract Git's index uses.  The
cache is **self-healing**: writing a file (e.g. ``muse checkout``) always
updates ``mtime``, causing a cache miss on the next scan.

Known corner-case ("racy Muse"): a file modified within the same filesystem
timestamp quantum *and* with identical size could be served a stale cache
entry.  This is identical to "racy git" and is not defended against here.

Storage
-------
``.muse/stat_cache.json`` — a versioned JSON document::

    {
        "version": 1,
        "entries": {
            "muse/core/snapshot.py": {
                "mtime": 1710000000.123456,
                "size": 4321,
                "object_hash": "<sha256-of-raw-bytes>",
                "dimensions": {
                    "symbols": "<sha256-of-ast-symbol-set>",
                    "imports": "<sha256-of-import-set>"
                }
            }
        }
    }

Writes are atomic: data is flushed to a ``.tmp`` sibling then renamed over
the target, so a crash mid-write never corrupts the cache.
"""

from __future__ import annotations

import hashlib
import json
import logging
import pathlib
from typing import TypedDict

logger = logging.getLogger(__name__)

_CACHE_VERSION = 1
_CACHE_FILENAME = "stat_cache.json"
_CHUNK = 65_536


class FileCacheEntry(TypedDict):
    """Persisted metadata for a single workspace file."""

    mtime: float
    size: int
    object_hash: str
    # Domain plugins write semantic hashes here after parsing.
    # Keys are dimension names ("symbols", "imports", "notes", …).
    # Empty dict == no dimension hashes cached yet; always safe to return None.
    dimensions: dict[str, str]


class _CacheDoc(TypedDict):
    """On-disk JSON document shape."""

    version: int
    entries: dict[str, FileCacheEntry]


def _hash_bytes(path: pathlib.Path) -> str:
    """Return the SHA-256 hex digest of *path*'s raw bytes.

    Reads in 64 KiB chunks so memory usage is constant regardless of file size.
    This is the single canonical implementation shared by the cache and all
    domain plugins — no more duplicated ``_hash_file`` helpers.
    """
    return _hash_str(str(path))


def _hash_str(path_str: str) -> str:
    """String-path variant of ``_hash_bytes`` — avoids constructing a Path object.

    Used in the hot inner loop of ``walk_workdir`` and plugin snapshot methods
    where the file path is already a plain string from ``os.walk``.
    """
    h = hashlib.sha256()
    with open(path_str, "rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


class StatCache:
    """Shared stat-based hash cache for all domain plugin ``snapshot()`` calls.

    Typical lifecycle inside a plugin's ``snapshot()``::

        cache = StatCache.load(root / ".muse")
        for file_path in walk(...):
            files[rel] = cache.get_object_hash(root, file_path)
        cache.prune(set(files))
        cache.save()

    The same instance can be passed to ``diff()`` or ``merge()`` logic to
    retrieve already-computed dimension hashes without re-parsing files.
    """

    def __init__(
        self, muse_dir: pathlib.Path | None, entries: dict[str, FileCacheEntry]
    ) -> None:
        self._muse_dir = muse_dir
        self._entries = entries
        self._dirty = False

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    @classmethod
    def load(cls, muse_dir: pathlib.Path) -> StatCache:
        """Load the cache from *muse_dir*/stat_cache.json.

        Validates the version field and every entry's field types on load so
        a corrupt or future-format file never poisons the cache.  Returns a
        fresh empty cache if the file is absent, unreadable, or version
        mismatches — never raises.

        Parsing is done inline (not via a typed helper) so that isinstance
        checks narrow from ``Any`` — the type returned by ``json.loads`` —
        giving mypy accurate control-flow narrowing without unreachable-branch
        false positives.
        """
        cache_file = muse_dir / _CACHE_FILENAME
        if cache_file.is_file():
            try:
                raw = json.loads(cache_file.read_text(encoding="utf-8"))
                if not (isinstance(raw, dict) and raw.get("version") == _CACHE_VERSION):
                    return cls(muse_dir, {})
                raw_entries = raw.get("entries")
                if not isinstance(raw_entries, dict):
                    return cls(muse_dir, {})
                entries: dict[str, FileCacheEntry] = {}
                for rel, ev in raw_entries.items():
                    if not isinstance(rel, str) or not isinstance(ev, dict):
                        continue
                    mtime = ev.get("mtime")
                    size = ev.get("size")
                    obj_hash = ev.get("object_hash")
                    dims = ev.get("dimensions")
                    if not (
                        isinstance(mtime, (int, float))
                        and isinstance(size, int)
                        and isinstance(obj_hash, str)
                        and isinstance(dims, dict)
                    ):
                        continue
                    entries[rel] = FileCacheEntry(
                        mtime=float(mtime),
                        size=size,
                        object_hash=obj_hash,
                        # Coerce dimension keys/values to str — guards against
                        # a cache written by a future version with non-str values.
                        dimensions={str(k): str(v) for k, v in dims.items()},
                    )
                return cls(muse_dir, entries)
            except (json.JSONDecodeError, KeyError, TypeError):
                logger.debug("⚠️ stat_cache.json unreadable — starting fresh")
        return cls(muse_dir, {})

    @classmethod
    def empty(cls) -> StatCache:
        """Return a no-op cache for contexts without a ``.muse`` directory."""
        return cls(None, {})

    # ------------------------------------------------------------------
    # Object hash — raw-bytes SHA-256
    # ------------------------------------------------------------------

    def get_cached(
        self, rel: str, abs_path_str: str, mtime: float, size: int
    ) -> str:
        """Fast inner-loop hash lookup with pre-computed stat values.

        Callers that already have ``(mtime, size)`` from an ``os.stat`` or
        ``os.walk`` call should use this method to avoid a redundant
        ``stat()`` syscall inside :meth:`get_object_hash`.

        Args:
            rel:          Workspace-relative POSIX path (cache key).
            abs_path_str: Absolute file path as a plain string — avoids
                          constructing a ``pathlib.Path`` in the hot loop.
            mtime:        ``st_mtime`` from the caller's stat result.
            size:         ``st_size`` from the caller's stat result.

        Returns:
            64-character lowercase hex SHA-256 digest.
        """
        entry = self._entries.get(rel)
        if entry is not None and entry["mtime"] == mtime and entry["size"] == size:
            return entry["object_hash"]

        obj_hash = _hash_str(abs_path_str)
        self._entries[rel] = FileCacheEntry(
            mtime=mtime,
            size=size,
            object_hash=obj_hash,
            dimensions={},
        )
        self._dirty = True
        return obj_hash

    def get_object_hash(self, root: pathlib.Path, file_path: pathlib.Path) -> str:
        """Return the SHA-256 of *file_path*, using the cache when valid.

        Convenience wrapper around :meth:`get_cached` for callers that work
        with ``pathlib.Path`` objects.  The hot inner loops of ``walk_workdir``
        and plugin snapshot methods call :meth:`get_cached` directly to skip
        pathlib overhead.

        Args:
            root:      Repository root — used to compute the workspace-relative
                       POSIX key.
            file_path: Absolute path to the file.

        Returns:
            64-character lowercase hex SHA-256 digest.
        """
        rel = file_path.relative_to(root).as_posix()
        st = file_path.stat()
        return self.get_cached(rel, str(file_path), st.st_mtime, st.st_size)

    # ------------------------------------------------------------------
    # Dimension hashes — domain-specific semantic hashes
    # ------------------------------------------------------------------

    def get_dimension(
        self,
        root: pathlib.Path,
        file_path: pathlib.Path,
        dimension: str,
    ) -> str | None:
        """Return a cached dimension hash, or ``None`` if not yet computed.

        Callers must verify that the entry is still valid by checking that
        the object hash hasn't changed (i.e. call ``get_object_hash`` first
        to ensure the entry is fresh).

        Args:
            root:      Repository root.
            file_path: Absolute path to the file.
            dimension: Dimension name, e.g. ``"symbols"`` or ``"notes"``.

        Returns:
            Cached hash string, or ``None`` if absent.
        """
        rel = file_path.relative_to(root).as_posix()
        entry = self._entries.get(rel)
        if entry is None:
            return None
        return entry["dimensions"].get(dimension)

    def set_dimension(
        self,
        root: pathlib.Path,
        file_path: pathlib.Path,
        dimension: str,
        hash_value: str,
    ) -> None:
        """Store a semantic hash for a specific dimension of *file_path*.

        Should be called by domain plugins after parsing a file whose object
        hash triggered a cache miss.  Silently ignored if the file has no
        entry (which should not happen in normal operation).

        Args:
            root:       Repository root.
            file_path:  Absolute path to the file.
            dimension:  Dimension name, e.g. ``"symbols"``.
            hash_value: Hash string to store.
        """
        rel = file_path.relative_to(root).as_posix()
        entry = self._entries.get(rel)
        if entry is None:
            return
        entry["dimensions"][dimension] = hash_value
        self._dirty = True

    # ------------------------------------------------------------------
    # Lifecycle helpers
    # ------------------------------------------------------------------

    def prune(self, known_paths: set[str]) -> None:
        """Remove entries for paths no longer present in the working tree.

        Call this after a full directory walk, passing the set of
        workspace-relative POSIX paths that were found.  Keeps the cache
        lean by evicting stale entries for deleted files.

        Args:
            known_paths: Set of rel-posix paths observed during the walk.
        """
        stale = set(self._entries) - known_paths
        if stale:
            for k in stale:
                del self._entries[k]
            self._dirty = True

    def save(self) -> None:
        """Atomically persist the cache to disk if it has changed.

        Uses a temp-file-then-rename pattern so a crash mid-write never
        leaves a corrupt cache file.  Silently skips when there is no
        ``.muse`` directory (e.g. in-memory unit tests).
        """
        if not self._dirty or self._muse_dir is None:
            return
        doc = _CacheDoc(version=_CACHE_VERSION, entries=self._entries)
        cache_file = self._muse_dir / _CACHE_FILENAME
        tmp = self._muse_dir / (_CACHE_FILENAME + ".tmp")
        tmp.write_text(
            json.dumps(doc, separators=(",", ":"), ensure_ascii=False),
            encoding="utf-8",
        )
        tmp.replace(cache_file)
        self._dirty = False
        logger.debug("✅ stat_cache saved (%d entries)", len(self._entries))


def load_cache(root: pathlib.Path) -> StatCache:
    """Convenience loader: return a ``StatCache`` for a repository root.

    Returns ``StatCache.empty()`` when *root* has no ``.muse`` directory
    so callers never need to guard against a missing repo.

    Args:
        root: Repository root (the directory that contains ``.muse/``).

    Returns:
        A ``StatCache`` instance ready for use.
    """
    muse_dir = root / ".muse"
    if muse_dir.is_dir():
        return StatCache.load(muse_dir)
    return StatCache.empty()

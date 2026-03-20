"""Tests for muse.core.stat_cache.

Coverage
--------
- Cache hit: file with unchanged (mtime, size) returns stored hash without I/O.
- Cache miss: new or modified file is re-hashed and entry is updated.
- Stale-entry pruning: entries for deleted files are removed.
- Dimension hash round-trip: set_dimension / get_dimension.
- Dimension eviction on object-hash miss: dimensions reset when file changes.
- Persistence: save() / load() round-trip via .muse/stat_cache.json.
- Atomic write: temp file is cleaned up; no corruption on concurrent use.
- empty(): no-op — save() is a no-op without a muse_dir.
- load_cache() convenience helper.
- walk_workdir() integration: cache is used and persisted automatically.
"""

from __future__ import annotations

import json
import pathlib
import time

import pytest

from muse.core.stat_cache import FileCacheEntry, StatCache, _hash_bytes, load_cache
from muse.core.snapshot import walk_workdir


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_muse_dir(tmp_path: pathlib.Path) -> pathlib.Path:
    muse_dir = tmp_path / ".muse"
    muse_dir.mkdir()
    return muse_dir


def _write(path: pathlib.Path, content: str = "hello") -> pathlib.Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# _hash_bytes — canonical hash function
# ---------------------------------------------------------------------------


class TestHashBytes:
    def test_matches_hashlib(self, tmp_path: pathlib.Path) -> None:
        import hashlib

        f = _write(tmp_path / "f.txt", "muse")
        expected = hashlib.sha256(b"muse").hexdigest()
        assert _hash_bytes(f) == expected

    def test_empty_file(self, tmp_path: pathlib.Path) -> None:
        import hashlib

        f = tmp_path / "empty.txt"
        f.write_bytes(b"")
        assert _hash_bytes(f) == hashlib.sha256(b"").hexdigest()

    def test_large_file_chunked(self, tmp_path: pathlib.Path) -> None:
        import hashlib

        data = b"x" * (200 * 1024)  # 200 KiB — forces multiple 64 KiB chunks
        f = tmp_path / "big.bin"
        f.write_bytes(data)
        assert _hash_bytes(f) == hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# StatCache — construction
# ---------------------------------------------------------------------------


class TestStatCacheConstruction:
    def test_load_missing_file_returns_empty(self, tmp_path: pathlib.Path) -> None:
        muse_dir = _make_muse_dir(tmp_path)
        cache = StatCache.load(muse_dir)
        assert cache._entries == {}

    def test_load_corrupt_json_returns_empty(self, tmp_path: pathlib.Path) -> None:
        muse_dir = _make_muse_dir(tmp_path)
        (muse_dir / "stat_cache.json").write_text("not json", encoding="utf-8")
        cache = StatCache.load(muse_dir)
        assert cache._entries == {}

    def test_load_wrong_version_returns_empty(self, tmp_path: pathlib.Path) -> None:
        muse_dir = _make_muse_dir(tmp_path)
        (muse_dir / "stat_cache.json").write_text(
            '{"version": 99, "entries": {}}', encoding="utf-8"
        )
        cache = StatCache.load(muse_dir)
        assert cache._entries == {}

    def test_empty_has_no_muse_dir(self, tmp_path: pathlib.Path) -> None:
        cache = StatCache.empty()
        assert cache._muse_dir is None
        assert cache._entries == {}

    def test_load_cache_helper_with_muse_dir(self, tmp_path: pathlib.Path) -> None:
        _make_muse_dir(tmp_path)
        cache = load_cache(tmp_path)
        assert isinstance(cache, StatCache)
        assert cache._muse_dir == tmp_path / ".muse"

    def test_load_cache_helper_without_muse_dir(self, tmp_path: pathlib.Path) -> None:
        cache = load_cache(tmp_path)
        assert cache._muse_dir is None


# ---------------------------------------------------------------------------
# StatCache — get_object_hash (hit / miss)
# ---------------------------------------------------------------------------


class TestGetObjectHash:
    def test_first_call_is_cache_miss(self, tmp_path: pathlib.Path) -> None:
        muse_dir = _make_muse_dir(tmp_path)
        f = _write(tmp_path / "a.py", "x = 1")
        cache = StatCache.load(muse_dir)

        h = cache.get_object_hash(tmp_path, f)

        assert h == _hash_bytes(f)
        assert cache._dirty is True
        assert "a.py" in cache._entries

    def test_second_call_is_cache_hit_no_dirty(self, tmp_path: pathlib.Path) -> None:
        muse_dir = _make_muse_dir(tmp_path)
        f = _write(tmp_path / "a.py", "x = 1")
        cache = StatCache.load(muse_dir)
        cache.get_object_hash(tmp_path, f)
        cache._dirty = False  # reset after first miss

        h2 = cache.get_object_hash(tmp_path, f)

        assert h2 == _hash_bytes(f)
        assert cache._dirty is False  # no re-hash, no dirty flag

    def test_modified_file_triggers_miss(self, tmp_path: pathlib.Path) -> None:
        muse_dir = _make_muse_dir(tmp_path)
        f = _write(tmp_path / "a.py", "x = 1")
        cache = StatCache.load(muse_dir)
        h1 = cache.get_object_hash(tmp_path, f)

        # Modify file content (ensure mtime changes on this filesystem).
        time.sleep(0.01)
        f.write_text("x = 2", encoding="utf-8")
        h2 = cache.get_object_hash(tmp_path, f)

        assert h1 != h2
        assert h2 == _hash_bytes(f)

    def test_same_content_new_mtime_triggers_miss_but_same_hash(
        self, tmp_path: pathlib.Path
    ) -> None:
        muse_dir = _make_muse_dir(tmp_path)
        f = _write(tmp_path / "a.py", "identical")
        cache = StatCache.load(muse_dir)
        h1 = cache.get_object_hash(tmp_path, f)

        time.sleep(0.01)
        f.write_text("identical", encoding="utf-8")
        h2 = cache.get_object_hash(tmp_path, f)

        # Cache miss because mtime changed, but hash is still the same.
        assert h1 == h2


# ---------------------------------------------------------------------------
# StatCache — dimension hashes
# ---------------------------------------------------------------------------


class TestDimensionHashes:
    def test_set_and_get_dimension(self, tmp_path: pathlib.Path) -> None:
        muse_dir = _make_muse_dir(tmp_path)
        f = _write(tmp_path / "src.py")
        cache = StatCache.load(muse_dir)
        cache.get_object_hash(tmp_path, f)  # ensure entry exists

        cache.set_dimension(tmp_path, f, "symbols", "abc123")

        assert cache.get_dimension(tmp_path, f, "symbols") == "abc123"

    def test_get_dimension_missing_key_returns_none(self, tmp_path: pathlib.Path) -> None:
        muse_dir = _make_muse_dir(tmp_path)
        f = _write(tmp_path / "src.py")
        cache = StatCache.load(muse_dir)
        cache.get_object_hash(tmp_path, f)

        assert cache.get_dimension(tmp_path, f, "nonexistent") is None

    def test_get_dimension_missing_entry_returns_none(self, tmp_path: pathlib.Path) -> None:
        muse_dir = _make_muse_dir(tmp_path)
        f = _write(tmp_path / "src.py")
        cache = StatCache.load(muse_dir)
        # Never called get_object_hash, so no entry exists.
        assert cache.get_dimension(tmp_path, f, "symbols") is None

    def test_dimension_evicted_on_object_hash_miss(self, tmp_path: pathlib.Path) -> None:
        """When a file changes, its dimension hashes must be cleared."""
        muse_dir = _make_muse_dir(tmp_path)
        f = _write(tmp_path / "src.py", "v1")
        cache = StatCache.load(muse_dir)
        cache.get_object_hash(tmp_path, f)
        cache.set_dimension(tmp_path, f, "symbols", "stale-hash")

        time.sleep(0.01)
        f.write_text("v2", encoding="utf-8")
        cache.get_object_hash(tmp_path, f)  # triggers miss → evicts dimensions

        assert cache.get_dimension(tmp_path, f, "symbols") is None

    def test_multiple_dimensions(self, tmp_path: pathlib.Path) -> None:
        muse_dir = _make_muse_dir(tmp_path)
        f = _write(tmp_path / "src.py")
        cache = StatCache.load(muse_dir)
        cache.get_object_hash(tmp_path, f)
        cache.set_dimension(tmp_path, f, "symbols", "sym-hash")
        cache.set_dimension(tmp_path, f, "imports", "imp-hash")

        assert cache.get_dimension(tmp_path, f, "symbols") == "sym-hash"
        assert cache.get_dimension(tmp_path, f, "imports") == "imp-hash"

    def test_set_dimension_noop_for_unknown_file(self, tmp_path: pathlib.Path) -> None:
        """set_dimension on a file with no entry must not crash."""
        muse_dir = _make_muse_dir(tmp_path)
        f = _write(tmp_path / "ghost.py")
        cache = StatCache.load(muse_dir)
        # No get_object_hash call → no entry.
        cache.set_dimension(tmp_path, f, "symbols", "x")  # must not raise


# ---------------------------------------------------------------------------
# StatCache — prune
# ---------------------------------------------------------------------------


class TestPrune:
    def test_prune_removes_stale_entries(self, tmp_path: pathlib.Path) -> None:
        muse_dir = _make_muse_dir(tmp_path)
        f1 = _write(tmp_path / "keep.py")
        f2 = _write(tmp_path / "gone.py")
        cache = StatCache.load(muse_dir)
        cache.get_object_hash(tmp_path, f1)
        cache.get_object_hash(tmp_path, f2)

        cache.prune({"keep.py"})

        assert "keep.py" in cache._entries
        assert "gone.py" not in cache._entries

    def test_prune_noop_when_all_present(self, tmp_path: pathlib.Path) -> None:
        muse_dir = _make_muse_dir(tmp_path)
        f = _write(tmp_path / "a.py")
        cache = StatCache.load(muse_dir)
        cache.get_object_hash(tmp_path, f)
        cache._dirty = False

        cache.prune({"a.py"})

        assert cache._dirty is False

    def test_prune_empty_known_set_clears_all(self, tmp_path: pathlib.Path) -> None:
        muse_dir = _make_muse_dir(tmp_path)
        f = _write(tmp_path / "a.py")
        cache = StatCache.load(muse_dir)
        cache.get_object_hash(tmp_path, f)

        cache.prune(set())

        assert cache._entries == {}


# ---------------------------------------------------------------------------
# StatCache — persistence (save / load round-trip)
# ---------------------------------------------------------------------------


class TestPersistence:
    def test_save_and_reload(self, tmp_path: pathlib.Path) -> None:
        muse_dir = _make_muse_dir(tmp_path)
        f = _write(tmp_path / "mod.py", "print('hi')")
        cache = StatCache.load(muse_dir)
        h = cache.get_object_hash(tmp_path, f)
        cache.save()

        assert (muse_dir / "stat_cache.json").is_file()

        cache2 = StatCache.load(muse_dir)
        cache2._dirty = False
        h2 = cache2.get_object_hash(tmp_path, f)

        assert h2 == h
        assert cache2._dirty is False  # served from cache, no re-hash

    def test_save_is_atomic_no_tmp_left(self, tmp_path: pathlib.Path) -> None:
        muse_dir = _make_muse_dir(tmp_path)
        f = _write(tmp_path / "x.py")
        cache = StatCache.load(muse_dir)
        cache.get_object_hash(tmp_path, f)
        cache.save()

        assert not (muse_dir / "stat_cache.json.tmp").exists()

    def test_save_noop_when_not_dirty(self, tmp_path: pathlib.Path) -> None:
        muse_dir = _make_muse_dir(tmp_path)
        cache = StatCache.load(muse_dir)
        cache.save()  # nothing written
        assert not (muse_dir / "stat_cache.json").exists()

    def test_empty_cache_save_is_noop(self) -> None:
        cache = StatCache.empty()
        cache.save()  # must not raise

    def test_dimensions_persisted(self, tmp_path: pathlib.Path) -> None:
        muse_dir = _make_muse_dir(tmp_path)
        f = _write(tmp_path / "s.py")
        cache = StatCache.load(muse_dir)
        cache.get_object_hash(tmp_path, f)
        cache.set_dimension(tmp_path, f, "symbols", "sym42")
        cache.save()

        cache2 = StatCache.load(muse_dir)
        # Validate entry shape — mtime/size unchanged so entry is still valid.
        assert cache2.get_dimension(tmp_path, f, "symbols") == "sym42"

    def test_json_format_is_versioned(self, tmp_path: pathlib.Path) -> None:
        muse_dir = _make_muse_dir(tmp_path)
        f = _write(tmp_path / "v.py")
        cache = StatCache.load(muse_dir)
        cache.get_object_hash(tmp_path, f)
        cache.save()

        raw = json.loads((muse_dir / "stat_cache.json").read_text(encoding="utf-8"))
        assert raw["version"] == 1
        assert "v.py" in raw["entries"]


# ---------------------------------------------------------------------------
# walk_workdir integration
# ---------------------------------------------------------------------------


class TestWalkWorkdirCacheIntegration:
    def test_walk_creates_cache_file(self, tmp_path: pathlib.Path) -> None:
        muse_dir = tmp_path / ".muse"
        muse_dir.mkdir()
        _write(tmp_path / "a.py", "x = 1")
        _write(tmp_path / "b.py", "y = 2")

        walk_workdir(tmp_path)

        assert (muse_dir / "stat_cache.json").is_file()

    def test_walk_second_call_uses_cache(self, tmp_path: pathlib.Path) -> None:
        """Second walk should hit cache for both files — no dirty flag set."""
        muse_dir = tmp_path / ".muse"
        muse_dir.mkdir()
        _write(tmp_path / "a.py", "x = 1")

        walk_workdir(tmp_path)  # cold — populates cache

        cache = StatCache.load(muse_dir)
        cache._dirty = False
        cache.get_object_hash(tmp_path, tmp_path / "a.py")
        # Should not set dirty because mtime/size unchanged.
        assert cache._dirty is False

    def test_walk_excludes_hidden_paths_from_cache(self, tmp_path: pathlib.Path) -> None:
        muse_dir = tmp_path / ".muse"
        muse_dir.mkdir()
        _write(tmp_path / "visible.py")
        _write(tmp_path / ".hidden.py")

        manifest = walk_workdir(tmp_path)

        assert "visible.py" in manifest
        assert ".hidden.py" not in manifest

    def test_walk_without_muse_dir_still_works(self, tmp_path: pathlib.Path) -> None:
        """walk_workdir must work correctly even with no .muse directory."""
        _write(tmp_path / "a.py", "ok")
        manifest = walk_workdir(tmp_path)
        assert "a.py" in manifest

    def test_walk_hashes_match_direct_hash(self, tmp_path: pathlib.Path) -> None:
        muse_dir = tmp_path / ".muse"
        muse_dir.mkdir()
        f = _write(tmp_path / "c.py", "content")

        manifest = walk_workdir(tmp_path)

        assert manifest["c.py"] == _hash_bytes(f)

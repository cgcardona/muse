"""Stress tests for the content-addressed object store.

Exercises:
- Write-then-read round-trip for varied payload sizes (1 byte … 10 MB).
- Idempotency: writing the same object ID twice is a no-op.
- has_object before and after writes.
- object_path sharding: first two hex chars as directory.
- read_object returns None for absent objects.
- restore_object copies bytes faithfully.
- write_object_from_path uses copy semantics, not load.
- Content integrity: read(write(content)) == content.
- Multiple distinct objects coexist without collision.
"""

import hashlib
import os
import pathlib
import secrets

import pytest

from muse.core.object_store import (
    has_object,
    object_path,
    objects_dir,
    read_object,
    restore_object,
    write_object,
    write_object_from_path,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def repo(tmp_path: pathlib.Path) -> pathlib.Path:
    (tmp_path / ".muse").mkdir()
    return tmp_path


# ---------------------------------------------------------------------------
# Basic round-trip
# ---------------------------------------------------------------------------


class TestRoundTrip:
    def test_write_then_read_small(self, repo: pathlib.Path) -> None:
        data = b"hello muse"
        oid = _sha256(data)
        write_object(repo, oid, data)
        assert read_object(repo, oid) == data

    def test_write_then_read_empty(self, repo: pathlib.Path) -> None:
        data = b""
        oid = _sha256(data)
        write_object(repo, oid, data)
        assert read_object(repo, oid) == data

    def test_write_then_read_single_byte(self, repo: pathlib.Path) -> None:
        data = b"\x00"
        oid = _sha256(data)
        write_object(repo, oid, data)
        assert read_object(repo, oid) == data

    def test_write_then_read_binary(self, repo: pathlib.Path) -> None:
        data = bytes(range(256)) * 100
        oid = _sha256(data)
        write_object(repo, oid, data)
        assert read_object(repo, oid) == data

    @pytest.mark.parametrize("size", [1, 100, 4096, 65536, 1_000_000])
    def test_write_then_read_various_sizes(self, repo: pathlib.Path, size: int) -> None:
        data = secrets.token_bytes(size)
        oid = _sha256(data)
        assert write_object(repo, oid, data) is True
        assert read_object(repo, oid) == data

    def test_content_integrity(self, repo: pathlib.Path) -> None:
        """Read back exactly what was written — not a truncated or padded version."""
        for i in range(20):
            data = f"object-content-{i}-{'x' * i}".encode()
            oid = _sha256(data)
            write_object(repo, oid, data)
            recovered = read_object(repo, oid)
            assert recovered == data
            assert len(recovered) == len(data)


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


class TestIdempotency:
    def test_double_write_returns_false_second_time(self, repo: pathlib.Path) -> None:
        data = b"idempotent"
        oid = _sha256(data)
        assert write_object(repo, oid, data) is True
        assert write_object(repo, oid, data) is False

    def test_double_write_does_not_corrupt(self, repo: pathlib.Path) -> None:
        data = b"original content"
        oid = _sha256(data)
        write_object(repo, oid, data)
        # Writing different content with the same ID raises ValueError (integrity check).
        # The object on disk is NOT overwritten — idempotency guard fires first.
        with pytest.raises(ValueError, match="Content integrity failure"):
            write_object(repo, oid, b"different content")
        assert read_object(repo, oid) == data

    def test_triple_write_stays_stable(self, repo: pathlib.Path) -> None:
        data = b"triple-write"
        oid = _sha256(data)
        for _ in range(3):
            write_object(repo, oid, data)
        assert read_object(repo, oid) == data


# ---------------------------------------------------------------------------
# has_object
# ---------------------------------------------------------------------------


class TestHasObject:
    def test_absent_before_write(self, repo: pathlib.Path) -> None:
        oid = _sha256(b"not yet written")
        assert not has_object(repo, oid)

    def test_present_after_write(self, repo: pathlib.Path) -> None:
        data = b"present"
        oid = _sha256(data)
        write_object(repo, oid, data)
        assert has_object(repo, oid)

    def test_other_objects_dont_shadow(self, repo: pathlib.Path) -> None:
        a = b"object-a"
        b_ = b"object-b"
        oid_a = _sha256(a)
        oid_b = _sha256(b_)
        write_object(repo, oid_a, a)
        assert has_object(repo, oid_a)
        assert not has_object(repo, oid_b)
        write_object(repo, oid_b, b_)
        assert has_object(repo, oid_b)


# ---------------------------------------------------------------------------
# Absent objects
# ---------------------------------------------------------------------------


class TestAbsentObjects:
    def test_read_absent_returns_none(self, repo: pathlib.Path) -> None:
        fake_oid = "a" * 64
        assert read_object(repo, fake_oid) is None

    def test_restore_absent_returns_false(self, repo: pathlib.Path, tmp_path: pathlib.Path) -> None:
        fake_oid = "b" * 64
        dest = tmp_path / "restored.bin"
        result = restore_object(repo, fake_oid, dest)
        assert result is False
        assert not dest.exists()

    def test_has_object_false_for_random_id(self, repo: pathlib.Path) -> None:
        for _ in range(10):
            assert not has_object(repo, secrets.token_hex(32))


# ---------------------------------------------------------------------------
# Sharding layout
# ---------------------------------------------------------------------------


class TestSharding:
    def test_object_path_uses_first_two_chars_as_dir(self, repo: pathlib.Path) -> None:
        oid = "ab" + "c" * 62
        path = object_path(repo, oid)
        assert path.parent.name == "ab"
        assert path.name == "c" * 62

    def test_objects_with_same_prefix_go_to_same_shard(self, repo: pathlib.Path) -> None:
        oid1 = "ff" + "0" * 62
        oid2 = "ff" + "1" * 62
        assert object_path(repo, oid1).parent == object_path(repo, oid2).parent

    def test_objects_with_different_prefix_go_to_different_shards(self, repo: pathlib.Path) -> None:
        # Use valid 64-char hex IDs with different first-two-char prefixes.
        oid1 = "aa" + "f" * 62
        oid2 = "bb" + "f" * 62
        assert object_path(repo, oid1).parent != object_path(repo, oid2).parent

    def test_256_shards_can_all_be_created(self, repo: pathlib.Path) -> None:
        """Write one object per possible shard prefix (00-ff).

        Finds data whose SHA-256 starts with each 2-hex prefix by brute-force,
        using a counter to stay deterministic.
        """
        import itertools
        written_prefixes: set[str] = set()
        for n in itertools.count():
            if len(written_prefixes) == 256:
                break
            data = f"shard-seed-{n}".encode()
            oid = _sha256(data)
            prefix = oid[:2]
            if prefix not in written_prefixes:
                write_object(repo, oid, data)
                written_prefixes.add(prefix)
        # Verify all 256 shard dirs exist.
        shards = [d.name for d in objects_dir(repo).iterdir() if d.is_dir()]
        assert len(shards) == 256


# ---------------------------------------------------------------------------
# write_object_from_path
# ---------------------------------------------------------------------------


class TestWriteObjectFromPath:
    def test_from_path_round_trip(self, repo: pathlib.Path, tmp_path: pathlib.Path) -> None:
        src = tmp_path / "source.bin"
        data = b"from-path-content"
        src.write_bytes(data)
        oid = _sha256(data)
        assert write_object_from_path(repo, oid, src) is True
        assert read_object(repo, oid) == data

    def test_from_path_idempotent(self, repo: pathlib.Path, tmp_path: pathlib.Path) -> None:
        src = tmp_path / "idem.bin"
        data = b"idempotent-from-path"
        src.write_bytes(data)
        oid = _sha256(data)
        write_object_from_path(repo, oid, src)
        assert write_object_from_path(repo, oid, src) is False


# ---------------------------------------------------------------------------
# restore_object
# ---------------------------------------------------------------------------


class TestRestoreObject:
    def test_restore_round_trip(self, repo: pathlib.Path, tmp_path: pathlib.Path) -> None:
        data = b"restore-me"
        oid = _sha256(data)
        write_object(repo, oid, data)
        dest = tmp_path / "sub" / "restored.bin"
        assert restore_object(repo, oid, dest) is True
        assert dest.read_bytes() == data

    def test_restore_creates_parent_dirs(self, repo: pathlib.Path, tmp_path: pathlib.Path) -> None:
        data = b"deep-restore"
        oid = _sha256(data)
        write_object(repo, oid, data)
        dest = tmp_path / "a" / "b" / "c" / "file.bin"
        restore_object(repo, oid, dest)
        assert dest.exists()

    def test_restore_large_object_intact(self, repo: pathlib.Path, tmp_path: pathlib.Path) -> None:
        data = secrets.token_bytes(2_000_000)
        oid = _sha256(data)
        write_object(repo, oid, data)
        dest = tmp_path / "large.bin"
        restore_object(repo, oid, dest)
        assert dest.read_bytes() == data


# ---------------------------------------------------------------------------
# Multiple distinct objects
# ---------------------------------------------------------------------------


class TestMultipleObjects:
    def test_100_distinct_objects_coexist(self, repo: pathlib.Path) -> None:
        written: dict[str, bytes] = {}
        for i in range(100):
            data = f"payload-{i:03d}-{'z' * i}".encode()
            oid = _sha256(data)
            write_object(repo, oid, data)
            written[oid] = data

        for oid, data in written.items():
            assert read_object(repo, oid) == data

    def test_all_objects_independently_addressable(self, repo: pathlib.Path) -> None:
        """Verify no two distinct objects collide in the store."""
        oids: list[str] = []
        for i in range(50):
            data = secrets.token_bytes(64)
            oid = _sha256(data)
            write_object(repo, oid, data)
            oids.append(oid)
        # All OIDs should be unique (probabilistic but essentially certain).
        assert len(set(oids)) == 50

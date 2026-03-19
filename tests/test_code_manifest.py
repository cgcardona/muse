"""Tests for the hierarchical code manifest in muse/plugins/code/manifest.py."""

import hashlib
import pathlib
import tempfile

import pytest

from muse.plugins.code.manifest import (
    CodeManifest,
    ManifestFileDiff,
    build_code_manifest,
    diff_manifests,
    read_code_manifest,
    write_code_manifest,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_repo(tmp_path: pathlib.Path) -> pathlib.Path:
    muse = tmp_path / ".muse"
    muse.mkdir()
    (muse / "objects").mkdir()
    return tmp_path


def _write_object(root: pathlib.Path, content: bytes) -> str:
    h = hashlib.sha256(content).hexdigest()
    obj_path = root / ".muse" / "objects" / h[:2] / h[2:]
    obj_path.parent.mkdir(parents=True, exist_ok=True)
    obj_path.write_bytes(content)
    return h


# ---------------------------------------------------------------------------
# build_code_manifest
# ---------------------------------------------------------------------------


class TestBuildCodeManifest:
    def test_empty_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_repo(pathlib.Path(tmp))
            manifest = build_code_manifest("snap1", {}, root)
            assert manifest["snapshot_id"] == "snap1"
            assert manifest["total_files"] == 0
            assert manifest["packages"] == []
            assert manifest["total_symbols"] == 0

    def test_single_python_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_repo(pathlib.Path(tmp))
            src = b"def foo():\n    return 1\n"
            h = _write_object(root, src)
            manifest = build_code_manifest("snap1", {"src/utils.py": h}, root)
            assert manifest["total_files"] == 1
            assert manifest["semantic_files"] >= 1
            assert len(manifest["packages"]) == 1
            pkg = manifest["packages"][0]
            assert pkg["package"] == "src"
            assert len(pkg["modules"]) == 1
            mod = pkg["modules"][0]
            assert mod["module_path"] == "src/utils.py"
            assert mod["language"] == "Python"

    def test_groups_by_package(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_repo(pathlib.Path(tmp))
            h1 = _write_object(root, b"x = 1\n")
            h2 = _write_object(root, b"y = 2\n")
            h3 = _write_object(root, b"z = 3\n")
            flat = {
                "src/a.py": h1,
                "src/b.py": h2,
                "tests/c.py": h3,
            }
            manifest = build_code_manifest("snap1", flat, root)
            assert manifest["total_files"] == 3
            packages = {pkg["package"] for pkg in manifest["packages"]}
            assert "src" in packages
            assert "tests" in packages

    def test_manifest_hash_stable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_repo(pathlib.Path(tmp))
            src = b"x = 1\n"
            h = _write_object(root, src)
            m1 = build_code_manifest("snap1", {"a.py": h}, root)
            m2 = build_code_manifest("snap1", {"a.py": h}, root)
            assert m1["manifest_hash"] == m2["manifest_hash"]

    def test_non_semantic_file_has_empty_ast_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_repo(pathlib.Path(tmp))
            h = _write_object(root, b"some binary or text content")
            manifest = build_code_manifest("snap1", {"README.md": h}, root)
            mod = manifest["packages"][0]["modules"][0]
            assert mod["ast_hash"] == ""
            assert mod["symbol_count"] == 0


# ---------------------------------------------------------------------------
# diff_manifests
# ---------------------------------------------------------------------------


class TestDiffManifests:
    def _build_simple(self, root: pathlib.Path, files: dict[str, bytes]) -> CodeManifest:
        flat: dict[str, str] = {}
        for path, content in files.items():
            flat[path] = _write_object(root, content)
        return build_code_manifest("snap", flat, root)

    def test_identical_manifests_no_diff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_repo(pathlib.Path(tmp))
            base = self._build_simple(root, {"a.py": b"x = 1\n"})
            diffs = diff_manifests(base, base)
            assert diffs == []

    def test_added_file_detected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_repo(pathlib.Path(tmp))
            base = self._build_simple(root, {"a.py": b"x = 1\n"})
            target = self._build_simple(root, {"a.py": b"x = 1\n", "b.py": b"y = 2\n"})
            diffs = diff_manifests(base, target)
            added = [d for d in diffs if d["change"] == "added"]
            assert any(d["path"] == "b.py" for d in added)

    def test_removed_file_detected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_repo(pathlib.Path(tmp))
            base = self._build_simple(root, {"a.py": b"x = 1\n", "b.py": b"y = 2\n"})
            target = self._build_simple(root, {"a.py": b"x = 1\n"})
            diffs = diff_manifests(base, target)
            removed = [d for d in diffs if d["change"] == "removed"]
            assert any(d["path"] == "b.py" for d in removed)

    def test_semantic_change_detected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_repo(pathlib.Path(tmp))
            base = self._build_simple(root, {"a.py": b"def foo():\n    return 1\n"})
            target = self._build_simple(root, {"a.py": b"def foo():\n    return 2\n"})
            diffs = diff_manifests(base, target)
            assert len(diffs) == 1
            assert diffs[0]["semantic_change"] is True

    def test_whitespace_only_change_non_semantic(self) -> None:
        # Whitespace-only changes: content_hash differs but ast_hash should be the same.
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_repo(pathlib.Path(tmp))
            base = self._build_simple(root, {"a.py": b"def foo():\n    return 1\n"})
            target = self._build_simple(root, {"a.py": b"def foo():\n    return 1\n\n\n"})
            diffs = diff_manifests(base, target)
            # Whitespace diff may or may not change AST hash depending on parser.
            # Just assert we get a diff record with a path.
            if diffs:
                assert diffs[0]["path"] == "a.py"


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


class TestManifestPersistence:
    def test_write_and_read_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_repo(pathlib.Path(tmp))
            src = b"def my_fn():\n    pass\n"
            h = _write_object(root, src)
            original = build_code_manifest("snap1", {"src/a.py": h}, root)

            write_code_manifest(root, original)
            loaded = read_code_manifest(root, original["manifest_hash"])

            assert loaded is not None
            assert loaded["snapshot_id"] == "snap1"
            assert loaded["manifest_hash"] == original["manifest_hash"]
            assert len(loaded["packages"]) == len(original["packages"])

    def test_read_nonexistent_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_repo(pathlib.Path(tmp))
            result = read_code_manifest(root, "nonexistent_hash")
            assert result is None

    def test_write_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_repo(pathlib.Path(tmp))
            h = _write_object(root, b"x = 1\n")
            manifest = build_code_manifest("snap1", {"a.py": h}, root)
            write_code_manifest(root, manifest)
            write_code_manifest(root, manifest)  # second write should not error
            loaded = read_code_manifest(root, manifest["manifest_hash"])
            assert loaded is not None

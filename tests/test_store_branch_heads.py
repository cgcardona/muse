"""Unit tests for get_all_branch_heads — store utility added with local transport."""

from __future__ import annotations

import pathlib

from muse.core.store import get_all_branch_heads


def _heads_dir(root: pathlib.Path) -> pathlib.Path:
    d = root / ".muse" / "refs" / "heads"
    d.mkdir(parents=True, exist_ok=True)
    return d


class TestGetAllBranchHeads:
    def test_empty_heads_dir_returns_empty_dict(self, tmp_path: pathlib.Path) -> None:
        _heads_dir(tmp_path)
        result = get_all_branch_heads(tmp_path)
        assert result == {}

    def test_missing_heads_dir_returns_empty_dict(self, tmp_path: pathlib.Path) -> None:
        # No .muse/ directory at all.
        result = get_all_branch_heads(tmp_path)
        assert result == {}

    def test_single_branch(self, tmp_path: pathlib.Path) -> None:
        heads = _heads_dir(tmp_path)
        (heads / "main").write_text("a" * 64)
        result = get_all_branch_heads(tmp_path)
        assert result == {"main": "a" * 64}

    def test_multiple_branches(self, tmp_path: pathlib.Path) -> None:
        heads = _heads_dir(tmp_path)
        (heads / "main").write_text("a" * 64)
        (heads / "dev").write_text("b" * 64)
        (heads / "feature").write_text("c" * 64)
        result = get_all_branch_heads(tmp_path)
        assert result == {
            "main": "a" * 64,
            "dev": "b" * 64,
            "feature": "c" * 64,
        }

    def test_whitespace_trimmed_from_ref_content(self, tmp_path: pathlib.Path) -> None:
        heads = _heads_dir(tmp_path)
        (heads / "main").write_text("  " + "a" * 64 + "\n")
        result = get_all_branch_heads(tmp_path)
        assert result["main"] == "a" * 64

    def test_empty_ref_file_excluded(self, tmp_path: pathlib.Path) -> None:
        heads = _heads_dir(tmp_path)
        (heads / "main").write_text("")
        result = get_all_branch_heads(tmp_path)
        assert "main" not in result

    def test_subdirectory_in_heads_is_skipped(self, tmp_path: pathlib.Path) -> None:
        """Namespaced branches (feature/foo) are represented as subdirs — only
        files are branch heads; subdirs are namespace containers."""
        heads = _heads_dir(tmp_path)
        (heads / "main").write_text("a" * 64)
        sub = heads / "feature"
        sub.mkdir()
        (sub / "my-branch").write_text("b" * 64)
        result = get_all_branch_heads(tmp_path)
        # Only the flat file is returned (subdirs need recursive handling
        # which is not the current contract — flat heads only).
        assert "main" in result
        assert "feature" not in result  # the dir itself is not a head

    def test_returns_dict_type(self, tmp_path: pathlib.Path) -> None:
        _heads_dir(tmp_path)
        result = get_all_branch_heads(tmp_path)
        assert isinstance(result, dict)

    def test_values_are_stripped_strings(self, tmp_path: pathlib.Path) -> None:
        heads = _heads_dir(tmp_path)
        cid = "f" * 64
        (heads / "release").write_text(cid + "\n")
        result = get_all_branch_heads(tmp_path)
        assert result["release"] == cid
        assert isinstance(result["release"], str)

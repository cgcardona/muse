"""Tests for muse/core/ignore.py — .museignore parser and path filter."""
from __future__ import annotations

import pathlib

import pytest

from muse.core.ignore import _matches, is_ignored, load_patterns


# ---------------------------------------------------------------------------
# load_patterns
# ---------------------------------------------------------------------------


class TestLoadPatterns:
    def test_returns_empty_when_no_file(self, tmp_path: pathlib.Path) -> None:
        assert load_patterns(tmp_path) == []

    def test_reads_patterns(self, tmp_path: pathlib.Path) -> None:
        (tmp_path / ".museignore").write_text("*.tmp\n*.bak\n")
        assert load_patterns(tmp_path) == ["*.tmp", "*.bak"]

    def test_strips_blank_lines(self, tmp_path: pathlib.Path) -> None:
        (tmp_path / ".museignore").write_text("\n*.tmp\n\n*.bak\n\n")
        assert load_patterns(tmp_path) == ["*.tmp", "*.bak"]

    def test_strips_comments(self, tmp_path: pathlib.Path) -> None:
        (tmp_path / ".museignore").write_text(
            "# ignore backups\n*.bak\n# and temps\n*.tmp\n"
        )
        assert load_patterns(tmp_path) == ["*.bak", "*.tmp"]

    def test_strips_whitespace_around_patterns(self, tmp_path: pathlib.Path) -> None:
        (tmp_path / ".museignore").write_text("  *.tmp  \n  *.bak  \n")
        assert load_patterns(tmp_path) == ["*.tmp", "*.bak"]

    def test_inline_comment_not_stripped(self, tmp_path: pathlib.Path) -> None:
        # Only leading-# lines are comments; inline # is part of the pattern.
        (tmp_path / ".museignore").write_text("*.tmp  # not a comment\n")
        assert load_patterns(tmp_path) == ["*.tmp  # not a comment"]

    def test_negation_pattern_preserved(self, tmp_path: pathlib.Path) -> None:
        (tmp_path / ".museignore").write_text("*.bak\n!keep.bak\n")
        assert load_patterns(tmp_path) == ["*.bak", "!keep.bak"]

    def test_empty_file(self, tmp_path: pathlib.Path) -> None:
        (tmp_path / ".museignore").write_text("")
        assert load_patterns(tmp_path) == []


# ---------------------------------------------------------------------------
# _matches (internal — gitignore path semantics)
# ---------------------------------------------------------------------------


class TestMatchesInternal:
    """Verify the core matching logic in isolation."""

    # ---- Patterns without slash: match any component ----

    def test_ext_pattern_matches_top_level(self) -> None:
        import pathlib as pl
        assert _matches(pl.PurePosixPath("drums.tmp"), "*.tmp")

    def test_ext_pattern_matches_nested(self) -> None:
        import pathlib as pl
        assert _matches(pl.PurePosixPath("tracks/drums.tmp"), "*.tmp")

    def test_ext_pattern_matches_deep_nested(self) -> None:
        import pathlib as pl
        assert _matches(pl.PurePosixPath("a/b/c/drums.tmp"), "*.tmp")

    def test_ext_pattern_no_false_positive(self) -> None:
        import pathlib as pl
        assert not _matches(pl.PurePosixPath("tracks/drums.mid"), "*.tmp")

    def test_exact_name_matches_any_depth(self) -> None:
        import pathlib as pl
        assert _matches(pl.PurePosixPath("a/b/.DS_Store"), ".DS_Store")

    def test_exact_name_top_level(self) -> None:
        import pathlib as pl
        assert _matches(pl.PurePosixPath(".DS_Store"), ".DS_Store")

    # ---- Patterns with slash: match full path from right ----

    def test_dir_ext_matches_direct_child(self) -> None:
        import pathlib as pl
        assert _matches(pl.PurePosixPath("tracks/drums.bak"), "tracks/*.bak")

    def test_dir_ext_no_match_different_dir(self) -> None:
        import pathlib as pl
        assert not _matches(pl.PurePosixPath("exports/drums.bak"), "tracks/*.bak")

    def test_double_star_matches_nested(self) -> None:
        import pathlib as pl
        assert _matches(pl.PurePosixPath("a/b/cache/index.dat"), "**/cache/*.dat")

    def test_double_star_matches_shallow(self) -> None:
        import pathlib as pl
        # **/cache/*.dat should match cache/index.dat (** = zero components)
        assert _matches(pl.PurePosixPath("cache/index.dat"), "**/cache/*.dat")

    # ---- Anchored patterns (leading /) ----

    def test_anchored_matches_root_level(self) -> None:
        import pathlib as pl
        assert _matches(pl.PurePosixPath("scratch.mid"), "/scratch.mid")

    def test_anchored_no_match_nested(self) -> None:
        import pathlib as pl
        assert not _matches(pl.PurePosixPath("tracks/scratch.mid"), "/scratch.mid")

    def test_anchored_dir_pattern_no_match_file(self) -> None:
        import pathlib as pl
        # /renders/*.wav anchored to root
        assert _matches(pl.PurePosixPath("renders/mix.wav"), "/renders/*.wav")
        assert not _matches(pl.PurePosixPath("exports/renders/mix.wav"), "/renders/*.wav")


# ---------------------------------------------------------------------------
# is_ignored — full rule evaluation with negation
# ---------------------------------------------------------------------------


class TestIsIgnored:
    def test_empty_patterns_ignores_nothing(self) -> None:
        assert not is_ignored("tracks/drums.mid", [])

    def test_simple_ext_ignored(self) -> None:
        assert is_ignored("drums.tmp", ["*.tmp"])

    def test_simple_ext_nested_ignored(self) -> None:
        assert is_ignored("tracks/drums.tmp", ["*.tmp"])

    def test_non_matching_not_ignored(self) -> None:
        assert not is_ignored("drums.mid", ["*.tmp"])

    def test_directory_pattern_not_applied_to_file(self) -> None:
        # Trailing / means directory-only; must not ignore a file.
        assert not is_ignored("renders/mix.wav", ["renders/"])

    def test_negation_un_ignores(self) -> None:
        patterns = ["*.bak", "!keep.bak"]
        assert is_ignored("session.bak", patterns)
        assert not is_ignored("keep.bak", patterns)

    def test_negation_nested_un_ignores(self) -> None:
        patterns = ["*.bak", "!tracks/keeper.bak"]
        assert is_ignored("tracks/session.bak", patterns)
        assert not is_ignored("tracks/keeper.bak", patterns)

    def test_last_rule_wins(self) -> None:
        # First rule ignores, second negates, third re-ignores.
        patterns = ["*.bak", "!session.bak", "*.bak"]
        assert is_ignored("session.bak", patterns)

    def test_anchored_pattern_root_only(self) -> None:
        patterns = ["/scratch.mid"]
        assert is_ignored("scratch.mid", patterns)
        assert not is_ignored("tracks/scratch.mid", patterns)

    def test_ds_store_at_any_depth(self) -> None:
        patterns = [".DS_Store"]
        assert is_ignored(".DS_Store", patterns)
        assert is_ignored("tracks/.DS_Store", patterns)
        assert is_ignored("a/b/c/.DS_Store", patterns)

    def test_double_star_glob(self) -> None:
        # Match *.pyc at any depth using a no-slash pattern.
        assert is_ignored("__pycache__/foo.pyc", ["*.pyc"])
        assert is_ignored("tracks/__pycache__/foo.pyc", ["*.pyc"])
        # Pattern with embedded slash + ** at start.
        assert is_ignored("cache/index.dat", ["**/cache/*.dat"])
        assert is_ignored("a/b/cache/index.dat", ["**/cache/*.dat"])

    def test_multiple_patterns_first_matches(self) -> None:
        patterns = ["*.tmp", "*.bak"]
        assert is_ignored("drums.tmp", patterns)
        assert is_ignored("drums.bak", patterns)
        assert not is_ignored("drums.mid", patterns)

    def test_negation_before_rule_has_no_effect(self) -> None:
        # Negation appears before the rule it would override — last rule wins,
        # so the file ends up ignored.
        patterns = ["!session.bak", "*.bak"]
        assert is_ignored("session.bak", patterns)


# ---------------------------------------------------------------------------
# Integration: MusicPlugin.snapshot() honours .museignore
# ---------------------------------------------------------------------------


class TestMusicPluginSnapshotIgnore:
    """End-to-end: .museignore filters out paths during snapshot()."""

    def _make_repo(self, tmp_path: pathlib.Path) -> pathlib.Path:
        """Create a minimal repo structure with a muse-work/ directory."""
        workdir = tmp_path / "muse-work"
        workdir.mkdir()
        return tmp_path

    def test_snapshot_without_museignore_includes_all(
        self, tmp_path: pathlib.Path
    ) -> None:
        from muse.plugins.music.plugin import MusicPlugin

        root = self._make_repo(tmp_path)
        workdir = root / "muse-work"
        (workdir / "beat.mid").write_text("data")
        (workdir / "session.tmp").write_text("temp")

        plugin = MusicPlugin()
        snap = plugin.snapshot(workdir)
        assert "beat.mid" in snap["files"]
        assert "session.tmp" in snap["files"]

    def test_snapshot_excludes_ignored_files(self, tmp_path: pathlib.Path) -> None:
        from muse.plugins.music.plugin import MusicPlugin

        root = self._make_repo(tmp_path)
        workdir = root / "muse-work"
        (workdir / "beat.mid").write_text("data")
        (workdir / "session.tmp").write_text("temp")
        (root / ".museignore").write_text("*.tmp\n")

        plugin = MusicPlugin()
        snap = plugin.snapshot(workdir)
        assert "beat.mid" in snap["files"]
        assert "session.tmp" not in snap["files"]

    def test_snapshot_negation_keeps_file(self, tmp_path: pathlib.Path) -> None:
        from muse.plugins.music.plugin import MusicPlugin

        root = self._make_repo(tmp_path)
        workdir = root / "muse-work"
        (workdir / "session.tmp").write_text("temp")
        (workdir / "important.tmp").write_text("keep me")
        (root / ".museignore").write_text("*.tmp\n!important.tmp\n")

        plugin = MusicPlugin()
        snap = plugin.snapshot(workdir)
        assert "session.tmp" not in snap["files"]
        assert "important.tmp" in snap["files"]

    def test_snapshot_nested_pattern(self, tmp_path: pathlib.Path) -> None:
        from muse.plugins.music.plugin import MusicPlugin

        root = self._make_repo(tmp_path)
        workdir = root / "muse-work"
        renders = workdir / "renders"
        renders.mkdir()
        (workdir / "beat.mid").write_text("data")
        (renders / "preview.wav").write_text("audio")
        (root / ".museignore").write_text("renders/*.wav\n")

        plugin = MusicPlugin()
        snap = plugin.snapshot(workdir)
        assert "beat.mid" in snap["files"]
        assert "renders/preview.wav" not in snap["files"]

    def test_snapshot_dotfiles_always_excluded(self, tmp_path: pathlib.Path) -> None:
        from muse.plugins.music.plugin import MusicPlugin

        root = self._make_repo(tmp_path)
        workdir = root / "muse-work"
        (workdir / "beat.mid").write_text("data")
        (workdir / ".DS_Store").write_bytes(b"\x00" * 16)
        # No .museignore — dotfiles excluded by the built-in rule.

        plugin = MusicPlugin()
        snap = plugin.snapshot(workdir)
        assert "beat.mid" in snap["files"]
        assert ".DS_Store" not in snap["files"]

    def test_snapshot_with_empty_museignore(self, tmp_path: pathlib.Path) -> None:
        from muse.plugins.music.plugin import MusicPlugin

        root = self._make_repo(tmp_path)
        workdir = root / "muse-work"
        (workdir / "beat.mid").write_text("data")
        (root / ".museignore").write_text("# just a comment\n\n")

        plugin = MusicPlugin()
        snap = plugin.snapshot(workdir)
        assert "beat.mid" in snap["files"]

    def test_snapshot_directory_pattern_does_not_exclude_file(
        self, tmp_path: pathlib.Path
    ) -> None:
        from muse.plugins.music.plugin import MusicPlugin

        root = self._make_repo(tmp_path)
        workdir = root / "muse-work"
        renders = workdir / "renders"
        renders.mkdir()
        (renders / "mix.wav").write_text("audio")
        # Directory-only pattern — should not exclude files.
        (root / ".museignore").write_text("renders/\n")

        plugin = MusicPlugin()
        snap = plugin.snapshot(workdir)
        # The file is NOT excluded because trailing-/ patterns are directory-only.
        assert "renders/mix.wav" in snap["files"]

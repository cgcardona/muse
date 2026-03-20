"""Tests for muse/core/ignore.py — .museignore TOML parser and path filter."""

import pathlib

import pytest

from muse.core.ignore import (
    MuseIgnoreConfig,
    _matches,
    is_ignored,
    load_ignore_config,
    resolve_patterns,
)


# ---------------------------------------------------------------------------
# load_ignore_config
# ---------------------------------------------------------------------------


class TestLoadIgnoreConfig:
    def test_returns_empty_when_no_file(self, tmp_path: pathlib.Path) -> None:
        assert load_ignore_config(tmp_path) == {}

    def test_empty_toml_file(self, tmp_path: pathlib.Path) -> None:
        (tmp_path / ".museignore").write_text("")
        assert load_ignore_config(tmp_path) == {}

    def test_toml_comments_only(self, tmp_path: pathlib.Path) -> None:
        (tmp_path / ".museignore").write_text("# just a comment\n")
        assert load_ignore_config(tmp_path) == {}

    def test_global_section_parsed(self, tmp_path: pathlib.Path) -> None:
        (tmp_path / ".museignore").write_text(
            '[global]\npatterns = ["*.tmp", "*.bak"]\n'
        )
        config = load_ignore_config(tmp_path)
        assert config.get("global", {}).get("patterns") == ["*.tmp", "*.bak"]

    def test_domain_section_parsed(self, tmp_path: pathlib.Path) -> None:
        (tmp_path / ".museignore").write_text(
            '[domain.midi]\npatterns = ["*.bak"]\n'
        )
        config = load_ignore_config(tmp_path)
        domain_map = config.get("domain", {})
        assert domain_map.get("midi", {}).get("patterns") == ["*.bak"]

    def test_multiple_domain_sections_parsed(self, tmp_path: pathlib.Path) -> None:
        content = (
            '[domain.midi]\npatterns = ["*.bak"]\n'
            '[domain.code]\npatterns = ["__pycache__/"]\n'
        )
        (tmp_path / ".museignore").write_text(content)
        config = load_ignore_config(tmp_path)
        domain_map = config.get("domain", {})
        assert domain_map.get("midi", {}).get("patterns") == ["*.bak"]
        assert domain_map.get("code", {}).get("patterns") == ["__pycache__/"]

    def test_global_and_domain_sections_parsed(self, tmp_path: pathlib.Path) -> None:
        content = (
            '[global]\npatterns = ["*.tmp"]\n'
            '[domain.midi]\npatterns = ["*.bak"]\n'
        )
        (tmp_path / ".museignore").write_text(content)
        config = load_ignore_config(tmp_path)
        assert config.get("global", {}).get("patterns") == ["*.tmp"]
        domain_map = config.get("domain", {})
        assert domain_map.get("midi", {}).get("patterns") == ["*.bak"]

    def test_negation_pattern_preserved(self, tmp_path: pathlib.Path) -> None:
        (tmp_path / ".museignore").write_text(
            '[global]\npatterns = ["*.bak", "!keep.bak"]\n'
        )
        config = load_ignore_config(tmp_path)
        assert config.get("global", {}).get("patterns") == ["*.bak", "!keep.bak"]

    def test_invalid_toml_raises_value_error(self, tmp_path: pathlib.Path) -> None:
        (tmp_path / ".museignore").write_text("this is not valid toml ][")
        with pytest.raises(ValueError, match=".museignore"):
            load_ignore_config(tmp_path)

    def test_section_without_patterns_key(self, tmp_path: pathlib.Path) -> None:
        # A section with no patterns key produces an empty DomainSection.
        (tmp_path / ".museignore").write_text("[global]\n")
        config = load_ignore_config(tmp_path)
        assert config.get("global") == {}

    def test_non_string_patterns_silently_dropped(
        self, tmp_path: pathlib.Path
    ) -> None:
        # Non-string items in the patterns array are silently skipped.
        (tmp_path / ".museignore").write_text(
            '[global]\npatterns = ["*.tmp", 42, true, "*.bak"]\n'
        )
        config = load_ignore_config(tmp_path)
        assert config.get("global", {}).get("patterns") == ["*.tmp", "*.bak"]


# ---------------------------------------------------------------------------
# resolve_patterns
# ---------------------------------------------------------------------------


class TestResolvePatterns:
    def test_empty_config_returns_empty(self) -> None:
        config: MuseIgnoreConfig = {}
        assert resolve_patterns(config, "midi") == []

    def test_global_only(self) -> None:
        config: MuseIgnoreConfig = {"global": {"patterns": ["*.tmp", ".DS_Store"]}}
        assert resolve_patterns(config, "midi") == ["*.tmp", ".DS_Store"]

    def test_domain_only(self) -> None:
        config: MuseIgnoreConfig = {"domain": {"midi": {"patterns": ["*.bak"]}}}
        assert resolve_patterns(config, "midi") == ["*.bak"]

    def test_global_and_matching_domain_merged(self) -> None:
        config: MuseIgnoreConfig = {
            "global": {"patterns": ["*.tmp"]},
            "domain": {"midi": {"patterns": ["*.bak"]}},
        }
        result = resolve_patterns(config, "midi")
        # Global comes first, then domain-specific.
        assert result == ["*.tmp", "*.bak"]

    def test_other_domain_patterns_excluded(self) -> None:
        config: MuseIgnoreConfig = {
            "global": {"patterns": ["*.tmp"]},
            "domain": {
                "midi": {"patterns": ["*.bak"]},
                "code": {"patterns": ["node_modules/"]},
            },
        }
        # Asking for "midi" — code patterns must not appear.
        result = resolve_patterns(config, "midi")
        assert "*.bak" in result
        assert "node_modules/" not in result

    def test_active_domain_not_in_config_returns_global_only(self) -> None:
        config: MuseIgnoreConfig = {
            "global": {"patterns": ["*.tmp"]},
            "domain": {"midi": {"patterns": ["*.bak"]}},
        }
        # Active domain "genomics" has no section — only global patterns.
        result = resolve_patterns(config, "genomics")
        assert result == ["*.tmp"]

    def test_global_section_without_patterns_key(self) -> None:
        config: MuseIgnoreConfig = {"global": {}}
        assert resolve_patterns(config, "midi") == []

    def test_domain_section_without_patterns_key(self) -> None:
        config: MuseIgnoreConfig = {"domain": {"midi": {}}}
        assert resolve_patterns(config, "midi") == []

    def test_order_preserved(self) -> None:
        config: MuseIgnoreConfig = {
            "global": {"patterns": ["a", "b", "c"]},
            "domain": {"midi": {"patterns": ["d", "e"]}},
        }
        assert resolve_patterns(config, "midi") == ["a", "b", "c", "d", "e"]

    def test_negation_in_global_preserved(self) -> None:
        config: MuseIgnoreConfig = {
            "global": {"patterns": ["*.bak", "!keep.bak"]},
        }
        patterns = resolve_patterns(config, "midi")
        assert patterns == ["*.bak", "!keep.bak"]

    def test_negation_in_domain_overrides_global(self) -> None:
        # A negation in the domain section can un-ignore a globally ignored path.
        config: MuseIgnoreConfig = {
            "global": {"patterns": ["*.bak"]},
            "domain": {"midi": {"patterns": ["!session.bak"]}},
        }
        patterns = resolve_patterns(config, "midi")
        # session.bak is globally ignored but negated by domain section.
        assert not is_ignored("session.bak", patterns)
        # other.bak is globally ignored and not negated.
        assert is_ignored("other.bak", patterns)


# ---------------------------------------------------------------------------
# _matches (internal — gitignore path semantics, unchanged)
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
# is_ignored — full rule evaluation with negation (unchanged layer)
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
# Integration: MidiPlugin.snapshot() honours .museignore TOML format
# ---------------------------------------------------------------------------


class TestMidiPluginSnapshotIgnore:
    """End-to-end: .museignore TOML format filters paths during snapshot()."""

    def _make_repo(self, tmp_path: pathlib.Path) -> pathlib.Path:
        """Create a minimal repo structure with a state/ directory."""
        workdir = tmp_path
        return tmp_path

    def test_snapshot_without_museignore_includes_all(
        self, tmp_path: pathlib.Path
    ) -> None:
        from muse.plugins.midi.plugin import MidiPlugin

        root = self._make_repo(tmp_path)
        workdir = root
        (workdir / "beat.mid").write_text("data")
        (workdir / "session.tmp").write_text("temp")

        plugin = MidiPlugin()
        snap = plugin.snapshot(workdir)
        assert "beat.mid" in snap["files"]
        assert "session.tmp" in snap["files"]

    def test_snapshot_excludes_global_pattern(self, tmp_path: pathlib.Path) -> None:
        from muse.plugins.midi.plugin import MidiPlugin

        root = self._make_repo(tmp_path)
        workdir = root
        (workdir / "beat.mid").write_text("data")
        (workdir / "session.tmp").write_text("temp")
        (root / ".museignore").write_text('[global]\npatterns = ["*.tmp"]\n')

        plugin = MidiPlugin()
        snap = plugin.snapshot(workdir)
        assert "beat.mid" in snap["files"]
        assert "session.tmp" not in snap["files"]

    def test_snapshot_excludes_domain_specific_pattern(
        self, tmp_path: pathlib.Path
    ) -> None:
        from muse.plugins.midi.plugin import MidiPlugin

        root = self._make_repo(tmp_path)
        workdir = root
        (workdir / "beat.mid").write_text("data")
        (workdir / "session.bak").write_text("backup")
        (root / ".museignore").write_text(
            '[domain.midi]\npatterns = ["*.bak"]\n'
        )

        plugin = MidiPlugin()
        snap = plugin.snapshot(workdir)
        assert "beat.mid" in snap["files"]
        assert "session.bak" not in snap["files"]

    def test_snapshot_domain_isolation_other_domain_ignored(
        self, tmp_path: pathlib.Path
    ) -> None:
        from muse.plugins.midi.plugin import MidiPlugin

        root = self._make_repo(tmp_path)
        workdir = root
        (workdir / "beat.mid").write_text("data")
        (workdir / "requirements.txt").write_text("pytest\n")
        # code-only ignore — must NOT apply to the midi plugin.
        (root / ".museignore").write_text(
            '[domain.code]\npatterns = ["requirements.txt"]\n'
        )

        plugin = MidiPlugin()
        snap = plugin.snapshot(workdir)
        # requirements.txt should remain because the [domain.code] section
        # does not apply when the active domain is "midi".
        assert "requirements.txt" in snap["files"]
        assert "beat.mid" in snap["files"]

    def test_snapshot_negation_keeps_file(self, tmp_path: pathlib.Path) -> None:
        from muse.plugins.midi.plugin import MidiPlugin

        root = self._make_repo(tmp_path)
        workdir = root
        (workdir / "session.tmp").write_text("temp")
        (workdir / "important.tmp").write_text("keep me")
        (root / ".museignore").write_text(
            '[global]\npatterns = ["*.tmp", "!important.tmp"]\n'
        )

        plugin = MidiPlugin()
        snap = plugin.snapshot(workdir)
        assert "session.tmp" not in snap["files"]
        assert "important.tmp" in snap["files"]

    def test_snapshot_domain_negation_overrides_global(
        self, tmp_path: pathlib.Path
    ) -> None:
        from muse.plugins.midi.plugin import MidiPlugin

        root = self._make_repo(tmp_path)
        workdir = root
        (workdir / "session.bak").write_text("backup")
        content = (
            '[global]\npatterns = ["*.bak"]\n'
            '[domain.midi]\npatterns = ["!session.bak"]\n'
        )
        (root / ".museignore").write_text(content)

        plugin = MidiPlugin()
        snap = plugin.snapshot(workdir)
        # session.bak is globally ignored but un-ignored by the midi domain section.
        assert "session.bak" in snap["files"]

    def test_snapshot_nested_pattern(self, tmp_path: pathlib.Path) -> None:
        from muse.plugins.midi.plugin import MidiPlugin

        root = self._make_repo(tmp_path)
        workdir = root
        renders = workdir / "renders"
        renders.mkdir()
        (workdir / "beat.mid").write_text("data")
        (renders / "preview.wav").write_text("audio")
        (root / ".museignore").write_text(
            '[global]\npatterns = ["renders/*.wav"]\n'
        )

        plugin = MidiPlugin()
        snap = plugin.snapshot(workdir)
        assert "beat.mid" in snap["files"]
        assert "renders/preview.wav" not in snap["files"]

    def test_snapshot_dotfiles_always_excluded(self, tmp_path: pathlib.Path) -> None:
        from muse.plugins.midi.plugin import MidiPlugin

        root = self._make_repo(tmp_path)
        workdir = root
        (workdir / "beat.mid").write_text("data")
        (workdir / ".DS_Store").write_bytes(b"\x00" * 16)
        # No .museignore — dotfiles excluded by the built-in plugin rule.

        plugin = MidiPlugin()
        snap = plugin.snapshot(workdir)
        assert "beat.mid" in snap["files"]
        assert ".DS_Store" not in snap["files"]

    def test_snapshot_with_empty_museignore(self, tmp_path: pathlib.Path) -> None:
        from muse.plugins.midi.plugin import MidiPlugin

        root = self._make_repo(tmp_path)
        workdir = root
        (workdir / "beat.mid").write_text("data")
        # Valid TOML — just a comment, no sections.
        (root / ".museignore").write_text("# empty config\n")

        plugin = MidiPlugin()
        snap = plugin.snapshot(workdir)
        assert "beat.mid" in snap["files"]

    def test_snapshot_directory_pattern_does_not_exclude_file(
        self, tmp_path: pathlib.Path
    ) -> None:
        from muse.plugins.midi.plugin import MidiPlugin

        root = self._make_repo(tmp_path)
        workdir = root
        renders = workdir / "renders"
        renders.mkdir()
        (renders / "mix.wav").write_text("audio")
        # Directory-only pattern — should not exclude files.
        (root / ".museignore").write_text('[global]\npatterns = ["renders/"]\n')

        plugin = MidiPlugin()
        snap = plugin.snapshot(workdir)
        # The file is NOT excluded because trailing-/ patterns are directory-only.
        assert "renders/mix.wav" in snap["files"]

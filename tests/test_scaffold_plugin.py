"""Tests for muse/plugins/scaffold/plugin.py — snapshot .museignore integration.

Verifies that the scaffold plugin reference implementation correctly honours
the TOML .museignore file during snapshot(), including global patterns, domain-
specific patterns, negation, and cross-domain isolation.
"""

from __future__ import annotations

import pathlib

from muse.plugins.scaffold.plugin import ScaffoldPlugin


class TestScaffoldPluginSnapshot:
    """ScaffoldPlugin.snapshot() honours .museignore TOML rules."""

    plugin = ScaffoldPlugin()

    def _make_repo(self, tmp_path: pathlib.Path) -> pathlib.Path:
        workdir = tmp_path
        return tmp_path

    def test_snapshot_no_ignore_includes_all(self, tmp_path: pathlib.Path) -> None:
        root = self._make_repo(tmp_path)
        workdir = root
        (workdir / "a.scaffold").write_text("data")
        (workdir / "b.scaffold").write_text("data")

        snap = self.plugin.snapshot(workdir)
        assert "a.scaffold" in snap["files"]
        assert "b.scaffold" in snap["files"]

    def test_snapshot_global_pattern_excluded(self, tmp_path: pathlib.Path) -> None:
        root = self._make_repo(tmp_path)
        workdir = root
        (workdir / "keep.scaffold").write_text("keep")
        (workdir / "skip.scaffold").write_text("skip")
        (root / ".museignore").write_text('[global]\npatterns = ["skip.scaffold"]\n')

        snap = self.plugin.snapshot(workdir)
        assert "keep.scaffold" in snap["files"]
        assert "skip.scaffold" not in snap["files"]

    def test_snapshot_domain_specific_pattern_excluded(
        self, tmp_path: pathlib.Path
    ) -> None:
        root = self._make_repo(tmp_path)
        workdir = root
        (workdir / "keep.scaffold").write_text("keep")
        (workdir / "generated.scaffold").write_text("generated")
        (root / ".museignore").write_text(
            '[domain.scaffold]\npatterns = ["generated.scaffold"]\n'
        )

        snap = self.plugin.snapshot(workdir)
        assert "keep.scaffold" in snap["files"]
        assert "generated.scaffold" not in snap["files"]

    def test_snapshot_other_domain_pattern_not_applied(
        self, tmp_path: pathlib.Path
    ) -> None:
        root = self._make_repo(tmp_path)
        workdir = root
        (workdir / "keep.scaffold").write_text("keep")
        # This pattern belongs to the "midi" domain — must NOT affect scaffold.
        (root / ".museignore").write_text(
            '[domain.midi]\npatterns = ["keep.scaffold"]\n'
        )

        snap = self.plugin.snapshot(workdir)
        assert "keep.scaffold" in snap["files"]

    def test_snapshot_negation_un_ignores(self, tmp_path: pathlib.Path) -> None:
        root = self._make_repo(tmp_path)
        workdir = root
        (workdir / "important.scaffold").write_text("keep me")
        (workdir / "other.scaffold").write_text("discard")
        (root / ".museignore").write_text(
            '[global]\npatterns = ["*.scaffold", "!important.scaffold"]\n'
        )

        snap = self.plugin.snapshot(workdir)
        assert "important.scaffold" in snap["files"]
        assert "other.scaffold" not in snap["files"]

    def test_snapshot_domain_negation_overrides_global(
        self, tmp_path: pathlib.Path
    ) -> None:
        root = self._make_repo(tmp_path)
        workdir = root
        (workdir / "keep.scaffold").write_text("keep")
        (root / ".museignore").write_text(
            '[global]\npatterns = ["*.scaffold"]\n'
            '[domain.scaffold]\npatterns = ["!keep.scaffold"]\n'
        )

        snap = self.plugin.snapshot(workdir)
        # keep.scaffold globally ignored but un-ignored by domain section.
        assert "keep.scaffold" in snap["files"]

    def test_snapshot_empty_museignore(self, tmp_path: pathlib.Path) -> None:
        root = self._make_repo(tmp_path)
        workdir = root
        (workdir / "a.scaffold").write_text("data")
        (root / ".museignore").write_text("# empty\n")

        snap = self.plugin.snapshot(workdir)
        assert "a.scaffold" in snap["files"]

    def test_snapshot_domain_is_scaffold(self, tmp_path: pathlib.Path) -> None:
        root = self._make_repo(tmp_path)
        workdir = root
        (workdir / "a.scaffold").write_text("data")

        snap = self.plugin.snapshot(workdir)
        assert snap["domain"] == "scaffold"

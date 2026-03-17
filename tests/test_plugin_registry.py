"""Unit tests for muse.plugins.registry — resolve_plugin, read_domain, registered_domains."""
from __future__ import annotations

import json
import pathlib

import pytest

from muse.core.errors import MuseCLIError
from muse.domain import MuseDomainPlugin
from muse.plugins.music.plugin import MusicPlugin
from muse.plugins.registry import read_domain, registered_domains, resolve_plugin


def _make_repo(tmp_path: pathlib.Path, domain: str = "music") -> pathlib.Path:
    """Scaffold a minimal .muse/repo.json so registry helpers can run."""
    muse_dir = tmp_path / ".muse"
    muse_dir.mkdir()
    (muse_dir / "repo.json").write_text(
        json.dumps({"repo_id": "test-id", "schema_version": "2", "domain": domain})
    )
    return tmp_path


class TestReadDomain:
    def test_returns_stored_domain(self, tmp_path: pathlib.Path) -> None:
        root = _make_repo(tmp_path, domain="music")
        assert read_domain(root) == "music"

    def test_defaults_to_music_when_key_missing(self, tmp_path: pathlib.Path) -> None:
        muse_dir = tmp_path / ".muse"
        muse_dir.mkdir()
        (muse_dir / "repo.json").write_text(json.dumps({"repo_id": "x"}))
        assert read_domain(tmp_path) == "music"

    def test_defaults_to_music_when_repo_json_absent(self, tmp_path: pathlib.Path) -> None:
        (tmp_path / ".muse").mkdir()
        assert read_domain(tmp_path) == "music"

    def test_defaults_to_music_when_muse_dir_absent(self, tmp_path: pathlib.Path) -> None:
        assert read_domain(tmp_path) == "music"


class TestResolvePlugin:
    def test_returns_music_plugin_for_music_domain(self, tmp_path: pathlib.Path) -> None:
        root = _make_repo(tmp_path, domain="music")
        plugin = resolve_plugin(root)
        assert isinstance(plugin, MusicPlugin)

    def test_returned_plugin_satisfies_protocol(self, tmp_path: pathlib.Path) -> None:
        root = _make_repo(tmp_path, domain="music")
        plugin = resolve_plugin(root)
        assert isinstance(plugin, MuseDomainPlugin)

    def test_raises_for_unknown_domain(self, tmp_path: pathlib.Path) -> None:
        root = _make_repo(tmp_path, domain="unknown-domain")
        with pytest.raises(MuseCLIError, match="unknown-domain"):
            resolve_plugin(root)

    def test_raises_error_mentions_registered_domains(self, tmp_path: pathlib.Path) -> None:
        root = _make_repo(tmp_path, domain="bogus")
        with pytest.raises(MuseCLIError, match="music"):
            resolve_plugin(root)

    def test_defaults_to_music_plugin_when_no_domain_key(self, tmp_path: pathlib.Path) -> None:
        muse_dir = tmp_path / ".muse"
        muse_dir.mkdir()
        (muse_dir / "repo.json").write_text(json.dumps({"repo_id": "x"}))
        plugin = resolve_plugin(tmp_path)
        assert isinstance(plugin, MusicPlugin)


class TestRegisteredDomains:
    def test_includes_music(self) -> None:
        assert "music" in registered_domains()

    def test_returns_sorted_list(self) -> None:
        domains = registered_domains()
        assert domains == sorted(domains)

    def test_returns_list_of_strings(self) -> None:
        domains = registered_domains()
        assert all(isinstance(d, str) for d in domains)

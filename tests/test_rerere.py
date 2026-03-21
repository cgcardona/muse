"""Tests for muse rerere — Reuse Recorded Resolution.

Covers:
- Core engine: fingerprinting, preimage recording, resolution save/load,
  apply_cached, auto_apply, record_resolutions, list_records, forget, clear, gc
- CLI: muse rerere, record, status, forget, clear, gc subcommands
- domain.py: RererePlugin optional protocol
- Integration: merge auto-apply + commit recording
"""

from __future__ import annotations

import datetime
import hashlib
import json
import pathlib

import pytest
from typer.testing import CliRunner

from muse.cli.app import cli
from muse.core import rerere as rerere_mod
from muse.core.rerere import (
    RerereRecord,
    auto_apply,
    clear_all,
    conflict_fingerprint,
    compute_fingerprint,
    forget_record,
    gc_stale,
    has_resolution,
    list_records,
    load_record,
    record_preimage,
    record_resolutions,
    rr_cache_dir,
    save_resolution,
)
from muse.core.object_store import write_object
from muse.core.schema import DomainSchema
from muse.domain import (
    DriftReport,
    LiveState,
    MergeResult,
    MuseDomainPlugin,
    RererePlugin,
    SnapshotManifest,
    StateSnapshot,
    StateDelta,
    StructuredDelta,
)

runner = CliRunner()


# ---------------------------------------------------------------------------
# Stub plugin implementations (fully typed, no Any, no Protocol inheritance)
# ---------------------------------------------------------------------------


class _StubPlugin:
    """Minimal domain plugin stub — satisfies MuseDomainPlugin structurally."""

    def snapshot(self, live_state: LiveState) -> StateSnapshot:
        raise NotImplementedError

    def diff(
        self,
        base: StateSnapshot,
        target: StateSnapshot,
        *,
        repo_root: pathlib.Path | None = None,
    ) -> StateDelta:
        raise NotImplementedError

    def merge(
        self,
        base: StateSnapshot,
        left: StateSnapshot,
        right: StateSnapshot,
        *,
        repo_root: pathlib.Path | None = None,
    ) -> MergeResult:
        raise NotImplementedError

    def drift(self, committed: StateSnapshot, live: LiveState) -> DriftReport:
        raise NotImplementedError

    def apply(self, delta: StateDelta, live_state: LiveState) -> LiveState:
        raise NotImplementedError

    def schema(self) -> DomainSchema:
        raise NotImplementedError


class _CustomFPPlugin(_StubPlugin):
    """Plugin that overrides conflict_fingerprint with a fixed custom value."""

    def __init__(self, fp: str) -> None:
        self._fp = fp

    def conflict_fingerprint(
        self,
        path: str,
        ours_id: str,
        theirs_id: str,
        repo_root: pathlib.Path,
    ) -> str:
        return self._fp


class _ErrorFPPlugin(_StubPlugin):
    """Plugin whose conflict_fingerprint always raises."""

    def conflict_fingerprint(
        self,
        path: str,
        ours_id: str,
        theirs_id: str,
        repo_root: pathlib.Path,
    ) -> str:
        raise RuntimeError("plugin error")


class _ShortFPPlugin(_StubPlugin):
    """Plugin whose conflict_fingerprint returns an invalid (short) fingerprint."""

    def conflict_fingerprint(
        self,
        path: str,
        ours_id: str,
        theirs_id: str,
        repo_root: pathlib.Path,
    ) -> str:
        return "too_short"


class _RerereEnabledPlugin(_StubPlugin):
    """Full RererePlugin-compatible stub with a deterministic fingerprint."""

    def conflict_fingerprint(
        self,
        path: str,
        ours_id: str,
        theirs_id: str,
        repo_root: pathlib.Path,
    ) -> str:
        return "e" * 64


def _make_plugin() -> MuseDomainPlugin:
    return _StubPlugin()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


@pytest.fixture()
def repo(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    """Initialise a minimal Muse repository in tmp_path and set it as cwd."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MUSE_REPO_ROOT", str(tmp_path))
    result = runner.invoke(cli, ["init", "--domain", "midi"])
    assert result.exit_code == 0, result.output
    return tmp_path


@pytest.fixture()
def ours_id(repo: pathlib.Path) -> str:
    content = b"ours content for conflict"
    oid = _sha(content)
    write_object(repo, oid, content)
    return oid


@pytest.fixture()
def theirs_id(repo: pathlib.Path) -> str:
    content = b"theirs content for conflict"
    oid = _sha(content)
    write_object(repo, oid, content)
    return oid


@pytest.fixture()
def resolution_id(repo: pathlib.Path) -> str:
    content = b"resolved content after conflict"
    oid = _sha(content)
    write_object(repo, oid, content)
    return oid


# ---------------------------------------------------------------------------
# Unit tests: fingerprinting
# ---------------------------------------------------------------------------


class TestConflictFingerprint:
    def test_deterministic(self) -> None:
        a = "a" * 64
        b = "b" * 64
        assert conflict_fingerprint(a, b) == conflict_fingerprint(a, b)

    def test_commutative(self) -> None:
        """Order of ours/theirs must not affect the fingerprint."""
        a = "a" * 64
        b = "b" * 64
        assert conflict_fingerprint(a, b) == conflict_fingerprint(b, a)

    def test_different_inputs_produce_different_fingerprints(self) -> None:
        a = "a" * 64
        b = "b" * 64
        c = "c" * 64
        assert conflict_fingerprint(a, b) != conflict_fingerprint(a, c)

    def test_identical_sides_produces_valid_fingerprint(self) -> None:
        a = "a" * 64
        fp = conflict_fingerprint(a, a)
        assert len(fp) == 64
        int(fp, 16)  # raises ValueError if not valid hex

    def test_output_is_64_char_hex(self) -> None:
        fp = conflict_fingerprint("a" * 64, "b" * 64)
        assert len(fp) == 64
        int(fp, 16)


class TestComputeFingerprint:
    def test_falls_back_to_default_for_plain_plugin(
        self, repo: pathlib.Path, ours_id: str, theirs_id: str
    ) -> None:
        plugin = _make_plugin()
        fp = compute_fingerprint("track.mid", ours_id, theirs_id, plugin, repo)
        assert fp == conflict_fingerprint(ours_id, theirs_id)

    def test_uses_plugin_fingerprint_when_conflict_fingerprint_present(
        self, repo: pathlib.Path, ours_id: str, theirs_id: str
    ) -> None:
        custom_fp = "d" * 64
        plugin = _CustomFPPlugin(custom_fp)
        fp = compute_fingerprint("track.mid", ours_id, theirs_id, plugin, repo)
        assert fp == custom_fp

    def test_falls_back_on_plugin_exception(
        self, repo: pathlib.Path, ours_id: str, theirs_id: str
    ) -> None:
        plugin = _ErrorFPPlugin()
        fp = compute_fingerprint("track.mid", ours_id, theirs_id, plugin, repo)
        assert fp == conflict_fingerprint(ours_id, theirs_id)

    def test_falls_back_on_invalid_short_fingerprint(
        self, repo: pathlib.Path, ours_id: str, theirs_id: str
    ) -> None:
        plugin = _ShortFPPlugin()
        fp = compute_fingerprint("track.mid", ours_id, theirs_id, plugin, repo)
        assert fp == conflict_fingerprint(ours_id, theirs_id)


# ---------------------------------------------------------------------------
# Unit tests: record_preimage / save_resolution / load_record
# ---------------------------------------------------------------------------


class TestPreimageLifecycle:
    def test_record_preimage_creates_meta_file(
        self, repo: pathlib.Path, ours_id: str, theirs_id: str
    ) -> None:
        plugin = _make_plugin()
        fp = record_preimage(repo, "beat.mid", ours_id, theirs_id, "midi", plugin)
        meta_p = repo / ".muse" / "rr-cache" / fp / "meta.json"
        assert meta_p.exists()
        data = json.loads(meta_p.read_text(encoding="utf-8"))
        assert data["path"] == "beat.mid"
        assert data["ours_id"] == ours_id
        assert data["theirs_id"] == theirs_id
        assert data["domain"] == "midi"

    def test_record_preimage_is_idempotent(
        self, repo: pathlib.Path, ours_id: str, theirs_id: str
    ) -> None:
        plugin = _make_plugin()
        fp1 = record_preimage(repo, "beat.mid", ours_id, theirs_id, "midi", plugin)
        fp2 = record_preimage(repo, "beat.mid", ours_id, theirs_id, "midi", plugin)
        assert fp1 == fp2

    def test_load_record_returns_none_for_missing_fingerprint(
        self, repo: pathlib.Path
    ) -> None:
        assert load_record(repo, "a" * 64) is None

    def test_load_record_no_resolution(
        self, repo: pathlib.Path, ours_id: str, theirs_id: str
    ) -> None:
        plugin = _make_plugin()
        fp = record_preimage(repo, "bass.mid", ours_id, theirs_id, "midi", plugin)
        rec = load_record(repo, fp)
        assert rec is not None
        assert rec.path == "bass.mid"
        assert rec.ours_id == ours_id
        assert rec.theirs_id == theirs_id
        assert rec.has_resolution is False
        assert rec.resolution_id is None

    def test_save_resolution_persists_and_loads(
        self,
        repo: pathlib.Path,
        ours_id: str,
        theirs_id: str,
        resolution_id: str,
    ) -> None:
        plugin = _make_plugin()
        fp = record_preimage(repo, "lead.mid", ours_id, theirs_id, "midi", plugin)
        save_resolution(repo, fp, resolution_id)
        rec = load_record(repo, fp)
        assert rec is not None
        assert rec.has_resolution is True
        assert rec.resolution_id == resolution_id

    def test_save_resolution_raises_without_preimage(
        self, repo: pathlib.Path, resolution_id: str
    ) -> None:
        with pytest.raises(FileNotFoundError):
            save_resolution(repo, "b" * 64, resolution_id)

    def test_save_resolution_rejects_invalid_id(
        self, repo: pathlib.Path, ours_id: str, theirs_id: str
    ) -> None:
        plugin = _make_plugin()
        fp = record_preimage(repo, "x.mid", ours_id, theirs_id, "midi", plugin)
        with pytest.raises(ValueError):
            save_resolution(repo, fp, "not-a-valid-id")

    def test_has_resolution_false_before_save(
        self, repo: pathlib.Path, ours_id: str, theirs_id: str
    ) -> None:
        plugin = _make_plugin()
        fp = record_preimage(repo, "y.mid", ours_id, theirs_id, "midi", plugin)
        assert has_resolution(repo, fp) is False

    def test_has_resolution_true_after_save(
        self,
        repo: pathlib.Path,
        ours_id: str,
        theirs_id: str,
        resolution_id: str,
    ) -> None:
        plugin = _make_plugin()
        fp = record_preimage(repo, "z.mid", ours_id, theirs_id, "midi", plugin)
        save_resolution(repo, fp, resolution_id)
        assert has_resolution(repo, fp) is True


# ---------------------------------------------------------------------------
# Unit tests: apply_cached
# ---------------------------------------------------------------------------


class TestApplyCached:
    def test_apply_restores_file_to_working_tree(
        self,
        repo: pathlib.Path,
        ours_id: str,
        theirs_id: str,
        resolution_id: str,
    ) -> None:
        from muse.core.rerere import apply_cached

        plugin = _make_plugin()
        fp = record_preimage(repo, "track.mid", ours_id, theirs_id, "midi", plugin)
        save_resolution(repo, fp, resolution_id)

        dest = repo / "track.mid"
        result = apply_cached(repo, fp, dest)

        assert result is True
        assert dest.exists()
        assert dest.read_bytes() == b"resolved content after conflict"

    def test_apply_returns_false_when_no_resolution(
        self, repo: pathlib.Path, ours_id: str, theirs_id: str
    ) -> None:
        from muse.core.rerere import apply_cached

        plugin = _make_plugin()
        fp = record_preimage(repo, "no_res.mid", ours_id, theirs_id, "midi", plugin)
        dest = repo / "no_res.mid"
        result = apply_cached(repo, fp, dest)
        assert result is False
        assert not dest.exists()

    def test_apply_returns_false_when_blob_missing_from_store(
        self, repo: pathlib.Path, ours_id: str, theirs_id: str
    ) -> None:
        from muse.core.rerere import _resolution_path, _write_atomic, apply_cached

        plugin = _make_plugin()
        fp = record_preimage(repo, "missing_blob.mid", ours_id, theirs_id, "midi", plugin)
        # Write a resolution ID that is not in the store.
        ghost_id = "f" * 64
        res_p = _resolution_path(repo, fp)
        _write_atomic(res_p, ghost_id)

        dest = repo / "missing_blob.mid"
        result = apply_cached(repo, fp, dest)
        assert result is False


# ---------------------------------------------------------------------------
# Unit tests: list_records / forget_record / clear_all / gc_stale
# ---------------------------------------------------------------------------


class TestBulkOperations:
    def test_list_records_empty_when_no_cache(self, repo: pathlib.Path) -> None:
        assert list_records(repo) == []

    def test_list_records_returns_all_entries(
        self, repo: pathlib.Path, ours_id: str, theirs_id: str
    ) -> None:
        plugin = _make_plugin()
        record_preimage(repo, "a.mid", ours_id, theirs_id, "midi", plugin)

        other_ours = _sha(b"other ours")
        other_theirs = _sha(b"other theirs")
        write_object(repo, other_ours, b"other ours")
        write_object(repo, other_theirs, b"other theirs")
        record_preimage(repo, "b.mid", other_ours, other_theirs, "midi", plugin)

        records = list_records(repo)
        assert len(records) == 2
        paths = {r.path for r in records}
        assert paths == {"a.mid", "b.mid"}

    def test_list_records_sorted_most_recent_first(
        self, repo: pathlib.Path
    ) -> None:
        plugin = _make_plugin()
        ids: list[str] = []
        for i in range(3):
            content = f"content {i}".encode()
            oid = _sha(content)
            write_object(repo, oid, content)
            ids.append(oid)

        record_preimage(repo, "first.mid", ids[0], ids[1], "midi", plugin)
        record_preimage(repo, "second.mid", ids[1], ids[2], "midi", plugin)

        records = list_records(repo)
        assert len(records) == 2
        assert records[0].recorded_at >= records[1].recorded_at

    def test_forget_record_removes_entry(
        self, repo: pathlib.Path, ours_id: str, theirs_id: str
    ) -> None:
        plugin = _make_plugin()
        fp = record_preimage(repo, "forget.mid", ours_id, theirs_id, "midi", plugin)
        assert forget_record(repo, fp) is True
        assert load_record(repo, fp) is None

    def test_forget_record_returns_false_for_missing(self, repo: pathlib.Path) -> None:
        assert forget_record(repo, "c" * 64) is False

    def test_clear_all_removes_all_entries(
        self, repo: pathlib.Path, ours_id: str, theirs_id: str
    ) -> None:
        plugin = _make_plugin()
        # Use genuinely distinct pairs so the commutative fingerprint produces
        # two separate cache entries.
        alt_ours = _sha(b"alt ours clear")
        alt_theirs = _sha(b"alt theirs clear")
        write_object(repo, alt_ours, b"alt ours clear")
        write_object(repo, alt_theirs, b"alt theirs clear")

        record_preimage(repo, "x.mid", ours_id, theirs_id, "midi", plugin)
        record_preimage(repo, "y.mid", alt_ours, alt_theirs, "midi", plugin)
        removed = clear_all(repo)
        assert removed == 2
        assert list_records(repo) == []

    def test_clear_all_on_empty_cache_returns_zero(self, repo: pathlib.Path) -> None:
        assert clear_all(repo) == 0

    def test_gc_removes_stale_preimage_only_entries(
        self, repo: pathlib.Path, ours_id: str, theirs_id: str, resolution_id: str
    ) -> None:
        plugin = _make_plugin()

        # Entry with resolution — keep regardless of age.
        fp_with_res = record_preimage(repo, "keep.mid", ours_id, theirs_id, "midi", plugin)
        save_resolution(repo, fp_with_res, resolution_id)

        # Young preimage-only — keep.
        young_ours = _sha(b"young ours")
        young_theirs = _sha(b"young theirs")
        write_object(repo, young_ours, b"young ours")
        write_object(repo, young_theirs, b"young theirs")
        record_preimage(repo, "young.mid", young_ours, young_theirs, "midi", plugin)

        # Stale preimage-only — should be removed.
        stale_ours = _sha(b"stale ours")
        stale_theirs = _sha(b"stale theirs")
        write_object(repo, stale_ours, b"stale ours")
        write_object(repo, stale_theirs, b"stale theirs")
        fp_stale = record_preimage(repo, "stale.mid", stale_ours, stale_theirs, "midi", plugin)

        meta_p = repo / ".muse" / "rr-cache" / fp_stale / "meta.json"
        data = json.loads(meta_p.read_text(encoding="utf-8"))
        old_ts = (
            datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=61)
        ).isoformat()
        data["recorded_at"] = old_ts
        meta_p.write_text(json.dumps(data), encoding="utf-8")

        removed = gc_stale(repo)
        assert removed == 1
        assert load_record(repo, fp_stale) is None
        assert load_record(repo, fp_with_res) is not None


# ---------------------------------------------------------------------------
# Unit tests: auto_apply
# ---------------------------------------------------------------------------


class TestAutoApply:
    def test_auto_apply_resolves_cached_conflicts(
        self,
        repo: pathlib.Path,
        ours_id: str,
        theirs_id: str,
        resolution_id: str,
    ) -> None:
        plugin = _make_plugin()
        fp = record_preimage(repo, "auto.mid", ours_id, theirs_id, "midi", plugin)
        save_resolution(repo, fp, resolution_id)

        ours_manifest = {"auto.mid": ours_id}
        theirs_manifest = {"auto.mid": theirs_id}

        resolved, remaining = auto_apply(
            repo, ["auto.mid"], ours_manifest, theirs_manifest, "midi", plugin
        )

        assert "auto.mid" in resolved
        assert resolved["auto.mid"] == resolution_id
        assert remaining == []
        assert (repo / "auto.mid").exists()

    def test_auto_apply_records_preimage_for_unresolved(
        self,
        repo: pathlib.Path,
        ours_id: str,
        theirs_id: str,
    ) -> None:
        plugin = _make_plugin()
        ours_manifest = {"new.mid": ours_id}
        theirs_manifest = {"new.mid": theirs_id}

        resolved, remaining = auto_apply(
            repo, ["new.mid"], ours_manifest, theirs_manifest, "midi", plugin
        )

        assert resolved == {}
        assert remaining == ["new.mid"]
        fp = conflict_fingerprint(ours_id, theirs_id)
        assert load_record(repo, fp) is not None

    def test_auto_apply_skips_deletion_conflicts(
        self, repo: pathlib.Path, ours_id: str
    ) -> None:
        plugin = _make_plugin()
        ours_manifest = {"deleted.mid": ours_id}
        theirs_manifest: dict[str, str] = {}

        resolved, remaining = auto_apply(
            repo, ["deleted.mid"], ours_manifest, theirs_manifest, "midi", plugin
        )
        assert resolved == {}
        assert "deleted.mid" in remaining

    def test_auto_apply_mixed_cached_and_uncached(
        self,
        repo: pathlib.Path,
        ours_id: str,
        theirs_id: str,
        resolution_id: str,
    ) -> None:
        plugin = _make_plugin()
        fp_a = record_preimage(repo, "a.mid", ours_id, theirs_id, "midi", plugin)
        save_resolution(repo, fp_a, resolution_id)

        b_ours = _sha(b"b ours")
        b_theirs = _sha(b"b theirs")
        write_object(repo, b_ours, b"b ours")
        write_object(repo, b_theirs, b"b theirs")

        ours_manifest = {"a.mid": ours_id, "b.mid": b_ours}
        theirs_manifest = {"a.mid": theirs_id, "b.mid": b_theirs}

        resolved, remaining = auto_apply(
            repo,
            ["a.mid", "b.mid"],
            ours_manifest,
            theirs_manifest,
            "midi",
            plugin,
        )

        assert "a.mid" in resolved
        assert "b.mid" in remaining


# ---------------------------------------------------------------------------
# Unit tests: record_resolutions
# ---------------------------------------------------------------------------


class TestRecordResolutions:
    def test_records_user_resolution_after_commit(
        self,
        repo: pathlib.Path,
        ours_id: str,
        theirs_id: str,
        resolution_id: str,
    ) -> None:
        plugin = _make_plugin()
        fp = record_preimage(repo, "resolved.mid", ours_id, theirs_id, "midi", plugin)

        new_manifest = {"resolved.mid": resolution_id}
        saved = record_resolutions(
            repo,
            ["resolved.mid"],
            {"resolved.mid": ours_id},
            {"resolved.mid": theirs_id},
            new_manifest,
            "midi",
            plugin,
        )

        assert "resolved.mid" in saved
        rec = load_record(repo, fp)
        assert rec is not None
        assert rec.resolution_id == resolution_id

    def test_skips_paths_not_in_new_manifest(
        self, repo: pathlib.Path, ours_id: str, theirs_id: str
    ) -> None:
        plugin = _make_plugin()
        saved = record_resolutions(
            repo,
            ["deleted.mid"],
            {"deleted.mid": ours_id},
            {"deleted.mid": theirs_id},
            {},
            "midi",
            plugin,
        )
        assert saved == []

    def test_creates_preimage_if_missing(
        self, repo: pathlib.Path, ours_id: str, theirs_id: str, resolution_id: str
    ) -> None:
        plugin = _make_plugin()
        new_manifest = {"late.mid": resolution_id}
        saved = record_resolutions(
            repo,
            ["late.mid"],
            {"late.mid": ours_id},
            {"late.mid": theirs_id},
            new_manifest,
            "midi",
            plugin,
        )
        assert "late.mid" in saved
        fp = conflict_fingerprint(ours_id, theirs_id)
        rec = load_record(repo, fp)
        assert rec is not None
        assert rec.resolution_id == resolution_id


# ---------------------------------------------------------------------------
# CLI tests: muse rerere (default — apply)
# ---------------------------------------------------------------------------


class TestRerereCliApply:
    def test_no_merge_in_progress(self, repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["rerere"], env={"HOME": str(repo)})
        assert result.exit_code == 0
        assert "nothing" in result.output.lower() or "No merge" in result.output

    def test_unknown_format_errors(self, repo: pathlib.Path) -> None:
        result = runner.invoke(
            cli, ["rerere", "--format", "xml"], env={"HOME": str(repo)}
        )
        assert result.exit_code != 0

    def test_json_format_with_no_merge(self, repo: pathlib.Path) -> None:
        result = runner.invoke(
            cli, ["rerere", "--format", "json"], env={"HOME": str(repo)}
        )
        assert result.exit_code == 0


class TestRerereCliRecord:
    def test_record_no_merge_in_progress(self, repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["rerere", "record"], env={"HOME": str(repo)})
        assert result.exit_code == 0
        assert "nothing" in result.output.lower() or "No merge" in result.output

    def test_record_unknown_format(self, repo: pathlib.Path) -> None:
        result = runner.invoke(
            cli, ["rerere", "record", "--format", "toml"], env={"HOME": str(repo)}
        )
        assert result.exit_code != 0


class TestRerereCliStatus:
    def test_status_empty_cache(self, repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["rerere", "status"], env={"HOME": str(repo)})
        assert result.exit_code == 0
        assert "No rerere records" in result.output

    def test_status_shows_records(
        self, repo: pathlib.Path, ours_id: str, theirs_id: str, resolution_id: str
    ) -> None:
        plugin = _make_plugin()
        fp = record_preimage(repo, "status.mid", ours_id, theirs_id, "midi", plugin)
        save_resolution(repo, fp, resolution_id)

        result = runner.invoke(cli, ["rerere", "status"], env={"HOME": str(repo)})
        assert result.exit_code == 0
        assert fp[:12] in result.output
        assert "status.mid" in result.output

    def test_status_json_format(
        self, repo: pathlib.Path, ours_id: str, theirs_id: str
    ) -> None:
        plugin = _make_plugin()
        record_preimage(repo, "json_status.mid", ours_id, theirs_id, "midi", plugin)

        result = runner.invoke(
            cli, ["rerere", "status", "--format", "json"], env={"HOME": str(repo)}
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["total"] == 1
        assert data["records"][0]["path"] == "json_status.mid"
        assert data["records"][0]["has_resolution"] is False


class TestRerereCliClear:
    def test_clear_empty(self, repo: pathlib.Path) -> None:
        result = runner.invoke(
            cli, ["rerere", "clear", "--yes"], env={"HOME": str(repo)}
        )
        assert result.exit_code == 0

    def test_clear_removes_records(
        self, repo: pathlib.Path, ours_id: str, theirs_id: str
    ) -> None:
        plugin = _make_plugin()
        record_preimage(repo, "clear.mid", ours_id, theirs_id, "midi", plugin)

        result = runner.invoke(
            cli, ["rerere", "clear", "--yes"], env={"HOME": str(repo)}
        )
        assert result.exit_code == 0
        assert list_records(repo) == []

    def test_clear_json_format(
        self, repo: pathlib.Path, ours_id: str, theirs_id: str
    ) -> None:
        plugin = _make_plugin()
        record_preimage(repo, "gc_test.mid", ours_id, theirs_id, "midi", plugin)

        result = runner.invoke(
            cli,
            ["rerere", "clear", "--yes", "--format", "json"],
            env={"HOME": str(repo)},
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["removed"] == 1


class TestRerereCliGC:
    def test_gc_nothing_to_remove(self, repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["rerere", "gc"], env={"HOME": str(repo)})
        assert result.exit_code == 0
        assert "nothing" in result.output.lower()

    def test_gc_json_format(self, repo: pathlib.Path) -> None:
        result = runner.invoke(
            cli, ["rerere", "gc", "--format", "json"], env={"HOME": str(repo)}
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "removed" in data


# ---------------------------------------------------------------------------
# Domain protocol: RererePlugin
# ---------------------------------------------------------------------------


class TestRererePluginProtocol:
    def test_isinstance_detection_with_all_methods(self) -> None:
        """RererePlugin is @runtime_checkable — isinstance must detect it structurally."""
        plugin = _RerereEnabledPlugin()
        assert isinstance(plugin, RererePlugin)

    def test_plain_plugin_is_not_rerere_plugin(self) -> None:
        plugin = _StubPlugin()
        assert not isinstance(plugin, RererePlugin)

    def test_custom_fp_plugin_isinstance(self) -> None:
        plugin = _CustomFPPlugin("f" * 64)
        assert isinstance(plugin, RererePlugin)


# ---------------------------------------------------------------------------
# Integration: rr-cache directory layout
# ---------------------------------------------------------------------------


class TestRRCacheLayout:
    def test_rr_cache_dir_path(self, repo: pathlib.Path) -> None:
        cache = rr_cache_dir(repo)
        assert cache == repo / ".muse" / "rr-cache"

    def test_preimage_creates_correct_directory_tree(
        self, repo: pathlib.Path, ours_id: str, theirs_id: str
    ) -> None:
        plugin = _make_plugin()
        fp = record_preimage(repo, "layout.mid", ours_id, theirs_id, "midi", plugin)
        entry_dir = repo / ".muse" / "rr-cache" / fp
        assert entry_dir.is_dir()
        assert (entry_dir / "meta.json").is_file()
        assert not (entry_dir / "resolution").exists()

    def test_resolution_file_contains_valid_hex_id(
        self,
        repo: pathlib.Path,
        ours_id: str,
        theirs_id: str,
        resolution_id: str,
    ) -> None:
        plugin = _make_plugin()
        fp = record_preimage(repo, "hex.mid", ours_id, theirs_id, "midi", plugin)
        save_resolution(repo, fp, resolution_id)
        res_p = repo / ".muse" / "rr-cache" / fp / "resolution"
        content = res_p.read_text(encoding="utf-8").strip()
        assert content == resolution_id
        assert len(content) == 64
        int(content, 16)

    def test_meta_json_is_valid_utf8(
        self, repo: pathlib.Path, ours_id: str, theirs_id: str
    ) -> None:
        plugin = _make_plugin()
        fp = record_preimage(repo, "utf8.mid", ours_id, theirs_id, "midi", plugin)
        meta_p = repo / ".muse" / "rr-cache" / fp / "meta.json"
        data = json.loads(meta_p.read_bytes().decode("utf-8"))
        assert isinstance(data, dict)

    def test_write_is_atomic(
        self, repo: pathlib.Path, ours_id: str, theirs_id: str
    ) -> None:
        """Atomic write must not leave temp files after success."""
        plugin = _make_plugin()
        record_preimage(repo, "atomic.mid", ours_id, theirs_id, "midi", plugin)
        fp = conflict_fingerprint(ours_id, theirs_id)
        entry_dir = repo / ".muse" / "rr-cache" / fp
        temp_files = list(entry_dir.glob(".rr-tmp-*"))
        assert temp_files == [], f"Unexpected temp files: {temp_files}"

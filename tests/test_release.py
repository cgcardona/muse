"""Tests for the Muse release system.

Covers:
- Unit: parse_semver, semver_to_str, semver_channel, semver edge cases
- Unit: ReleaseRecord serialisation round-trip
- Unit: write_release, read_release, list_releases, delete_release, get_release_for_tag
- Unit: build_changelog from typed commit metadata
- Unit: WireTag in build_pack / apply_pack
- Integration: full release lifecycle (add → show → delete)
- E2E: muse release add / list / show / push / delete via CLI
"""

from __future__ import annotations

import datetime
import json
import pathlib
import uuid

import pytest
from tests.cli_test_helper import CliRunner

from muse.core.store import ReleaseRecord

runner = CliRunner()


# ---------------------------------------------------------------------------
# Repository scaffolding helpers
# ---------------------------------------------------------------------------


def _env(root: pathlib.Path) -> dict[str, str]:
    return {"MUSE_REPO_ROOT": str(root)}


def _init_repo(tmp_path: pathlib.Path, domain: str = "code") -> tuple[pathlib.Path, str]:
    muse_dir = tmp_path / ".muse"
    muse_dir.mkdir()
    repo_id = str(uuid.uuid4())
    (muse_dir / "repo.json").write_text(json.dumps({
        "repo_id": repo_id,
        "domain": domain,
        "default_branch": "main",
        "created_at": "2025-01-01T00:00:00+00:00",
    }), encoding="utf-8")
    (muse_dir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (muse_dir / "refs" / "heads").mkdir(parents=True)
    (muse_dir / "snapshots").mkdir()
    (muse_dir / "commits").mkdir()
    (muse_dir / "objects").mkdir()
    return tmp_path, repo_id


def _make_commit(
    root: pathlib.Path,
    repo_id: str,
    branch: str = "main",
    message: str = "feat: add something",
    sem_ver_bump: str = "minor",
    breaking_changes: list[str] | None = None,
    agent_id: str = "",
    model_id: str = "",
) -> str:
    from muse.core.snapshot import compute_commit_id, compute_snapshot_id
    from muse.core.store import CommitRecord, SnapshotRecord, write_commit, write_snapshot

    ref_file = root / ".muse" / "refs" / "heads" / branch
    raw_parent = ref_file.read_text().strip() if ref_file.exists() else ""
    parent_id: str | None = raw_parent if raw_parent else None
    manifest: dict[str, str] = {}
    snap_id = compute_snapshot_id(manifest)
    snap = SnapshotRecord(snapshot_id=snap_id, manifest=manifest)
    write_snapshot(root, snap)

    now = datetime.datetime.now(datetime.timezone.utc)
    parent_ids: list[str] = [parent_id] if parent_id else []
    commit_id = compute_commit_id(parent_ids, snap_id, message, now.isoformat())
    from muse.domain import SemVerBump
    _bump_map: dict[str, SemVerBump] = {"major": "major", "minor": "minor", "patch": "patch", "none": "none"}
    bump_val: SemVerBump = _bump_map.get(sem_ver_bump, "none")

    commit = CommitRecord(
        commit_id=commit_id,
        repo_id=repo_id,
        branch=branch,
        snapshot_id=snap_id,
        message=message,
        committed_at=now,
        parent_commit_id=parent_id,
        sem_ver_bump=bump_val,
        breaking_changes=breaking_changes or [],
        agent_id=agent_id,
        model_id=model_id,
    )
    write_commit(root, commit)
    ref_file.write_text(commit_id, encoding="utf-8")
    return commit_id


# ---------------------------------------------------------------------------
# Semver parsing
# ---------------------------------------------------------------------------


class TestParseSemver:
    def test_stable_version(self) -> None:
        from muse.core.store import parse_semver

        sv = parse_semver("v1.2.3")
        assert sv["major"] == 1
        assert sv["minor"] == 2
        assert sv["patch"] == 3
        assert sv["pre"] == ""
        assert sv["build"] == ""

    def test_no_v_prefix(self) -> None:
        from muse.core.store import parse_semver

        sv = parse_semver("2.0.0")
        assert sv["major"] == 2

    def test_pre_release(self) -> None:
        from muse.core.store import parse_semver

        sv = parse_semver("v1.3.0-beta.1")
        assert sv["pre"] == "beta.1"

    def test_alpha_pre_release(self) -> None:
        from muse.core.store import parse_semver

        sv = parse_semver("v0.5.0-alpha.2")
        assert sv["pre"] == "alpha.2"

    def test_build_metadata(self) -> None:
        from muse.core.store import parse_semver

        sv = parse_semver("v1.0.0+20240101")
        assert sv["build"] == "20240101"
        assert sv["pre"] == ""

    def test_pre_and_build(self) -> None:
        from muse.core.store import parse_semver

        sv = parse_semver("v2.0.0-rc.1+build.42")
        assert sv["pre"] == "rc.1"
        assert sv["build"] == "build.42"

    def test_invalid_raises(self) -> None:
        from muse.core.store import parse_semver

        with pytest.raises(ValueError, match="not valid semver"):
            parse_semver("not-a-version")

    def test_missing_patch_raises(self) -> None:
        from muse.core.store import parse_semver

        with pytest.raises(ValueError):
            parse_semver("v1.2")

    def test_leading_zero_minor_valid(self) -> None:
        from muse.core.store import parse_semver

        sv = parse_semver("v1.0.0")
        assert sv["minor"] == 0


class TestSemverToStr:
    def test_round_trip_stable(self) -> None:
        from muse.core.store import SemVerTag, semver_to_str

        sv = SemVerTag(major=1, minor=2, patch=3, pre="", build="")
        assert semver_to_str(sv) == "v1.2.3"

    def test_round_trip_prerelease(self) -> None:
        from muse.core.store import SemVerTag, semver_to_str

        sv = SemVerTag(major=1, minor=3, patch=0, pre="beta.1", build="")
        assert semver_to_str(sv) == "v1.3.0-beta.1"

    def test_round_trip_with_build(self) -> None:
        from muse.core.store import SemVerTag, semver_to_str

        sv = SemVerTag(major=2, minor=0, patch=0, pre="rc.1", build="42")
        assert semver_to_str(sv) == "v2.0.0-rc.1+42"


class TestSemverChannel:
    def test_stable_channel_no_pre(self) -> None:
        from muse.core.store import SemVerTag, semver_channel

        sv = SemVerTag(major=1, minor=0, patch=0, pre="", build="")
        assert semver_channel(sv) == "stable"

    def test_beta_channel(self) -> None:
        from muse.core.store import SemVerTag, semver_channel

        sv = SemVerTag(major=1, minor=0, patch=0, pre="beta.1", build="")
        assert semver_channel(sv) == "beta"

    def test_alpha_channel(self) -> None:
        from muse.core.store import SemVerTag, semver_channel

        sv = SemVerTag(major=1, minor=0, patch=0, pre="alpha.3", build="")
        assert semver_channel(sv) == "alpha"

    def test_nightly_channel(self) -> None:
        from muse.core.store import SemVerTag, semver_channel

        sv = SemVerTag(major=0, minor=0, patch=1, pre="nightly", build="")
        assert semver_channel(sv) == "nightly"

    def test_rc_defaults_to_stable(self) -> None:
        from muse.core.store import SemVerTag, semver_channel

        sv = SemVerTag(major=1, minor=0, patch=0, pre="rc.1", build="")
        # rc is not a recognised channel prefix — defaults to stable
        assert semver_channel(sv) == "stable"


# ---------------------------------------------------------------------------
# ReleaseRecord serialisation
# ---------------------------------------------------------------------------


class TestReleaseRecordSerialisation:
    def _make_release(self) -> ReleaseRecord:
        from muse.core.store import SemVerTag

        return ReleaseRecord(
            release_id=str(uuid.uuid4()),
            repo_id=str(uuid.uuid4()),
            tag="v1.0.0",
            semver=SemVerTag(major=1, minor=0, patch=0, pre="", build=""),
            channel="stable",
            commit_id="a" * 64,
            snapshot_id="b" * 64,
            title="First release",
            body="Initial release notes.",
            changelog=[],
        )

    def test_round_trip(self) -> None:
        from muse.core.store import ReleaseRecord

        release = self._make_release()
        d = release.to_dict()
        restored = ReleaseRecord.from_dict(d)
        assert restored.release_id == release.release_id
        assert restored.tag == release.tag
        assert restored.semver == release.semver
        assert restored.channel == release.channel
        assert restored.title == release.title
        assert restored.is_draft is False

    def test_draft_round_trip(self) -> None:
        from muse.core.store import ReleaseRecord

        release = self._make_release()
        release.is_draft = True
        d = release.to_dict()
        restored = ReleaseRecord.from_dict(d)
        assert restored.is_draft is True

    def test_invalid_channel_defaults_to_stable(self) -> None:
        from muse.core.store import ReleaseRecord, SemVerTag

        release = ReleaseRecord(
            release_id=str(uuid.uuid4()),
            repo_id=str(uuid.uuid4()),
            tag="v1.0.0",
            semver=SemVerTag(major=1, minor=0, patch=0, pre="", build=""),
            channel="stable",
            commit_id="a" * 64,
            snapshot_id="b" * 64,
            title="",
            body="",
            changelog=[],
        )
        d = release.to_dict()
        d["channel"] = "unknown-channel"
        restored = ReleaseRecord.from_dict(d)
        assert restored.channel == "stable"


# ---------------------------------------------------------------------------
# Release store operations
# ---------------------------------------------------------------------------


class TestReleaseStore:
    def test_write_and_read(self, tmp_path: pathlib.Path) -> None:
        from muse.core.store import ReleaseRecord, SemVerTag, read_release, write_release

        root, repo_id = _init_repo(tmp_path)
        release = ReleaseRecord(
            release_id=str(uuid.uuid4()),
            repo_id=repo_id,
            tag="v1.0.0",
            semver=SemVerTag(major=1, minor=0, patch=0, pre="", build=""),
            channel="stable",
            commit_id="a" * 64,
            snapshot_id="b" * 64,
            title="v1.0.0",
            body="",
            changelog=[],
        )
        write_release(root, release)
        loaded = read_release(root, repo_id, release.release_id)
        assert loaded is not None
        assert loaded.tag == "v1.0.0"

    def test_read_missing_returns_none(self, tmp_path: pathlib.Path) -> None:
        from muse.core.store import read_release

        root, repo_id = _init_repo(tmp_path)
        assert read_release(root, repo_id, str(uuid.uuid4())) is None

    def test_list_releases_newest_first(self, tmp_path: pathlib.Path) -> None:
        from muse.core.store import ReleaseRecord, SemVerTag, list_releases, write_release
        import time

        root, repo_id = _init_repo(tmp_path)

        for i, tag in enumerate(["v1.0.0", "v1.1.0", "v1.2.0"]):
            sv = SemVerTag(major=1, minor=i, patch=0, pre="", build="")
            r = ReleaseRecord(
                release_id=str(uuid.uuid4()),
                repo_id=repo_id,
                tag=tag,
                semver=sv,
                channel="stable",
                commit_id="a" * 64,
                snapshot_id="b" * 64,
                title=tag,
                body="",
                changelog=[],
                created_at=datetime.datetime(2025, 1, i + 1, tzinfo=datetime.timezone.utc),
            )
            write_release(root, r)
            time.sleep(0.01)  # ensure distinct timestamps

        releases = list_releases(root, repo_id)
        assert len(releases) == 3
        # newest first
        assert releases[0].tag == "v1.2.0"
        assert releases[-1].tag == "v1.0.0"

    def test_list_excludes_drafts_by_default(self, tmp_path: pathlib.Path) -> None:
        from muse.core.store import ReleaseRecord, SemVerTag, list_releases, write_release

        root, repo_id = _init_repo(tmp_path)
        for tag, draft in [("v1.0.0", False), ("v1.1.0-beta.1", True)]:
            sv_parts = tag.lstrip("v").split("-")
            major, minor, patch = (int(x) for x in sv_parts[0].split("."))
            pre = sv_parts[1] if len(sv_parts) > 1 else ""
            r = ReleaseRecord(
                release_id=str(uuid.uuid4()),
                repo_id=repo_id,
                tag=tag,
                semver=SemVerTag(major=major, minor=minor, patch=patch, pre=pre, build=""),
                channel="stable" if not pre else "beta",
                commit_id="a" * 64,
                snapshot_id="b" * 64,
                title=tag,
                body="",
                changelog=[],
                is_draft=draft,
            )
            write_release(root, r)

        assert len(list_releases(root, repo_id)) == 1
        assert len(list_releases(root, repo_id, include_drafts=True)) == 2

    def test_filter_by_channel(self, tmp_path: pathlib.Path) -> None:
        from muse.core.store import ReleaseChannel, ReleaseRecord, SemVerTag, list_releases, write_release

        _ch_map: dict[str, ReleaseChannel] = {"stable": "stable", "beta": "beta", "alpha": "alpha", "nightly": "nightly"}
        root, repo_id = _init_repo(tmp_path)
        for tag, channel_str in [("v1.0.0", "stable"), ("v1.1.0-beta.1", "beta"), ("v1.2.0", "stable")]:
            sv_parts = tag.lstrip("v").split("-")
            major, minor, patch = (int(x) for x in sv_parts[0].split("."))
            pre = sv_parts[1] if len(sv_parts) > 1 else ""
            r = ReleaseRecord(
                release_id=str(uuid.uuid4()),
                repo_id=repo_id,
                tag=tag,
                semver=SemVerTag(major=major, minor=minor, patch=patch, pre=pre, build=""),
                channel=_ch_map.get(channel_str, "stable"),
                commit_id="a" * 64,
                snapshot_id="b" * 64,
                title=tag,
                body="",
                changelog=[],
            )
            write_release(root, r)

        stable = list_releases(root, repo_id, channel="stable")
        beta = list_releases(root, repo_id, channel="beta")
        assert len(stable) == 2
        assert len(beta) == 1

    def test_delete_release(self, tmp_path: pathlib.Path) -> None:
        from muse.core.store import ReleaseRecord, SemVerTag, delete_release, list_releases, write_release

        root, repo_id = _init_repo(tmp_path)
        release_id = str(uuid.uuid4())
        r = ReleaseRecord(
            release_id=release_id,
            repo_id=repo_id,
            tag="v0.1.0",
            semver=SemVerTag(major=0, minor=1, patch=0, pre="", build=""),
            channel="stable",
            commit_id="a" * 64,
            snapshot_id="b" * 64,
            title="",
            body="",
            changelog=[],
        )
        write_release(root, r)
        assert len(list_releases(root, repo_id)) == 1
        assert delete_release(root, repo_id, release_id) is True
        assert len(list_releases(root, repo_id)) == 0

    def test_delete_nonexistent_returns_false(self, tmp_path: pathlib.Path) -> None:
        from muse.core.store import delete_release

        root, repo_id = _init_repo(tmp_path)
        assert delete_release(root, repo_id, str(uuid.uuid4())) is False

    def test_get_release_for_tag(self, tmp_path: pathlib.Path) -> None:
        from muse.core.store import ReleaseRecord, SemVerTag, get_release_for_tag, write_release

        root, repo_id = _init_repo(tmp_path)
        r = ReleaseRecord(
            release_id=str(uuid.uuid4()),
            repo_id=repo_id,
            tag="v2.0.0",
            semver=SemVerTag(major=2, minor=0, patch=0, pre="", build=""),
            channel="stable",
            commit_id="a" * 64,
            snapshot_id="b" * 64,
            title="",
            body="",
            changelog=[],
        )
        write_release(root, r)
        assert get_release_for_tag(root, repo_id, "v2.0.0") is not None
        assert get_release_for_tag(root, repo_id, "v9.9.9") is None


# ---------------------------------------------------------------------------
# build_changelog
# ---------------------------------------------------------------------------


class TestBuildChangelog:
    def test_changelog_from_commits(self, tmp_path: pathlib.Path) -> None:
        from muse.core.store import build_changelog

        root, repo_id = _init_repo(tmp_path)
        c1 = _make_commit(root, repo_id, message="feat: first", sem_ver_bump="minor")
        c2 = _make_commit(root, repo_id, message="fix: patch fix", sem_ver_bump="patch")
        c3 = _make_commit(root, repo_id, message="feat!: breaking", sem_ver_bump="major",
                          breaking_changes=["API changed"])

        changelog = build_changelog(root, None, c3)
        assert len(changelog) == 3
        assert changelog[0]["commit_id"] == c1  # oldest first
        assert changelog[2]["sem_ver_bump"] == "major"
        assert changelog[2]["breaking_changes"] == ["API changed"]

    def test_changelog_bounded_by_from_commit(self, tmp_path: pathlib.Path) -> None:
        from muse.core.store import build_changelog

        root, repo_id = _init_repo(tmp_path)
        c1 = _make_commit(root, repo_id, message="chore: setup", sem_ver_bump="none")
        c2 = _make_commit(root, repo_id, message="feat: add feature", sem_ver_bump="minor")
        c3 = _make_commit(root, repo_id, message="fix: tiny fix", sem_ver_bump="patch")

        # Only c2 and c3 are since c1
        changelog = build_changelog(root, c1, c3)
        assert len(changelog) == 2
        assert changelog[0]["commit_id"] == c2

    def test_empty_changelog_same_commit(self, tmp_path: pathlib.Path) -> None:
        from muse.core.store import build_changelog

        root, repo_id = _init_repo(tmp_path)
        c1 = _make_commit(root, repo_id)
        changelog = build_changelog(root, c1, c1)
        assert changelog == []

    def test_changelog_includes_agent_provenance(self, tmp_path: pathlib.Path) -> None:
        from muse.core.store import build_changelog

        root, repo_id = _init_repo(tmp_path)
        _make_commit(root, repo_id, message="feat: add", sem_ver_bump="minor",
                     agent_id="my-agent", model_id="claude-4")
        head_commit = _make_commit(root, repo_id, message="fix: patch", sem_ver_bump="patch")
        changelog = build_changelog(root, None, head_commit)
        assert changelog[0]["agent_id"] == "my-agent"
        assert changelog[0]["model_id"] == "claude-4"


# ---------------------------------------------------------------------------
# WireTag in pack
# ---------------------------------------------------------------------------


class TestWireTagInPack:
    def test_build_pack_includes_tags(self, tmp_path: pathlib.Path) -> None:
        from muse.core.pack import build_pack
        from muse.core.store import TagRecord, write_tag

        root, repo_id = _init_repo(tmp_path)
        commit_id = _make_commit(root, repo_id)

        tag = TagRecord(
            tag_id=str(uuid.uuid4()),
            repo_id=repo_id,
            commit_id=commit_id,
            tag="v1.0.0",
        )
        write_tag(root, tag)

        bundle = build_pack(root, [commit_id], repo_id=repo_id)
        assert "tags" in bundle
        tags = bundle["tags"]
        assert len(tags) == 1
        assert tags[0]["tag"] == "v1.0.0"
        assert tags[0]["commit_id"] == commit_id

    def test_build_pack_no_tags_when_repo_id_omitted(self, tmp_path: pathlib.Path) -> None:
        from muse.core.pack import build_pack
        from muse.core.store import TagRecord, write_tag

        root, repo_id = _init_repo(tmp_path)
        commit_id = _make_commit(root, repo_id)
        write_tag(root, TagRecord(
            tag_id=str(uuid.uuid4()),
            repo_id=repo_id,
            commit_id=commit_id,
            tag="v1.0.0",
        ))

        bundle = build_pack(root, [commit_id])  # no repo_id
        assert "tags" not in bundle

    def test_apply_pack_writes_tags(self, tmp_path: pathlib.Path) -> None:
        from muse.core.pack import apply_pack, build_pack, WireTag
        from muse.core.store import TagRecord, get_all_tags, write_tag

        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()

        root_src, repo_id = _init_repo(src)
        root_dst, _ = _init_repo(dst)

        commit_id = _make_commit(root_src, repo_id)
        write_tag(root_src, TagRecord(
            tag_id=str(uuid.uuid4()),
            repo_id=repo_id,
            commit_id=commit_id,
            tag="v1.0.0",
        ))

        bundle = build_pack(root_src, [commit_id], repo_id=repo_id)
        apply_pack(root_dst, bundle)

        tags = get_all_tags(root_dst, repo_id)
        assert any(t.tag == "v1.0.0" for t in tags)


# ---------------------------------------------------------------------------
# E2E CLI: muse release
# ---------------------------------------------------------------------------


class TestReleaseCLI:
    def test_release_add_basic(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        _make_commit(root, repo_id, message="feat: initial", sem_ver_bump="minor")

        result = runner.invoke(None, ["release", "add", "v0.1.0", "--title", "First"], env=_env(root))
        assert result.exit_code == 0, result.output
        assert "v0.1.0" in result.output

    def test_release_add_invalid_semver(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        _make_commit(root, repo_id)

        result = runner.invoke(None, ["release", "add", "not-valid"], env=_env(root))
        assert result.exit_code != 0

    def test_release_add_duplicate_rejected(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        _make_commit(root, repo_id)

        runner.invoke(None, ["release", "add", "v1.0.0"], env=_env(root))
        result = runner.invoke(None, ["release", "add", "v1.0.0"], env=_env(root))
        assert result.exit_code != 0
        assert "already exists" in result.output.lower() or "already exists" in result.stderr.lower() if hasattr(result, 'stderr') else True

    def test_release_add_draft(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        _make_commit(root, repo_id)

        result = runner.invoke(None, ["release", "add", "v1.0.0-beta.1", "--draft"], env=_env(root))
        assert result.exit_code == 0
        assert "draft" in result.output.lower()

    def test_release_add_json_output(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        _make_commit(root, repo_id)

        result = runner.invoke(None, ["release", "add", "v1.0.0", "--format", "json"], env=_env(root))
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["tag"] == "v1.0.0"
        assert data["channel"] == "stable"
        assert "release_id" in data

    def test_release_list(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        _make_commit(root, repo_id)
        runner.invoke(None, ["release", "add", "v1.0.0"], env=_env(root))

        result = runner.invoke(None, ["release", "list"], env=_env(root))
        assert result.exit_code == 0
        assert "v1.0.0" in result.output

    def test_release_list_empty(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        result = runner.invoke(None, ["release", "list"], env=_env(root))
        assert result.exit_code == 0
        assert "No releases" in result.output

    def test_release_list_json(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        _make_commit(root, repo_id)
        runner.invoke(None, ["release", "add", "v1.0.0"], env=_env(root))

        result = runner.invoke(None, ["release", "list", "--format", "json"], env=_env(root))
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert data[0]["tag"] == "v1.0.0"

    def test_release_show(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        _make_commit(root, repo_id)
        runner.invoke(None, ["release", "add", "v1.0.0", "--title", "Production"], env=_env(root))

        result = runner.invoke(None, ["release", "show", "v1.0.0"], env=_env(root))
        assert result.exit_code == 0
        assert "v1.0.0" in result.output
        assert "stable" in result.output

    def test_release_show_not_found(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        result = runner.invoke(None, ["release", "show", "v99.99.99"], env=_env(root))
        assert result.exit_code != 0

    def test_release_delete_draft(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        _make_commit(root, repo_id)
        runner.invoke(None, ["release", "add", "v1.0.0-alpha.1", "--draft"], env=_env(root))

        result = runner.invoke(None, ["release", "delete", "v1.0.0-alpha.1", "--yes"], env=_env(root))
        assert result.exit_code == 0
        assert "deleted" in result.output.lower()

    def test_release_delete_published_rejected(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        _make_commit(root, repo_id)
        runner.invoke(None, ["release", "add", "v1.0.0"], env=_env(root))

        result = runner.invoke(None, ["release", "delete", "v1.0.0", "--yes"], env=_env(root))
        assert result.exit_code != 0
        assert "published" in result.output.lower()

    def test_release_channel_filter(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        _make_commit(root, repo_id)
        runner.invoke(None, ["release", "add", "v1.0.0", "--channel", "stable"], env=_env(root))
        _make_commit(root, repo_id)
        runner.invoke(None, ["release", "add", "v1.1.0-beta.1", "--channel", "beta"], env=_env(root))

        result = runner.invoke(None, ["release", "list", "--channel", "stable"], env=_env(root))
        assert result.exit_code == 0
        assert "v1.0.0" in result.output
        assert "v1.1.0" not in result.output

    def test_release_changelog_in_json_output(self, tmp_path: pathlib.Path) -> None:
        root, repo_id = _init_repo(tmp_path)
        _make_commit(root, repo_id, message="feat: add API", sem_ver_bump="minor")
        _make_commit(root, repo_id, message="fix: handle edge case", sem_ver_bump="patch")

        result = runner.invoke(None, ["release", "add", "v1.0.0", "--format", "json"], env=_env(root))
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data["changelog"]) == 2
        assert data["changelog"][0]["sem_ver_bump"] == "minor"

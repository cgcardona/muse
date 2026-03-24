"""Tests for muse.plugins.code.release_analysis.compute_release_analysis."""

from __future__ import annotations

import pathlib
import uuid
from datetime import datetime, timezone

import pytest

from muse.core.store import (
    ChangelogEntry,
    ReleaseRecord,
    SemanticReleaseReport,
    SemVerTag,
    write_release,
)
from muse.plugins.code.release_analysis import _empty_report, compute_release_analysis


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def repo(tmp_path: pathlib.Path) -> pathlib.Path:
    """Minimal repo layout: .muse/commits/, snapshots/, objects/, releases/."""
    muse = tmp_path / ".muse"
    for sub in ("commits", "snapshots", "objects", "releases", "refs", "refs/heads"):
        (muse / sub).mkdir(parents=True, exist_ok=True)
    (muse / "HEAD").write_text("ref: refs/heads/main\n")
    repo_id = str(uuid.uuid4())
    import json
    (muse / "repo.json").write_text(json.dumps({"repo_id": repo_id}))
    return tmp_path


def _make_release(repo_root: pathlib.Path, tag: str = "v1.0.0") -> ReleaseRecord:
    import json
    muse = repo_root / ".muse"
    repo_id = json.loads((muse / "repo.json").read_text())["repo_id"]
    snap_id = "a" * 64
    # Write a minimal snapshot.
    snap_dir = muse / "snapshots"
    snap_dir.mkdir(exist_ok=True)
    snap_file = snap_dir / f"{snap_id}.json"
    snap_file.write_text(json.dumps({
        "snapshot_id": snap_id,
        "manifest": {},
        "created_at": datetime.now(timezone.utc).isoformat(),
    }))
    # Write a minimal commit.
    commit_id = "b" * 64
    commit_dir = muse / "commits"
    commit_dir.mkdir(exist_ok=True)
    (commit_dir / f"{commit_id}.json").write_text(json.dumps({
        "commit_id": commit_id,
        "repo_id": repo_id,
        "branch": "main",
        "snapshot_id": snap_id,
        "message": "initial",
        "committed_at": datetime.now(timezone.utc).isoformat(),
        "parent_commit_id": None,
        "sem_ver_bump": "minor",
        "breaking_changes": [],
        "agent_id": "",
        "model_id": "",
        "format_version": 5,
        "reviewed_by": [],
        "test_runs": 0,
    }))
    semver = SemVerTag(major=1, minor=0, patch=0, pre="", build="")
    changelog: list[ChangelogEntry] = [
        ChangelogEntry(
            commit_id=commit_id,
            message="initial",
            sem_ver_bump="minor",
            breaking_changes=[],
            author="gabriel",
            committed_at=datetime.now(timezone.utc).isoformat(),
            agent_id="",
            model_id="",
        )
    ]
    return ReleaseRecord(
        release_id=str(uuid.uuid4()),
        repo_id=repo_id,
        tag=tag,
        semver=semver,
        channel="stable",
        commit_id=commit_id,
        snapshot_id=snap_id,
        title="Test release",
        body="",
        changelog=changelog,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestEmptyReport:
    def test_empty_report_has_all_keys(self) -> None:
        report = _empty_report()
        assert report["languages"] == []
        assert report["total_files"] == 0
        assert report["total_symbols"] == 0
        assert report["api_added"] == []
        assert report["human_commits"] == 0


class TestComputeReleaseAnalysis:
    def test_returns_semantic_report_shape(self, repo: pathlib.Path) -> None:
        release = _make_release(repo)
        report = compute_release_analysis(repo, release)
        # Must return a dict with the expected keys.
        assert isinstance(report, dict)
        required = {
            "languages", "total_files", "semantic_files", "total_symbols",
            "symbols_by_kind", "files_changed", "api_added", "api_removed",
            "api_modified", "file_hotspots", "refactor_events",
            "breaking_changes", "human_commits", "agent_commits",
            "unique_agents", "unique_models", "reviewers",
        }
        assert required.issubset(report.keys())

    def test_empty_manifest_yields_zero_symbols(self, repo: pathlib.Path) -> None:
        release = _make_release(repo)
        report = compute_release_analysis(repo, release)
        assert report["total_symbols"] == 0
        assert report["total_files"] == 0
        assert report["languages"] == []

    def test_human_commit_counted(self, repo: pathlib.Path) -> None:
        release = _make_release(repo)
        # changelog has one entry with no agent_id → human commit
        report = compute_release_analysis(repo, release)
        assert report["human_commits"] == 1
        assert report["agent_commits"] == 0

    def test_agent_commit_counted(self, repo: pathlib.Path) -> None:
        release = _make_release(repo)
        release.changelog[0]["agent_id"] = "code-bot"
        release.changelog[0]["model_id"] = "claude-opus-4"
        report = compute_release_analysis(repo, release)
        assert report["agent_commits"] == 1
        assert report["human_commits"] == 0
        assert report["unique_agents"] == ["code-bot"]
        assert report["unique_models"] == ["claude-opus-4"]

    def test_missing_snapshot_returns_empty_report(self, repo: pathlib.Path) -> None:
        release = _make_release(repo)
        release.snapshot_id = "c" * 64  # nonexistent snapshot
        report = compute_release_analysis(repo, release)
        assert report["total_files"] == 0
        assert report["languages"] == []

    def test_exception_in_analysis_returns_empty_report(self, repo: pathlib.Path) -> None:
        """Even if analysis explodes, push must not fail."""
        release = _make_release(repo)
        # Corrupt the snapshot file so _compute raises.
        snap_file = repo / ".muse" / "snapshots" / f"{release.snapshot_id}.json"
        snap_file.write_text("not valid json {{{")
        report = compute_release_analysis(repo, release)
        assert isinstance(report, dict)


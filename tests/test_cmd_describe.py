"""Tests for ``muse describe`` and ``muse/core/describe.py``.

Covers: no tags fallback to SHA, tag at tip, tag behind tip (distance),
--long format, --require-tag exit-1, --format json, core describe_commit,
stress: deep ancestry.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import pathlib

import pytest
from typer.testing import CliRunner

from muse.cli.app import cli
from muse.core.describe import describe_commit
from muse.core.object_store import write_object
from muse.core.snapshot import compute_snapshot_id
from muse.core.store import CommitRecord, SnapshotRecord, TagRecord, write_commit, write_snapshot, write_tag

runner = CliRunner()

_REPO_ID = "describe-test"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _init_repo(path: pathlib.Path) -> pathlib.Path:
    muse = path / ".muse"
    for d in ("commits", "snapshots", "objects", "refs/heads", f"tags/{_REPO_ID}"):
        (muse / d).mkdir(parents=True, exist_ok=True)
    (muse / "HEAD").write_text("ref: refs/heads/main", encoding="utf-8")
    (muse / "repo.json").write_text(
        json.dumps({"repo_id": _REPO_ID, "domain": "midi"}), encoding="utf-8"
    )
    return path


def _env(repo: pathlib.Path) -> dict[str, str]:
    return {"MUSE_REPO_ROOT": str(repo)}


def _make_commit(
    root: pathlib.Path,
    parent_id: str | None = None,
    content: bytes = b"data",
    branch: str = "main",
) -> str:
    obj_id = _sha(content)
    write_object(root, obj_id, content)
    manifest = {f"file_{obj_id[:8]}.txt": obj_id}
    snap_id = compute_snapshot_id(manifest)
    write_snapshot(root, SnapshotRecord(snapshot_id=snap_id, manifest=manifest))
    committed_at = datetime.datetime.now(datetime.timezone.utc)
    suffix = parent_id or "root"
    commit_id = _sha(f"{suffix}:{snap_id}:{committed_at.isoformat()}".encode())
    write_commit(root, CommitRecord(
        commit_id=commit_id,
        repo_id=_REPO_ID,
        branch=branch,
        snapshot_id=snap_id,
        message=f"commit on {branch}",
        committed_at=committed_at,
        parent_commit_id=parent_id,
    ))
    (root / ".muse" / "refs" / "heads" / branch).write_text(commit_id, encoding="utf-8")
    return commit_id


def _make_tag(root: pathlib.Path, tag: str, commit_id: str) -> None:
    import uuid as _uuid
    write_tag(root, TagRecord(
        tag_id=str(_uuid.uuid4()),
        tag=tag,
        commit_id=commit_id,
        repo_id=_REPO_ID,
        created_at=datetime.datetime.now(datetime.timezone.utc),
    ))


# ---------------------------------------------------------------------------
# Unit: core describe_commit
# ---------------------------------------------------------------------------


def test_describe_no_tags_returns_short_sha(tmp_path: pathlib.Path) -> None:
    _init_repo(tmp_path)
    cid = _make_commit(tmp_path, content=b"alpha")
    result = describe_commit(tmp_path, _REPO_ID, cid)
    assert result["tag"] is None
    assert result["name"] == cid[:12]
    assert result["short_sha"] == cid[:12]


def test_describe_tag_at_tip(tmp_path: pathlib.Path) -> None:
    _init_repo(tmp_path)
    cid = _make_commit(tmp_path, content=b"beta")
    _make_tag(tmp_path, "v1.0.0", cid)
    result = describe_commit(tmp_path, _REPO_ID, cid)
    assert result["tag"] == "v1.0.0"
    assert result["distance"] == 0
    assert result["name"] == "v1.0.0"


def test_describe_tag_one_hop_behind(tmp_path: pathlib.Path) -> None:
    _init_repo(tmp_path)
    cid1 = _make_commit(tmp_path, content=b"first")
    _make_tag(tmp_path, "v0.9.0", cid1)
    cid2 = _make_commit(tmp_path, parent_id=cid1, content=b"second")
    result = describe_commit(tmp_path, _REPO_ID, cid2)
    assert result["tag"] == "v0.9.0"
    assert result["distance"] == 1
    assert result["name"] == "v0.9.0~1"


def test_describe_long_format(tmp_path: pathlib.Path) -> None:
    _init_repo(tmp_path)
    cid = _make_commit(tmp_path, content=b"gamma")
    _make_tag(tmp_path, "v2.0.0", cid)
    result = describe_commit(tmp_path, _REPO_ID, cid, long_format=True)
    assert result["tag"] == "v2.0.0"
    assert result["distance"] == 0
    # Long format always includes distance + SHA.
    assert "v2.0.0-0-g" in result["name"]


# ---------------------------------------------------------------------------
# CLI: muse describe
# ---------------------------------------------------------------------------


def test_describe_cli_help() -> None:
    result = runner.invoke(cli, ["describe", "--help"])
    assert result.exit_code == 0
    assert "--long" in result.output or "-l" in result.output


def test_describe_cli_no_commits(tmp_path: pathlib.Path) -> None:
    _init_repo(tmp_path)
    result = runner.invoke(cli, ["describe"], env=_env(tmp_path))
    assert result.exit_code != 0


def test_describe_cli_text_output(tmp_path: pathlib.Path) -> None:
    _init_repo(tmp_path)
    cid = _make_commit(tmp_path, content=b"cli-test")
    _make_tag(tmp_path, "v3.0.0", cid)
    result = runner.invoke(cli, ["describe"], env=_env(tmp_path))
    assert result.exit_code == 0
    assert "v3.0.0" in result.output


def test_describe_cli_json_output(tmp_path: pathlib.Path) -> None:
    _init_repo(tmp_path)
    cid = _make_commit(tmp_path, content=b"json-test")
    _make_tag(tmp_path, "v4.0.0", cid)
    result = runner.invoke(cli, ["describe", "--format", "json"], env=_env(tmp_path))
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["tag"] == "v4.0.0"
    assert data["distance"] == 0
    assert "commit_id" in data


def test_describe_cli_require_tag_fails_without_tags(tmp_path: pathlib.Path) -> None:
    _init_repo(tmp_path)
    _make_commit(tmp_path, content=b"no-tags")
    result = runner.invoke(cli, ["describe", "--require-tag"], env=_env(tmp_path))
    assert result.exit_code != 0


def test_describe_cli_long_flag(tmp_path: pathlib.Path) -> None:
    _init_repo(tmp_path)
    cid = _make_commit(tmp_path, content=b"long")
    _make_tag(tmp_path, "v5.0.0", cid)
    result = runner.invoke(cli, ["describe", "--long"], env=_env(tmp_path))
    assert result.exit_code == 0
    assert "v5.0.0-0-g" in result.output


def test_describe_cli_short_flags(tmp_path: pathlib.Path) -> None:
    _init_repo(tmp_path)
    cid = _make_commit(tmp_path, content=b"short-flags")
    _make_tag(tmp_path, "v6.0.0", cid)
    result = runner.invoke(cli, ["describe", "-l", "-f", "json"], env=_env(tmp_path))
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert "v6.0.0" in data["name"]


# ---------------------------------------------------------------------------
# Stress: deep ancestry (100 commits, tag at root)
# ---------------------------------------------------------------------------


def test_describe_stress_deep_ancestry(tmp_path: pathlib.Path) -> None:
    _init_repo(tmp_path)
    prev: str | None = None
    first_commit_id = ""
    for i in range(100):
        cid = _make_commit(tmp_path, parent_id=prev, content=f"step {i}".encode())
        if i == 0:
            first_commit_id = cid
        prev = cid

    _make_tag(tmp_path, "v-root", first_commit_id)
    assert prev is not None
    result = describe_commit(tmp_path, _REPO_ID, prev)
    assert result["tag"] == "v-root"
    assert result["distance"] == 99
    assert "v-root~99" == result["name"]

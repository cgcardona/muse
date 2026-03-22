"""Tests for ``muse plumbing snapshot-diff``.

Verifies categorisation of added/modified/deleted paths, resolution of
snapshot IDs, commit IDs, and branch names, text-format output, and error
handling for unresolvable refs.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import pathlib

from tests.cli_test_helper import CliRunner

cli = None  # argparse migration — CliRunner ignores this arg
from muse.core.errors import ExitCode
from muse.core.object_store import write_object
from muse.core.store import CommitRecord, SnapshotRecord, write_commit, write_snapshot

runner = CliRunner()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sha(data: bytes | str) -> str:
    raw = data if isinstance(data, bytes) else data.encode()
    return hashlib.sha256(raw).hexdigest()


def _init_repo(path: pathlib.Path) -> pathlib.Path:
    muse = path / ".muse"
    (muse / "commits").mkdir(parents=True)
    (muse / "snapshots").mkdir(parents=True)
    (muse / "objects").mkdir(parents=True)
    (muse / "refs" / "heads").mkdir(parents=True)
    (muse / "HEAD").write_text("ref: refs/heads/main", encoding="utf-8")
    (muse / "repo.json").write_text(
        json.dumps({"repo_id": "test-repo", "domain": "midi"}), encoding="utf-8"
    )
    return path


def _env(repo: pathlib.Path) -> dict[str, str]:
    return {"MUSE_REPO_ROOT": str(repo)}


def _obj(repo: pathlib.Path, content: bytes) -> str:
    oid = _sha(content)
    write_object(repo, oid, content)
    return oid


def _snap(repo: pathlib.Path, manifest: dict[str, str]) -> str:
    sid = _sha(json.dumps(sorted(manifest.items())))
    write_snapshot(
        repo,
        SnapshotRecord(
            snapshot_id=sid,
            manifest=manifest,
            created_at=datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc),
        ),
    )
    return sid


def _commit(repo: pathlib.Path, tag: str, sid: str, branch: str = "main") -> str:
    cid = _sha(tag)
    write_commit(
        repo,
        CommitRecord(
            commit_id=cid,
            repo_id="test-repo",
            branch=branch,
            snapshot_id=sid,
            message=tag,
            committed_at=datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc),
            author="tester",
            parent_commit_id=None,
        ),
    )
    ref = repo / ".muse" / "refs" / "heads" / branch
    ref.write_text(cid, encoding="utf-8")
    return cid


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSnapshotDiff:
    def test_added_deleted_categorised_correctly(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        shared = _obj(repo, b"shared")
        new_obj = _obj(repo, b"new")
        sid_a = _snap(repo, {"shared.mid": shared, "old.mid": shared})
        sid_b = _snap(repo, {"shared.mid": shared, "new.mid": new_obj})
        result = runner.invoke(cli, ["plumbing", "snapshot-diff", sid_a, sid_b], env=_env(repo))
        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        assert [e["path"] for e in data["added"]] == ["new.mid"]
        assert [e["path"] for e in data["deleted"]] == ["old.mid"]
        assert data["modified"] == []
        assert data["total_changes"] == 2

    def test_modified_entry_contains_both_object_ids(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        v1 = _obj(repo, b"v1")
        v2 = _obj(repo, b"v2")
        sid_a = _snap(repo, {"track.mid": v1})
        sid_b = _snap(repo, {"track.mid": v2})
        result = runner.invoke(cli, ["plumbing", "snapshot-diff", sid_a, sid_b], env=_env(repo))
        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        assert len(data["modified"]) == 1
        mod = data["modified"][0]
        assert mod["path"] == "track.mid"
        assert mod["object_id_a"] == v1
        assert mod["object_id_b"] == v2

    def test_zero_changes_when_snapshots_identical(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        obj = _obj(repo, b"same")
        sid = _snap(repo, {"f.mid": obj})
        result = runner.invoke(cli, ["plumbing", "snapshot-diff", sid, sid], env=_env(repo))
        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        assert data["total_changes"] == 0

    def test_resolves_by_branch_name(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        obj_a = _obj(repo, b"a")
        obj_b = _obj(repo, b"b")
        _commit(repo, "cmt-main", _snap(repo, {"a.mid": obj_a}), branch="main")
        _commit(repo, "cmt-dev", _snap(repo, {"b.mid": obj_b}), branch="dev")
        (repo / ".muse" / "HEAD").write_text("ref: refs/heads/main", encoding="utf-8")
        result = runner.invoke(cli, ["plumbing", "snapshot-diff", "main", "dev"], env=_env(repo))
        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        assert data["total_changes"] == 2

    def test_text_format_shows_status_letters(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        shared = _obj(repo, b"s")
        new_obj = _obj(repo, b"n")
        sid_a = _snap(repo, {"gone.mid": shared})
        sid_b = _snap(repo, {"new.mid": new_obj})
        result = runner.invoke(
            cli, ["plumbing", "snapshot-diff", "--format", "text", sid_a, sid_b], env=_env(repo)
        )
        assert result.exit_code == 0, result.output
        assert "A  new.mid" in result.stdout
        assert "D  gone.mid" in result.stdout

    def test_stat_flag_appends_summary(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        sid_a = _snap(repo, {"gone.mid": _obj(repo, b"g")})
        sid_b = _snap(repo, {"new.mid": _obj(repo, b"n")})
        result = runner.invoke(
            cli,
            ["plumbing", "snapshot-diff", "--format", "text", "--stat", sid_a, sid_b],
            env=_env(repo),
        )
        assert result.exit_code == 0, result.output
        assert "added" in result.stdout
        assert "deleted" in result.stdout

    def test_unresolvable_ref_exits_user_error(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        result = runner.invoke(
            cli, ["plumbing", "snapshot-diff", "no-such-thing", "also-missing"], env=_env(repo)
        )
        assert result.exit_code == ExitCode.USER_ERROR
        assert "error" in json.loads(result.stdout)

    def test_results_sorted_lexicographically(self, tmp_path: pathlib.Path) -> None:
        repo = _init_repo(tmp_path)
        sid_a = _snap(repo, {})
        sid_b = _snap(
            repo, {"z.mid": _obj(repo, b"z"), "a.mid": _obj(repo, b"a"), "m.mid": _obj(repo, b"m")}
        )
        result = runner.invoke(cli, ["plumbing", "snapshot-diff", sid_a, sid_b], env=_env(repo))
        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        added_paths = [e["path"] for e in data["added"]]
        assert added_paths == sorted(added_paths)

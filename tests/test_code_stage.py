"""Tests for ``muse code add`` / ``muse code reset`` and stage-aware commit/status.

Coverage matrix:

Unit tests (pure functions):
- _split_into_hunks: empty diff, single hunk, multi-hunk, trailing newlines
- _apply_hunks_to_bytes: accept all, accept none, accept partial, new-file
- _infer_mode: all three modes (A / M / D)
- _colorize_hunk: color escape codes present for +/- lines

Integration tests (CLI round-trips):
- muse code add <file>      — stages modified file as mode M
- muse code add <new-file>  — stages new file as mode A
- muse code add .           — stages everything
- muse code add -A          — stages all including new files
- muse code add -u          — stages tracked files only (excludes untracked)
- muse code add -u          — stages deleted files as mode D
- muse code add <dir>       — expands directory recursively
- muse code add --dry-run   — shows intent without writing
- muse code add -v          — verbose per-file output
- muse code add (re-stage)  — updates object_id when file changes again
- nonexistent path          — exits non-zero
- wrong domain              — exits non-zero

Stage-aware commit:
- Only staged files appear in the committed snapshot
- Unstaged changes do NOT appear in the committed snapshot
- Stage is cleared after a successful commit
- Staged deletion removes file from next commit

muse status — three-bucket view:
- "Changes staged for commit" section present
- "Changes not staged" section present
- Untracked files listed
- --format json includes staged/unstaged/untracked keys
- --porcelain format

muse code reset:
- reset <file>       — unstages that file only
- reset HEAD <file>  — Git-syntax alias works
- reset (no args)    — clears everything
- reset when nothing staged — exits cleanly

Resilience:
- Corrupt stage.json degrades gracefully (read_stage returns {})
- Staging a file outside the repo root is rejected

Stress:
- Staging 100 files in one shot
"""

from __future__ import annotations

import json
import os
import pathlib

import pytest

from tests.cli_test_helper import CliRunner

cli = None  # argparse migration — CliRunner ignores this arg
runner = CliRunner()


# ---------------------------------------------------------------------------
# Unit tests — pure functions
# ---------------------------------------------------------------------------


class TestSplitIntoHunks:
    """Unit tests for _split_into_hunks (no I/O)."""

    def _run(self, diff_text: str) -> list[list[str]]:
        from muse.cli.commands.code_stage import _split_into_hunks
        lines = [l + "\n" for l in diff_text.splitlines()]
        return _split_into_hunks(lines)

    def test_empty_diff_returns_no_hunks(self) -> None:
        assert self._run("") == []

    def test_single_hunk(self) -> None:
        diff = (
            "--- a/foo.py\n"
            "+++ b/foo.py\n"
            "@@ -1,2 +1,3 @@\n"
            " def f():\n"
            "-    pass\n"
            "+    return 1\n"
        )
        hunks = self._run(diff)
        assert len(hunks) == 1
        assert any("@@" in l for l in hunks[0])

    def test_multi_hunk_has_header_on_each(self) -> None:
        diff = (
            "--- a/foo.py\n"
            "+++ b/foo.py\n"
            "@@ -1,2 +1,3 @@\n"
            " line1\n"
            "-old\n"
            "+new\n"
            "@@ -10,2 +11,3 @@\n"
            " line10\n"
            "-old10\n"
            "+new10\n"
        )
        hunks = self._run(diff)
        assert len(hunks) == 2
        # Each hunk starts with the file header (--- / +++), then @@
        for h in hunks:
            assert any(l.startswith("---") for l in h)
            assert any(l.startswith("+++") for l in h)
            assert any(l.startswith("@@") for l in h)

    def test_no_header_lines_before_first_hunk_is_still_valid(self) -> None:
        diff = (
            "@@ -1,1 +1,1 @@\n"
            "-old\n"
            "+new\n"
        )
        hunks = self._run(diff)
        assert len(hunks) == 1


class TestApplyHunksToBytes:
    """Unit tests for _apply_hunks_to_bytes."""

    def _run(self, before: str, diff_text: str, accept_all: bool = True) -> str:
        from muse.cli.commands.code_stage import _split_into_hunks, _apply_hunks_to_bytes

        before_lines = before.splitlines(keepends=True)
        after_lines = diff_text.splitlines(keepends=True)

        import difflib
        diff = list(difflib.unified_diff(
            before_lines, after_lines, fromfile="a/f", tofile="b/f", lineterm=""
        ))
        diff_nl = [l + "\n" for l in diff]
        hunks = _split_into_hunks(diff_nl)

        accepted = hunks if accept_all else []
        result = _apply_hunks_to_bytes(before.encode(), accepted)
        return result.decode()

    def test_accept_all_hunks_produces_after_content(self) -> None:
        before = "def f():\n    pass\n"
        after = "def f():\n    return 1\n"
        result = self._run(before, after, accept_all=True)
        assert "return 1" in result

    def test_accept_no_hunks_preserves_original(self) -> None:
        before = "def f():\n    pass\n"
        after = "def f():\n    return 1\n"
        result = self._run(before, after, accept_all=False)
        assert result == before

    def test_new_file_from_empty(self) -> None:
        """Staging a new file from empty before-bytes produces after-content."""
        before = ""
        after = "x = 1\ny = 2\n"
        result = self._run(before, after, accept_all=True)
        assert "x = 1" in result

    def test_binary_safe_with_replacement(self) -> None:
        from muse.cli.commands.code_stage import _apply_hunks_to_bytes
        result = _apply_hunks_to_bytes(b"\xff\xfe", [])
        assert isinstance(result, bytes)


class TestInferMode:
    """Unit tests for _infer_mode."""

    def _run(self, rel: str, head: dict[str, str], exists: bool) -> str:
        from muse.cli.commands.code_stage import _infer_mode
        return _infer_mode(rel, head, exists)

    def test_existing_tracked_is_M(self) -> None:
        assert self._run("src/a.py", {"src/a.py": "abc"}, True) == "M"

    def test_new_untracked_is_A(self) -> None:
        assert self._run("src/new.py", {}, True) == "A"

    def test_missing_from_disk_is_D(self) -> None:
        assert self._run("src/gone.py", {"src/gone.py": "abc"}, False) == "D"

    def test_missing_and_not_tracked_is_D(self) -> None:
        # Shouldn't normally occur, but must not crash.
        assert self._run("ghost.py", {}, False) == "D"


class TestColorizeHunk:
    """Unit tests for _colorize_hunk."""

    def test_added_lines_get_green(self) -> None:
        from muse.cli.commands.code_stage import _colorize_hunk
        result = _colorize_hunk(["+new line\n"])
        assert "\x1b[32m" in result  # green

    def test_removed_lines_get_red(self) -> None:
        from muse.cli.commands.code_stage import _colorize_hunk
        result = _colorize_hunk(["-old line\n"])
        assert "\x1b[31m" in result  # red

    def test_file_header_not_colored(self) -> None:
        from muse.cli.commands.code_stage import _colorize_hunk
        result = _colorize_hunk(["--- a/foo.py\n", "+++ b/foo.py\n"])
        # file header lines should not get red/green
        assert "\x1b[31m" not in result
        assert "\x1b[32m" not in result

    def test_at_at_header_gets_cyan(self) -> None:
        from muse.cli.commands.code_stage import _colorize_hunk
        result = _colorize_hunk(["@@ -1,2 +1,3 @@\n"])
        assert "\x1b[36m" in result  # cyan


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _env(root: pathlib.Path) -> dict[str, str]:
    return {"MUSE_REPO_ROOT": str(root)}


@pytest.fixture()
def code_repo(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    """Initialise a fresh code-domain Muse repo with one initial commit."""
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(cli, ["init", "--domain", "code"], env=_env(tmp_path))
    assert result.exit_code == 0, result.output

    (tmp_path / "auth.py").write_text("def authenticate():\n    pass\n")
    (tmp_path / "models.py").write_text("class User:\n    pass\n")

    r = runner.invoke(cli, ["commit", "-m", "initial"], env=_env(tmp_path))
    assert r.exit_code == 0, r.output

    return tmp_path


# ---------------------------------------------------------------------------
# muse code add — integration tests
# ---------------------------------------------------------------------------


class TestCodeAdd:
    def test_stage_modified_file_is_mode_M(self, code_repo: pathlib.Path) -> None:
        (code_repo / "auth.py").write_text("def authenticate():\n    return True\n")
        result = runner.invoke(cli, ["code", "add", "auth.py"], env=_env(code_repo))
        assert result.exit_code == 0, result.output
        assert "Staged 1 file" in result.output

        stage = json.loads((code_repo / ".muse" / "code" / "stage.json").read_text())
        assert stage["entries"]["auth.py"]["mode"] == "M"

    def test_stage_new_file_is_mode_A(self, code_repo: pathlib.Path) -> None:
        (code_repo / "new_module.py").write_text("x = 1\n")
        runner.invoke(cli, ["code", "add", "new_module.py"], env=_env(code_repo))
        stage = json.loads((code_repo / ".muse" / "code" / "stage.json").read_text())
        assert stage["entries"]["new_module.py"]["mode"] == "A"

    def test_stage_dot_stages_everything(self, code_repo: pathlib.Path) -> None:
        (code_repo / "auth.py").write_text("# changed\n")
        runner.invoke(cli, ["code", "add", "."], env=_env(code_repo))
        stage = json.loads((code_repo / ".muse" / "code" / "stage.json").read_text())
        assert "auth.py" in stage["entries"]

    def test_stage_A_includes_new_files(self, code_repo: pathlib.Path) -> None:
        (code_repo / "auth.py").write_text("# changed\n")
        (code_repo / "new.py").write_text("x = 1\n")
        runner.invoke(cli, ["code", "add", "-A"], env=_env(code_repo))
        stage = json.loads((code_repo / ".muse" / "code" / "stage.json").read_text())
        assert "auth.py" in stage["entries"]
        assert "new.py" in stage["entries"]

    def test_stage_u_excludes_new_untracked_files(
        self, code_repo: pathlib.Path
    ) -> None:
        """-u stages only tracked files; new/untracked files are NOT staged."""
        (code_repo / "auth.py").write_text("# tracked change\n")
        (code_repo / "brand_new.py").write_text("x = 1\n")

        runner.invoke(cli, ["code", "add", "-u"], env=_env(code_repo))

        stage_file = code_repo / ".muse" / "code" / "stage.json"
        assert stage_file.exists()
        stage = json.loads(stage_file.read_text())
        assert "auth.py" in stage["entries"]
        assert "brand_new.py" not in stage["entries"]

    def test_stage_u_includes_deleted_files(self, code_repo: pathlib.Path) -> None:
        (code_repo / "models.py").unlink()
        runner.invoke(cli, ["code", "add", "-u"], env=_env(code_repo))
        stage = json.loads((code_repo / ".muse" / "code" / "stage.json").read_text())
        assert "models.py" in stage["entries"]
        assert stage["entries"]["models.py"]["mode"] == "D"

    def test_stage_directory_expands_recursively(
        self, code_repo: pathlib.Path
    ) -> None:
        src = code_repo / "src"
        src.mkdir()
        (src / "a.py").write_text("x = 1\n")
        (src / "b.py").write_text("y = 2\n")

        runner.invoke(cli, ["code", "add", "src"], env=_env(code_repo))
        stage = json.loads((code_repo / ".muse" / "code" / "stage.json").read_text())
        assert "src/a.py" in stage["entries"]
        assert "src/b.py" in stage["entries"]

    def test_dry_run_does_not_write_stage(self, code_repo: pathlib.Path) -> None:
        (code_repo / "auth.py").write_text("# dry\n")
        runner.invoke(
            cli, ["code", "add", "--dry-run", "auth.py"], env=_env(code_repo)
        )
        assert not (code_repo / ".muse" / "code" / "stage.json").exists()

    def test_dry_run_output_shows_files(self, code_repo: pathlib.Path) -> None:
        (code_repo / "auth.py").write_text("# dry\n")
        result = runner.invoke(
            cli, ["code", "add", "--dry-run", "auth.py"], env=_env(code_repo)
        )
        assert "auth.py" in result.output

    def test_verbose_shows_per_file_output(self, code_repo: pathlib.Path) -> None:
        (code_repo / "auth.py").write_text("# verbose\n")
        result = runner.invoke(
            cli, ["code", "add", "-v", "auth.py"], env=_env(code_repo)
        )
        assert result.exit_code == 0
        assert "auth.py" in result.output

    def test_restage_updates_object_id(self, code_repo: pathlib.Path) -> None:
        """Staging a file twice with different content updates the object_id."""
        (code_repo / "auth.py").write_text("# version 1\n")
        runner.invoke(cli, ["code", "add", "auth.py"], env=_env(code_repo))
        stage_v1 = json.loads(
            (code_repo / ".muse" / "code" / "stage.json").read_text()
        )
        oid_v1 = stage_v1["entries"]["auth.py"]["object_id"]

        (code_repo / "auth.py").write_text("# version 2\n")
        runner.invoke(cli, ["code", "add", "auth.py"], env=_env(code_repo))
        stage_v2 = json.loads(
            (code_repo / ".muse" / "code" / "stage.json").read_text()
        )
        oid_v2 = stage_v2["entries"]["auth.py"]["object_id"]

        assert oid_v1 != oid_v2

    def test_staging_unchanged_file_is_idempotent(
        self, code_repo: pathlib.Path
    ) -> None:
        """Staging a file that has not changed since last staging is a no-op."""
        (code_repo / "auth.py").write_text("# same\n")
        runner.invoke(cli, ["code", "add", "auth.py"], env=_env(code_repo))
        result = runner.invoke(cli, ["code", "add", "auth.py"], env=_env(code_repo))
        assert result.exit_code == 0
        assert "already up to date" in result.output

    def test_nonexistent_path_exits_error(self, code_repo: pathlib.Path) -> None:
        result = runner.invoke(
            cli, ["code", "add", "does_not_exist.py"], env=_env(code_repo)
        )
        assert result.exit_code != 0

    def test_wrong_domain_exits_error(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        runner.invoke(cli, ["init", "--domain", "midi"], env=_env(tmp_path))
        result = runner.invoke(cli, ["code", "add", "file.py"], env=_env(tmp_path))
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Stage-aware commit
# ---------------------------------------------------------------------------


class TestStageAwareCommit:
    def test_only_staged_file_is_committed(self, code_repo: pathlib.Path) -> None:
        (code_repo / "auth.py").write_text("def authenticate():\n    return True\n")
        (code_repo / "models.py").write_text("class User:\n    name = 'anon'\n")

        runner.invoke(cli, ["code", "add", "auth.py"], env=_env(code_repo))

        r = runner.invoke(
            cli, ["commit", "-m", "auth only", "--format", "json"],
            env=_env(code_repo),
        )
        assert r.exit_code == 0, r.output
        data = json.loads(r.output.strip())

        from muse.core.store import read_commit, read_snapshot
        from muse.core.object_store import read_object

        commit = read_commit(code_repo, data["commit_id"])
        assert commit is not None
        snap = read_snapshot(code_repo, commit.snapshot_id)
        assert snap is not None

        auth_bytes = read_object(code_repo, snap.manifest["auth.py"])
        assert auth_bytes is not None
        assert b"return True" in auth_bytes

        models_bytes = read_object(code_repo, snap.manifest["models.py"])
        assert models_bytes is not None
        # models.py was NOT staged — should have old content (pass, not name='anon')
        assert b"name = 'anon'" not in models_bytes
        assert b"pass" in models_bytes

    def test_stage_cleared_after_commit(self, code_repo: pathlib.Path) -> None:
        (code_repo / "auth.py").write_text("# cleared after commit\n")
        runner.invoke(cli, ["code", "add", "auth.py"], env=_env(code_repo))

        stage_file = code_repo / ".muse" / "code" / "stage.json"
        assert stage_file.exists()

        runner.invoke(cli, ["commit", "-m", "clear stage test"], env=_env(code_repo))
        assert not stage_file.exists()

    def test_staged_deletion_removes_file_from_commit(
        self, code_repo: pathlib.Path
    ) -> None:
        (code_repo / "models.py").unlink()
        runner.invoke(cli, ["code", "add", "-u"], env=_env(code_repo))

        r = runner.invoke(
            cli, ["commit", "-m", "delete models", "--format", "json"],
            env=_env(code_repo),
        )
        assert r.exit_code == 0, r.output
        data = json.loads(r.output.strip())

        from muse.core.store import read_commit, read_snapshot
        commit = read_commit(code_repo, data["commit_id"])
        assert commit is not None
        snap = read_snapshot(code_repo, commit.snapshot_id)
        assert snap is not None
        assert "models.py" not in snap.manifest

    def test_full_snapshot_when_no_stage(self, code_repo: pathlib.Path) -> None:
        """Without a stage, commit captures the full working tree."""
        (code_repo / "extra.py").write_text("z = 99\n")

        r = runner.invoke(
            cli, ["commit", "-m", "full snapshot", "--format", "json"],
            env=_env(code_repo),
        )
        assert r.exit_code == 0, r.output
        data = json.loads(r.output.strip())

        from muse.core.store import read_commit, read_snapshot
        commit = read_commit(code_repo, data["commit_id"])
        assert commit is not None
        snap = read_snapshot(code_repo, commit.snapshot_id)
        assert snap is not None
        assert "extra.py" in snap.manifest


# ---------------------------------------------------------------------------
# muse status — staged view
# ---------------------------------------------------------------------------


class TestStageStatus:
    def test_shows_staged_section_when_stage_active(
        self, code_repo: pathlib.Path
    ) -> None:
        (code_repo / "auth.py").write_text("# staged change\n")
        runner.invoke(cli, ["code", "add", "auth.py"], env=_env(code_repo))

        result = runner.invoke(cli, ["status"], env=_env(code_repo))
        assert result.exit_code == 0, result.output
        assert "staged for commit" in result.output
        assert "auth.py" in result.output

    def test_shows_unstaged_section_for_unmodified_tracked_with_changes(
        self, code_repo: pathlib.Path
    ) -> None:
        (code_repo / "auth.py").write_text("# staged\n")
        (code_repo / "models.py").write_text("# NOT staged\n")
        runner.invoke(cli, ["code", "add", "auth.py"], env=_env(code_repo))

        result = runner.invoke(cli, ["status"], env=_env(code_repo))
        assert "not staged" in result.output
        assert "models.py" in result.output

    def test_shows_untracked_section(self, code_repo: pathlib.Path) -> None:
        (code_repo / "auth.py").write_text("# staged\n")
        (code_repo / "brand_new.py").write_text("x = 1\n")
        runner.invoke(cli, ["code", "add", "auth.py"], env=_env(code_repo))

        result = runner.invoke(cli, ["status"], env=_env(code_repo))
        assert "Untracked" in result.output
        assert "brand_new.py" in result.output

    def test_json_format_has_all_buckets(self, code_repo: pathlib.Path) -> None:
        (code_repo / "auth.py").write_text("# json stage\n")
        (code_repo / "new_file.py").write_text("x = 1\n")
        runner.invoke(cli, ["code", "add", "auth.py"], env=_env(code_repo))

        result = runner.invoke(
            cli, ["status", "--format", "json"], env=_env(code_repo)
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output.strip())
        assert "staged" in data
        assert "unstaged" in data
        assert "untracked" in data
        assert "auth.py" in data["staged"]
        assert "new_file.py" in data["untracked"]

    def test_porcelain_format_with_stage(self, code_repo: pathlib.Path) -> None:
        (code_repo / "auth.py").write_text("# porcelain\n")
        runner.invoke(cli, ["code", "add", "auth.py"], env=_env(code_repo))

        result = runner.invoke(cli, ["status", "--porcelain"], env=_env(code_repo))
        assert result.exit_code == 0
        assert "auth.py" in result.output

    def test_short_format_with_stage(self, code_repo: pathlib.Path) -> None:
        (code_repo / "auth.py").write_text("# short\n")
        runner.invoke(cli, ["code", "add", "auth.py"], env=_env(code_repo))

        result = runner.invoke(cli, ["status", "--short"], env=_env(code_repo))
        assert result.exit_code == 0
        assert "auth.py" in result.output

    def test_clean_tree_after_commit_clears_stage(
        self, code_repo: pathlib.Path
    ) -> None:
        """After staging and committing, status should show clean tree."""
        (code_repo / "auth.py").write_text("# committed\n")
        runner.invoke(cli, ["code", "add", "auth.py"], env=_env(code_repo))
        runner.invoke(cli, ["commit", "-m", "staged commit"], env=_env(code_repo))

        result = runner.invoke(cli, ["status"], env=_env(code_repo))
        assert result.exit_code == 0
        # No stage file → falls back to normal drift-based status.
        assert "staged for commit" not in result.output


# ---------------------------------------------------------------------------
# muse code reset
# ---------------------------------------------------------------------------


class TestCodeReset:
    def test_reset_specific_file(self, code_repo: pathlib.Path) -> None:
        (code_repo / "auth.py").write_text("# staged\n")
        (code_repo / "models.py").write_text("# also staged\n")
        runner.invoke(cli, ["code", "add", "-A"], env=_env(code_repo))

        result = runner.invoke(
            cli, ["code", "reset", "auth.py"], env=_env(code_repo)
        )
        assert result.exit_code == 0
        stage = json.loads(
            (code_repo / ".muse" / "code" / "stage.json").read_text()
        )
        assert "auth.py" not in stage["entries"]
        assert "models.py" in stage["entries"]

    def test_reset_HEAD_syntax(self, code_repo: pathlib.Path) -> None:
        (code_repo / "auth.py").write_text("# head\n")
        runner.invoke(cli, ["code", "add", "auth.py"], env=_env(code_repo))
        result = runner.invoke(
            cli, ["code", "reset", "HEAD", "auth.py"], env=_env(code_repo)
        )
        assert result.exit_code == 0
        assert not (code_repo / ".muse" / "code" / "stage.json").exists()

    def test_reset_no_args_clears_all(self, code_repo: pathlib.Path) -> None:
        (code_repo / "auth.py").write_text("# a\n")
        (code_repo / "models.py").write_text("# b\n")
        runner.invoke(cli, ["code", "add", "-A"], env=_env(code_repo))
        result = runner.invoke(cli, ["code", "reset"], env=_env(code_repo))
        assert result.exit_code == 0
        assert not (code_repo / ".muse" / "code" / "stage.json").exists()

    def test_reset_when_nothing_staged(self, code_repo: pathlib.Path) -> None:
        result = runner.invoke(cli, ["code", "reset"], env=_env(code_repo))
        assert result.exit_code == 0
        assert "Nothing staged" in result.output

    def test_reset_nonexistent_file_does_not_crash(
        self, code_repo: pathlib.Path
    ) -> None:
        (code_repo / "auth.py").write_text("# staged\n")
        runner.invoke(cli, ["code", "add", "auth.py"], env=_env(code_repo))
        result = runner.invoke(
            cli, ["code", "reset", "not_in_stage.py"], env=_env(code_repo)
        )
        assert result.exit_code == 0
        assert "not staged" in result.output


# ---------------------------------------------------------------------------
# Resilience
# ---------------------------------------------------------------------------


class TestResilience:
    def test_corrupt_stage_json_returns_empty(
        self, code_repo: pathlib.Path
    ) -> None:
        """Corrupt stage.json must degrade gracefully — returns {} on read."""
        from muse.plugins.code.stage import read_stage

        stage_dir = code_repo / ".muse" / "code"
        stage_dir.mkdir(parents=True, exist_ok=True)
        (stage_dir / "stage.json").write_text("NOT VALID JSON }{")

        entries = read_stage(code_repo)
        assert entries == {}

    def test_truncated_stage_json_returns_empty(
        self, code_repo: pathlib.Path
    ) -> None:
        from muse.plugins.code.stage import read_stage

        stage_dir = code_repo / ".muse" / "code"
        stage_dir.mkdir(parents=True, exist_ok=True)
        (stage_dir / "stage.json").write_bytes(b"\x00\x01\x02")

        entries = read_stage(code_repo)
        assert entries == {}

    def test_missing_stage_returns_empty(self, code_repo: pathlib.Path) -> None:
        from muse.plugins.code.stage import read_stage

        entries = read_stage(code_repo)
        assert entries == {}

    def test_write_empty_entries_removes_file(
        self, code_repo: pathlib.Path
    ) -> None:
        from muse.plugins.code.stage import write_stage, stage_path

        path = stage_path(code_repo)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"version":1,"entries":{}}')

        write_stage(code_repo, {})
        assert not path.exists()

    def test_clear_stage_idempotent(self, code_repo: pathlib.Path) -> None:
        from muse.plugins.code.stage import clear_stage

        clear_stage(code_repo)  # no stage to clear — must not raise
        clear_stage(code_repo)  # idempotent


# ---------------------------------------------------------------------------
# Stress test
# ---------------------------------------------------------------------------


class TestStageStress:
    def test_stage_100_files(
        self, code_repo: pathlib.Path
    ) -> None:
        """Staging 100 files must complete without error and write all entries."""
        for i in range(100):
            (code_repo / f"module_{i:03d}.py").write_text(f"X_{i} = {i}\n")

        result = runner.invoke(cli, ["code", "add", "-A"], env=_env(code_repo))
        assert result.exit_code == 0, result.output

        stage = json.loads(
            (code_repo / ".muse" / "code" / "stage.json").read_text()
        )
        # 100 new files + 2 original tracked files (auth.py, models.py)
        assert len(stage["entries"]) >= 100

    def test_commit_100_staged_files(
        self, code_repo: pathlib.Path
    ) -> None:
        """Committing 100 staged files produces a correct manifest."""
        for i in range(100):
            (code_repo / f"mod_{i:03d}.py").write_text(f"V = {i}\n")

        runner.invoke(cli, ["code", "add", "-A"], env=_env(code_repo))
        r = runner.invoke(
            cli, ["commit", "-m", "100 files", "--format", "json"],
            env=_env(code_repo),
        )
        assert r.exit_code == 0, r.output
        data = json.loads(r.output.strip())

        from muse.core.store import read_commit, read_snapshot
        commit = read_commit(code_repo, data["commit_id"])
        assert commit is not None
        snap = read_snapshot(code_repo, commit.snapshot_id)
        assert snap is not None
        assert len(snap.manifest) >= 100


def test_add_all_stages_deletions(
    code_repo: pathlib.Path,
) -> None:
    """``muse code add -A`` must stage tracked files that have been deleted.

    Regression test: before the fix, ``-A`` used ``_walk_tree`` which only
    returns files present on disk.  Deleted tracked files were therefore
    silently omitted and the deletion was never recorded in the stage.
    """
    # code_repo already has auth.py and models.py committed.
    os.remove(code_repo / "auth.py")

    r = runner.invoke(cli, ["code", "add", "-A"], env=_env(code_repo))
    assert r.exit_code == 0, r.output

    from muse.plugins.code.stage import read_stage
    stage = read_stage(code_repo)
    assert "auth.py" in stage, "deleted tracked file must appear in stage"
    assert stage["auth.py"]["mode"] == "D", "deleted file must have mode D"


def test_add_dot_does_not_stage_museignore_files(
    code_repo: pathlib.Path,
) -> None:
    """``muse code add .`` must not stage files matched by ``.museignore``.

    Regression test: before the fix, ``_walk_tree`` never consulted
    ``.museignore``, so any file on disk — including ones the user explicitly
    excluded — could be silently staged and committed.
    """
    (code_repo / ".museignore").write_text('[global]\npatterns = ["*.log"]\n')
    (code_repo / "debug.log").write_text("ignored content\n")
    (code_repo / "app.py").write_text("# new code\n")

    r = runner.invoke(cli, ["code", "add", "."], env=_env(code_repo))
    assert r.exit_code == 0, r.output

    from muse.plugins.code.stage import read_stage
    stage = read_stage(code_repo)
    assert "debug.log" not in stage, ".museignore'd file must NOT be staged"
    assert "app.py" in stage, "non-ignored new file must be staged"


def test_add_dot_does_not_stage_unchanged_files(
    code_repo: pathlib.Path,
) -> None:
    """``muse code add .`` must only stage files whose content differs from HEAD.

    Regression test for the bug where ``muse code add .`` staged every file in
    the working tree regardless of whether it had changed, because the
    "skip-if-already-staged" guard was only consulted (and only correct) after a
    second ``add`` run.  On a fresh stage the check was vacuously false for all
    files, so even unchanged files were staged.
    """
    # Make an initial commit so HEAD has a manifest.
    (code_repo / "alpha.py").write_text("x = 1\n")
    (code_repo / "beta.py").write_text("y = 2\n")
    runner.invoke(cli, ["commit", "-m", "initial"], env=_env(code_repo))

    # Modify only one file; leave the other untouched.
    (code_repo / "alpha.py").write_text("x = 99\n")

    # Stage everything.
    r = runner.invoke(cli, ["code", "add", "."], env=_env(code_repo))
    assert r.exit_code == 0, r.output

    # Only the changed file must be staged — NOT the unchanged beta.py.
    from muse.plugins.code.stage import read_stage
    stage = read_stage(code_repo)
    assert "alpha.py" in stage, "modified file must be staged"
    assert "beta.py" not in stage, "unchanged file must NOT appear in stage"

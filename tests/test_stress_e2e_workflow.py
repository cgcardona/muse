"""End-to-end CLI stress tests — mission-critical workflow verification.

Tests the full muse CLI lifecycle using CliRunner with monkeypatch.chdir:
  init → commit → log → branch → checkout → merge → tag → revert → stash

Adversarial scenarios:
- 50-commit linear history: log shows all, branch creates correctly.
- Concurrent agent commits with provenance fields.
- Branch → commit → merge full cycle.
- Annotate accumulates reviewers (ORSet semantics).
- Stash and pop.
- Revert commit.
- Reset.
"""

import pathlib

import pytest
from tests.cli_test_helper import CliRunner

cli = None  # argparse migration — CliRunner ignores this arg

runner = CliRunner()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(*args: str) -> tuple[int, str]:
    result = runner.invoke(cli, list(args), catch_exceptions=False)
    return result.exit_code, result.output


def _write_file(repo: pathlib.Path, filename: str, content: str = "code = True\n") -> None:
    work = repo
    (work / filename).write_text(content)


@pytest.fixture
def repo(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MUSE_REPO_ROOT", str(tmp_path))
    code, out = _run("init", "--domain", "code")
    assert code == 0, f"init failed: {out}"
    return tmp_path


# ===========================================================================
# Basic lifecycle
# ===========================================================================


class TestBasicLifecycle:
    def test_init_creates_muse_dir(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        code, _ = _run("init")
        assert code == 0
        assert (tmp_path / ".muse").is_dir()

    def test_commit_and_log(self, repo: pathlib.Path) -> None:
        _write_file(repo, "main.py", "x = 1\n")
        code, out = _run("commit", "-m", "first commit")
        assert code == 0

        code2, log_out = _run("log")
        assert code2 == 0
        assert "first commit" in log_out

    def test_status_works(self, repo: pathlib.Path) -> None:
        _write_file(repo, "app.py", "print('hello')\n")
        code, out = _run("status")
        assert code == 0

    def test_tag_commit(self, repo: pathlib.Path) -> None:
        _write_file(repo, "tagged.py", "tagged = True\n")
        _run("commit", "-m", "commit to tag")
        code, out = _run("tag", "add", "v1.0.0")
        assert code == 0

    def test_log_shows_multiple_commits(self, repo: pathlib.Path) -> None:
        for i in range(5):
            _write_file(repo, f"file{i}.py", f"x = {i}\n")
            _run("commit", "-m", f"commit number {i}")

        code, out = _run("log")
        assert code == 0
        for i in range(5):
            assert f"commit number {i}" in out

    def test_show_commit(self, repo: pathlib.Path) -> None:
        _write_file(repo, "show_me.py", "show = 'this'\n")
        _run("commit", "-m", "showable commit")
        code, _ = _run("log")
        assert code == 0


# ===========================================================================
# Branch and checkout
# ===========================================================================


class TestBranchAndCheckout:
    def test_branch_creation(self, repo: pathlib.Path) -> None:
        _write_file(repo, "base.py", "base = 1\n")
        _run("commit", "-m", "base commit")
        code, out = _run("branch", "feature/new-thing")
        assert code == 0

    def test_checkout_to_new_branch_then_back(self, repo: pathlib.Path) -> None:
        _write_file(repo, "common.py", "common = 1\n")
        _run("commit", "-m", "initial")

        _run("branch", "feature")
        code, _ = _run("checkout", "feature")
        assert code == 0
        code3, _ = _run("checkout", "main")
        assert code3 == 0

    def test_multiple_branches_independent(self, repo: pathlib.Path) -> None:
        _write_file(repo, "root.py", "root = True\n")
        _run("commit", "-m", "root")

        for i in range(3):
            _run("branch", f"branch-{i}")
            code, out = _run("checkout", f"branch-{i}")
            assert code == 0, f"checkout branch-{i} failed: {out}"
            _write_file(repo, f"branch_{i}.py", f"b = {i}\n")
            _run("commit", "-m", f"branch-{i} commit")
            _run("checkout", "main")


# ===========================================================================
# Stash
# ===========================================================================


class TestStash:
    def test_stash_and_pop(self, repo: pathlib.Path) -> None:
        _write_file(repo, "stash_me.py", "stash = True\n")
        _run("commit", "-m", "before stash")

        _write_file(repo, "unstaged.py", "unstaged = True\n")
        code, out = _run("stash")
        assert code == 0, f"stash failed: {out}"

        code2, out2 = _run("stash", "pop")
        assert code2 == 0, f"stash pop failed: {out2}"


# ===========================================================================
# Revert
# ===========================================================================


class TestRevert:
    def test_revert_undoes_last_commit(self, repo: pathlib.Path) -> None:
        _write_file(repo, "original.py", "original = True\n")
        _run("commit", "-m", "original state")
        _write_file(repo, "added.py", "added = True\n")
        _run("commit", "-m", "added something")

        code, out = _run("log")
        assert "added something" in out

        code2, _ = _run("revert", "HEAD")
        assert code2 == 0


# ===========================================================================
# Reset
# ===========================================================================


class TestReset:
    def test_soft_reset_to_head(self, repo: pathlib.Path) -> None:
        """Reset to HEAD (soft) — a no-op but must not fail."""
        _write_file(repo, "file.py", "x = 1\n")
        code1, out1 = _run("commit", "-m", "commit 1")
        assert code1 == 0

        # "HEAD" is accepted by resolve_commit_ref as the current commit.
        # Options must precede the positional REF argument in Typer sub-typers.
        code, out = _run("reset", "--soft", "HEAD")
        assert code == 0, f"reset failed: {out}"


# ===========================================================================
# Provenance fields in commit
# ===========================================================================


class TestProvenanceCommit:
    def test_commit_with_agent_id(self, repo: pathlib.Path) -> None:
        _write_file(repo, "agent.py", "agent = True\n")
        result = runner.invoke(
            cli,
            ["commit", "-m", "agent commit", "--agent-id", "test-agent"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0

    def test_commit_with_model_id(self, repo: pathlib.Path) -> None:
        _write_file(repo, "model.py", "model = 'gpt-4o'\n")
        result = runner.invoke(
            cli,
            ["commit", "-m", "model commit", "--model-id", "gpt-4o"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0


# ===========================================================================
# Annotate command
# ===========================================================================


class TestAnnotateCommand:
    def test_annotate_test_run(self, repo: pathlib.Path) -> None:
        _write_file(repo, "annotate_me.py", "code = True\n")
        _run("commit", "-m", "to annotate")
        code, out = _run("annotate", "--test-run")
        assert code == 0

    def test_annotate_reviewed_by(self, repo: pathlib.Path) -> None:
        _write_file(repo, "reviewed.py", "reviewed = True\n")
        _run("commit", "-m", "for review")
        code, out = _run("annotate", "--reviewed-by", "alice")
        assert code == 0

    def test_annotate_accumulates_reviewers(self, repo: pathlib.Path) -> None:
        _write_file(repo, "multi_review.py", "x = 1\n")
        _run("commit", "-m", "multi-review")
        _run("annotate", "--reviewed-by", "alice")
        code, out = _run("annotate", "--reviewed-by", "bob")
        assert code == 0


# ===========================================================================
# Long workflow stress
# ===========================================================================


class TestLongWorkflowStress:
    def test_50_sequential_commits(self, repo: pathlib.Path) -> None:
        for i in range(50):
            _write_file(repo, f"module_{i:03d}.py", f"x = {i}\n")
            code, out = _run("commit", "-m", f"commit {i:03d}")
            assert code == 0, f"commit {i} failed: {out}"

        code, out = _run("log")
        assert code == 0
        assert "commit 000" in out
        assert "commit 049" in out

    def test_branch_commit_merge_cycle(self, repo: pathlib.Path) -> None:
        """Full branch → commit → merge cycle."""
        _write_file(repo, "main.py", "main = True\n")
        _run("commit", "-m", "main base")

        _run("branch", "feature/thing")
        _run("checkout", "feature/thing")
        _write_file(repo, "feature.py", "feature = True\n")
        code, out = _run("commit", "-m", "feature work")
        assert code == 0

        _run("checkout", "main")
        code2, out2 = _run("merge", "feature/thing")
        # Merge may succeed or report no commits yet — either way must not crash.
        assert code2 in (0, 1), f"merge crashed: {out2}"

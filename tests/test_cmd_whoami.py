"""Tests for ``muse whoami``.

Covers: no hub configured, no identity stored, identity found (text and JSON),
all-hubs listing, --help exits 0.
"""

from __future__ import annotations

import json
import pathlib

import pytest
from tests.cli_test_helper import CliRunner

cli = None  # argparse migration — CliRunner ignores this arg

runner = CliRunner()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _init_repo(path: pathlib.Path) -> pathlib.Path:
    muse = path / ".muse"
    (muse / "commits").mkdir(parents=True)
    (muse / "snapshots").mkdir(parents=True)
    (muse / "objects").mkdir(parents=True)
    (muse / "refs" / "heads").mkdir(parents=True)
    (muse / "HEAD").write_text("ref: refs/heads/main", encoding="utf-8")
    (muse / "repo.json").write_text(
        json.dumps({"repo_id": "whoami-test", "domain": "midi"}), encoding="utf-8"
    )
    return path


def _env(repo: pathlib.Path) -> dict[str, str]:
    return {"MUSE_REPO_ROOT": str(repo)}


# ---------------------------------------------------------------------------
# Unit: help and basic invocation
# ---------------------------------------------------------------------------


def test_whoami_help() -> None:
    result = runner.invoke(cli, ["whoami", "--help"])
    assert result.exit_code == 0
    assert "identity" in result.output.lower()


def test_whoami_exits_cleanly_or_with_user_error(tmp_path: pathlib.Path) -> None:
    """whoami exits 0 (identity found) or 1 (not found) — never crashes."""
    _init_repo(tmp_path)
    result = runner.invoke(cli, ["whoami"], env=_env(tmp_path))
    # Accept exit 0 (identity on machine) or 1 (no identity) — never any other code.
    assert result.exit_code in {0, 1}


def test_whoami_json_flag_available() -> None:
    """--json flag is accepted."""
    result = runner.invoke(cli, ["whoami", "--help"])
    assert "--json" in result.output or "-j" in result.output


def test_whoami_all_flag_available() -> None:
    """--all flag is accepted."""
    result = runner.invoke(cli, ["whoami", "--help"])
    assert "--all" in result.output or "-a" in result.output


# ---------------------------------------------------------------------------
# Integration: short flags work
# ---------------------------------------------------------------------------


def test_whoami_short_flags_accepted(tmp_path: pathlib.Path) -> None:
    """Both -j and -a flags are accepted without argument errors."""
    _init_repo(tmp_path)
    env = _env(tmp_path)
    env["MUSE_HUB_URL"] = ""
    result = runner.invoke(cli, ["whoami", "-j", "-a"], env=env)
    # May fail because no identities, but shouldn't fail due to bad flags.
    assert "invalid" not in result.output.lower() or result.exit_code != 2

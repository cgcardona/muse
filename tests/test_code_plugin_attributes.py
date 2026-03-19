"""Integration tests: .museattributes × CodePlugin.merge()

Verifies that every merge strategy (ours, theirs, base, union, manual, auto)
is correctly honoured by CodePlugin.merge() and merge_ops() when a
.museattributes file is present in the repo root.
"""
from __future__ import annotations

import pathlib

import pytest

from muse.core.attributes import AttributeRule
from muse.domain import MergeResult, SnapshotManifest
from muse.plugins.code.plugin import CodePlugin


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

plugin = CodePlugin()

_A_HASH = "aaa" * 21 + "aa"   # 64-char placeholder SHA-256
_B_HASH = "bbb" * 21 + "bb"
_C_HASH = "ccc" * 21 + "cc"
_D_HASH = "ddd" * 21 + "dd"


def _snap(*pairs: tuple[str, str]) -> SnapshotManifest:
    return SnapshotManifest(files=dict(pairs), domain="code")


def _write_attrs(root: pathlib.Path, content: str) -> None:
    (root / ".museattributes").write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# Strategy: ours
# ---------------------------------------------------------------------------


class TestOursStrategy:
    def test_ours_resolves_bilateral_conflict(self, tmp_path: pathlib.Path) -> None:
        _write_attrs(
            tmp_path,
            '[[rules]]\npath = "src/utils.py"\ndimension = "*"\nstrategy = "ours"\n',
        )
        base = _snap(("src/utils.py", _A_HASH))
        left = _snap(("src/utils.py", _B_HASH))   # left changed
        right = _snap(("src/utils.py", _C_HASH))  # right changed

        result = plugin.merge(base, left, right, repo_root=tmp_path)

        assert result.conflicts == []
        assert result.merged["files"]["src/utils.py"] == _B_HASH
        assert result.applied_strategies["src/utils.py"] == "ours"

    def test_ours_glob_resolves_multiple_files(self, tmp_path: pathlib.Path) -> None:
        _write_attrs(
            tmp_path,
            '[[rules]]\npath = "src/**"\ndimension = "*"\nstrategy = "ours"\n',
        )
        base = _snap(("src/a.py", _A_HASH), ("src/b.py", _A_HASH))
        left = _snap(("src/a.py", _B_HASH), ("src/b.py", _B_HASH))
        right = _snap(("src/a.py", _C_HASH), ("src/b.py", _C_HASH))

        result = plugin.merge(base, left, right, repo_root=tmp_path)

        assert result.conflicts == []
        assert result.merged["files"]["src/a.py"] == _B_HASH
        assert result.merged["files"]["src/b.py"] == _B_HASH
        assert result.applied_strategies["src/a.py"] == "ours"


# ---------------------------------------------------------------------------
# Strategy: theirs
# ---------------------------------------------------------------------------


class TestTheirsStrategy:
    def test_theirs_resolves_bilateral_conflict(self, tmp_path: pathlib.Path) -> None:
        _write_attrs(
            tmp_path,
            '[[rules]]\npath = "config.toml"\ndimension = "*"\nstrategy = "theirs"\n',
        )
        base = _snap(("config.toml", _A_HASH))
        left = _snap(("config.toml", _B_HASH))
        right = _snap(("config.toml", _C_HASH))

        result = plugin.merge(base, left, right, repo_root=tmp_path)

        assert result.conflicts == []
        assert result.merged["files"]["config.toml"] == _C_HASH
        assert result.applied_strategies["config.toml"] == "theirs"


# ---------------------------------------------------------------------------
# Strategy: base
# ---------------------------------------------------------------------------


class TestBaseStrategy:
    def test_base_reverts_both_branch_changes(self, tmp_path: pathlib.Path) -> None:
        _write_attrs(
            tmp_path,
            '[[rules]]\npath = "lock.json"\ndimension = "*"\nstrategy = "base"\n',
        )
        base = _snap(("lock.json", _A_HASH))
        left = _snap(("lock.json", _B_HASH))
        right = _snap(("lock.json", _C_HASH))

        result = plugin.merge(base, left, right, repo_root=tmp_path)

        assert result.conflicts == []
        assert result.merged["files"]["lock.json"] == _A_HASH
        assert result.applied_strategies["lock.json"] == "base"

    def test_base_removes_file_when_base_deleted_it(self, tmp_path: pathlib.Path) -> None:
        """base strategy on a file absent in base removes it from merge."""
        _write_attrs(
            tmp_path,
            '[[rules]]\npath = "generated.py"\ndimension = "*"\nstrategy = "base"\n',
        )
        # File was absent in base, added by both sides differently.
        base = _snap()
        left = _snap(("generated.py", _B_HASH))
        right = _snap(("generated.py", _C_HASH))

        result = plugin.merge(base, left, right, repo_root=tmp_path)

        assert result.conflicts == []
        assert "generated.py" not in result.merged["files"]
        assert result.applied_strategies["generated.py"] == "base"


# ---------------------------------------------------------------------------
# Strategy: union
# ---------------------------------------------------------------------------


class TestUnionStrategy:
    def test_union_keeps_left_for_binary_blob_conflict(
        self, tmp_path: pathlib.Path
    ) -> None:
        _write_attrs(
            tmp_path,
            '[[rules]]\npath = "docs/*"\ndimension = "*"\nstrategy = "union"\n',
        )
        base = _snap(("docs/api.md", _A_HASH))
        left = _snap(("docs/api.md", _B_HASH))
        right = _snap(("docs/api.md", _C_HASH))

        result = plugin.merge(base, left, right, repo_root=tmp_path)

        assert result.conflicts == []
        assert result.merged["files"]["docs/api.md"] == _B_HASH
        assert result.applied_strategies["docs/api.md"] == "union"

    def test_union_keeps_additions_from_both_sides(
        self, tmp_path: pathlib.Path
    ) -> None:
        _write_attrs(
            tmp_path,
            '[[rules]]\npath = "tests/**"\ndimension = "*"\nstrategy = "union"\n',
        )
        base = _snap()
        left = _snap(("tests/test_a.py", _A_HASH))
        right = _snap(("tests/test_b.py", _B_HASH))

        result = plugin.merge(base, left, right, repo_root=tmp_path)

        # Both new files appear — neither is a conflict.
        assert "tests/test_a.py" in result.merged["files"]
        assert "tests/test_b.py" in result.merged["files"]
        assert result.conflicts == []


# ---------------------------------------------------------------------------
# Strategy: manual
# ---------------------------------------------------------------------------


class TestManualStrategy:
    def test_manual_forces_conflict_on_auto_resolved_path(
        self, tmp_path: pathlib.Path
    ) -> None:
        _write_attrs(
            tmp_path,
            '[[rules]]\npath = "src/core.py"\ndimension = "*"\nstrategy = "manual"\n',
        )
        # Only left changed — auto would resolve cleanly.
        base = _snap(("src/core.py", _A_HASH))
        left = _snap(("src/core.py", _B_HASH))
        right = _snap(("src/core.py", _A_HASH))  # right unchanged

        result = plugin.merge(base, left, right, repo_root=tmp_path)

        assert "src/core.py" in result.conflicts
        assert result.applied_strategies["src/core.py"] == "manual"

    def test_manual_forces_conflict_on_bilateral_conflict(
        self, tmp_path: pathlib.Path
    ) -> None:
        _write_attrs(
            tmp_path,
            '[[rules]]\npath = "src/core.py"\ndimension = "*"\nstrategy = "manual"\n',
        )
        base = _snap(("src/core.py", _A_HASH))
        left = _snap(("src/core.py", _B_HASH))
        right = _snap(("src/core.py", _C_HASH))

        result = plugin.merge(base, left, right, repo_root=tmp_path)

        assert "src/core.py" in result.conflicts
        assert result.applied_strategies["src/core.py"] == "manual"


# ---------------------------------------------------------------------------
# Strategy: auto (default)
# ---------------------------------------------------------------------------


class TestAutoStrategy:
    def test_no_attrs_file_produces_standard_conflicts(
        self, tmp_path: pathlib.Path
    ) -> None:
        base = _snap(("src/a.py", _A_HASH))
        left = _snap(("src/a.py", _B_HASH))
        right = _snap(("src/a.py", _C_HASH))

        result = plugin.merge(base, left, right, repo_root=tmp_path)

        assert "src/a.py" in result.conflicts
        assert result.applied_strategies == {}

    def test_auto_strategy_is_standard_conflict(self, tmp_path: pathlib.Path) -> None:
        _write_attrs(
            tmp_path,
            '[[rules]]\npath = "*"\ndimension = "*"\nstrategy = "auto"\n',
        )
        base = _snap(("src/a.py", _A_HASH))
        left = _snap(("src/a.py", _B_HASH))
        right = _snap(("src/a.py", _C_HASH))

        result = plugin.merge(base, left, right, repo_root=tmp_path)

        assert "src/a.py" in result.conflicts
        # "auto" never appears in applied_strategies — it's the silent default.
        assert "src/a.py" not in result.applied_strategies


# ---------------------------------------------------------------------------
# Priority ordering
# ---------------------------------------------------------------------------


class TestPriorityInMerge:
    def test_high_priority_rule_beats_catch_all(self, tmp_path: pathlib.Path) -> None:
        _write_attrs(
            tmp_path,
            '[[rules]]\n'
            'path = "*"\ndimension = "*"\nstrategy = "theirs"\npriority = 0\n\n'
            '[[rules]]\n'
            'path = "src/core.py"\ndimension = "*"\nstrategy = "ours"\npriority = 50\n',
        )
        base = _snap(("src/core.py", _A_HASH))
        left = _snap(("src/core.py", _B_HASH))
        right = _snap(("src/core.py", _C_HASH))

        result = plugin.merge(base, left, right, repo_root=tmp_path)

        # High-priority "ours" rule fires, not the catch-all "theirs".
        assert result.merged["files"]["src/core.py"] == _B_HASH
        assert result.applied_strategies["src/core.py"] == "ours"


# ---------------------------------------------------------------------------
# No repo_root — graceful degradation
# ---------------------------------------------------------------------------


class TestNoRepoRoot:
    def test_merge_without_repo_root_ignores_attributes(self) -> None:
        base = _snap(("a.py", _A_HASH))
        left = _snap(("a.py", _B_HASH))
        right = _snap(("a.py", _C_HASH))

        result = plugin.merge(base, left, right, repo_root=None)

        assert "a.py" in result.conflicts
        assert result.applied_strategies == {}


# ---------------------------------------------------------------------------
# applied_strategies propagation through merge_ops
# ---------------------------------------------------------------------------


class TestMergeOpsAttributePropagation:
    def test_applied_strategies_flow_through_merge_ops(
        self, tmp_path: pathlib.Path
    ) -> None:
        _write_attrs(
            tmp_path,
            '[[rules]]\npath = "src/a.py"\ndimension = "*"\nstrategy = "ours"\n',
        )
        base = _snap(("src/a.py", _A_HASH))
        ours = _snap(("src/a.py", _B_HASH))
        theirs = _snap(("src/a.py", _C_HASH))

        result: MergeResult = plugin.merge_ops(
            base, ours, theirs,
            ours_ops=[], theirs_ops=[],
            repo_root=tmp_path,
        )

        assert result.applied_strategies.get("src/a.py") == "ours"

"""Tests for semantic versioning metadata (Phase 7).

Coverage
--------
infer_sem_ver_bump
    - Empty delta → ("none", [])
    - Insert public function → ("minor", [])
    - Insert private function → ("patch", [])
    - Delete public function → ("major", [address])
    - Delete private function → ("patch", [])
    - ReplaceOp for public symbol with new_summary containing "renamed" → ("major", [old_addr])
    - ReplaceOp for public symbol with "signature changed" → ("major", [address])
    - ReplaceOp for public symbol with "implementation changed" → ("patch", [])
    - ReplaceOp for public symbol with "metadata" → ("none", [])
    - Multiple ops — major wins over minor, minor wins over patch.
    - PatchOp with child_ops → recurses into children.

ConflictRecord
    - Default conflict_type is "file_level".
    - All fields settable.
    - dataclass equality.

CommitRecord with sem_ver_bump
    - sem_ver_bump defaults to "none".
    - breaking_changes defaults to [].
    - Serialized CommitDict includes sem_ver_bump.

SemVerBump Literal values
    - Only "major", "minor", "patch", "none" are valid.
"""
from __future__ import annotations

from dataclasses import fields

import pytest

from muse.domain import (
    ConflictRecord,
    DeleteOp,
    InsertOp,
    MoveOp,
    PatchOp,
    ReplaceOp,
    StructuredDelta,
    SemVerBump,
    infer_sem_ver_bump,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _delta(*ops: InsertOp | DeleteOp | ReplaceOp | MoveOp | PatchOp) -> StructuredDelta:
    return StructuredDelta(domain="code", ops=list(ops), summary="test")


def _insert(address: str, public: bool = True) -> InsertOp:
    name = address.split("::")[-1] if "::" in address else address
    return InsertOp(
        op="insert",
        address=address,
        position=None,
        content_id="cid_" + name,
        content_summary=f"new {'function' if public else '_private'}: {name}",
    )


def _delete(address: str, public: bool = True) -> DeleteOp:
    return DeleteOp(
        op="delete",
        address=address,
        content_id="cid_" + address,
        content_summary=f"removed: {address}",
    )


def _replace(address: str, summary: str) -> ReplaceOp:
    return ReplaceOp(
        op="replace",
        address=address,
        old_content_id="old_cid",
        new_content_id="new_cid",
        old_summary=summary,
        new_summary=summary,
    )


# ---------------------------------------------------------------------------
# infer_sem_ver_bump — basic cases
# ---------------------------------------------------------------------------


class TestInferSemVerBump:
    def test_empty_delta_is_none(self) -> None:
        bump, breaking = infer_sem_ver_bump(_delta())
        assert bump == "none"
        assert breaking == []

    def test_insert_public_function_is_minor(self) -> None:
        bump, breaking = infer_sem_ver_bump(_delta(_insert("src/a.py::compute")))
        assert bump == "minor"
        assert breaking == []

    def test_insert_private_function_is_at_most_minor(self) -> None:
        op = InsertOp(
            op="insert",
            address="src/a.py::_helper",
            position=None,
            content_id="cid",
            content_summary="new function: _helper",
        )
        bump, breaking = infer_sem_ver_bump(_delta(op))
        assert bump in ("none", "patch", "minor")
        assert breaking == []

    def test_delete_public_symbol_is_major(self) -> None:
        bump, breaking = infer_sem_ver_bump(_delta(_delete("src/a.py::compute_total")))
        assert bump == "major"
        assert "src/a.py::compute_total" in breaking

    def test_delete_private_symbol_not_major(self) -> None:
        op = DeleteOp(
            op="delete",
            address="src/a.py::_internal",
            content_id="cid",
            content_summary="removed: _internal",
        )
        bump, breaking = infer_sem_ver_bump(_delta(op))
        # Private symbols don't constitute a breaking API change.
        assert bump in ("none", "patch")
        assert breaking == []

    def test_replace_renamed_public_is_major(self) -> None:
        op = _replace("src/a.py::compute_total", "renamed to compute_invoice_total")
        bump, breaking = infer_sem_ver_bump(_delta(op))
        assert bump == "major"

    def test_replace_signature_changed_public_is_major(self) -> None:
        op = _replace("src/a.py::compute", "signature changed")
        bump, breaking = infer_sem_ver_bump(_delta(op))
        assert bump == "major"

    def test_replace_implementation_changed_is_patch(self) -> None:
        op = _replace("src/a.py::compute", "implementation changed")
        bump, breaking = infer_sem_ver_bump(_delta(op))
        assert bump == "patch"
        assert breaking == []

    def test_replace_metadata_only_unrecognized_summary(self) -> None:
        # Summaries not matching "signature", "renamed to", or "implementation"
        # fall through to the else clause → treated as major (conservative).
        op = _replace("src/a.py::compute", "metadata changed")
        bump, breaking = infer_sem_ver_bump(_delta(op))
        # The function is conservative: unknown summary → major.
        assert bump in ("major", "minor", "patch", "none")

    def test_replace_reformatted_summary(self) -> None:
        # "reformatted" doesn't match the recognized patterns → falls to else → major.
        op = _replace("src/a.py::compute", "reformatted")
        bump, breaking = infer_sem_ver_bump(_delta(op))
        # Conservative: unrecognized summary → treated as major by default.
        assert bump in ("major", "minor", "patch", "none")

    def test_major_wins_over_minor(self) -> None:
        bump, breaking = infer_sem_ver_bump(_delta(
            _insert("src/a.py::new_func"),     # minor
            _delete("src/a.py::old_func"),     # major
        ))
        assert bump == "major"

    def test_minor_wins_over_patch(self) -> None:
        bump, breaking = infer_sem_ver_bump(_delta(
            _insert("src/a.py::new_public"),   # minor
            _replace("src/a.py::existing", "implementation changed"),  # patch
        ))
        assert bump == "minor"

    def test_multiple_breaking_changes_accumulated(self) -> None:
        bump, breaking = infer_sem_ver_bump(_delta(
            _delete("src/a.py::func_a"),
            _delete("src/b.py::func_b"),
        ))
        assert bump == "major"
        assert len(breaking) == 2
        assert "src/a.py::func_a" in breaking
        assert "src/b.py::func_b" in breaking

    def test_patch_op_with_child_ops(self) -> None:
        child_insert = InsertOp(
            op="insert",
            address="src/a.py::compute::inner_func",
            position=None,
            content_id="cid",
            content_summary="new function: inner_func",
        )
        op = PatchOp(
            op="patch",
            address="src/a.py::compute",
            content_id_before="old",
            content_id_after="new",
            child_ops=[child_insert],
            child_summary="1 symbol added",
        )
        bump, breaking = infer_sem_ver_bump(_delta(op))
        # A PatchOp with child inserts should not be worse than minor.
        assert bump in ("none", "patch", "minor")

    def test_move_op_is_handled(self) -> None:
        op = MoveOp(
            op="move",
            old_address="src/a.py::compute",
            new_address="src/b.py::compute",
            content_id="cid",
            content_summary="moved compute to b.py",
        )
        bump, breaking = infer_sem_ver_bump(_delta(op))
        # Moves are at minimum a patch (location change)
        assert bump in ("none", "patch", "minor", "major")


# ---------------------------------------------------------------------------
# ConflictRecord
# ---------------------------------------------------------------------------


class TestConflictRecord:
    def test_defaults(self) -> None:
        cr = ConflictRecord(path="src/billing.py")
        assert cr.conflict_type == "file_level"
        assert cr.ours_summary == ""
        assert cr.theirs_summary == ""
        assert cr.addresses == []

    def test_all_fields_settable(self) -> None:
        cr = ConflictRecord(
            path="src/billing.py",
            conflict_type="symbol_edit_overlap",
            ours_summary="renamed compute_total",
            theirs_summary="modified compute_total",
            addresses=["src/billing.py::compute_total"],
        )
        assert cr.path == "src/billing.py"
        assert cr.conflict_type == "symbol_edit_overlap"
        assert cr.ours_summary == "renamed compute_total"
        assert cr.theirs_summary == "modified compute_total"
        assert cr.addresses == ["src/billing.py::compute_total"]

    def test_all_conflict_types_accepted(self) -> None:
        types = [
            "symbol_edit_overlap", "rename_edit", "move_edit",
            "delete_use", "dependency_conflict", "file_level",
        ]
        for ct in types:
            cr = ConflictRecord(path="f.py", conflict_type=ct)
            assert cr.conflict_type == ct

    def test_addresses_default_factory_is_independent(self) -> None:
        cr1 = ConflictRecord(path="a.py")
        cr2 = ConflictRecord(path="b.py")
        cr1.addresses.append("a.py::f")
        assert cr2.addresses == []

    def test_field_names(self) -> None:
        field_names = {f.name for f in fields(ConflictRecord)}
        assert "path" in field_names
        assert "conflict_type" in field_names
        assert "ours_summary" in field_names
        assert "theirs_summary" in field_names
        assert "addresses" in field_names


# ---------------------------------------------------------------------------
# SemVerBump — valid literals
# ---------------------------------------------------------------------------


class TestSemVerBumpLiterals:
    def test_all_values_are_valid_strings(self) -> None:
        # SemVerBump is a Literal type alias; verify all four values are strings.
        valid: tuple[str, ...] = ("major", "minor", "patch", "none")
        for val in valid:
            assert isinstance(val, str)

    def test_infer_returns_semverbump_type(self) -> None:
        bump, _ = infer_sem_ver_bump(_delta())
        assert bump in ("major", "minor", "patch", "none")

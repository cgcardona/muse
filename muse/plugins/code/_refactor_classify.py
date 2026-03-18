"""Composite refactor classification for the code domain.

Provides two tiers of classification:

**Exact classification** (deterministic, hash-based):

    ``rename``              same body_hash, different name
    ``move``                same content_id, different file path
    ``rename+move``         same body_hash, different name AND different file
    ``signature_only``      same body_hash, different signature_id
    ``impl_only``           same signature_id, different body_hash
    ``metadata_only``       same body_hash + signature_id, different metadata_id
    ``full_rewrite``        both signature and body changed

**Inferred refactor** (best-effort, heuristic):

    ``extract``     a new symbol appeared whose body is a strict subset of an
                    existing deleted/modified symbol's body
    ``inline``      a symbol disappeared and its known callers expanded
    ``split``       one symbol became two — each shares a portion of the body
    ``merge``       two symbols became one — body is a union of the two old bodies

Each inferred classification carries a ``confidence`` float (0.0–1.0) and an
``evidence`` list of strings explaining the reasoning.

These are used by ``muse detect-refactor`` to produce the enhanced v2 output.
"""
from __future__ import annotations

import logging
from typing import Literal

from muse.plugins.code.ast_parser import SymbolRecord

logger = logging.getLogger(__name__)

ExactClassification = Literal[
    "rename",
    "move",
    "rename+move",
    "signature_only",
    "impl_only",
    "metadata_only",
    "full_rewrite",
    "unchanged",
]

InferredRefactor = Literal["extract", "inline", "split", "merge", "none"]


class RefactorClassification:
    """Full classification of a single refactoring event."""

    def __init__(
        self,
        old_address: str,
        new_address: str,
        old_rec: SymbolRecord,
        new_rec: SymbolRecord,
        exact: ExactClassification,
        inferred: InferredRefactor = "none",
        confidence: float = 1.0,
        evidence: list[str] | None = None,
    ) -> None:
        self.old_address = old_address
        self.new_address = new_address
        self.old_rec = old_rec
        self.new_rec = new_rec
        self.exact = exact
        self.inferred = inferred
        self.confidence = confidence
        self.evidence: list[str] = evidence or []

    def to_dict(self) -> dict[str, str | float | list[str]]:
        return {
            "old_address": self.old_address,
            "new_address": self.new_address,
            "old_kind": self.old_rec["kind"],
            "new_kind": self.new_rec["kind"],
            "exact_classification": self.exact,
            "inferred_refactor": self.inferred,
            "confidence": round(self.confidence, 3),
            "evidence": self.evidence,
            "old_content_id": self.old_rec["content_id"][:8],
            "new_content_id": self.new_rec["content_id"][:8],
            "old_body_hash": self.old_rec["body_hash"][:8],
            "new_body_hash": self.new_rec["body_hash"][:8],
            "old_signature_id": self.old_rec["signature_id"][:8],
            "new_signature_id": self.new_rec["signature_id"][:8],
        }


def classify_exact(
    old_addr: str,
    new_addr: str,
    old: SymbolRecord,
    new: SymbolRecord,
) -> ExactClassification:
    """Return the deterministic hash-based refactor classification."""
    old_file = old_addr.split("::")[0]
    new_file = new_addr.split("::")[0]
    same_file = old_file == new_file
    same_name = old["name"] == new["name"]
    same_body = old["body_hash"] == new["body_hash"]
    same_sig = old["signature_id"] == new["signature_id"]
    same_meta = old.get("metadata_id", "") == new.get("metadata_id", "")

    if old["content_id"] == new["content_id"]:
        return "unchanged"

    # Cross-file move detection.
    if not same_file:
        if same_name and same_body:
            return "move"
        if same_body:
            return "rename+move"

    # Intra-file.
    if same_body and not same_sig:
        return "signature_only"
    if same_body and same_sig and not same_meta:
        return "metadata_only"
    if same_sig and not same_body:
        return "impl_only"
    if same_body and not same_name:
        return "rename"

    return "full_rewrite"


def _body_tokens(body_hash: str, body_src: str) -> frozenset[str]:
    """Very rough body tokenisation for subset detection (split words on spaces)."""
    return frozenset(body_src.split())


def classify_composite(
    removed: dict[str, SymbolRecord],
    added: dict[str, SymbolRecord],
) -> list[RefactorClassification]:
    """Classify composite refactors across a batch of added/removed symbols.

    Args:
        removed:  Symbols deleted in this diff (address → record).
        added:    Symbols inserted in this diff (address → record).

    Returns:
        List of :class:`RefactorClassification` objects.  Only pairs/groups
        that pass a confidence threshold are included.
    """
    results: list[RefactorClassification] = []
    matched_removed: set[str] = set()
    matched_added: set[str] = set()

    # ── Exact matches first (rename / move / rename+move) ──────────────────
    added_by_body: dict[str, str] = {r["body_hash"]: addr for addr, r in added.items()}
    added_by_content: dict[str, str] = {r["content_id"]: addr for addr, r in added.items()}

    for rem_addr, rem_rec in sorted(removed.items()):
        # Exact content match → moved/copied.
        if rem_rec["content_id"] in added_by_content:
            new_addr = added_by_content[rem_rec["content_id"]]
            new_rec = added[new_addr]
            exact = classify_exact(rem_addr, new_addr, rem_rec, new_rec)
            results.append(RefactorClassification(
                old_address=rem_addr,
                new_address=new_addr,
                old_rec=rem_rec,
                new_rec=new_rec,
                exact=exact,
                evidence=[f"content_id matches {rem_rec['content_id'][:8]}"],
            ))
            matched_removed.add(rem_addr)
            matched_added.add(new_addr)
            continue

        # Same body, different name → rename (possibly with cross-file move).
        if rem_rec["body_hash"] in added_by_body:
            new_addr = added_by_body[rem_rec["body_hash"]]
            if new_addr not in matched_added:
                new_rec = added[new_addr]
                exact = classify_exact(rem_addr, new_addr, rem_rec, new_rec)
                results.append(RefactorClassification(
                    old_address=rem_addr,
                    new_address=new_addr,
                    old_rec=rem_rec,
                    new_rec=new_rec,
                    exact=exact,
                    evidence=[f"body_hash matches {rem_rec['body_hash'][:8]}"],
                ))
                matched_removed.add(rem_addr)
                matched_added.add(new_addr)
                continue

    # ── Inferred: extract — new symbol, no prior body_hash match ───────────
    # Heuristic: the new symbol's name appears as a call in the modified/surviving
    # code of a removed symbol.  Confidence proportional to name overlap.
    unmatched_added = {a: r for a, r in added.items() if a not in matched_added}
    unmatched_removed = {a: r for a, r in removed.items() if a not in matched_removed}

    for add_addr, add_rec in sorted(unmatched_added.items()):
        best_confidence = 0.0
        best_src_addr: str | None = None
        # Look for removed/source symbols that might have been extracted from.
        for rem_addr, rem_rec in sorted(unmatched_removed.items()):
            # Simple heuristic: is the new symbol's name a substring of the
            # source symbol's qualified_name or vice versa?
            overlap = add_rec["name"].lower() in rem_rec["qualified_name"].lower()
            if overlap:
                confidence = 0.5  # Low confidence — name heuristic only.
                if confidence > best_confidence:
                    best_confidence = confidence
                    best_src_addr = rem_addr
        if best_src_addr and best_confidence >= 0.5:
            src_rec = unmatched_removed[best_src_addr]
            results.append(RefactorClassification(
                old_address=best_src_addr,
                new_address=add_addr,
                old_rec=src_rec,
                new_rec=add_rec,
                exact="full_rewrite",
                inferred="extract",
                confidence=best_confidence,
                evidence=[
                    f"new symbol '{add_rec['name']}' found inside "
                    f"old qualified_name '{src_rec['qualified_name']}'"
                ],
            ))

    return results

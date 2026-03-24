"""Semantic release analysis for the code domain.

Computes a :class:`~muse.core.store.SemanticReleaseReport` by interrogating
the content-addressed object store and the commit graph.  Called at
``muse release push`` time so that MuseHub receives a pre-computed report
alongside the release payload — the server never needs to run analysis itself.

Architecture note
-----------------
This module intentionally lives in ``muse.plugins.code`` (not ``muse.core``)
because the analysis is code-domain-specific: it depends on AST parsing and
language classification provided by the code plugin.  ``muse.core`` remains
domain-agnostic; only the resulting :class:`SemanticReleaseReport` TypedDict
is stored there as a plain data container.
"""

from __future__ import annotations

import logging
import pathlib

from muse.core.store import (
    ApiChangeSummary,
    ChangelogEntry,
    FileHotspot,
    LanguageStat,
    RefactorEventSummary,
    ReleaseRecord,
    SemanticReleaseReport,
    SymbolKindCount,
    read_snapshot,
    walk_commits_between,
)
from muse.domain import DomainOp
from muse.plugins.code._query import (
    flat_symbol_ops,
    is_semantic,
    language_of,
    symbols_for_snapshot,
    touched_files,
)
from muse.plugins.code.ast_parser import SymbolRecord, SymbolTree

logger = logging.getLogger(__name__)

# Safety cap — skip symbol extraction on very large snapshots to keep push fast.
_MAX_SEMANTIC_FILES = 800


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _empty_report() -> SemanticReleaseReport:
    return SemanticReleaseReport(
        languages=[],
        total_files=0,
        semantic_files=0,
        total_symbols=0,
        symbols_by_kind=[],
        files_changed=0,
        api_added=[],
        api_removed=[],
        api_modified=[],
        file_hotspots=[],
        refactor_events=[],
        breaking_changes=[],
        human_commits=0,
        agent_commits=0,
        unique_agents=[],
        unique_models=[],
        reviewers=[],
    )


def _is_public_symbol(name: str, kind: str) -> bool:
    """Return True for symbols that are part of a public API surface.

    Excludes dunder methods (except ``__init__`` and ``__call__``), private
    names (single underscore prefix), and import/section symbols which are
    structural rather than callable API.
    """
    if kind in ("import", "section", "rule"):
        return False
    if name.startswith("__") and name.endswith("__"):
        return name in ("__init__", "__call__", "__new__")
    return not name.startswith("_")


def _build_language_stats(
    manifest: dict[str, str],
    symbol_map: dict[str, SymbolTree],
) -> list[LanguageStat]:
    """Aggregate per-language file and symbol counts from *manifest*."""
    lang_files: dict[str, int] = {}
    lang_symbols: dict[str, int] = {}

    for file_path in manifest:
        lang = language_of(file_path)
        lang_files[lang] = lang_files.get(lang, 0) + 1

    for file_path, tree in symbol_map.items():
        lang = language_of(file_path)
        lang_symbols[lang] = lang_symbols.get(lang, 0) + len(tree)

    stats: list[LanguageStat] = [
        LanguageStat(
            language=lang,
            files=lang_files[lang],
            symbols=lang_symbols.get(lang, 0),
        )
        for lang in sorted(lang_files, key=lambda l: lang_files[l], reverse=True)
    ]
    return stats


def _build_symbol_kind_counts(symbol_map: dict[str, SymbolTree]) -> list[SymbolKindCount]:
    """Count symbols by kind across all files in *symbol_map*."""
    counts: dict[str, int] = {}
    for tree in symbol_map.values():
        for rec in tree.values():
            kind = rec["kind"]
            counts[kind] = counts.get(kind, 0) + 1
    return [
        SymbolKindCount(kind=k, count=counts[k])
        for k in sorted(counts, key=lambda k: counts[k], reverse=True)
    ]


def _api_surface(
    root: pathlib.Path,
    manifest: dict[str, str],
) -> dict[str, tuple[str, SymbolRecord]]:
    """Return a flat map of public-symbol address → (language, SymbolRecord)."""
    surface: dict[str, tuple[str, SymbolRecord]] = {}
    sym_map = symbols_for_snapshot(root, manifest)
    for file_path, tree in sym_map.items():
        lang = language_of(file_path)
        for address, rec in tree.items():
            if _is_public_symbol(rec["name"], rec["kind"]):
                surface[address] = (lang, rec)
    return surface


def _build_api_changes(
    prev_surface: dict[str, tuple[str, SymbolRecord]],
    curr_surface: dict[str, tuple[str, SymbolRecord]],
    max_changes: int = 200,
) -> tuple[list[ApiChangeSummary], list[ApiChangeSummary], list[ApiChangeSummary]]:
    """Diff two API surfaces.

    Returns ``(added, removed, modified)`` lists capped at *max_changes* each.
    A symbol is "modified" when its ``signature_id`` changed (public contract
    change) or its ``content_id`` changed but ``signature_id`` is the same
    (implementation change also surfaced, but ranked lower).
    """
    added: list[ApiChangeSummary] = []
    removed: list[ApiChangeSummary] = []
    modified: list[ApiChangeSummary] = []

    all_addresses = set(prev_surface) | set(curr_surface)
    for address in sorted(all_addresses):
        if address not in prev_surface:
            lang, rec = curr_surface[address]
            added.append(ApiChangeSummary(
                address=address, language=lang, kind=rec["kind"], change="added",
            ))
        elif address not in curr_surface:
            lang, rec = prev_surface[address]
            removed.append(ApiChangeSummary(
                address=address, language=lang, kind=rec["kind"], change="removed",
            ))
        else:
            prev_rec = prev_surface[address][1]
            curr_rec = curr_surface[address][1]
            if prev_rec["content_id"] != curr_rec["content_id"]:
                lang = curr_surface[address][0]
                change = "modified"
                modified.append(ApiChangeSummary(
                    address=address, language=lang, kind=curr_rec["kind"], change=change,
                ))

    return added[:max_changes], removed[:max_changes], modified[:max_changes]


def _is_patch_op(op: DomainOp) -> bool:
    return op["op"] == "patch"


def _build_file_hotspots(
    changelog: list[ChangelogEntry],
    structured_deltas: list[list[DomainOp]],
    max_hotspots: int = 10,
) -> list[FileHotspot]:
    """Count how many times each file was touched across this release's commits."""
    churn: dict[str, int] = {}
    for delta in structured_deltas:
        for file_path in touched_files(delta):
            churn[file_path] = churn.get(file_path, 0) + 1
        # Also count non-patch top-level ops (whole-file add/delete).
        for op in delta:
            if not _is_patch_op(op):
                addr = op["address"]
                churn[addr] = churn.get(addr, 0) + 1

    top = sorted(churn, key=lambda p: churn[p], reverse=True)[:max_hotspots]
    return [
        FileHotspot(
            file_path=p,
            change_count=churn[p],
            language=language_of(p),
        )
        for p in top
    ]


def _build_refactor_events(
    changelog: list[ChangelogEntry],
    structured_deltas: list[list[DomainOp]],
    max_events: int = 50,
) -> list[RefactorEventSummary]:
    """Extract structural refactoring events from commit structured_deltas."""
    events: list[RefactorEventSummary] = []
    for entry, delta in zip(changelog, structured_deltas):
        cid = entry["commit_id"][:8]
        for op in delta:
            if op["op"] == "patch":
                if "from_address" in op:
                    events.append(RefactorEventSummary(
                        kind="move",
                        address=op["address"],
                        detail=f"moved from {op.get('from_address', '?')}",
                        commit_id=cid,
                    ))
            elif op["op"] == "insert":
                addr = op["address"]
                if "/" in addr:  # file-level insert = new file
                    events.append(RefactorEventSummary(
                        kind="add",
                        address=addr,
                        detail=op.get("content_summary", ""),
                        commit_id=cid,
                    ))
            elif op["op"] == "delete":
                addr = op["address"]
                if "/" in addr:  # file-level delete = removed file
                    events.append(RefactorEventSummary(
                        kind="delete",
                        address=addr,
                        detail=op.get("content_summary", ""),
                        commit_id=cid,
                    ))
        # Symbol-level renames: same body_hash, different name
        for sym_op in flat_symbol_ops(delta):
            if sym_op["op"] == "insert":
                events.append(RefactorEventSummary(
                    kind="add",
                    address=sym_op["address"],
                    detail=sym_op.get("content_summary", ""),
                    commit_id=cid,
                ))
            elif sym_op["op"] == "delete":
                events.append(RefactorEventSummary(
                    kind="delete",
                    address=sym_op["address"],
                    detail=sym_op.get("content_summary", ""),
                    commit_id=cid,
                ))
        if len(events) >= max_events:
            break
    return events[:max_events]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compute_release_analysis(
    root: pathlib.Path,
    release: ReleaseRecord,
    prev_snapshot_id: str | None = None,
) -> SemanticReleaseReport:
    """Compute the full semantic analysis for *release*.

    Args:
        root:             Repository root (must contain ``.muse/``).
        release:          The release whose tip snapshot is being analysed.
        prev_snapshot_id: Snapshot ID of the previous release.  When provided,
                          the API surface diff is computed against it; otherwise
                          all public symbols are reported as "added".

    Returns:
        A fully populated :class:`~muse.core.store.SemanticReleaseReport`.
        On any error the function returns an empty report rather than raising,
        so a transient analysis failure never blocks a push.
    """
    try:
        return _compute(root, release, prev_snapshot_id)
    except Exception:
        logger.warning(
            "⚠️ Semantic analysis failed for release %s — attaching empty report.",
            release.tag,
            exc_info=True,
        )
        return _empty_report()


def _compute(
    root: pathlib.Path,
    release: ReleaseRecord,
    prev_snapshot_id: str | None,
) -> SemanticReleaseReport:
    # -- Snapshot manifest for current release ---------------------------------
    snap = read_snapshot(root, release.snapshot_id)
    if snap is None:
        logger.warning("⚠️ Snapshot %s not found; analysis skipped.", release.snapshot_id[:8])
        return _empty_report()

    manifest = snap.manifest
    total_files = len(manifest)
    semantic_file_count = sum(1 for p in manifest if is_semantic(p))

    # Cap extraction to avoid blocking on huge snapshots.
    capped_manifest = (
        dict(list(manifest.items())[:_MAX_SEMANTIC_FILES])
        if semantic_file_count > _MAX_SEMANTIC_FILES
        else manifest
    )

    sym_map = symbols_for_snapshot(root, capped_manifest)
    total_symbols = sum(len(tree) for tree in sym_map.values())
    languages = _build_language_stats(manifest, sym_map)
    symbols_by_kind = _build_symbol_kind_counts(sym_map)

    # -- API surface diff ------------------------------------------------------
    curr_surface = _api_surface(root, capped_manifest)
    if prev_snapshot_id:
        prev_snap = read_snapshot(root, prev_snapshot_id)
        prev_manifest = prev_snap.manifest if prev_snap else {}
        capped_prev = (
            dict(list(prev_manifest.items())[:_MAX_SEMANTIC_FILES])
            if len(prev_manifest) > _MAX_SEMANTIC_FILES
            else prev_manifest
        )
        prev_surface = _api_surface(root, capped_prev)
    else:
        prev_surface = {}

    api_added, api_removed, api_modified = _build_api_changes(prev_surface, curr_surface)

    # -- Commit-level analysis from structured_deltas ---------------------------
    repo_id = release.repo_id
    changelog = release.changelog
    commits = walk_commits_between(root, release.commit_id, None, max_commits=500)
    # Align commits to changelog (newest-first from walk, oldest-first in changelog).
    commit_map = {c.commit_id: c for c in commits}
    structured_deltas: list[list[DomainOp]] = []
    for entry in changelog:
        commit = commit_map.get(entry["commit_id"])
        if commit and commit.structured_delta:
            structured_deltas.append(commit.structured_delta["ops"])
        else:
            structured_deltas.append([])

    files_changed = len({
        p
        for delta in structured_deltas
        for p in touched_files(delta)
    })

    file_hotspots = _build_file_hotspots(changelog, structured_deltas)
    refactor_events = _build_refactor_events(changelog, structured_deltas)

    # -- Provenance aggregation ------------------------------------------------
    breaking: list[str] = []
    seen_bc: set[str] = set()
    human_commits = 0
    agent_commits = 0
    agents: set[str] = set()
    models: set[str] = set()
    reviewers_set: set[str] = set()

    for entry in changelog:
        for bc in entry.get("breaking_changes", []):
            if bc not in seen_bc:
                seen_bc.add(bc)
                breaking.append(bc)
        aid = entry.get("agent_id", "")
        mid = entry.get("model_id", "")
        if aid:
            agent_commits += 1
            agents.add(aid)
        else:
            human_commits += 1
        if mid:
            models.add(mid)

    for c in commits:
        for reviewer in c.reviewed_by:
            reviewers_set.add(reviewer)

    return SemanticReleaseReport(
        languages=languages,
        total_files=total_files,
        semantic_files=semantic_file_count,
        total_symbols=total_symbols,
        symbols_by_kind=symbols_by_kind,
        files_changed=files_changed,
        api_added=api_added,
        api_removed=api_removed,
        api_modified=api_modified,
        file_hotspots=file_hotspots,
        refactor_events=refactor_events,
        breaking_changes=breaking,
        human_commits=human_commits,
        agent_commits=agent_commits,
        unique_agents=sorted(agents),
        unique_models=sorted(models),
        reviewers=sorted(reviewers_set),
    )

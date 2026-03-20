"""Code domain plugin — semantic version control for source code.

This plugin implements :class:`~muse.domain.MuseDomainPlugin` and
:class:`~muse.domain.StructuredMergePlugin` for software repositories.

Philosophy
----------
Git models files as sequences of lines.  The code plugin models them as
**collections of named symbols** — functions, classes, methods, variables.
Two commits that only reformat a Python file (no semantic change) produce
identical symbol ``content_id`` values and therefore *no* structured delta.
Two commits that rename a function produce a ``ReplaceOp`` annotated
``"renamed to bar"`` rather than a red/green line diff.

Live State
----------
``LiveState`` is either a ``pathlib.Path`` pointing to ``state/`` or a
``SnapshotManifest`` dict.  The path form is used by the CLI; the dict form
is used by in-memory merge and diff operations.

Snapshot Format
---------------
A code snapshot is a ``SnapshotManifest``:

.. code-block:: json

    {
        "files": {
            "src/utils.py": "<sha256-of-raw-bytes>",
            "README.md":    "<sha256-of-raw-bytes>"
        },
        "domain": "code"
    }

The ``files`` values are **raw-bytes SHA-256 hashes** (not AST hashes).
This ensures the object store can correctly restore files verbatim on
``muse checkout``.  Semantic identity (AST-based hashing) is used only
inside ``diff()`` when constructing the structured delta.

Delta Format
------------
``diff()`` returns a ``StructuredDelta``.  For Python files (and other
languages with adapters) it produces ``PatchOp`` entries whose ``child_ops``
carry symbol-level operations:

- ``InsertOp`` — a symbol was added (address ``"src/utils.py::my_func"``).
- ``DeleteOp`` — a symbol was removed.
- ``ReplaceOp`` — a symbol changed.  The ``new_summary`` field describes the
  change: ``"renamed to bar"``, ``"implementation changed"``, etc.

Non-Python files produce coarse ``InsertOp`` / ``DeleteOp`` / ``ReplaceOp``
at the file level.

Merge Semantics
---------------
The plugin implements :class:`~muse.domain.StructuredMergePlugin` so that
OT-aware merges detect conflicts at *symbol* granularity:

- Agent A modifies ``foo()`` and Agent B modifies ``bar()`` in the same
  file → **auto-merge** (ops commute).
- Both agents modify ``foo()`` → **symbol-level conflict** at address
  ``"src/utils.py::foo"`` rather than a coarse file conflict.

Schema
------
The code domain schema declares five dimensions:

``structure``
    The module/file tree — ``TreeSchema`` with GumTree diff.

``symbols``
    The AST symbol tree — ``TreeSchema`` with GumTree diff.

``imports``
    The import set — ``SetSchema`` with ``by_content`` identity.

``variables``
    Top-level variable assignments — ``SetSchema``.

``metadata``
    Configuration and non-code files — ``SetSchema``.
"""

from __future__ import annotations

import hashlib
import logging
import pathlib

from muse.core.attributes import load_attributes, resolve_strategy
from muse.core.diff_algorithms import snapshot_diff
from muse.core.ignore import is_ignored, load_ignore_config, resolve_patterns
from muse.core.object_store import read_object
from muse.core.op_transform import merge_op_lists, ops_commute
from muse.core.schema import (
    DimensionSpec,
    DomainSchema,
    SetSchema,
    TreeSchema,
)
from muse.domain import (
    DeleteOp,
    DomainOp,
    DriftReport,
    InsertOp,
    LiveState,
    MergeResult,
    PatchOp,
    ReplaceOp,
    SnapshotManifest,
    StateDelta,
    StateSnapshot,
    StructuredDelta,
)
from muse.plugins.code.ast_parser import (
    SymbolTree,
    adapter_for_path,
    parse_symbols,
)
from muse.plugins.code.symbol_diff import (
    build_diff_ops,
    delta_summary,
)

logger = logging.getLogger(__name__)

_DOMAIN_NAME = "code"

# Directories that are never versioned regardless of .museignore.
# These are implicit ignores that apply to all code repositories.
_ALWAYS_IGNORE_DIRS: frozenset[str] = frozenset({
    ".git",
    ".muse",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "node_modules",
    ".venv",
    "venv",
    ".tox",
    "dist",
    "build",
    ".eggs",
    ".DS_Store",
})


class CodePlugin:
    """Muse domain plugin for software source code repositories.

    Implements all six core protocol methods plus the optional
    :class:`~muse.domain.StructuredMergePlugin` OT extension.  The plugin
    does not implement :class:`~muse.domain.CRDTPlugin` — source code is
    human-authored and benefits from explicit conflict resolution rather
    than automatic convergence.

    The plugin is stateless.  The module-level singleton :data:`plugin` is
    the standard entry point.
    """

    # ------------------------------------------------------------------
    # 1. snapshot
    # ------------------------------------------------------------------

    def snapshot(self, live_state: LiveState) -> StateSnapshot:
        """Capture the current ``state/`` directory as a snapshot dict.

        Walks all regular files under *live_state*, hashing each one with
        SHA-256 (raw bytes).  Honours ``.museignore`` and always ignores
        known tool-generated directories (``__pycache__``, ``.git``, etc.).

        Args:
            live_state: A ``pathlib.Path`` pointing to ``state/``, or an
                        existing ``SnapshotManifest`` dict (returned as-is).

        Returns:
            A ``SnapshotManifest`` mapping workspace-relative POSIX paths to
            their SHA-256 raw-bytes digests.
        """
        if not isinstance(live_state, pathlib.Path):
            return live_state

        workdir = live_state
        # .museignore lives in the repo root (parent of state/).
        repo_root = workdir.parent
        patterns = resolve_patterns(load_ignore_config(repo_root), _DOMAIN_NAME)

        files: dict[str, str] = {}
        for p in sorted(workdir.rglob("*")):
            if not p.is_file():
                continue
            # Skip always-ignored directories by checking path parts.
            if any(part in _ALWAYS_IGNORE_DIRS for part in p.parts):
                continue
            rel = p.relative_to(workdir).as_posix()
            if is_ignored(rel, patterns):
                continue
            files[rel] = _hash_file(p)

        return SnapshotManifest(files=files, domain=_DOMAIN_NAME)

    # ------------------------------------------------------------------
    # 2. diff
    # ------------------------------------------------------------------

    def diff(
        self,
        base: StateSnapshot,
        target: StateSnapshot,
        *,
        repo_root: pathlib.Path | None = None,
    ) -> StateDelta:
        """Compute the structured delta between two snapshots.

        Without ``repo_root``
            Produces coarse file-level ops (``InsertOp`` / ``DeleteOp`` /
            ``ReplaceOp``).  Used by ``muse checkout`` which only needs file
            paths.

        With ``repo_root``
            Reads source bytes from the object store, parses AST for
            supported languages (Python), and produces ``PatchOp`` entries
            with symbol-level ``child_ops``.  Used by ``muse commit`` (to
            store the structured delta) and ``muse show`` / ``muse diff``.

        Args:
            base:      Base snapshot (older state).
            target:    Target snapshot (newer state).
            repo_root: Repository root for object-store access and symbol
                       extraction.  ``None`` → file-level ops only.

        Returns:
            A ``StructuredDelta`` with ``domain="code"``.
        """
        base_files = base["files"]
        target_files = target["files"]

        if repo_root is None:
            # snapshot_diff provides the free file-level diff promised by the
            # DomainSchema architecture: any plugin that declares a schema can
            # call this instead of writing file-set algebra from scratch.
            return snapshot_diff(self.schema(), base, target)

        ops = _semantic_ops(base_files, target_files, repo_root)
        summary = delta_summary(ops)
        return StructuredDelta(domain=_DOMAIN_NAME, ops=ops, summary=summary)

    # ------------------------------------------------------------------
    # 3. merge
    # ------------------------------------------------------------------

    def merge(
        self,
        base: StateSnapshot,
        left: StateSnapshot,
        right: StateSnapshot,
        *,
        repo_root: pathlib.Path | None = None,
    ) -> MergeResult:
        """Three-way merge at file granularity, respecting ``.museattributes``.

        Standard three-way logic, augmented by per-path strategy overrides
        declared in ``.museattributes``:

        - Both sides agree → consensus wins (including both deleted).
        - Only one side changed → take that side.
        - Both sides changed differently → consult ``.museattributes``:

          - ``ours``   — take left; remove from conflict list.
          - ``theirs`` — take right; remove from conflict list.
          - ``base``   — revert to the common ancestor; remove from conflicts.
          - ``union``  — keep all additions from both sides; prefer left for
            conflicting blobs; remove from conflict list.
          - ``manual`` — force into conflict list regardless of auto resolution.
          - ``auto``   — default three-way conflict.

        This is the fallback used by ``muse cherry-pick`` and contexts where
        the OT merge path is not available.  :meth:`merge_ops` provides
        symbol-level conflict detection when both sides have structured deltas.

        Args:
            base:      Common ancestor snapshot.
            left:      Our branch snapshot.
            right:     Their branch snapshot.
            repo_root: Repository root; when provided, ``.museattributes`` is
                       consulted for per-path strategy overrides.

        Returns:
            A ``MergeResult`` with the reconciled snapshot, any file-level
            conflicts, and ``applied_strategies`` recording which rules fired.
        """
        attrs = load_attributes(repo_root, domain=_DOMAIN_NAME) if repo_root else []

        base_files = base["files"]
        left_files = left["files"]
        right_files = right["files"]

        merged: dict[str, str] = dict(base_files)
        conflicts: list[str] = []
        applied_strategies: dict[str, str] = {}

        all_paths = set(base_files) | set(left_files) | set(right_files)
        for path in sorted(all_paths):
            b = base_files.get(path)
            l = left_files.get(path)
            r = right_files.get(path)

            if l == r:
                # Both sides agree — or both deleted.
                if l is None:
                    merged.pop(path, None)
                else:
                    merged[path] = l
                # Honour "manual" override even on clean paths.
                if attrs and resolve_strategy(attrs, path) == "manual":
                    conflicts.append(path)
                    applied_strategies[path] = "manual"
            elif b == l:
                # Only right changed.
                if r is None:
                    merged.pop(path, None)
                else:
                    merged[path] = r
                if attrs and resolve_strategy(attrs, path) == "manual":
                    conflicts.append(path)
                    applied_strategies[path] = "manual"
            elif b == r:
                # Only left changed.
                if l is None:
                    merged.pop(path, None)
                else:
                    merged[path] = l
                if attrs and resolve_strategy(attrs, path) == "manual":
                    conflicts.append(path)
                    applied_strategies[path] = "manual"
            else:
                # Both sides changed differently — consult attributes.
                strategy = resolve_strategy(attrs, path) if attrs else "auto"
                if strategy == "ours":
                    merged[path] = l or b or ""
                    applied_strategies[path] = "ours"
                elif strategy == "theirs":
                    merged[path] = r or b or ""
                    applied_strategies[path] = "theirs"
                elif strategy == "base":
                    if b is None:
                        merged.pop(path, None)
                    else:
                        merged[path] = b
                    applied_strategies[path] = "base"
                elif strategy == "union":
                    # For file-level blobs, full union is not representable —
                    # prefer left and keep all additions from both branches.
                    merged[path] = l or r or b or ""
                    applied_strategies[path] = "union"
                elif strategy == "manual":
                    conflicts.append(path)
                    merged[path] = l or r or b or ""
                    applied_strategies[path] = "manual"
                else:
                    # "auto" — standard three-way conflict.
                    conflicts.append(path)
                    merged[path] = l or r or b or ""

        return MergeResult(
            merged=SnapshotManifest(files=merged, domain=_DOMAIN_NAME),
            conflicts=conflicts,
            applied_strategies=applied_strategies,
        )

    # ------------------------------------------------------------------
    # 4. drift
    # ------------------------------------------------------------------

    def drift(self, committed: StateSnapshot, live: LiveState) -> DriftReport:
        """Report how much the working tree has drifted from the last commit.

        Called by ``muse status``.  Takes a snapshot of the current live
        state and diffs it against the committed snapshot.

        Args:
            committed: The last committed snapshot.
            live:      Current live state (path or snapshot manifest).

        Returns:
            A ``DriftReport`` describing what has changed since the last commit.
        """
        current = self.snapshot(live)
        delta = self.diff(committed, current)
        return DriftReport(
            has_drift=len(delta["ops"]) > 0,
            summary=delta["summary"],
            delta=delta,
        )

    # ------------------------------------------------------------------
    # 5. apply
    # ------------------------------------------------------------------

    def apply(self, delta: StateDelta, live_state: LiveState) -> LiveState:
        """Apply a delta to the working tree.

        Called by ``muse checkout`` after the core engine has already
        restored file-level objects from the object store.  The code plugin
        has no domain-specific post-processing to perform, so this is a
        pass-through.

        Args:
            delta:      The typed operation list (unused at post-checkout time).
            live_state: Current live state (returned unchanged).

        Returns:
            *live_state* unchanged.
        """
        return live_state

    # ------------------------------------------------------------------
    # 6. schema
    # ------------------------------------------------------------------

    def schema(self) -> DomainSchema:
        """Declare the structural schema of the code domain.

        Returns:
            A ``DomainSchema`` with five semantic dimensions:
            ``structure``, ``symbols``, ``imports``, ``variables``,
            and ``metadata``.
        """
        return DomainSchema(
            domain=_DOMAIN_NAME,
            description=(
                "Semantic version control for source code. "
                "Treats code as a structured system of named symbols "
                "(functions, classes, methods) rather than lines of text. "
                "Two commits that only reformat a file produce no delta. "
                "Renames and moves are detected via content-addressed "
                "symbol identity."
            ),
            top_level=TreeSchema(
                kind="tree",
                node_type="module",
                diff_algorithm="gumtree",
            ),
            dimensions=[
                DimensionSpec(
                    name="structure",
                    description=(
                        "Module / file tree. Tracks which files exist and "
                        "how they relate to each other."
                    ),
                    schema=TreeSchema(
                        kind="tree",
                        node_type="file",
                        diff_algorithm="gumtree",
                    ),
                    independent_merge=False,
                ),
                DimensionSpec(
                    name="symbols",
                    description=(
                        "AST symbol tree. Functions, classes, methods, and "
                        "variables — the primary unit of semantic change."
                    ),
                    schema=TreeSchema(
                        kind="tree",
                        node_type="symbol",
                        diff_algorithm="gumtree",
                    ),
                    independent_merge=True,
                ),
                DimensionSpec(
                    name="imports",
                    description=(
                        "Import set. Tracks added / removed import statements "
                        "as an unordered set — order is semantically irrelevant."
                    ),
                    schema=SetSchema(
                        kind="set",
                        element_type="import",
                        identity="by_content",
                    ),
                    independent_merge=True,
                ),
                DimensionSpec(
                    name="variables",
                    description=(
                        "Top-level variable and constant assignments. "
                        "Tracked as an unordered set."
                    ),
                    schema=SetSchema(
                        kind="set",
                        element_type="variable",
                        identity="by_content",
                    ),
                    independent_merge=True,
                ),
                DimensionSpec(
                    name="metadata",
                    description=(
                        "Non-code files: configuration, documentation, "
                        "build scripts, etc. Tracked at file granularity."
                    ),
                    schema=SetSchema(
                        kind="set",
                        element_type="file",
                        identity="by_content",
                    ),
                    independent_merge=True,
                ),
            ],
            merge_mode="three_way",
            schema_version=1,
        )

    # ------------------------------------------------------------------
    # StructuredMergePlugin — OT extension
    # ------------------------------------------------------------------

    def merge_ops(
        self,
        base: StateSnapshot,
        ours_snap: StateSnapshot,
        theirs_snap: StateSnapshot,
        ours_ops: list[DomainOp],
        theirs_ops: list[DomainOp],
        *,
        repo_root: pathlib.Path | None = None,
    ) -> MergeResult:
        """Operation-level three-way merge using Operational Transformation.

        Uses :func:`~muse.core.op_transform.merge_op_lists` to determine
        which ``DomainOp`` pairs commute (auto-mergeable) and which conflict.
        For ``PatchOp`` entries at the same file address, the engine recurses
        into ``child_ops`` — so two agents modifying *different* functions in
        the same file auto-merge, while concurrent modifications to the *same*
        function produce a symbol-level conflict address.

        The reconciled ``merged`` snapshot is produced by the file-level
        three-way :meth:`merge` fallback (we cannot reconstruct merged source
        bytes without a text-merge pass).  This is correct for all cases where
        the two sides touched *different* files.  For the same-file-different-
        symbol case the merged manifest holds the *ours* version of the file —
        annotated as a conflict-free merge — which may require the user to
        re-apply the theirs changes manually.  This limitation is documented
        and will be lifted in a future release that implements source-level
        patching.

        Args:
            base:        Common ancestor snapshot.
            ours_snap:   Our branch's final snapshot.
            theirs_snap: Their branch's final snapshot.
            ours_ops:    Our branch's typed operation list.
            theirs_ops:  Their branch's typed operation list.
            repo_root:   Repository root for ``.museattributes`` lookup.

        Returns:
            A ``MergeResult`` where ``conflicts`` contains symbol-level
            addresses (e.g. ``"src/utils.py::calculate_total"``) rather than
            bare file paths.
        """
        # The core OT engine's _op_key for PatchOp hashes only the file path
        # and child_domain — not the child_ops themselves.  This means two
        # PatchOps for the same file are treated as "consensus" regardless of
        # whether they touch the same or different symbols.  We therefore
        # implement symbol-level conflict detection directly here.

        attrs = load_attributes(repo_root, domain=_DOMAIN_NAME) if repo_root else []

        # ── Step 1: symbol-level conflict detection for PatchOps ──────────
        ours_patches: dict[str, PatchOp] = {
            op["address"]: op for op in ours_ops if op["op"] == "patch"
        }
        theirs_patches: dict[str, PatchOp] = {
            op["address"]: op for op in theirs_ops if op["op"] == "patch"
        }

        conflict_addresses: set[str] = set()
        for path in ours_patches:
            if path not in theirs_patches:
                continue
            for our_child in ours_patches[path]["child_ops"]:
                for their_child in theirs_patches[path]["child_ops"]:
                    if not ops_commute(our_child, their_child):
                        conflict_addresses.add(our_child["address"])

        # ── Step 2: coarse OT for non-PatchOp ops (file-level inserts/deletes) ──
        non_patch_ours: list[DomainOp] = [op for op in ours_ops if op["op"] != "patch"]
        non_patch_theirs: list[DomainOp] = [op for op in theirs_ops if op["op"] != "patch"]
        file_result = merge_op_lists(
            base_ops=[],
            ours_ops=non_patch_ours,
            theirs_ops=non_patch_theirs,
        )
        for our_op, _ in file_result.conflict_ops:
            conflict_addresses.add(our_op["address"])

        # ── Step 3: apply .museattributes to symbol-level conflicts ──────
        # Symbol addresses are of the form "src/utils.py::function_name".
        # We resolve strategy against the file path portion so that a
        # path = "src/**/*.py" / strategy = "ours" rule suppresses symbol
        # conflicts in those files, not just file-level manifest conflicts.
        op_applied_strategies: dict[str, str] = {}
        resolved_conflicts: list[str] = []
        if attrs:
            for addr in sorted(conflict_addresses):
                file_path = addr.split("::")[0] if "::" in addr else addr
                strategy = resolve_strategy(attrs, file_path)
                if strategy in ("ours", "theirs", "base", "union"):
                    op_applied_strategies[addr] = strategy
                elif strategy == "manual":
                    resolved_conflicts.append(addr)
                    op_applied_strategies[addr] = "manual"
                else:
                    resolved_conflicts.append(addr)
        else:
            resolved_conflicts = sorted(conflict_addresses)

        merged_ops: list[DomainOp] = list(file_result.merged_ops) + list(ours_ops)

        # Fall back to file-level merge for the manifest (carries its own
        # applied_strategies from file-level attribute resolution).
        fallback = self.merge(base, ours_snap, theirs_snap, repo_root=repo_root)
        combined_strategies = {**fallback.applied_strategies, **op_applied_strategies}
        return MergeResult(
            merged=fallback.merged,
            conflicts=resolved_conflicts,
            applied_strategies=combined_strategies,
            dimension_reports=fallback.dimension_reports,
            op_log=merged_ops,
        )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _hash_file(path: pathlib.Path) -> str:
    """Return the SHA-256 hex digest of *path*'s raw bytes."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65_536), b""):
            h.update(chunk)
    return h.hexdigest()


def _file_level_ops(
    base_files: dict[str, str],
    target_files: dict[str, str],
) -> list[DomainOp]:
    """Produce coarse file-level ops (no AST parsing)."""
    base_paths = set(base_files)
    target_paths = set(target_files)
    ops: list[DomainOp] = []

    for path in sorted(target_paths - base_paths):
        ops.append(InsertOp(
            op="insert",
            address=path,
            position=None,
            content_id=target_files[path],
            content_summary=f"added {path}",
        ))
    for path in sorted(base_paths - target_paths):
        ops.append(DeleteOp(
            op="delete",
            address=path,
            position=None,
            content_id=base_files[path],
            content_summary=f"removed {path}",
        ))
    for path in sorted(base_paths & target_paths):
        if base_files[path] != target_files[path]:
            ops.append(ReplaceOp(
                op="replace",
                address=path,
                position=None,
                old_content_id=base_files[path],
                new_content_id=target_files[path],
                old_summary=f"{path} (before)",
                new_summary=f"{path} (after)",
            ))
    return ops


def _semantic_ops(
    base_files: dict[str, str],
    target_files: dict[str, str],
    repo_root: pathlib.Path,
) -> list[DomainOp]:
    """Produce symbol-level ops by reading files from the object store."""
    base_paths = set(base_files)
    target_paths = set(target_files)
    changed_paths = (
        (target_paths - base_paths)          # added
        | (base_paths - target_paths)         # removed
        | {                                   # modified
            p for p in base_paths & target_paths
            if base_files[p] != target_files[p]
        }
    )

    base_trees: dict[str, SymbolTree] = {}
    target_trees: dict[str, SymbolTree] = {}

    for path in changed_paths:
        if path in base_files:
            raw = read_object(repo_root, base_files[path])
            if raw is not None:
                base_trees[path] = _parse_with_fallback(raw, path)

        if path in target_files:
            raw = read_object(repo_root, target_files[path])
            if raw is not None:
                target_trees[path] = _parse_with_fallback(raw, path)

    return build_diff_ops(base_files, target_files, base_trees, target_trees)


def _parse_with_fallback(source: bytes, file_path: str) -> SymbolTree:
    """Parse symbols from *source*, returning an empty tree on any error."""
    try:
        return parse_symbols(source, file_path)
    except Exception:
        logger.debug("Symbol parsing failed for %s — falling back to file-level.", file_path)
        return {}


def _load_symbol_trees_from_workdir(
    workdir: pathlib.Path,
    manifest: dict[str, str],
) -> dict[str, SymbolTree]:
    """Build symbol trees for all files in *manifest* that live in *workdir*."""
    trees: dict[str, SymbolTree] = {}
    for rel_path in manifest:
        file_path = workdir / rel_path
        if not file_path.is_file():
            continue
        try:
            source = file_path.read_bytes()
        except OSError:
            continue
        suffix = pathlib.PurePosixPath(rel_path).suffix.lower()
        adapter = adapter_for_path(rel_path)
        if adapter.supported_extensions().intersection({suffix}):
            trees[rel_path] = _parse_with_fallback(source, rel_path)
    return trees


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

#: The singleton plugin instance registered in ``muse/plugins/registry.py``.
plugin = CodePlugin()

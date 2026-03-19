"""Hierarchical code snapshot manifests for the Muse code domain.

A code manifest organises a snapshot's files into a three-level hierarchy::

    PackageManifest          ← top-level package (directory)
      └─ ModuleManifest      ← one source file
           └─ FileEntry      ← per-file metadata + hashes

This structure enables *partial re-parsing*: when merging, only files whose
``content_hash`` changed need to be re-parsed by the AST engine.  Files in
unchanged modules are reused from the cached manifest, making three-way
merges on large codebases significantly faster.

The ``ast_hash`` in :class:`FileEntry` is a SHA-256 of the file's *symbol
tree* rather than its raw bytes.  Two files that differ only in whitespace or
comments will have the same ``ast_hash``, meaning the AST engine reports
"no semantic change" for that file.

Public API
----------
- :class:`FileEntry`         — per-file metadata.
- :class:`ModuleManifest`    — one source file's manifest entry.
- :class:`PackageManifest`   — directory-level grouping.
- :class:`CodeManifest`      — complete hierarchical snapshot manifest.
- :func:`build_code_manifest`  — build from a flat snapshot manifest.
- :func:`diff_manifests`       — find added/removed/modified files between two.
- :func:`write_code_manifest`  — persist to ``.muse/code_manifests/<id>.json``.
- :func:`read_code_manifest`   — load from disk.
"""
from __future__ import annotations

import hashlib
import json
import logging
import pathlib
from typing import TypedDict

from muse.core.object_store import read_object

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


class FileEntry(TypedDict):
    """Metadata for a single source file.

    ``path``          Workspace-relative POSIX path.
    ``content_hash``  SHA-256 of the raw file bytes (from the object store).
    ``ast_hash``      SHA-256 of the symbol-tree JSON (semantic identity).
                      Empty string when AST parsing is unavailable.
    ``language``      Display language name (``"Python"``, ``"TypeScript"``…).
    ``symbol_count``  Number of top-level + nested symbols extracted.
    ``size_bytes``    Raw file size in bytes (0 if unavailable).
    """

    path: str
    content_hash: str
    ast_hash: str
    language: str
    symbol_count: int
    size_bytes: int


class ModuleManifest(TypedDict):
    """Manifest for one source file (module).

    ``module_path``    Workspace-relative POSIX path.
    ``content_hash``   Raw-bytes SHA-256 (same as ``FileEntry.content_hash``).
    ``ast_hash``       Symbol-tree SHA-256 for semantic change detection.
    ``language``       Language name.
    ``symbol_count``   Number of symbols in this file.
    """

    module_path: str
    content_hash: str
    ast_hash: str
    language: str
    symbol_count: int


class PackageManifest(TypedDict):
    """Directory-level grouping of modules.

    ``package``        Workspace-relative POSIX directory path.
    ``package_hash``   SHA-256 of all sorted ``content_hash`` values in the
                       package — stable fingerprint for change detection.
    ``modules``        All ``ModuleManifest`` entries in this package.
    ``total_files``    Total number of files (all types, not just semantic).
    ``semantic_files`` Number of AST-parseable files.
    """

    package: str
    package_hash: str
    modules: list[ModuleManifest]
    total_files: int
    semantic_files: int


class CodeManifest(TypedDict):
    """Complete hierarchical manifest for one code snapshot.

    ``snapshot_id``    The snapshot this manifest was built from.
    ``manifest_hash``  SHA-256 of this manifest's JSON — stable cache key.
    ``packages``       All :class:`PackageManifest` entries, sorted by path.
    ``total_files``    Total files in the snapshot.
    ``semantic_files`` AST-parseable files.
    ``total_symbols``  Sum of ``symbol_count`` across all modules.
    """

    snapshot_id: str
    manifest_hash: str
    packages: list[PackageManifest]
    total_files: int
    semantic_files: int
    total_symbols: int


# ---------------------------------------------------------------------------
# Language detection
# ---------------------------------------------------------------------------

_SUFFIX_LANG: dict[str, str] = {
    ".py": "Python", ".pyi": "Python",
    ".ts": "TypeScript", ".tsx": "TypeScript",
    ".js": "JavaScript", ".jsx": "JavaScript",
    ".mjs": "JavaScript", ".cjs": "JavaScript",
    ".go": "Go",
    ".rs": "Rust",
    ".java": "Java",
    ".cs": "C#",
    ".c": "C", ".h": "C",
    ".cpp": "C++", ".cc": "C++", ".cxx": "C++", ".hpp": "C++",
    ".rb": "Ruby",
    ".kt": "Kotlin", ".kts": "Kotlin",
}

_SEMANTIC_SUFFIXES = frozenset(_SUFFIX_LANG)


def _language_of(file_path: str) -> str:
    suffix = pathlib.PurePosixPath(file_path).suffix.lower()
    return _SUFFIX_LANG.get(suffix, suffix or "(no ext)")


def _is_semantic(file_path: str) -> bool:
    suffix = pathlib.PurePosixPath(file_path).suffix.lower()
    return suffix in _SEMANTIC_SUFFIXES


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


def build_code_manifest(
    snapshot_id: str,
    flat_manifest: dict[str, str],
    repo_root: pathlib.Path,
) -> CodeManifest:
    """Build a :class:`CodeManifest` from a flat ``{path: content_hash}`` dict.

    Attempts to parse each semantic file into a symbol tree to compute
    ``ast_hash`` and ``symbol_count``.  Falls back gracefully for binary files
    or parse errors.

    Args:
        snapshot_id:    The snapshot this manifest represents.
        flat_manifest:  ``{workspace_path: sha256}`` from the snapshot.
        repo_root:      Repository root for object store access.

    Returns:
        A fully populated :class:`CodeManifest`.
    """
    # Late import to avoid circular dependencies.
    from muse.plugins.code.ast_parser import parse_symbols

    # Group files by parent directory.
    pkg_files: dict[str, list[str]] = {}
    for file_path in sorted(flat_manifest):
        pkg = str(pathlib.PurePosixPath(file_path).parent)
        pkg_files.setdefault(pkg, []).append(file_path)

    packages: list[PackageManifest] = []
    total_symbols = 0
    total_semantic = 0

    for pkg_path, file_paths in sorted(pkg_files.items()):
        modules: list[ModuleManifest] = []
        pkg_hashes: list[str] = []
        pkg_semantic = 0

        for file_path in sorted(file_paths):
            content_hash = flat_manifest[file_path]
            lang = _language_of(file_path)
            is_sem = _is_semantic(file_path)
            ast_hash = ""
            sym_count = 0

            if is_sem:
                source = read_object(repo_root, content_hash)
                if source is not None:
                    try:
                        symbols = parse_symbols(source, file_path)
                        sym_count = len(symbols)
                        # ast_hash = SHA-256 of sorted symbol content IDs.
                        sig = hashlib.sha256(
                            "|".join(
                                sorted(s["content_id"] for s in symbols.values())
                            ).encode()
                        ).hexdigest()
                        ast_hash = sig
                    except Exception:
                        logger.debug("AST parse failed for %s", file_path)
                pkg_semantic += 1
                total_semantic += 1

            total_symbols += sym_count
            pkg_hashes.append(content_hash)
            modules.append(ModuleManifest(
                module_path=file_path,
                content_hash=content_hash,
                ast_hash=ast_hash,
                language=lang,
                symbol_count=sym_count,
            ))

        pkg_hash = hashlib.sha256("|".join(sorted(pkg_hashes)).encode()).hexdigest()
        packages.append(PackageManifest(
            package=pkg_path,
            package_hash=pkg_hash,
            modules=modules,
            total_files=len(file_paths),
            semantic_files=pkg_semantic,
        ))

    manifest_json = json.dumps(
        {"snapshot_id": snapshot_id, "packages": packages}, sort_keys=True
    )
    manifest_hash = hashlib.sha256(manifest_json.encode()).hexdigest()

    return CodeManifest(
        snapshot_id=snapshot_id,
        manifest_hash=manifest_hash,
        packages=packages,
        total_files=len(flat_manifest),
        semantic_files=total_semantic,
        total_symbols=total_symbols,
    )


# ---------------------------------------------------------------------------
# Diff
# ---------------------------------------------------------------------------


class ManifestFileDiff(TypedDict):
    """Change record from :func:`diff_manifests` for one file."""

    path: str
    change: str        # "added" | "removed" | "modified" | "ast_changed"
    old_hash: str
    new_hash: str
    old_ast_hash: str
    new_ast_hash: str
    semantic_change: bool  # True when ast_hash differs (real code change)


def diff_manifests(
    base: CodeManifest,
    target: CodeManifest,
) -> list[ManifestFileDiff]:
    """Produce a per-file change list between two :class:`CodeManifest` objects.

    Files with identical ``content_hash`` values are skipped (no change).
    Files where only the ``content_hash`` changed but ``ast_hash`` is the same
    are marked ``"modified"`` with ``semantic_change=False`` — e.g. whitespace
    or comment-only diffs.  Files where ``ast_hash`` changed are ``"ast_changed"``
    with ``semantic_change=True``.

    Args:
        base:   Manifest for the earlier state (e.g. parent commit).
        target: Manifest for the later state (e.g. current commit).

    Returns:
        Sorted list of :class:`ManifestFileDiff` records.
    """
    base_modules: dict[str, ModuleManifest] = {}
    for pkg in base["packages"]:
        for mod in pkg["modules"]:
            base_modules[mod["module_path"]] = mod

    target_modules: dict[str, ModuleManifest] = {}
    for pkg in target["packages"]:
        for mod in pkg["modules"]:
            target_modules[mod["module_path"]] = mod

    diffs: list[ManifestFileDiff] = []

    all_paths = sorted(set(base_modules) | set(target_modules))
    for path in all_paths:
        bm = base_modules.get(path)
        tm = target_modules.get(path)

        if bm is None and tm is not None:
            diffs.append(ManifestFileDiff(
                path=path, change="added",
                old_hash="", new_hash=tm["content_hash"],
                old_ast_hash="", new_ast_hash=tm["ast_hash"],
                semantic_change=True,
            ))
        elif bm is not None and tm is None:
            diffs.append(ManifestFileDiff(
                path=path, change="removed",
                old_hash=bm["content_hash"], new_hash="",
                old_ast_hash=bm["ast_hash"], new_ast_hash="",
                semantic_change=True,
            ))
        elif bm is not None and tm is not None:
            if bm["content_hash"] == tm["content_hash"]:
                continue  # No change at all.
            ast_changed = bm["ast_hash"] != tm["ast_hash"]
            diffs.append(ManifestFileDiff(
                path=path,
                change="ast_changed" if ast_changed else "modified",
                old_hash=bm["content_hash"], new_hash=tm["content_hash"],
                old_ast_hash=bm["ast_hash"], new_ast_hash=tm["ast_hash"],
                semantic_change=ast_changed,
            ))

    return diffs


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def write_code_manifest(repo_root: pathlib.Path, manifest: CodeManifest) -> None:
    """Persist a :class:`CodeManifest` to ``.muse/code_manifests/<hash>.json``.

    Args:
        repo_root: Repository root.
        manifest:  The manifest to write.
    """
    store_dir = repo_root / ".muse" / "code_manifests"
    store_dir.mkdir(parents=True, exist_ok=True)
    path = store_dir / f"{manifest['manifest_hash']}.json"
    if not path.exists():
        path.write_text(json.dumps(manifest))


def read_code_manifest(
    repo_root: pathlib.Path, manifest_hash: str
) -> CodeManifest | None:
    """Load a :class:`CodeManifest` by its hash.

    Args:
        repo_root:     Repository root.
        manifest_hash: The ``manifest_hash`` of the target manifest.

    Returns:
        The deserialized :class:`CodeManifest`, or ``None`` if not found.
    """
    path = repo_root / ".muse" / "code_manifests" / f"{manifest_hash}.json"
    if not path.exists():
        return None
    raw: CodeManifest = json.loads(path.read_text())
    return raw

"""Rerere — Reuse Recorded Resolution for Muse VCS conflicts.

Records how a user resolves a merge conflict, keyed by a deterministic
content fingerprint, so that identical conflicts are resolved automatically
on future merges.  Analogous to ``git rerere`` but domain-agnostic and
extended to support semantic fingerprinting via the optional
:class:`~muse.domain.RererePlugin` sub-protocol.

Storage layout
--------------

Each cached resolution lives under ``.muse/rr-cache/<fingerprint>/``::

    meta.json   — conflict metadata (path, ours_id, theirs_id, domain, recorded_at)
    resolution  — 64-char hex SHA-256 of the resolved blob

Both files use UTF-8 encoding.  Writes are atomic (temp file + ``os.replace``).

Fingerprinting
--------------

The default fingerprint is::

    SHA-256( min(ours_id, theirs_id) + ":" + max(ours_id, theirs_id) )

Content-addressed IDs are already SHA-256 hashes; sorting ensures
commutativity — resolving A vs B is identical to resolving B vs A.

Plugins may implement :class:`~muse.domain.RererePlugin` to provide
semantically richer fingerprints.  For example, a MIDI plugin can hash
note-event structure rather than raw blob IDs, so the same musical conflict
is recognised even when surrounding timing context shifts.

Thread safety
-------------

Every write uses ``os.replace`` (POSIX rename), which is atomic on all
supported platforms.  Concurrent readers always see a complete or absent file,
never a partial write.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import logging
import os
import pathlib
import tempfile
from dataclasses import dataclass
from typing import TYPE_CHECKING, TypedDict

from muse.core.validation import validate_object_id

if TYPE_CHECKING:
    from muse.domain import MuseDomainPlugin

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_RR_CACHE = "rr-cache"
_META_FILE = "meta.json"
_RESOLUTION_FILE = "resolution"

# Maximum number of rr-cache entries scanned by list_records() / gc().
# Guards against degenerate repos with millions of entries.
_MAX_SCAN = 100_000


# ---------------------------------------------------------------------------
# Wire-format TypedDict for meta.json
# ---------------------------------------------------------------------------


class RerereMetaDict(TypedDict):
    """JSON-serialisable form of a rerere preimage record."""

    fingerprint: str
    path: str
    ours_id: str
    theirs_id: str
    domain: str
    recorded_at: str  # ISO 8601


# ---------------------------------------------------------------------------
# Public dataclass returned to callers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RerereRecord:
    """A cached conflict resolution entry.

    ``fingerprint``   — Hex fingerprint that identifies the conflict.
    ``path``          — Workspace-relative POSIX path that conflicted.
    ``ours_id``       — Object ID of the "ours" version at conflict time.
    ``theirs_id``     — Object ID of the "theirs" version at conflict time.
    ``domain``        — Domain name that produced the conflict.
    ``recorded_at``   — When the preimage was recorded.
    ``resolution_id`` — Object ID of the chosen resolution, or ``None`` when
                        the user has not yet committed a resolution.
    """

    fingerprint: str
    path: str
    ours_id: str
    theirs_id: str
    domain: str
    recorded_at: datetime.datetime
    resolution_id: str | None = None

    @property
    def has_resolution(self) -> bool:
        """``True`` when a resolution blob has been recorded."""
        return self.resolution_id is not None


# ---------------------------------------------------------------------------
# Directory helpers
# ---------------------------------------------------------------------------


def rr_cache_dir(root: pathlib.Path) -> pathlib.Path:
    """Return the path to ``.muse/rr-cache/`` (may not yet exist)."""
    return root / ".muse" / _RR_CACHE


def _entry_dir(root: pathlib.Path, fingerprint: str) -> pathlib.Path:
    return rr_cache_dir(root) / fingerprint


def _meta_path(root: pathlib.Path, fingerprint: str) -> pathlib.Path:
    return _entry_dir(root, fingerprint) / _META_FILE


def _resolution_path(root: pathlib.Path, fingerprint: str) -> pathlib.Path:
    return _entry_dir(root, fingerprint) / _RESOLUTION_FILE


# ---------------------------------------------------------------------------
# Fingerprinting
# ---------------------------------------------------------------------------


def conflict_fingerprint(ours_id: str, theirs_id: str) -> str:
    """Return the default content fingerprint for a conflict.

    Hashes the sorted pair of object IDs so the result is the same regardless
    of which branch is "ours" vs "theirs".

    Args:
        ours_id:   SHA-256 object ID of the "ours" version.
        theirs_id: SHA-256 object ID of the "theirs" version.

    Returns:
        64-char lowercase hex SHA-256 fingerprint.
    """
    lo, hi = sorted((ours_id, theirs_id))
    canonical = f"{lo}:{hi}"
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compute_fingerprint(
    path: str,
    ours_id: str,
    theirs_id: str,
    plugin: MuseDomainPlugin,
    repo_root: pathlib.Path,
) -> str:
    """Return the fingerprint for a conflict, preferring plugin semantics.

    Checks whether *plugin* implements the optional
    :class:`~muse.domain.RererePlugin` sub-protocol.  When it does the
    plugin's domain-aware fingerprint is used, giving richer matching (e.g.
    note-event structure for MIDI rather than raw blob hashes).  Otherwise
    falls back to the default content fingerprint.

    Args:
        path:      Workspace-relative POSIX path of the conflicting file.
        ours_id:   Object ID of the "ours" blob.
        theirs_id: Object ID of the "theirs" blob.
        plugin:    Active domain plugin instance.
        repo_root: Repository root for plugin context.

    Returns:
        64-char hex fingerprint.
    """
    from muse.domain import RererePlugin

    if isinstance(plugin, RererePlugin):
        try:
            fp = plugin.conflict_fingerprint(path, ours_id, theirs_id, repo_root)
            if fp and len(fp) == 64:
                return fp
            logger.warning(
                "⚠️ Plugin conflict_fingerprint returned invalid value %r — "
                "falling back to default fingerprint",
                fp,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "⚠️ Plugin conflict_fingerprint raised %s — "
                "falling back to default fingerprint",
                exc,
            )
    return conflict_fingerprint(ours_id, theirs_id)


# ---------------------------------------------------------------------------
# Atomic write helper
# ---------------------------------------------------------------------------


def _write_atomic(dest: pathlib.Path, content: str) -> None:
    """Write *content* to *dest* atomically via a temp file + rename."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_str = tempfile.mkstemp(dir=dest.parent, prefix=".rr-tmp-")
    tmp = pathlib.Path(tmp_str)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.replace(tmp, dest)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


# ---------------------------------------------------------------------------
# Record / save / load
# ---------------------------------------------------------------------------


def record_preimage(
    root: pathlib.Path,
    path: str,
    ours_id: str,
    theirs_id: str,
    domain: str,
    plugin: MuseDomainPlugin,
) -> str:
    """Record the preimage of a conflict so it can be replayed later.

    Writes ``.muse/rr-cache/<fingerprint>/meta.json`` but does NOT write a
    resolution (that happens after the user commits).  Idempotent: re-recording
    the same conflict is a no-op.

    Args:
        root:      Repository root.
        path:      Workspace-relative POSIX path of the conflicting file.
        ours_id:   SHA-256 of the "ours" blob at conflict time.
        theirs_id: SHA-256 of the "theirs" blob at conflict time.
        domain:    Active domain name (e.g. ``"midi"``, ``"code"``).
        plugin:    Active domain plugin (for optional fingerprint override).

    Returns:
        The 64-char hex fingerprint identifying this conflict.
    """
    validate_object_id(ours_id)
    validate_object_id(theirs_id)

    fp = compute_fingerprint(path, ours_id, theirs_id, plugin, root)
    meta_p = _meta_path(root, fp)

    if meta_p.exists():
        logger.debug("rerere: preimage %s already recorded for '%s'", fp[:8], path)
        return fp

    meta: RerereMetaDict = {
        "fingerprint": fp,
        "path": path,
        "ours_id": ours_id,
        "theirs_id": theirs_id,
        "domain": domain,
        "recorded_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    _write_atomic(meta_p, json.dumps(meta, indent=2))
    logger.debug("rerere: recorded preimage %s for '%s'", fp[:8], path)
    return fp


def save_resolution(root: pathlib.Path, fingerprint: str, resolution_id: str) -> None:
    """Persist the object ID of the chosen resolution for a conflict.

    Written after the user resolves and commits.  Idempotent if the same
    resolution is saved twice.

    Args:
        root:          Repository root.
        fingerprint:   64-char hex fingerprint from :func:`record_preimage`.
        resolution_id: SHA-256 of the resolved blob.

    Raises:
        FileNotFoundError: When no preimage exists for *fingerprint*.
        ValueError:         When *resolution_id* is not a valid object ID.
    """
    validate_object_id(resolution_id)

    meta_p = _meta_path(root, fingerprint)
    if not meta_p.exists():
        raise FileNotFoundError(
            f"No rerere preimage found for fingerprint {fingerprint[:8]}. "
            "Run 'muse rerere record' first."
        )

    res_p = _resolution_path(root, fingerprint)
    _write_atomic(res_p, resolution_id)
    logger.debug(
        "rerere: saved resolution %s for fingerprint %s",
        resolution_id[:8],
        fingerprint[:8],
    )


def load_record(root: pathlib.Path, fingerprint: str) -> RerereRecord | None:
    """Load a :class:`RerereRecord` from disk.

    Returns ``None`` when the fingerprint is not in the cache.

    Args:
        root:        Repository root.
        fingerprint: 64-char hex fingerprint.

    Returns:
        :class:`RerereRecord` or ``None``.
    """
    meta_p = _meta_path(root, fingerprint)
    if not meta_p.exists():
        return None

    try:
        data = json.loads(meta_p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("⚠️ Failed to read rerere meta %s: %s", meta_p, exc)
        return None

    try:
        recorded_at = datetime.datetime.fromisoformat(str(data.get("recorded_at", "")))
    except ValueError:
        recorded_at = datetime.datetime.now(datetime.timezone.utc)

    res_p = _resolution_path(root, fingerprint)
    resolution_id: str | None = None
    if res_p.exists():
        try:
            raw = res_p.read_text(encoding="utf-8").strip()
            validate_object_id(raw)
            resolution_id = raw
        except (OSError, ValueError):
            resolution_id = None

    return RerereRecord(
        fingerprint=fingerprint,
        path=str(data.get("path", "")),
        ours_id=str(data.get("ours_id", "")),
        theirs_id=str(data.get("theirs_id", "")),
        domain=str(data.get("domain", "")),
        recorded_at=recorded_at,
        resolution_id=resolution_id,
    )


# ---------------------------------------------------------------------------
# Resolution application
# ---------------------------------------------------------------------------


def has_resolution(root: pathlib.Path, fingerprint: str) -> bool:
    """Return ``True`` when a resolution blob has been saved for *fingerprint*."""
    return _resolution_path(root, fingerprint).exists()


def apply_cached(
    root: pathlib.Path,
    fingerprint: str,
    dest: pathlib.Path,
) -> bool:
    """Restore the cached resolution blob to *dest* in the working tree.

    Args:
        root:        Repository root.
        fingerprint: Conflict fingerprint.
        dest:        Absolute path to write the restored file.

    Returns:
        ``True`` on success, ``False`` when there is no cached resolution or
        the resolution blob is not in the local object store.
    """
    res_p = _resolution_path(root, fingerprint)
    if not res_p.exists():
        return False

    try:
        resolution_id = res_p.read_text(encoding="utf-8").strip()
        validate_object_id(resolution_id)
    except (OSError, ValueError) as exc:
        logger.warning("⚠️ rerere: invalid resolution file for %s: %s", fingerprint[:8], exc)
        return False

    from muse.core.object_store import has_object, restore_object

    if not has_object(root, resolution_id):
        logger.warning(
            "⚠️ rerere: resolution blob %s for %s not in local store — "
            "cannot auto-apply (was this rr-cache transferred from another machine?)",
            resolution_id[:8],
            fingerprint[:8],
        )
        return False

    dest.parent.mkdir(parents=True, exist_ok=True)
    ok = restore_object(root, resolution_id, dest)
    if ok:
        logger.debug(
            "rerere: applied resolution %s → %s", resolution_id[:8], dest.name
        )
    return ok


# ---------------------------------------------------------------------------
# Bulk operations: list, forget, clear, gc
# ---------------------------------------------------------------------------


def list_records(root: pathlib.Path) -> list[RerereRecord]:
    """Return all :class:`RerereRecord` entries in the rr-cache.

    Scans ``.muse/rr-cache/`` and loads each entry.  Skips unreadable entries
    with a warning.  Capped at :data:`_MAX_SCAN` entries to protect against
    degenerate repos.

    Args:
        root: Repository root.

    Returns:
        List of :class:`RerereRecord`, sorted by ``recorded_at`` descending
        (most recent first).
    """
    cache_dir = rr_cache_dir(root)
    if not cache_dir.exists():
        return []

    records: list[RerereRecord] = []
    count = 0
    for entry in cache_dir.iterdir():
        if count >= _MAX_SCAN:
            logger.warning(
                "⚠️ rerere: rr-cache has more than %d entries — scan truncated",
                _MAX_SCAN,
            )
            break
        count += 1
        if not entry.is_dir():
            continue
        record = load_record(root, entry.name)
        if record is not None:
            records.append(record)

    records.sort(key=lambda r: r.recorded_at, reverse=True)
    return records


def forget_record(root: pathlib.Path, fingerprint: str) -> bool:
    """Remove the rr-cache entry for *fingerprint*.

    Deletes both ``meta.json`` and ``resolution`` (if present), then removes
    the entry directory if empty.

    Args:
        root:        Repository root.
        fingerprint: Conflict fingerprint to forget.

    Returns:
        ``True`` if the entry existed and was removed, ``False`` otherwise.
    """
    entry = _entry_dir(root, fingerprint)
    if not entry.exists():
        return False

    for child in entry.iterdir():
        child.unlink(missing_ok=True)
    try:
        entry.rmdir()
    except OSError:
        pass  # not empty — leave it

    logger.debug("rerere: forgot resolution for fingerprint %s", fingerprint[:8])
    return True


def clear_all(root: pathlib.Path) -> int:
    """Remove every entry from the rr-cache.

    Args:
        root: Repository root.

    Returns:
        Number of entries removed.
    """
    cache_dir = rr_cache_dir(root)
    if not cache_dir.exists():
        return 0

    removed = 0
    for entry in cache_dir.iterdir():
        if not entry.is_dir():
            continue
        for child in entry.iterdir():
            child.unlink(missing_ok=True)
        try:
            entry.rmdir()
            removed += 1
        except OSError:
            pass

    logger.debug("rerere: cleared %d rr-cache entries", removed)
    return removed


def gc_stale(root: pathlib.Path) -> int:
    """Remove preimage-only entries older than 60 days (no resolution recorded).

    Keeps all entries that have a resolution regardless of age.  This mirrors
    Git's ``git rerere gc`` behaviour.

    Args:
        root: Repository root.

    Returns:
        Number of entries removed.
    """
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=60)
    records = list_records(root)
    removed = 0
    for rec in records:
        if rec.has_resolution:
            continue
        if rec.recorded_at < cutoff:
            if forget_record(root, rec.fingerprint):
                removed += 1
    logger.debug("rerere gc: removed %d stale preimage-only entries", removed)
    return removed


# ---------------------------------------------------------------------------
# High-level integration helpers called by merge and commit commands
# ---------------------------------------------------------------------------


def auto_apply(
    root: pathlib.Path,
    conflict_paths: list[str],
    ours_manifest: dict[str, str],
    theirs_manifest: dict[str, str],
    domain: str,
    plugin: MuseDomainPlugin,
) -> tuple[dict[str, str], list[str]]:
    """Attempt to auto-resolve conflicts using cached rerere resolutions.

    For each *conflict_paths* entry:

    1. Compute the fingerprint from the ours/theirs object IDs.
    2. Check whether a resolution has been saved for that fingerprint.
    3. If yes: restore the resolution blob to the working tree path and
       collect the resolved ``{path: resolution_id}`` mapping.
    4. If no: leave the conflict for manual resolution and record the preimage.

    Args:
        root:            Repository root.
        conflict_paths:  Workspace-relative POSIX paths that conflicted.
        ours_manifest:   ``{path: object_id}`` for the "ours" snapshot.
        theirs_manifest: ``{path: object_id}`` for the "theirs" snapshot.
        domain:          Active domain name string.
        plugin:          Active domain plugin instance.

    Returns:
        ``(resolved, remaining)`` where:

        - ``resolved`` maps path → resolution_object_id for auto-resolved paths.
        - ``remaining`` is the list of paths still requiring manual resolution.
    """
    resolved: dict[str, str] = {}
    remaining: list[str] = []

    for path in conflict_paths:
        ours_id = ours_manifest.get(path, "")
        theirs_id = theirs_manifest.get(path, "")

        if not ours_id or not theirs_id:
            # One side deleted the file — cannot fingerprint, leave for manual.
            remaining.append(path)
            continue

        fp = compute_fingerprint(path, ours_id, theirs_id, plugin, root)

        if has_resolution(root, fp):
            # Load resolution_id before restoring so we can add it to the manifest.
            res_p = _resolution_path(root, fp)
            try:
                resolution_id = res_p.read_text(encoding="utf-8").strip()
                validate_object_id(resolution_id)
            except (OSError, ValueError):
                remaining.append(path)
                continue

            dest = root / path
            if apply_cached(root, fp, dest):
                resolved[path] = resolution_id
                logger.info("✅ rerere: auto-resolved '%s'", path)
            else:
                # Resolution blob missing from store — fall back to manual.
                remaining.append(path)
        else:
            # No cached resolution — record preimage for future use.
            record_preimage(root, path, ours_id, theirs_id, domain, plugin)
            remaining.append(path)

    return resolved, remaining


def record_resolutions(
    root: pathlib.Path,
    conflict_paths: list[str],
    ours_manifest: dict[str, str],
    theirs_manifest: dict[str, str],
    new_manifest: dict[str, str],
    domain: str,
    plugin: MuseDomainPlugin,
) -> list[str]:
    """Record how the user resolved each conflict after a merge commit.

    Called by ``muse commit`` immediately after writing a merge commit that
    resolves a conflicted merge.  For each previously conflicting path the
    function reads the resolution object ID from the new snapshot manifest and
    saves it to ``.muse/rr-cache/<fingerprint>/resolution``.

    Args:
        root:            Repository root.
        conflict_paths:  Paths that were listed as conflicts in MERGE_STATE.
        ours_manifest:   ``{path: object_id}`` for the "ours" snapshot.
        theirs_manifest: ``{path: object_id}`` for the "theirs" snapshot.
        new_manifest:    ``{path: object_id}`` from the committed merge snapshot.
        domain:          Active domain name string.
        plugin:          Active domain plugin instance.

    Returns:
        List of paths for which a resolution was successfully saved.
    """
    saved: list[str] = []

    for path in conflict_paths:
        ours_id = ours_manifest.get(path, "")
        theirs_id = theirs_manifest.get(path, "")
        resolution_id = new_manifest.get(path, "")

        if not ours_id or not theirs_id or not resolution_id:
            # Cannot record: one side deleted the file or not in new snapshot.
            continue

        try:
            validate_object_id(resolution_id)
        except ValueError:
            continue

        fp = compute_fingerprint(path, ours_id, theirs_id, plugin, root)

        # Ensure the preimage exists (it may have been written by auto_apply).
        if not _meta_path(root, fp).exists():
            record_preimage(root, path, ours_id, theirs_id, domain, plugin)

        try:
            save_resolution(root, fp, resolution_id)
            saved.append(path)
            logger.info(
                "✅ rerere: recorded resolution for '%s' (fingerprint %s)",
                path,
                fp[:8],
            )
        except (FileNotFoundError, ValueError) as exc:
            logger.warning("⚠️ rerere: failed to save resolution for '%s': %s", path, exc)

    return saved

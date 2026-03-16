"""File-based commit and snapshot store for the Muse VCS.

This module replaces the SQLAlchemy-backed ``db.py`` from the original
Maestro-embedded implementation. All commit and snapshot metadata is stored
as JSON files under ``.muse/`` — no external database required.

Layout
------

    .muse/
        commits/<commit_id>.json     — one JSON file per commit
        snapshots/<snapshot_id>.json — one JSON file per snapshot manifest
        tags/<repo_id>/<tag_id>.json — tag records
        objects/<sha2>/<sha62>       — content-addressed blobs (via object_store.py)
        refs/heads/<branch>          — branch HEAD pointers (plain text commit IDs)
        HEAD                         — symbolic ref: "refs/heads/main"
        repo.json                    — repository identity

Commit JSON schema
------------------

    {
        "commit_id": "<sha256>",
        "repo_id": "<uuid>",
        "branch": "main",
        "parent_commit_id": null | "<sha256>",
        "parent2_commit_id": null | "<sha256>",
        "snapshot_id": "<sha256>",
        "message": "Add verse melody",
        "author": "gabriel",
        "committed_at": "2026-03-16T12:00:00+00:00",
        "metadata": {}
    }

Snapshot JSON schema
--------------------

    {
        "snapshot_id": "<sha256>",
        "manifest": {"tracks/drums.mid": "<sha256>", ...},
        "created_at": "2026-03-16T12:00:00+00:00"
    }

All functions are synchronous — file I/O on a local ``.muse/`` directory
does not require async. This removes the SQLAlchemy/asyncpg dependency from
the CLI entirely.
"""
from __future__ import annotations

import datetime
import json
import logging
import pathlib
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

_COMMITS_DIR = "commits"
_SNAPSHOTS_DIR = "snapshots"
_TAGS_DIR = "tags"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class CommitRecord:
    """An immutable commit record stored as a JSON file under .muse/commits/."""

    commit_id: str
    repo_id: str
    branch: str
    snapshot_id: str
    message: str
    committed_at: datetime.datetime
    parent_commit_id: str | None = None
    parent2_commit_id: str | None = None
    author: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["committed_at"] = self.committed_at.isoformat()
        d["metadata"] = self.metadata or {}
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "CommitRecord":
        committed_at_raw = d.get("committed_at", "")
        try:
            committed_at = datetime.datetime.fromisoformat(str(committed_at_raw))
        except ValueError:
            committed_at = datetime.datetime.now(datetime.timezone.utc)
        return cls(
            commit_id=str(d.get("commit_id", "")),
            repo_id=str(d.get("repo_id", "")),
            branch=str(d.get("branch", "")),
            snapshot_id=str(d.get("snapshot_id", "")),
            message=str(d.get("message", "")),
            committed_at=committed_at,
            parent_commit_id=d.get("parent_commit_id") or None,
            parent2_commit_id=d.get("parent2_commit_id") or None,
            author=str(d.get("author", "")),
            metadata=dict(d.get("metadata") or {}),
        )


@dataclass
class SnapshotRecord:
    """An immutable snapshot record stored as a JSON file under .muse/snapshots/."""

    snapshot_id: str
    manifest: dict[str, str]
    created_at: datetime.datetime = field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc)
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "manifest": self.manifest,
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SnapshotRecord":
        created_at_raw = d.get("created_at", "")
        try:
            created_at = datetime.datetime.fromisoformat(str(created_at_raw))
        except ValueError:
            created_at = datetime.datetime.now(datetime.timezone.utc)
        return cls(
            snapshot_id=str(d.get("snapshot_id", "")),
            manifest=dict(d.get("manifest") or {}),
            created_at=created_at,
        )


@dataclass
class TagRecord:
    """A semantic tag attached to a commit."""

    tag_id: str
    repo_id: str
    commit_id: str
    tag: str
    created_at: datetime.datetime = field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc)
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "tag_id": self.tag_id,
            "repo_id": self.repo_id,
            "commit_id": self.commit_id,
            "tag": self.tag,
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "TagRecord":
        created_at_raw = d.get("created_at", "")
        try:
            created_at = datetime.datetime.fromisoformat(str(created_at_raw))
        except ValueError:
            created_at = datetime.datetime.now(datetime.timezone.utc)
        return cls(
            tag_id=str(d.get("tag_id", str(uuid.uuid4()))),
            repo_id=str(d.get("repo_id", "")),
            commit_id=str(d.get("commit_id", "")),
            tag=str(d.get("tag", "")),
            created_at=created_at,
        )


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def _commits_dir(repo_root: pathlib.Path) -> pathlib.Path:
    return repo_root / ".muse" / _COMMITS_DIR


def _snapshots_dir(repo_root: pathlib.Path) -> pathlib.Path:
    return repo_root / ".muse" / _SNAPSHOTS_DIR


def _tags_dir(repo_root: pathlib.Path, repo_id: str) -> pathlib.Path:
    return repo_root / ".muse" / _TAGS_DIR / repo_id


def _commit_path(repo_root: pathlib.Path, commit_id: str) -> pathlib.Path:
    return _commits_dir(repo_root) / f"{commit_id}.json"


def _snapshot_path(repo_root: pathlib.Path, snapshot_id: str) -> pathlib.Path:
    return _snapshots_dir(repo_root) / f"{snapshot_id}.json"


# ---------------------------------------------------------------------------
# Commit operations
# ---------------------------------------------------------------------------


def write_commit(repo_root: pathlib.Path, commit: CommitRecord) -> None:
    """Persist a commit record to ``.muse/commits/<commit_id>.json``."""
    _commits_dir(repo_root).mkdir(parents=True, exist_ok=True)
    path = _commit_path(repo_root, commit.commit_id)
    if path.exists():
        logger.debug("⚠️ Commit %s already exists — skipped", commit.commit_id[:8])
        return
    path.write_text(json.dumps(commit.to_dict(), indent=2) + "\n")
    logger.debug("✅ Stored commit %s branch=%r", commit.commit_id[:8], commit.branch)


def read_commit(repo_root: pathlib.Path, commit_id: str) -> CommitRecord | None:
    """Load a commit record by ID, or ``None`` if it does not exist."""
    path = _commit_path(repo_root, commit_id)
    if not path.exists():
        return None
    try:
        return CommitRecord.from_dict(json.loads(path.read_text()))
    except (json.JSONDecodeError, KeyError) as exc:
        logger.warning("⚠️ Corrupt commit file %s: %s", path, exc)
        return None


def update_commit_metadata(
    repo_root: pathlib.Path,
    commit_id: str,
    key: str,
    value: Any,
) -> bool:
    """Set a single key in a commit's metadata dict.

    Returns ``True`` on success, ``False`` if the commit is not found.
    """
    commit = read_commit(repo_root, commit_id)
    if commit is None:
        logger.warning("⚠️ Commit %s not found — cannot update metadata", commit_id[:8])
        return False
    commit.metadata[key] = value
    path = _commit_path(repo_root, commit_id)
    path.write_text(json.dumps(commit.to_dict(), indent=2) + "\n")
    logger.debug("✅ Set %s=%r on commit %s", key, value, commit_id[:8])
    return True


def get_head_commit_id(repo_root: pathlib.Path, branch: str) -> str | None:
    """Return the commit ID at HEAD of *branch*, or ``None`` for an empty branch."""
    ref_path = repo_root / ".muse" / "refs" / "heads" / branch
    if not ref_path.exists():
        return None
    raw = ref_path.read_text().strip()
    return raw if raw else None


def get_head_snapshot_id(
    repo_root: pathlib.Path,
    repo_id: str,
    branch: str,
) -> str | None:
    """Return the snapshot_id at HEAD of *branch*, or ``None``."""
    commit_id = get_head_commit_id(repo_root, branch)
    if commit_id is None:
        return None
    commit = read_commit(repo_root, commit_id)
    if commit is None:
        return None
    return commit.snapshot_id


def resolve_commit_ref(
    repo_root: pathlib.Path,
    repo_id: str,
    branch: str,
    ref: str | None,
) -> CommitRecord | None:
    """Resolve a commit reference to a ``CommitRecord``.

    *ref* may be:
    - ``None`` / ``"HEAD"`` — the most recent commit on *branch*.
    - A full or abbreviated commit SHA — resolved by prefix scan.
    """
    if ref is None or ref.upper() == "HEAD":
        commit_id = get_head_commit_id(repo_root, branch)
        if commit_id is None:
            return None
        return read_commit(repo_root, commit_id)

    # Try exact match
    commit = read_commit(repo_root, ref)
    if commit is not None:
        return commit

    # Prefix scan
    return _find_commit_by_prefix(repo_root, ref)


def _find_commit_by_prefix(
    repo_root: pathlib.Path, prefix: str
) -> CommitRecord | None:
    """Find the first commit whose ID starts with *prefix*."""
    commits_dir = _commits_dir(repo_root)
    if not commits_dir.exists():
        return None
    for path in commits_dir.glob(f"{prefix}*.json"):
        try:
            return CommitRecord.from_dict(json.loads(path.read_text()))
        except (json.JSONDecodeError, KeyError):
            continue
    return None


def find_commits_by_prefix(
    repo_root: pathlib.Path, prefix: str
) -> list[CommitRecord]:
    """Return all commits whose ID starts with *prefix*."""
    commits_dir = _commits_dir(repo_root)
    if not commits_dir.exists():
        return []
    results: list[CommitRecord] = []
    for path in commits_dir.glob(f"{prefix}*.json"):
        try:
            results.append(CommitRecord.from_dict(json.loads(path.read_text())))
        except (json.JSONDecodeError, KeyError):
            continue
    return results


def get_commits_for_branch(
    repo_root: pathlib.Path,
    repo_id: str,
    branch: str,
) -> list[CommitRecord]:
    """Return all commits on *branch*, newest first, by walking the parent chain."""
    commits: list[CommitRecord] = []
    commit_id = get_head_commit_id(repo_root, branch)
    seen: set[str] = set()
    while commit_id and commit_id not in seen:
        seen.add(commit_id)
        commit = read_commit(repo_root, commit_id)
        if commit is None:
            break
        commits.append(commit)
        commit_id = commit.parent_commit_id
    return commits


def get_all_commits(repo_root: pathlib.Path) -> list[CommitRecord]:
    """Return all commits in the store (order not guaranteed)."""
    commits_dir = _commits_dir(repo_root)
    if not commits_dir.exists():
        return []
    results: list[CommitRecord] = []
    for path in commits_dir.glob("*.json"):
        try:
            results.append(CommitRecord.from_dict(json.loads(path.read_text())))
        except (json.JSONDecodeError, KeyError):
            continue
    return results


# ---------------------------------------------------------------------------
# Snapshot operations
# ---------------------------------------------------------------------------


def write_snapshot(repo_root: pathlib.Path, snapshot: SnapshotRecord) -> None:
    """Persist a snapshot record to ``.muse/snapshots/<snapshot_id>.json``."""
    _snapshots_dir(repo_root).mkdir(parents=True, exist_ok=True)
    path = _snapshot_path(repo_root, snapshot.snapshot_id)
    if path.exists():
        logger.debug("⚠️ Snapshot %s already exists — skipped", snapshot.snapshot_id[:8])
        return
    path.write_text(json.dumps(snapshot.to_dict(), indent=2) + "\n")
    logger.debug("✅ Stored snapshot %s (%d files)", snapshot.snapshot_id[:8], len(snapshot.manifest))


def read_snapshot(repo_root: pathlib.Path, snapshot_id: str) -> SnapshotRecord | None:
    """Load a snapshot record by ID, or ``None`` if it does not exist."""
    path = _snapshot_path(repo_root, snapshot_id)
    if not path.exists():
        return None
    try:
        return SnapshotRecord.from_dict(json.loads(path.read_text()))
    except (json.JSONDecodeError, KeyError) as exc:
        logger.warning("⚠️ Corrupt snapshot file %s: %s", path, exc)
        return None


def get_commit_snapshot_manifest(
    repo_root: pathlib.Path, commit_id: str
) -> dict[str, str] | None:
    """Return the file manifest for the snapshot attached to *commit_id*, or ``None``."""
    commit = read_commit(repo_root, commit_id)
    if commit is None:
        logger.warning("⚠️ Commit %s not found", commit_id[:8])
        return None
    snapshot = read_snapshot(repo_root, commit.snapshot_id)
    if snapshot is None:
        logger.warning(
            "⚠️ Snapshot %s referenced by commit %s not found",
            commit.snapshot_id[:8],
            commit_id[:8],
        )
        return None
    return dict(snapshot.manifest)


def get_head_snapshot_manifest(
    repo_root: pathlib.Path, repo_id: str, branch: str
) -> dict[str, str] | None:
    """Return the manifest of the most recent commit on *branch*, or ``None``."""
    snapshot_id = get_head_snapshot_id(repo_root, repo_id, branch)
    if snapshot_id is None:
        return None
    snapshot = read_snapshot(repo_root, snapshot_id)
    if snapshot is None:
        return None
    return dict(snapshot.manifest)


def get_all_object_ids(repo_root: pathlib.Path, repo_id: str) -> list[str]:
    """Return all object IDs referenced by any snapshot in this repo."""
    object_ids: set[str] = set()
    for commit in get_all_commits(repo_root):
        snapshot = read_snapshot(repo_root, commit.snapshot_id)
        if snapshot is not None:
            object_ids.update(snapshot.manifest.values())
    return sorted(object_ids)


# ---------------------------------------------------------------------------
# Tag operations
# ---------------------------------------------------------------------------


def write_tag(repo_root: pathlib.Path, tag: TagRecord) -> None:
    """Persist a tag record to ``.muse/tags/<repo_id>/<tag_id>.json``."""
    tags_dir = _tags_dir(repo_root, tag.repo_id)
    tags_dir.mkdir(parents=True, exist_ok=True)
    path = tags_dir / f"{tag.tag_id}.json"
    path.write_text(json.dumps(tag.to_dict(), indent=2) + "\n")
    logger.debug("✅ Stored tag %r on commit %s", tag.tag, tag.commit_id[:8])


def get_tags_for_commit(
    repo_root: pathlib.Path, repo_id: str, commit_id: str
) -> list[TagRecord]:
    """Return all tags attached to *commit_id*."""
    tags_dir = _tags_dir(repo_root, repo_id)
    if not tags_dir.exists():
        return []
    results: list[TagRecord] = []
    for path in tags_dir.glob("*.json"):
        try:
            record = TagRecord.from_dict(json.loads(path.read_text()))
            if record.commit_id == commit_id:
                results.append(record)
        except (json.JSONDecodeError, KeyError):
            continue
    return results


def get_all_tags(repo_root: pathlib.Path, repo_id: str) -> list[TagRecord]:
    """Return all tags in this repository."""
    tags_dir = _tags_dir(repo_root, repo_id)
    if not tags_dir.exists():
        return []
    results: list[TagRecord] = []
    for path in tags_dir.glob("*.json"):
        try:
            results.append(TagRecord.from_dict(json.loads(path.read_text())))
        except (json.JSONDecodeError, KeyError):
            continue
    return results


# ---------------------------------------------------------------------------
# Remote sync helpers (push/pull)
# ---------------------------------------------------------------------------


def store_pulled_commit(repo_root: pathlib.Path, commit_data: dict[str, Any]) -> bool:
    """Persist a commit received from a remote into local storage.

    Idempotent — silently skips if the commit already exists. Returns
    ``True`` if the row was newly written, ``False`` if it already existed.
    """
    commit_id = str(commit_data.get("commit_id", ""))
    if not commit_id:
        logger.warning("⚠️ store_pulled_commit: missing commit_id — skipping")
        return False

    if read_commit(repo_root, commit_id) is not None:
        logger.debug("⚠️ Pulled commit %s already exists — skipped", commit_id[:8])
        return False

    commit = CommitRecord.from_dict(commit_data)
    write_commit(repo_root, commit)

    # Ensure a (possibly stub) snapshot record exists.
    snapshot_id = str(commit_data.get("snapshot_id", ""))
    if snapshot_id and read_snapshot(repo_root, snapshot_id) is None:
        stub = SnapshotRecord(
            snapshot_id=snapshot_id,
            manifest=dict(commit_data.get("manifest") or {}),
        )
        write_snapshot(repo_root, stub)

    return True


def store_pulled_object_metadata(
    repo_root: pathlib.Path, object_data: dict[str, Any]
) -> bool:
    """Register an object descriptor received from a remote.

    The actual blob bytes are stored by ``object_store.write_object``.
    This function records that the object is known (for GC and push-delta
    computation). Currently a no-op since objects are content-addressed
    files — presence in ``.muse/objects/`` is the ground truth.
    """
    return True

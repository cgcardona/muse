"""File-based commit and snapshot store for the Muse VCS.

All commit and snapshot metadata is stored as JSON files under ``.muse/`` —
no external database required.

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
from dataclasses import dataclass, field
from typing import TypedDict

from muse.domain import SemVerBump, StructuredDelta

logger = logging.getLogger(__name__)

_COMMITS_DIR = "commits"
_SNAPSHOTS_DIR = "snapshots"
_TAGS_DIR = "tags"


# ---------------------------------------------------------------------------
# Wire-format TypedDicts (JSON-serialisable, used by to_dict / from_dict)
# ---------------------------------------------------------------------------


class CommitDict(TypedDict, total=False):
    """JSON-serialisable representation of a CommitRecord.

    ``structured_delta`` is the typed delta produced by the domain plugin's
    ``diff()`` at commit time. ``None`` on the initial commit (no parent to
    diff against).

    ``sem_ver_bump`` and ``breaking_changes`` are v2 semantic versioning
    metadata.  Absent (treated as ``"none"`` / ``[]``) for legacy records and
    non-code domains.
    """

    commit_id: str
    repo_id: str
    branch: str
    snapshot_id: str
    message: str
    committed_at: str
    parent_commit_id: str | None
    parent2_commit_id: str | None
    author: str
    metadata: dict[str, str]
    structured_delta: StructuredDelta | None
    sem_ver_bump: SemVerBump
    breaking_changes: list[str]


class SnapshotDict(TypedDict):
    """JSON-serialisable representation of a SnapshotRecord."""

    snapshot_id: str
    manifest: dict[str, str]
    created_at: str


class TagDict(TypedDict):
    """JSON-serialisable representation of a TagRecord."""

    tag_id: str
    repo_id: str
    commit_id: str
    tag: str
    created_at: str


class RemoteCommitPayload(TypedDict, total=False):
    """Wire format received from a remote during push/pull.

    All fields are optional because the payload may omit fields that are
    unknown to older protocol versions. Callers validate required fields
    before constructing a CommitRecord from this payload.
    """

    commit_id: str
    repo_id: str
    branch: str
    snapshot_id: str
    message: str
    committed_at: str
    parent_commit_id: str | None
    parent2_commit_id: str | None
    author: str
    metadata: dict[str, str]
    manifest: dict[str, str]


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class CommitRecord:
    """An immutable commit record stored as a JSON file under .muse/commits/.

    v2 fields (``sem_ver_bump`` and ``breaking_changes``) are populated by the
    commit command when a code-domain delta is available.  They default to
    ``"none"`` and ``[]`` for legacy records and non-code domains.
    """

    commit_id: str
    repo_id: str
    branch: str
    snapshot_id: str
    message: str
    committed_at: datetime.datetime
    parent_commit_id: str | None = None
    parent2_commit_id: str | None = None
    author: str = ""
    metadata: dict[str, str] = field(default_factory=dict)
    structured_delta: StructuredDelta | None = None
    sem_ver_bump: SemVerBump = "none"
    breaking_changes: list[str] = field(default_factory=list)

    def to_dict(self) -> CommitDict:
        return CommitDict(
            commit_id=self.commit_id,
            repo_id=self.repo_id,
            branch=self.branch,
            snapshot_id=self.snapshot_id,
            message=self.message,
            committed_at=self.committed_at.isoformat(),
            parent_commit_id=self.parent_commit_id,
            parent2_commit_id=self.parent2_commit_id,
            author=self.author,
            metadata=dict(self.metadata),
            structured_delta=self.structured_delta,
            sem_ver_bump=self.sem_ver_bump,
            breaking_changes=list(self.breaking_changes),
        )

    @classmethod
    def from_dict(cls, d: CommitDict) -> "CommitRecord":
        try:
            committed_at = datetime.datetime.fromisoformat(d["committed_at"])
        except (ValueError, KeyError):
            committed_at = datetime.datetime.now(datetime.timezone.utc)
        return cls(
            commit_id=d["commit_id"],
            repo_id=d["repo_id"],
            branch=d["branch"],
            snapshot_id=d["snapshot_id"],
            message=d["message"],
            committed_at=committed_at,
            parent_commit_id=d.get("parent_commit_id"),
            parent2_commit_id=d.get("parent2_commit_id"),
            author=d.get("author", ""),
            metadata=dict(d.get("metadata") or {}),
            structured_delta=d.get("structured_delta"),
            sem_ver_bump=d.get("sem_ver_bump", "none"),
            breaking_changes=list(d.get("breaking_changes") or []),
        )


@dataclass
class SnapshotRecord:
    """An immutable snapshot record stored as a JSON file under .muse/snapshots/."""

    snapshot_id: str
    manifest: dict[str, str]
    created_at: datetime.datetime = field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc)
    )

    def to_dict(self) -> SnapshotDict:
        return SnapshotDict(
            snapshot_id=self.snapshot_id,
            manifest=self.manifest,
            created_at=self.created_at.isoformat(),
        )

    @classmethod
    def from_dict(cls, d: SnapshotDict) -> "SnapshotRecord":
        try:
            created_at = datetime.datetime.fromisoformat(d["created_at"])
        except (ValueError, KeyError):
            created_at = datetime.datetime.now(datetime.timezone.utc)
        return cls(
            snapshot_id=d["snapshot_id"],
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

    def to_dict(self) -> TagDict:
        return TagDict(
            tag_id=self.tag_id,
            repo_id=self.repo_id,
            commit_id=self.commit_id,
            tag=self.tag,
            created_at=self.created_at.isoformat(),
        )

    @classmethod
    def from_dict(cls, d: TagDict) -> "TagRecord":
        try:
            created_at = datetime.datetime.fromisoformat(d["created_at"])
        except (ValueError, KeyError):
            created_at = datetime.datetime.now(datetime.timezone.utc)
        return cls(
            tag_id=d.get("tag_id", str(uuid.uuid4())),
            repo_id=d["repo_id"],
            commit_id=d["commit_id"],
            tag=d["tag"],
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
    value: str,
) -> bool:
    """Set a single string key in a commit's metadata dict.

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


def walk_commits_between(
    repo_root: pathlib.Path,
    to_commit_id: str,
    from_commit_id: str | None = None,
    max_commits: int = 10_000,
) -> list[CommitRecord]:
    """Return commits reachable from *to_commit_id*, stopping before *from_commit_id*.

    Walks the parent chain from *to_commit_id* backwards.  Returns commits in
    newest-first order (callers can reverse for oldest-first).

    Args:
        repo_root:      Repository root.
        to_commit_id:   Inclusive end of the range.
        from_commit_id: Exclusive start; ``None`` means walk to the initial commit.
        max_commits:    Safety cap.

    Returns:
        List of ``CommitRecord`` objects, newest first.
    """
    commits: list[CommitRecord] = []
    seen: set[str] = set()
    current_id: str | None = to_commit_id
    while current_id and current_id not in seen and len(commits) < max_commits:
        seen.add(current_id)
        if current_id == from_commit_id:
            break
        commit = read_commit(repo_root, current_id)
        if commit is None:
            break
        commits.append(commit)
        current_id = commit.parent_commit_id
    return commits


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


def store_pulled_commit(
    repo_root: pathlib.Path, commit_data: RemoteCommitPayload
) -> bool:
    """Persist a commit received from a remote into local storage.

    Idempotent — silently skips if the commit already exists. Returns
    ``True`` if the row was newly written, ``False`` if it already existed.
    """
    commit_id = commit_data.get("commit_id") or ""
    if not commit_id:
        logger.warning("⚠️ store_pulled_commit: missing commit_id — skipping")
        return False

    if read_commit(repo_root, commit_id) is not None:
        logger.debug("⚠️ Pulled commit %s already exists — skipped", commit_id[:8])
        return False

    commit_dict = CommitDict(
        commit_id=commit_id,
        repo_id=commit_data.get("repo_id") or "",
        branch=commit_data.get("branch") or "",
        snapshot_id=commit_data.get("snapshot_id") or "",
        message=commit_data.get("message") or "",
        committed_at=commit_data.get("committed_at") or "",
        parent_commit_id=commit_data.get("parent_commit_id"),
        parent2_commit_id=commit_data.get("parent2_commit_id"),
        author=commit_data.get("author") or "",
        metadata=dict(commit_data.get("metadata") or {}),
        structured_delta=None,
    )
    write_commit(repo_root, CommitRecord.from_dict(commit_dict))

    # Ensure a (possibly stub) snapshot record exists.
    snapshot_id = commit_data.get("snapshot_id") or ""
    if snapshot_id and read_snapshot(repo_root, snapshot_id) is None:
        manifest: dict[str, str] = dict(commit_data.get("manifest") or {})
        write_snapshot(repo_root, SnapshotRecord(
            snapshot_id=snapshot_id,
            manifest=manifest,
        ))

    return True


def store_pulled_object_metadata(
    repo_root: pathlib.Path, object_data: dict[str, str]
) -> bool:
    """Register an object descriptor received from a remote.

    The actual blob bytes are stored by ``object_store.write_object``.
    This function records that the object is known (for GC and push-delta
    computation). Currently a no-op since objects are content-addressed
    files — presence in ``.muse/objects/`` is the ground truth.
    """
    return True

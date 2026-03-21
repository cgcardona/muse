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
        HEAD                         — "ref: refs/heads/main" | "commit: <sha256>"
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
import re
import uuid
from dataclasses import dataclass, field
from typing import Literal, TypedDict

from muse.core.validation import (
    sanitize_glob_prefix,
    validate_branch_name,
    validate_ref_id,
    validate_repo_id,
)
from muse.domain import SemVerBump, StructuredDelta

logger = logging.getLogger(__name__)

_COMMITS_DIR = "commits"
_SNAPSHOTS_DIR = "snapshots"
_TAGS_DIR = "tags"

# ---------------------------------------------------------------------------
# HEAD file — typed I/O
# ---------------------------------------------------------------------------
#
# Muse HEAD format
# ----------------
# The ``.muse/HEAD`` file is always one of two self-describing forms:
#
#   ref: refs/heads/<branch>    — symbolic ref; HEAD points to a branch
#   commit: <sha256>            — detached HEAD; HEAD points to a commit
#
# The ``ref:`` prefix is adopted from Git because it is the right design:
# a file that can hold two semantically different things should say which
# one it holds.  The ``commit:`` prefix for detached HEAD is a Muse
# extension — Git uses a bare SHA, which is ambiguous (SHA-1? SHA-256?).
# Muse makes the hash algorithm implicit in the prefix, leaving the door
# open for future algorithm identifiers without changing the parsing rule.
#
# There is no backward-compatibility layer; every write site uses
# ``write_head_branch`` / ``write_head_commit`` and every read site uses
# ``read_head`` / ``read_current_branch``.


class SymbolicHead(TypedDict):
    """HEAD points to a named branch."""

    kind: Literal["branch"]
    branch: str


class DetachedHead(TypedDict):
    """HEAD points directly to a commit (detached HEAD state)."""

    kind: Literal["commit"]
    commit_id: str


HeadState = SymbolicHead | DetachedHead


def read_head(repo_root: pathlib.Path) -> HeadState:
    """Parse ``.muse/HEAD`` and return a typed :data:`HeadState`.

    Raises :exc:`ValueError` for any content that does not match the two
    expected forms so callers never receive an ambiguous raw string.
    """
    raw = (repo_root / ".muse" / "HEAD").read_text().strip()
    if raw.startswith("ref: refs/heads/"):
        branch = raw.removeprefix("ref: refs/heads/").strip()
        validate_branch_name(branch)
        return SymbolicHead(kind="branch", branch=branch)
    if raw.startswith("commit: "):
        commit_id = raw.removeprefix("commit: ").strip()
        if not re.fullmatch(r"[0-9a-f]{64}", commit_id):
            raise ValueError(f"Malformed commit ID in HEAD: {commit_id!r}")
        return DetachedHead(kind="commit", commit_id=commit_id)
    raise ValueError(
        f"Malformed HEAD: {raw!r}. "
        "Expected 'ref: refs/heads/<branch>' or 'commit: <sha256>'."
    )


def read_current_branch(repo_root: pathlib.Path) -> str:
    """Return the currently checked-out branch name.

    Raises :exc:`ValueError` when the repository is in detached HEAD state
    so callers that cannot operate without a branch get a clear error
    rather than silently receiving a commit ID as a branch name.
    """
    state = read_head(repo_root)
    if state["kind"] != "branch":
        raise ValueError(
            "Repository is in detached HEAD state. "
            "Run 'muse checkout <branch>' to return to a branch."
        )
    return state["branch"]


def write_head_branch(repo_root: pathlib.Path, branch: str) -> None:
    """Write a symbolic ref to ``.muse/HEAD``.

    Format: ``ref: refs/heads/<branch>`` — self-describing; the ``ref:``
    prefix unambiguously identifies the entry as a symbolic reference.
    """
    validate_branch_name(branch)
    (repo_root / ".muse" / "HEAD").write_text(f"ref: refs/heads/{branch}\n")


def write_head_commit(repo_root: pathlib.Path, commit_id: str) -> None:
    """Write a direct commit reference to ``.muse/HEAD`` (detached HEAD).

    Format: ``commit: <sha256>`` — the ``commit:`` prefix is a Muse
    extension that makes the entry self-describing in all states.  Unlike
    Git (which stores a bare hash), this makes the hash type explicit and
    leaves room for future algorithm prefixes without parsing heuristics.
    """
    if not re.fullmatch(r"[0-9a-f]{64}", commit_id):
        raise ValueError(f"commit_id must be a 64-char hex string, got: {commit_id!r}")
    (repo_root / ".muse" / "HEAD").write_text(f"commit: {commit_id}\n")


# ---------------------------------------------------------------------------
# Wire-format TypedDicts (JSON-serialisable, used by to_dict / from_dict)
# ---------------------------------------------------------------------------


class CommitDict(TypedDict, total=False):
    """JSON-serialisable representation of a CommitRecord.

    ``structured_delta`` is the typed delta produced by the domain plugin's
    ``diff()`` at commit time. ``None`` on the initial commit (no parent to
    diff against).

    ``sem_ver_bump`` and ``breaking_changes`` are semantic versioning
    metadata.  Absent (treated as ``"none"`` / ``[]``) for older records and
    non-code domains.

    Agent provenance fields (all optional, default ``""`` for older records):

    ``agent_id``     Stable identity string for the committing agent or human
                     (e.g. ``"counterpoint-bot"`` or ``"gabriel"``).
    ``model_id``     Model identifier when the author is an AI agent
                     (e.g. ``"claude-opus-4"``).  Empty for human authors.
    ``toolchain_id`` Toolchain that produced the commit
                     (e.g. ``"cursor-agent-v2"``).
    ``prompt_hash``  SHA-256 of the instruction/prompt that triggered this
                     commit.  Privacy-preserving: the hash identifies the
                     prompt without storing its content.
    ``signature``    HMAC-SHA256 hex digest of ``commit_id`` using the
                     agent's shared key.  Verifiable with
                     :func:`muse.core.provenance.verify_commit_hmac`.
    ``signer_key_id`` Fingerprint of the signing key
                     (SHA-256[:16] of the raw key bytes).
    ``format_version`` Schema evolution counter.  Each phase of the Muse
                     supercharge plan that extends the commit record bumps
                     this value.  Readers use it to know which optional fields
                     are present:

                     - ``1`` — base record (commit_id, snapshot_id, parent, message, author)
                     - ``2`` — adds ``structured_delta`` (Phase 1: Typed Delta Algebra)
                     - ``3`` — adds ``sem_ver_bump``, ``breaking_changes``
                               (Phase 2: Domain Schema)
                     - ``4`` — adds agent provenance: ``agent_id``, ``model_id``,
                               ``toolchain_id``, ``prompt_hash``, ``signature``,
                               ``signer_key_id`` (Phase 4: Agent Identity)
                     - ``5`` — adds CRDT annotation fields: ``reviewed_by``
                               (ORSet of reviewer IDs), ``test_runs``
                               (GCounter of test-run events)

                     Old records without this field default to ``1``.
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
    agent_id: str
    model_id: str
    toolchain_id: str
    prompt_hash: str
    signature: str
    signer_key_id: str
    format_version: int
    # CRDT-backed annotation fields (format_version >= 5).
    # ``reviewed_by`` is the logical state of an ORSet: a list of unique
    # reviewer identifiers.  Merging two records takes the union (set join).
    # ``test_runs`` is a GCounter: monotonically increasing test-run count.
    # Both fields are absent in older records and default to [] / 0.
    reviewed_by: list[str]
    test_runs: int


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

    ``sem_ver_bump`` and ``breaking_changes`` are populated by the commit command
    when a code-domain delta is available.  They default to ``"none"`` and ``[]``
    for older records and non-code domains.

    Agent provenance fields default to ``""`` so that existing JSON without
    them deserialises without error.  See :class:`CommitDict` for field semantics.
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
    agent_id: str = ""
    model_id: str = ""
    toolchain_id: str = ""
    prompt_hash: str = ""
    signature: str = ""
    signer_key_id: str = ""
    #: Schema evolution counter — see :class:`CommitDict` for the version table.
    #: Version 5 adds ``reviewed_by`` (ORSet) and ``test_runs`` (GCounter).
    format_version: int = 5
    reviewed_by: list[str] = field(default_factory=list)
    test_runs: int = 0

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
            agent_id=self.agent_id,
            model_id=self.model_id,
            toolchain_id=self.toolchain_id,
            prompt_hash=self.prompt_hash,
            signature=self.signature,
            signer_key_id=self.signer_key_id,
            format_version=self.format_version,
            reviewed_by=list(self.reviewed_by),
            test_runs=self.test_runs,
        )

    @classmethod
    def from_dict(cls, d: CommitDict) -> "CommitRecord":
        try:
            committed_at = datetime.datetime.fromisoformat(d["committed_at"])
        except (ValueError, KeyError):
            logger.warning(
                "⚠️ Commit record has missing or unparseable committed_at; "
                "substituting current time. The record may have been tampered with."
            )
            committed_at = datetime.datetime.now(datetime.timezone.utc)

        # Runtime type guards — JSON can contain anything; fail loud rather than
        # silently carrying non-string IDs into path construction.
        commit_id = d["commit_id"]
        if not isinstance(commit_id, str):
            raise TypeError(f"commit_id must be str, got {type(commit_id).__name__}")
        snapshot_id = d["snapshot_id"]
        if not isinstance(snapshot_id, str):
            raise TypeError(f"snapshot_id must be str, got {type(snapshot_id).__name__}")
        branch = d["branch"]
        if not isinstance(branch, str):
            raise TypeError(f"branch must be str, got {type(branch).__name__}")

        return cls(
            commit_id=commit_id,
            repo_id=d["repo_id"] if isinstance(d.get("repo_id"), str) else "",
            branch=branch,
            snapshot_id=snapshot_id,
            message=d["message"] if isinstance(d.get("message"), str) else "",
            committed_at=committed_at,
            parent_commit_id=d.get("parent_commit_id"),
            parent2_commit_id=d.get("parent2_commit_id"),
            author=d.get("author", ""),
            metadata=dict(d.get("metadata") or {}),
            structured_delta=d.get("structured_delta"),
            sem_ver_bump=d.get("sem_ver_bump", "none"),
            breaking_changes=list(d.get("breaking_changes") or []),
            agent_id=d.get("agent_id", ""),
            model_id=d.get("model_id", ""),
            toolchain_id=d.get("toolchain_id", ""),
            prompt_hash=d.get("prompt_hash", ""),
            signature=d.get("signature", ""),
            signer_key_id=d.get("signer_key_id", ""),
            format_version=d.get("format_version", 1),
            reviewed_by=list(d.get("reviewed_by") or []),
            test_runs=int(d.get("test_runs") or 0),
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
    # Validate repo_id to prevent path traversal via crafted IDs from remote data.
    # Uses a best-effort guard (no path separators or dot-sequences).
    if "/" in repo_id or "\\" in repo_id or ".." in repo_id or not repo_id:
        raise ValueError(f"repo_id {repo_id!r} contains unsafe path components.")
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
    """Load a commit record by ID, or ``None`` if it does not exist.

    Callers that accept user-supplied or remote-supplied commit IDs should
    validate the ID with :func:`~muse.core.validation.validate_ref_id` before
    calling this function.  This function itself accepts any string to support
    internal uses with computed IDs.
    """
    path = _commit_path(repo_root, commit_id)
    if not path.exists():
        return None
    try:
        return CommitRecord.from_dict(json.loads(path.read_text()))
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        logger.warning("⚠️ Corrupt commit file %s: %s", path, exc)
        return None


def overwrite_commit(repo_root: pathlib.Path, commit: CommitRecord) -> None:
    """Overwrite an existing commit record on disk (e.g. for annotation updates).

    Unlike :func:`write_commit`, this function always writes the record even if
    the file already exists.  Use only for annotation fields
    (``reviewed_by``, ``test_runs``) that are semantically additive — never
    for changing history (commit_id, parent, snapshot, message).

    Args:
        repo_root: Repository root.
        commit:    The updated commit record to persist.
    """
    _commits_dir(repo_root).mkdir(parents=True, exist_ok=True)
    path = _commit_path(repo_root, commit.commit_id)
    path.write_text(json.dumps(commit.to_dict(), indent=2) + "\n")
    logger.debug("✅ Updated annotation on commit %s", commit.commit_id[:8])


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
    validate_branch_name(branch)
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

    Performs a safe prefix scan (glob metacharacters stripped from *ref*) so
    user-supplied references cannot glob the entire commits directory.
    """
    if ref is None or ref.upper() == "HEAD":
        commit_id = get_head_commit_id(repo_root, branch)
        if commit_id is None:
            return None
        return read_commit(repo_root, commit_id)

    # Sanitize user-supplied ref before using it in any filesystem operation.
    safe_ref = sanitize_glob_prefix(ref)

    # Try exact match — only if it looks like a full 64-char hex ID.
    try:
        validate_ref_id(safe_ref)
        commit = read_commit(repo_root, safe_ref)
        if commit is not None:
            return commit
    except ValueError:
        pass  # Not a full hex ID — fall through to prefix scan.

    # Prefix scan with sanitized prefix.
    return _find_commit_by_prefix(repo_root, safe_ref)


def _find_commit_by_prefix(
    repo_root: pathlib.Path, prefix: str
) -> CommitRecord | None:
    """Find the first commit whose ID starts with *prefix*.

    Glob metacharacters are stripped from *prefix* before use to prevent
    callers from turning a targeted lookup into an arbitrary directory scan.
    """
    commits_dir = _commits_dir(repo_root)
    if not commits_dir.exists():
        return None
    safe_prefix = sanitize_glob_prefix(prefix)
    for path in commits_dir.glob(f"{safe_prefix}*.json"):
        try:
            return CommitRecord.from_dict(json.loads(path.read_text()))
        except (json.JSONDecodeError, KeyError, TypeError):
            continue
    return None


def find_commits_by_prefix(
    repo_root: pathlib.Path, prefix: str
) -> list[CommitRecord]:
    """Return all commits whose ID starts with *prefix*."""
    commits_dir = _commits_dir(repo_root)
    if not commits_dir.exists():
        return []
    safe_prefix = sanitize_glob_prefix(prefix)
    results: list[CommitRecord] = []
    for path in commits_dir.glob(f"{safe_prefix}*.json"):
        try:
            results.append(CommitRecord.from_dict(json.loads(path.read_text())))
        except (json.JSONDecodeError, KeyError, TypeError):
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
        except (json.JSONDecodeError, KeyError, TypeError):
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
    """Load a snapshot record by ID, or ``None`` if it does not exist.

    Callers that accept user-supplied or remote-supplied snapshot IDs should
    validate the ID with :func:`~muse.core.validation.validate_ref_id` before
    calling this function.  This function itself accepts any string to support
    internal uses with computed IDs.
    """
    path = _snapshot_path(repo_root, snapshot_id)
    if not path.exists():
        return None
    try:
        return SnapshotRecord.from_dict(json.loads(path.read_text()))
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
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
        except (json.JSONDecodeError, KeyError, TypeError):
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
        except (json.JSONDecodeError, KeyError, TypeError):
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

    All ID fields from the remote payload are validated before any filesystem
    operation to prevent path-traversal attacks via crafted remote responses.
    """
    commit_id = commit_data.get("commit_id") or ""
    if not commit_id:
        logger.warning("⚠️ store_pulled_commit: missing commit_id — skipping")
        return False

    try:
        validate_ref_id(commit_id)
    except ValueError as exc:
        logger.warning("⚠️ store_pulled_commit: invalid commit_id %r — %s", commit_id, exc)
        return False

    snapshot_id = commit_data.get("snapshot_id") or ""
    if snapshot_id:
        try:
            validate_ref_id(snapshot_id)
        except ValueError as exc:
            logger.warning(
                "⚠️ store_pulled_commit: invalid snapshot_id %r — %s", snapshot_id, exc
            )
            return False

    branch = commit_data.get("branch") or ""
    if branch:
        try:
            validate_branch_name(branch)
        except ValueError as exc:
            logger.warning(
                "⚠️ store_pulled_commit: invalid branch %r — %s", branch, exc
            )
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

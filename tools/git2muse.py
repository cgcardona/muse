"""git2muse — Replay a Git commit graph into a Muse repository.

Usage
-----
::

    python tools/git2muse.py [--repo-root PATH] [--dry-run] [--verbose]

Strategy
--------
1. Walk ``main`` branch commits oldest-first and create Muse commits on the
   Muse ``main`` branch preserving the original author, timestamp, and message.
2. Walk ``dev`` branch commits oldest-first that are not already on ``main``
   and replay them onto a Muse ``dev`` branch, branching from the correct
   ancestor.
3. Skip merge commits (commits with more than one parent) — they carry no
   unique file-state delta; the Muse DAG is reconstructed faithfully through
   the parent chain on each branch.

For each Git commit the tool:
- Extracts the commit's file tree into ``state/`` using ``git archive``.
- Removes files that Muse should not snapshot (build artefacts, caches, IDE
  files, etc.) according to a hard-coded exclusion list that mirrors
  ``.museignore``.
- Calls the Muse Python API directly (bypassing the CLI) so the original
  Git author name, e-mail, and committer timestamp are preserved verbatim in
  the Muse ``CommitRecord``.
- Updates the Muse branch HEAD ref so the Muse repo tracks the same history.

After a successful run the Muse repo under ``.muse/`` contains a full code-
domain representation of the project history and is ready to push to MuseHub.
"""

from __future__ import annotations

import argparse
import datetime
import logging
import pathlib
import shutil
import subprocess
import sys
import tarfile
import tempfile

# ---------------------------------------------------------------------------
# Bootstrap: make sure the project root is on sys.path so we can import muse
# even when running from the tools/ directory.
# ---------------------------------------------------------------------------
_REPO_ROOT = pathlib.Path(__file__).parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from muse.core.object_store import write_object
from muse.core.reflog import append_reflog
from muse.core.store import (
    CommitRecord,
    SnapshotRecord,
    get_head_commit_id,
    write_commit,
    write_head_branch,
    write_snapshot,
)
from muse.core.snapshot import compute_commit_id, compute_snapshot_id, walk_workdir

logger = logging.getLogger("git2muse")

# ---------------------------------------------------------------------------
# Files / dirs that should never end up in a Muse snapshot.
# These mirror .museignore + the hidden-directory exclusion in walk_workdir.
# ---------------------------------------------------------------------------

_EXCLUDE_PREFIXES: tuple[str, ...] = (
    ".git/",
    ".muse/",
    ".muse",
    ".venv/",
    ".tox/",
    ".mypy_cache/",
    ".pytest_cache/",
    ".hypothesis/",
    ".github/",
    ".DS_Store",
    "artifacts/",
    "__pycache__/",
)

_EXCLUDE_SUFFIXES: tuple[str, ...] = (
    ".pyc",
    ".pyo",
    ".egg-info",
    ".swp",
    ".swo",
    ".tmp",
    "Thumbs.db",
    ".DS_Store",
)


def _should_exclude(rel_path: str) -> bool:
    """Return True if *rel_path* should be excluded from the Muse snapshot."""
    for prefix in _EXCLUDE_PREFIXES:
        if rel_path.startswith(prefix) or rel_path == prefix.rstrip("/"):
            return True
    for suffix in _EXCLUDE_SUFFIXES:
        if rel_path.endswith(suffix):
            return True
    # Skip hidden files/dirs at the top level (mirrors walk_workdir behaviour).
    first_component = rel_path.split("/")[0]
    if first_component.startswith("."):
        return True
    return False


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------


def _git(repo_root: pathlib.Path, *args: str) -> str:
    """Run a git command and return stdout (stripped)."""
    result = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _git_commits_oldest_first(
    repo_root: pathlib.Path,
    branch: str,
    exclude_branches: list[str] | None = None,
) -> list[str]:
    """Return SHA1 hashes oldest-first for *branch*.

    When *exclude_branches* is given, commits reachable from any of those
    branches are excluded (used to extract dev-only commits).
    """
    cmd = ["log", "--topo-order", "--reverse", "--format=%H"]
    if exclude_branches:
        cmd.append(branch)
        for excl in exclude_branches:
            cmd.append(f"^{excl}")
    else:
        cmd.append(branch)
    raw = _git(repo_root, *cmd)
    return [line for line in raw.splitlines() if line.strip()]


_META_SEP = "|||GIT2MUSE|||"


def _git_commit_meta(repo_root: pathlib.Path, sha: str) -> dict[str, str]:
    """Return author name, email, timestamp, and message for *sha*."""
    fmt = f"%an{_META_SEP}%ae{_META_SEP}%at{_META_SEP}%B"
    raw = _git(repo_root, "show", "-s", f"--format={fmt}", sha)
    parts = raw.split(_META_SEP, 3)
    if len(parts) < 4:
        return {"name": "unknown", "email": "", "ts": "0", "message": sha[:12]}
    name, email, ts, message = parts
    return {
        "name": name.strip(),
        "email": email.strip(),
        "ts": ts.strip(),
        "message": message.strip(),
    }


def _git_parent_shas(repo_root: pathlib.Path, sha: str) -> list[str]:
    """Return parent SHA1s for *sha* (empty list for root commits)."""
    raw = _git(repo_root, "log", "-1", "--format=%P", sha)
    return [p for p in raw.split() if p]


def _is_merge_commit(repo_root: pathlib.Path, sha: str) -> bool:
    return len(_git_parent_shas(repo_root, sha)) > 1


def _extract_tree_to(
    repo_root: pathlib.Path,
    sha: str,
    dest: pathlib.Path,
) -> None:
    """Extract the git tree for *sha* into *dest*, applying exclusions."""
    # Wipe and recreate dest for a clean slate.
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)

    # git archive produces a tar stream of the commit tree.
    archive = subprocess.run(
        ["git", "archive", "--format=tar", sha],
        cwd=repo_root,
        capture_output=True,
        check=True,
    )
    with tempfile.NamedTemporaryFile(suffix=".tar", delete=False) as tmp:
        tmp.write(archive.stdout)
        tmp_path = pathlib.Path(tmp.name)

    try:
        with tarfile.open(tmp_path) as tf:
            for member in tf.getmembers():
                if not member.isfile():
                    continue
                # removeprefix strips only the literal "./" tar prefix, not
                # individual characters — lstrip("./") was incorrectly turning
                # ".cursorignore" into "cursorignore" and ".github/" into "github/".
                rel = member.name.removeprefix("./")
                if _should_exclude(rel):
                    continue
                target = dest / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                f = tf.extractfile(member)
                if f is not None:
                    target.write_bytes(f.read())
    finally:
        tmp_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Muse snapshot helpers (bypass CLI to preserve git metadata)
# ---------------------------------------------------------------------------


def _build_manifest(workdir: pathlib.Path) -> dict[str, str]:
    """Walk *workdir* using Muse's canonical walker and return a manifest.

    Delegates to :func:`muse.core.snapshot.walk_workdir` so the exclusion
    rules, hidden-file logic, and path normalisation are always in sync with
    what ``muse commit`` produces.  Using the same walker prevents the tool
    from drifting out of sync as Muse evolves.
    """
    return walk_workdir(workdir)


def _store_objects(
    repo_root: pathlib.Path,
    workdir: pathlib.Path,
    manifest: dict[str, str],
) -> None:
    """Write all objects referenced in *manifest* to the object store."""
    for rel, oid in manifest.items():
        fpath = workdir / rel
        if not fpath.exists():
            logger.warning("⚠️ Missing file in workdir: %s", rel)
            continue
        content = fpath.read_bytes()
        write_object(repo_root, oid, content)


# ---------------------------------------------------------------------------
# Branch ref helpers (direct file I/O — mirrors store.py internal logic)
# ---------------------------------------------------------------------------


def _refs_dir(repo_root: pathlib.Path) -> pathlib.Path:
    return repo_root / ".muse" / "refs" / "heads"


def _set_branch_head(
    repo_root: pathlib.Path, branch: str, commit_id: str
) -> None:
    ref_path = _refs_dir(repo_root) / branch
    ref_path.parent.mkdir(parents=True, exist_ok=True)
    ref_path.write_text(commit_id + "\n")


def _get_branch_head(repo_root: pathlib.Path, branch: str) -> str | None:
    ref_path = _refs_dir(repo_root) / branch
    if not ref_path.exists():
        return None
    return ref_path.read_text().strip() or None


def _set_head_ref(repo_root: pathlib.Path, branch: str) -> None:
    write_head_branch(repo_root, branch)


def _ensure_branch_exists(repo_root: pathlib.Path, branch: str) -> None:
    _refs_dir(repo_root).mkdir(parents=True, exist_ok=True)
    ref_path = _refs_dir(repo_root) / branch
    if not ref_path.exists():
        ref_path.write_text("")


# ---------------------------------------------------------------------------
# Core replay logic
# ---------------------------------------------------------------------------


def _replay_commit(
    repo_root: pathlib.Path,
    workdir: pathlib.Path,
    git_sha: str,
    muse_branch: str,
    parent_muse_id: str | None,
    meta: dict[str, str],
    repo_id: str,
    dry_run: bool,
) -> str:
    """Replay one Git commit into the Muse object store.

    Returns the new Muse commit ID.
    """
    # Build manifest from workdir (already populated by caller).
    manifest = _build_manifest(workdir)

    # Compute snapshot ID deterministically.
    snapshot_id = compute_snapshot_id(manifest)

    # Build CommitRecord with original Git metadata.
    committed_at = datetime.datetime.fromtimestamp(
        int(meta["ts"]), tz=datetime.timezone.utc
    )
    author = f"{meta['name']} <{meta['email']}>"
    message = meta["message"] or git_sha[:12]

    committed_at_iso = committed_at.isoformat()
    parent_ids = [parent_muse_id] if parent_muse_id else []
    commit_id = compute_commit_id(
        parent_ids=parent_ids,
        snapshot_id=snapshot_id,
        message=message,
        committed_at_iso=committed_at_iso,
    )

    if dry_run:
        logger.info(
            "[dry-run] Would create commit %s (git: %s) on %s | %s",
            commit_id[:12],
            git_sha[:12],
            muse_branch,
            message[:60],
        )
        return commit_id

    # Write objects into the content-addressed store.
    _store_objects(repo_root, workdir, manifest)

    # Write snapshot record.
    snap = SnapshotRecord(snapshot_id=snapshot_id, manifest=manifest)
    write_snapshot(repo_root, snap)

    # Write commit record.
    record = CommitRecord(
        commit_id=commit_id,
        repo_id=repo_id,
        branch=muse_branch,
        snapshot_id=snapshot_id,
        message=message,
        committed_at=committed_at,
        parent_commit_id=parent_muse_id,
        author=author,
    )
    write_commit(repo_root, record)

    # Advance branch HEAD and record in reflog so `muse reflog` works.
    _set_branch_head(repo_root, muse_branch, commit_id)
    append_reflog(
        repo_root,
        muse_branch,
        old_id=parent_muse_id,
        new_id=commit_id,
        author=author,
        operation=f"git2muse: {message[:60]}",
    )

    return commit_id


def _replay_branch(
    repo_root: pathlib.Path,
    workdir: pathlib.Path,
    git_shas: list[str],
    muse_branch: str,
    start_parent_muse_id: str | None,
    repo_id: str,
    dry_run: bool,
    verbose: bool,
) -> dict[str, str]:
    """Replay a list of git SHAs (oldest first) onto *muse_branch*.

    Returns a mapping of git_sha → muse_commit_id for every replayed commit.
    """
    _ensure_branch_exists(repo_root, muse_branch)

    git_to_muse: dict[str, str] = {}
    parent_muse_id = start_parent_muse_id
    total = len(git_shas)

    for i, git_sha in enumerate(git_shas, 1):
        meta = _git_commit_meta(repo_root, git_sha)

        if verbose or i % 10 == 0 or i == 1 or i == total:
            logger.info(
                "[%s] %d/%d  git:%s  '%s'",
                muse_branch,
                i,
                total,
                git_sha[:12],
                meta["message"][:60],
            )

        # Populate state/ with this commit's tree.
        if not dry_run:
            _extract_tree_to(repo_root, git_sha, workdir)

        muse_id = _replay_commit(
            repo_root=repo_root,
            workdir=workdir,
            git_sha=git_sha,
            muse_branch=muse_branch,
            parent_muse_id=parent_muse_id,
            meta=meta,
            repo_id=repo_id,
            dry_run=dry_run,
        )

        git_to_muse[git_sha] = muse_id
        parent_muse_id = muse_id

    return git_to_muse


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _load_repo_id(repo_root: pathlib.Path) -> str:
    import json
    repo_json = repo_root / ".muse" / "repo.json"
    data: dict[str, str] = json.loads(repo_json.read_text())
    return data["repo_id"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Replay a Git commit graph into a Muse repository."
    )
    parser.add_argument(
        "--repo-root",
        type=pathlib.Path,
        default=_REPO_ROOT,
        help="Path to the repository root (default: parent of this script).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log what would happen without writing anything.",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Log every commit (default: log every 10 + first/last).",
    )
    parser.add_argument(
        "--branch",
        default="all",
        help="Which git branch(es) to replay: 'main', 'dev', or 'all' (default).",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s  %(message)s",
    )

    repo_root: pathlib.Path = args.repo_root.resolve()
    dry_run: bool = args.dry_run
    verbose: bool = args.verbose
    branch_arg: str = args.branch

    # Auto-initialise if .muse/ doesn't exist yet.
    if not (repo_root / ".muse" / "repo.json").exists():
        logger.info("No .muse/repo.json found — running 'muse init --domain code' …")
        if not dry_run:
            result = subprocess.run(
                ["muse", "init", "--domain", "code"],
                cwd=repo_root,
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                logger.error("❌ muse init failed:\n%s", result.stderr)
                return 1
            logger.info("✅ muse init --domain code succeeded")

    repo_id = _load_repo_id(repo_root)
    logger.info("✅ Muse repo ID: %s", repo_id)

    # Use a temp directory for git archive extraction — the repo root IS the
    # working tree and must never be wiped between replays.
    with tempfile.TemporaryDirectory(prefix="git2muse-") as _tmpdir:
        workdir = pathlib.Path(_tmpdir)

        # -----------------------------------------------------------------------
        # Phase 1: main branch
        # -----------------------------------------------------------------------
        all_git_to_muse: dict[str, str] = {}

        if branch_arg in ("main", "all"):
            logger.info("━━━ Phase 1: replaying main branch ━━━")
            main_shas = _git_commits_oldest_first(repo_root, "main")
            # Skip merge commits — they add no unique tree delta.
            main_shas = [
                s for s in main_shas
                if not _is_merge_commit(repo_root, s)
            ]
            logger.info("  %d non-merge commits on main", len(main_shas))

            _set_head_ref(repo_root, "main")
            mapping = _replay_branch(
                repo_root=repo_root,
                workdir=workdir,
                git_shas=main_shas,
                muse_branch="main",
                start_parent_muse_id=None,
                repo_id=repo_id,
                dry_run=dry_run,
                verbose=verbose,
            )
            all_git_to_muse.update(mapping)
            logger.info("✅ main: %d commits written", len(mapping))

        # -----------------------------------------------------------------------
        # Phase 2: dev branch (commits not reachable from main)
        # -----------------------------------------------------------------------
        if branch_arg in ("dev", "all"):
            logger.info("━━━ Phase 2: replaying dev branch ━━━")
            dev_only_shas = _git_commits_oldest_first(
                repo_root, "dev", exclude_branches=["main"]
            )
            dev_only_shas = [
                s for s in dev_only_shas
                if not _is_merge_commit(repo_root, s)
            ]
            logger.info("  %d dev-only non-merge commits", len(dev_only_shas))

            if dev_only_shas:
                # Find the git parent of the oldest dev-only commit — it should
                # already be in all_git_to_muse (it's a main commit).
                oldest_dev_sha = dev_only_shas[0]
                git_parents = _git_parent_shas(repo_root, oldest_dev_sha)
                branch_parent_muse_id: str | None = None
                for gp in git_parents:
                    if gp in all_git_to_muse:
                        branch_parent_muse_id = all_git_to_muse[gp]
                        break
                if branch_parent_muse_id is None:
                    # Fall back to current main HEAD.
                    branch_parent_muse_id = _get_branch_head(repo_root, "main")

                _set_head_ref(repo_root, "dev")
                mapping = _replay_branch(
                    repo_root=repo_root,
                    workdir=workdir,
                    git_shas=dev_only_shas,
                    muse_branch="dev",
                    start_parent_muse_id=branch_parent_muse_id,
                    repo_id=repo_id,
                    dry_run=dry_run,
                    verbose=verbose,
                )
                all_git_to_muse.update(mapping)
                logger.info("✅ dev: %d commits written", len(mapping))
            else:
                logger.info("  dev has no unique commits beyond main — skipping")

    # Leave HEAD pointing at main.
    if not dry_run:
        _set_head_ref(repo_root, "main")

    # Summary.
    main_count = len(all_git_to_muse)
    logger.info("━━━ Done ━━━  total Muse commits written: %d", main_count)

    return 0


if __name__ == "__main__":
    sys.exit(main())

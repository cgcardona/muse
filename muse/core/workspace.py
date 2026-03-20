"""Workspace management — compose multiple Muse repositories.

A *workspace* is a collection of related Muse repositories that are developed
together.  Think of a film score that references a sound library, a machine
learning pipeline that includes a dataset repo, or a multi-service codebase
where each service lives in its own Muse repo.

Design
------
Workspaces are distinct from worktrees:

- A **worktree** is one checkout of *one* repo with *one* ``.muse/`` store.
- A **workspace** is an envelope that *links* multiple separate repos together.

The workspace manifest lives at ``.muse/workspace.toml``::

    [[members]]
    name = "core"
    url = "https://musehub.ai/acme/core"
    path = "repos/core"       # relative to workspace root
    branch = "main"           # pinned branch

    [[members]]
    name = "dataset"
    url = "https://musehub.ai/acme/dataset"
    path = "repos/dataset"
    branch = "v2"

Agent workflow
--------------
Each member repo is a fully independent Muse repository.  Agents can commit
to member repos independently and the workspace provides a unified status view
and one-shot sync.

``muse workspace sync`` walks all members and runs ``muse fetch`` + ``muse pull``
so the workspace root always has the latest HEAD for every pinned branch.
"""

from __future__ import annotations

import json
import logging
import pathlib
import subprocess
from dataclasses import dataclass
from typing import TypedDict

logger = logging.getLogger(__name__)

_WORKSPACE_FILE = ".muse/workspace.toml"


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


class WorkspaceMemberDict(TypedDict):
    """One entry in the workspace manifest."""

    name: str
    url: str
    path: str
    branch: str


class WorkspaceManifestDict(TypedDict):
    """Top-level workspace manifest."""

    members: list[WorkspaceMemberDict]


@dataclass
class WorkspaceMemberStatus:
    """Runtime status of one workspace member."""

    name: str
    path: pathlib.Path
    branch: str
    url: str
    present: bool
    head_commit: str | None


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def _workspace_path(repo_root: pathlib.Path) -> pathlib.Path:
    return repo_root / ".muse" / "workspace.toml"


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def _load_manifest(repo_root: pathlib.Path) -> WorkspaceManifestDict | None:
    import tomllib

    path = _workspace_path(repo_root)
    if not path.exists():
        return None
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("⚠️ Could not read workspace manifest: %s", exc)
        return None
    members: list[WorkspaceMemberDict] = []
    for m in raw.get("members", []):
        if not isinstance(m, dict):
            continue
        members.append(WorkspaceMemberDict(
            name=str(m.get("name", "")),
            url=str(m.get("url", "")),
            path=str(m.get("path", "")),
            branch=str(m.get("branch", "main")),
        ))
    return WorkspaceManifestDict(members=members)


def _save_manifest(repo_root: pathlib.Path, manifest: WorkspaceManifestDict) -> None:
    path = _workspace_path(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    for m in manifest["members"]:
        lines.append("[[members]]")
        lines.append(f'name   = "{m["name"]}"')
        lines.append(f'url    = "{m["url"]}"')
        lines.append(f'path   = "{m["path"]}"')
        lines.append(f'branch = "{m["branch"]}"')
        lines.append("")
    tmp = path.with_suffix(".tmp")
    tmp.write_text("\n".join(lines), encoding="utf-8")
    tmp.replace(path)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def add_workspace_member(
    repo_root: pathlib.Path,
    name: str,
    url: str,
    path: str = "",
    branch: str = "main",
) -> None:
    """Register a new member repository in the workspace manifest.

    Args:
        repo_root:  The workspace root (where ``.muse/`` lives).
        name:       Short identifier for this member.
        url:        URL or local path to the member Muse repository.
        path:       Relative checkout path inside the workspace (default: ``repos/<name>``).
        branch:     Branch to track (default: ``main``).

    Raises:
        ValueError: If a member with the same name already exists.
    """
    from muse.core.validation import validate_branch_name

    validate_branch_name(name)
    effective_path = path or f"repos/{name}"

    manifest = _load_manifest(repo_root) or WorkspaceManifestDict(members=[])
    for m in manifest["members"]:
        if m["name"] == name:
            raise ValueError(f"Workspace member '{name}' already exists.")

    manifest["members"].append(WorkspaceMemberDict(
        name=name,
        url=url,
        path=effective_path,
        branch=branch,
    ))
    _save_manifest(repo_root, manifest)


def remove_workspace_member(repo_root: pathlib.Path, name: str) -> None:
    """Remove a member from the workspace manifest (does not delete the directory).

    Raises:
        ValueError: If no member with that name exists.
    """
    manifest = _load_manifest(repo_root)
    if manifest is None:
        raise ValueError("No workspace manifest found.")
    before = len(manifest["members"])
    manifest["members"] = [m for m in manifest["members"] if m["name"] != name]
    if len(manifest["members"]) == before:
        raise ValueError(f"Workspace member '{name}' not found.")
    _save_manifest(repo_root, manifest)


def list_workspace_members(repo_root: pathlib.Path) -> list[WorkspaceMemberStatus]:
    """Return status for every workspace member."""
    manifest = _load_manifest(repo_root)
    if manifest is None:
        return []

    results: list[WorkspaceMemberStatus] = []
    for m in manifest["members"]:
        member_path = repo_root / m["path"]
        present = member_path.exists() and (member_path / ".muse").exists()
        head_commit: str | None = None
        if present:
            try:
                from muse.core.store import get_head_commit_id
                head_commit = get_head_commit_id(member_path, m["branch"])
            except Exception:
                pass
        results.append(WorkspaceMemberStatus(
            name=m["name"],
            path=member_path,
            branch=m["branch"],
            url=m["url"],
            present=present,
            head_commit=head_commit,
        ))
    return results


def sync_workspace_member(
    repo_root: pathlib.Path,
    member: WorkspaceMemberDict,
) -> str:
    """Clone or pull the latest state for one workspace member.

    Returns:
        A status string: 'cloned', 'pulled', or 'error: <msg>'.
    """
    member_path = repo_root / member["path"]

    if not member_path.exists() or not (member_path / ".muse").exists():
        # Clone from URL.
        member_path.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            ["muse", "clone", member["url"], str(member_path)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return f"error: {result.stderr.strip()[:200]}"
        return "cloned"

    # Pull to get latest.
    result = subprocess.run(
        ["muse", "pull", "--branch", member["branch"]],
        capture_output=True,
        text=True,
        cwd=str(member_path),
    )
    if result.returncode != 0:
        return f"error: {result.stderr.strip()[:200]}"
    return "pulled"


def sync_workspace(
    repo_root: pathlib.Path,
    member_name: str | None = None,
) -> list[tuple[str, str]]:
    """Sync all (or one named) workspace members.

    Returns:
        List of (member_name, status_str) pairs.
    """
    manifest = _load_manifest(repo_root)
    if manifest is None:
        return []

    targets = (
        [m for m in manifest["members"] if m["name"] == member_name]
        if member_name is not None
        else manifest["members"]
    )

    results: list[tuple[str, str]] = []
    for m in targets:
        status = sync_workspace_member(repo_root, m)
        results.append((m["name"], status))
    return results

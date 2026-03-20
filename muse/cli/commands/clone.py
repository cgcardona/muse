"""muse clone — create a local copy of a remote Muse repository.

Downloads the complete commit history, snapshots, and objects from a remote
MuseHub repository into a new local directory.  After cloning:

- A full ``.muse/`` directory is created with the remote's repo_id and domain.
- The ``origin`` remote is configured to point at the source URL.
- The default branch is checked out into the working tree (the cloned directory root).

Usage
-----

    muse clone <url>            Clone into a directory named after the last URL segment.
    muse clone <url> <dir>      Clone into a specific directory.

The target directory must not already contain a ``.muse/`` repository.
"""

from __future__ import annotations

import datetime
import json
import logging
import pathlib
import shutil
import uuid

import typer

from muse.cli.config import set_remote, set_remote_head, set_upstream
from muse.core.errors import ExitCode
from muse.core.pack import apply_pack
from muse.core.store import get_all_commits, read_commit, read_snapshot
from muse.core.transport import HttpTransport, TransportError
from muse.core.workdir import apply_manifest

logger = logging.getLogger(__name__)

app = typer.Typer()

_SCHEMA_VERSION = "2"

_DEFAULT_CONFIG = """\
[user]
name = ""
email = ""

[auth]
token = ""

[remotes]

[domain]
# Domain-specific configuration keys depend on the active domain.
"""


def _infer_dir_name(url: str) -> str:
    """Derive a local directory name from the last non-empty segment of *url*."""
    stripped = url.rstrip("/")
    last = stripped.rsplit("/", 1)[-1]
    return last if last else "muse-repo"


def _init_muse_dir(
    target: pathlib.Path,
    repo_id: str,
    domain: str,
    default_branch: str,
) -> None:
    """Create the ``.muse/`` directory tree inside *target*.

    Mirrors the layout created by ``muse init`` but uses the remote's
    ``repo_id`` and ``domain`` so local and remote identity stay in sync.
    """
    muse_dir = target / ".muse"
    (muse_dir / "refs" / "heads").mkdir(parents=True, exist_ok=True)
    for subdir in ("objects", "commits", "snapshots"):
        (muse_dir / subdir).mkdir(exist_ok=True)

    repo_meta: dict[str, str] = {
        "repo_id": repo_id,
        "schema_version": _SCHEMA_VERSION,
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "domain": domain,
    }
    (muse_dir / "repo.json").write_text(json.dumps(repo_meta, indent=2) + "\n")
    (muse_dir / "HEAD").write_text(f"refs/heads/{default_branch}\n")
    (muse_dir / "refs" / "heads" / default_branch).write_text("")
    (muse_dir / "config.toml").write_text(_DEFAULT_CONFIG)

def _restore_working_tree(root: pathlib.Path, commit_id: str) -> None:
    """Restore the working tree to the snapshot referenced by *commit_id*."""
    commit = read_commit(root, commit_id)
    if commit is None:
        return
    snap = read_snapshot(root, commit.snapshot_id)
    if snap is None:
        return
    apply_manifest(root, snap.manifest)


@app.callback(invoke_without_command=True)
def clone(
    ctx: typer.Context,
    url: str = typer.Argument(..., help="URL of the remote Muse repository to clone."),
    directory: str | None = typer.Argument(
        None,
        help="Local directory to clone into. Defaults to the last segment of the URL.",
    ),
    branch: str | None = typer.Option(
        None, "--branch", "-b", help="Branch to check out after cloning (default: remote default branch)."
    ),
) -> None:
    """Create a local copy of a remote Muse repository.

    Downloads the full commit history, snapshots, and objects and checks out
    the default branch.  The cloned repo has ``origin`` set to *url*.
    """
    # clone does not need to be inside a Muse repo — it creates a new one.
    target_name = directory or _infer_dir_name(url)
    target = pathlib.Path.cwd() / target_name

    if (target / ".muse").exists():
        typer.echo(f"❌ '{target}' is already a Muse repository.")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    transport = HttpTransport()

    # Fetch remote repository info (branch heads, domain, default branch).
    typer.echo(f"Cloning from {url} …")
    try:
        info = transport.fetch_remote_info(url, token=None)
    except TransportError as exc:
        typer.echo(f"❌ Cannot reach remote: {exc}")
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)

    remote_repo_id = info["repo_id"] or str(uuid.uuid4())
    domain = info["domain"] or "midi"
    default_branch = branch or info["default_branch"] or "main"

    if not info["branch_heads"]:
        typer.echo("❌ Remote repository has no branches (empty repository).")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    default_commit_id = info["branch_heads"].get(default_branch)
    if default_commit_id is None:
        # Fall back to the first available branch.
        first_branch, default_commit_id = next(iter(info["branch_heads"].items()))
        typer.echo(
            f"  ⚠️ Branch '{default_branch}' not found; checking out '{first_branch}'."
        )
        default_branch = first_branch

    # Initialise local repository structure.
    target.mkdir(parents=True, exist_ok=True)
    try:
        _init_muse_dir(target, remote_repo_id, domain, default_branch)
    except OSError as exc:
        typer.echo(f"❌ Failed to create repository at '{target}': {exc}")
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)

    # Fetch full pack (no have — we want everything).
    want = list(info["branch_heads"].values())
    try:
        bundle = transport.fetch_pack(url, token=None, want=want, have=[])
    except TransportError as exc:
        typer.echo(f"❌ Fetch failed: {exc}")
        shutil.rmtree(target, ignore_errors=True)
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)

    apply_result = apply_pack(target, bundle)

    # Write branch head refs for every remote branch.
    for b, cid in info["branch_heads"].items():
        ref_file = target / ".muse" / "refs" / "heads" / b
        ref_file.parent.mkdir(parents=True, exist_ok=True)
        ref_file.write_text(cid)
        set_remote_head("origin", b, cid, target)

    # Configure origin remote and upstream tracking.
    set_remote("origin", url, target)
    set_upstream(default_branch, "origin", target)

    # Restore working tree from the default branch HEAD.
    _restore_working_tree(target, default_commit_id)

    commits_received = len(bundle.get("commits") or [])
    typer.echo(
        f"✅ Cloned into '{target_name}' — "
        f"{commits_received} commit(s), {apply_result['objects_written']} object(s), "
        f"domain={domain}, branch={default_branch} ({default_commit_id[:8]})"
    )

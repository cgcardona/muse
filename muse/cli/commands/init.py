"""muse init — initialise a new Muse repository.

Creates the ``.muse/`` directory tree in the current working directory.

Layout::

    .muse/
        repo.json          — repo_id, schema_version, created_at
        HEAD               — symbolic ref → refs/heads/main
        refs/heads/main    — empty (no commits yet)
        config.toml        — [user], [auth], [remotes] stubs
        objects/           — content-addressed blobs (created on first commit)
        commits/           — commit records (JSON, one file per commit)
        snapshots/         — snapshot manifests (JSON, one file per snapshot)
    muse-work/             — working tree (absent for --bare repos)
"""
from __future__ import annotations

import datetime
import json
import logging
import pathlib
import shutil
import uuid

import typer

from muse.core.errors import ExitCode
from muse.core.repo import find_repo_root

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
"""

_BARE_CONFIG = """\
[core]
bare = true

[user]
name = ""
email = ""

[auth]
token = ""

[remotes]
"""


@app.callback(invoke_without_command=True)
def init(
    ctx: typer.Context,
    bare: bool = typer.Option(False, "--bare", help="Initialise as a bare repository (no muse-work/)."),
    template: str | None = typer.Option(None, "--template", metavar="PATH", help="Copy PATH contents into muse-work/."),
    default_branch: str = typer.Option("main", "--default-branch", metavar="BRANCH", help="Name of the initial branch."),
    force: bool = typer.Option(False, "--force", help="Re-initialise even if already a Muse repository."),
) -> None:
    """Initialise a new Muse repository in the current directory."""
    cwd = pathlib.Path.cwd()
    muse_dir = cwd / ".muse"

    template_path: pathlib.Path | None = None
    if template is not None:
        template_path = pathlib.Path(template)
        if not template_path.is_dir():
            typer.echo(f"❌ Template path is not a directory: {template_path}")
            raise typer.Exit(code=ExitCode.USER_ERROR)

    already_exists = muse_dir.is_dir()
    if already_exists and not force:
        typer.echo(f"Already a Muse repository at {cwd}.\nUse --force to reinitialise.")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    existing_repo_id: str | None = None
    if force and already_exists:
        repo_json = muse_dir / "repo.json"
        if repo_json.exists():
            try:
                existing_repo_id = json.loads(repo_json.read_text()).get("repo_id")
            except (json.JSONDecodeError, OSError):
                pass

    try:
        (muse_dir / "refs" / "heads").mkdir(parents=True, exist_ok=True)
        for subdir in ("objects", "commits", "snapshots"):
            (muse_dir / subdir).mkdir(exist_ok=True)

        repo_id = existing_repo_id or str(uuid.uuid4())
        repo_meta: dict[str, str | bool] = {
            "repo_id": repo_id,
            "schema_version": _SCHEMA_VERSION,
            "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
        if bare:
            repo_meta["bare"] = True
        (muse_dir / "repo.json").write_text(json.dumps(repo_meta, indent=2) + "\n")

        (muse_dir / "HEAD").write_text(f"refs/heads/{default_branch}\n")

        ref_file = muse_dir / "refs" / "heads" / default_branch
        if not ref_file.exists() or force:
            ref_file.write_text("")

        config_path = muse_dir / "config.toml"
        if not config_path.exists():
            config_path.write_text(_BARE_CONFIG if bare else _DEFAULT_CONFIG)

        if not bare:
            work_dir = cwd / "muse-work"
            work_dir.mkdir(exist_ok=True)
            if template_path is not None:
                for item in template_path.iterdir():
                    dest = work_dir / item.name
                    if item.is_dir():
                        shutil.copytree(item, dest, dirs_exist_ok=True)
                    else:
                        shutil.copy2(item, dest)

    except PermissionError:
        typer.echo(f"❌ Permission denied: cannot write to {cwd}.")
        raise typer.Exit(code=ExitCode.USER_ERROR)
    except OSError as exc:
        typer.echo(f"❌ Failed to initialise repository: {exc}")
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)

    action = "Reinitialised" if (force and already_exists) else "Initialised"
    kind = "bare " if bare else ""
    typer.echo(f"✅ {action} {kind}Muse repository in {muse_dir}")

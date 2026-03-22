"""muse init — initialise a new Muse repository.

Creates the ``.muse/`` directory tree in the current working directory.

Layout::

    .muse/
        repo.json           — repo_id, schema_version, domain, created_at
        HEAD                — symbolic ref → refs/heads/main
        refs/heads/main     — empty (no commits yet)
        config.toml         — [user], [auth], [remotes], [domain] stubs
        objects/            — content-addressed blobs (SHA-256 sharded)
        commits/            — commit records (JSON, one file per commit)
        snapshots/          — snapshot manifests (JSON, one file per snapshot)
    .museattributes         — TOML merge strategy overrides (working-tree only)
    .museignore             — TOML ignore rules (working-tree only)

The repository root IS the working tree.  There is no ``state/`` subdirectory.
Bare repositories (``--bare``) have no working tree; they store only ``.muse/``
and do not receive ``.museattributes`` or ``.museignore``.
"""

from __future__ import annotations

import argparse
import datetime
import json
import logging
import pathlib
import shutil
import sys
import uuid

from muse._version import __version__ as _SCHEMA_VERSION
from muse.core.errors import ExitCode
from muse.core.store import write_head_branch
from muse.core.validation import validate_branch_name, validate_domain_name

logger = logging.getLogger(__name__)

_DEFAULT_CONFIG = """\
[user]
name = ""
email = ""
type = "human"    # "human" | "agent"

[hub]
# url = "https://musehub.ai"
# Run `muse hub connect <url>` to attach this repo to MuseHub.
# Run `muse auth login` to authenticate.
# Credentials are stored in ~/.muse/identity.toml — never here.

[remotes]

[domain]
# Domain-specific configuration. Keys depend on the active domain plugin.
"""

_BARE_CONFIG = """\
[core]
bare = true

[user]
name = ""
email = ""
type = "human"    # "human" | "agent"

[hub]
# url = "https://musehub.ai"

[remotes]

[domain]
"""

_MUSEIGNORE_HEADER = """\
# .museignore — snapshot exclusion rules for this repository.

"""

_MUSEIGNORE_GLOBAL = """\
[global]
patterns = [
    ".DS_Store",
    "Thumbs.db",
    "*.tmp",
    "*.swp",
    "*.swo",
]
"""

_MUSEIGNORE_DOMAIN_BLOCKS: dict[str, str] = {
    "midi": """\
[domain.midi]
patterns = [
    "*.bak",
    "*.autosave",
    "/renders/",
    "/exports/",
    "/previews/",
]
""",
    "code": """\
[domain.code]
patterns = [
    "__pycache__/",
    "*.pyc",
    "*.pyo",
    "node_modules/",
    "dist/",
    "build/",
    ".venv/",
    "venv/",
    ".tox/",
    "*.egg-info/",
]
""",
}


def _museignore_template(domain: str) -> str:
    """Return a TOML ``.museignore`` template pre-filled for *domain*.

        The ``[global]`` section covers cross-domain OS artifacts.  The
        ``[domain.<name>]`` section lists patterns specific to the chosen domain.
        Patterns from other domains are never loaded at snapshot time.

    """
    domain_block = _MUSEIGNORE_DOMAIN_BLOCKS.get(domain, f"""\
[domain.{domain}]
# patterns = []
""")
    return _MUSEIGNORE_HEADER + _MUSEIGNORE_GLOBAL + "\n" + domain_block


def _museattributes_template(domain: str) -> str:
    """Return a TOML `.museattributes` template pre-filled with *domain*."""
    return f"""\
# .museattributes — merge strategy overrides for this repository.

[meta]
domain = "{domain}"

# [[rules]]
# path      = "*"
# dimension = "*"
# strategy  = "auto"
"""


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the init subcommand."""
    parser = subparsers.add_parser(
        "init",
        help="Initialise a new Muse repository.",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--bare", action="store_true", help="Initialise as a bare repository.")
    parser.add_argument("--template", default=None, metavar="PATH", help="Copy PATH contents into the working tree.")
    parser.add_argument("--default-branch", default="main", metavar="BRANCH", dest="default_branch", help="Name of the initial branch.")
    parser.add_argument("--force", "-f", action="store_true", help="Re-initialise even if already a Muse repository.")
    parser.add_argument("--domain", "-d", default="code", help="Domain plugin to use (e.g. code, midi).")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> None:
    """Initialise a new Muse repository in the current directory."""
    bare: bool = args.bare
    template: str | None = args.template
    default_branch: str = args.default_branch
    force: bool = args.force
    domain: str = args.domain

    try:
        validate_branch_name(default_branch)
    except ValueError as exc:
        print(f"❌ Invalid --default-branch: {exc}")
        raise SystemExit(ExitCode.USER_ERROR)

    try:
        validate_domain_name(domain)
    except ValueError as exc:
        print(f"❌ Invalid --domain: {exc}")
        raise SystemExit(ExitCode.USER_ERROR)

    cwd = pathlib.Path.cwd()
    muse_dir = cwd / ".muse"

    template_path: pathlib.Path | None = None
    if template is not None:
        template_path = pathlib.Path(template).resolve()
        if not template_path.is_dir():
            print(f"❌ Template path is not a directory: {template_path}")
            raise SystemExit(ExitCode.USER_ERROR)

    already_exists = muse_dir.is_dir()
    if already_exists and not force:
        print(f"Already a Muse repository at {cwd}.\nUse --force to reinitialise.")
        raise SystemExit(ExitCode.USER_ERROR)

    existing_repo_id: str | None = None
    if force and already_exists:
        repo_json = muse_dir / "repo.json"
        if repo_json.exists():
            try:
                raw_id = json.loads(repo_json.read_text(encoding="utf-8")).get("repo_id")
                if isinstance(raw_id, str):
                    existing_repo_id = raw_id
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
            "domain": domain,
        }
        if bare:
            repo_meta["bare"] = True
        (muse_dir / "repo.json").write_text(
            json.dumps(repo_meta, indent=2) + "\n", encoding="utf-8"
        )

        write_head_branch(muse_dir.parent, default_branch)

        ref_file = muse_dir / "refs" / "heads" / default_branch
        if not ref_file.exists() or force:
            ref_file.write_text("", encoding="utf-8")

        config_path = muse_dir / "config.toml"
        if not config_path.exists():
            config_path.write_text(
                _BARE_CONFIG if bare else _DEFAULT_CONFIG, encoding="utf-8"
            )

        if not bare:
            attrs_path = cwd / ".museattributes"
            if not attrs_path.exists():
                attrs_path.write_text(_museattributes_template(domain), encoding="utf-8")

            ignore_path = cwd / ".museignore"
            if not ignore_path.exists():
                ignore_path.write_text(_museignore_template(domain), encoding="utf-8")

        if not bare and template_path is not None:
            for item in template_path.iterdir():
                dest = cwd / item.name
                if item.is_dir():
                    shutil.copytree(item, dest, dirs_exist_ok=True)
                else:
                    shutil.copy2(item, dest)

    except PermissionError:
        print(f"❌ Permission denied: cannot write to {cwd}.")
        raise SystemExit(ExitCode.USER_ERROR)
    except OSError as exc:
        print(f"❌ Failed to initialise repository: {exc}")
        raise SystemExit(ExitCode.INTERNAL_ERROR)

    action = "Reinitialised" if (force and already_exists) else "Initialised"
    kind = "bare " if bare else ""
    print(f"✅ {action} {kind}Muse repository in {muse_dir}")

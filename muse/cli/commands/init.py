"""muse init — initialise a new Muse repository.

Creates the ``.muse/`` directory tree in the current working directory.

Layout::

    .muse/
        repo.json           — repo_id, schema_version, domain, created_at
        HEAD                — symbolic ref → refs/heads/main
        refs/heads/main     — empty (no commits yet)
        config.toml         — [user], [auth], [remotes], [domain] stubs
        objects/            — content-addressed blobs (created on first commit)
        commits/            — commit records (JSON, one file per commit)
        snapshots/          — snapshot manifests (JSON, one file per snapshot)
    .museattributes         — TOML merge strategy overrides (created in repo root)
    .museignore             — TOML ignore rules (created in repo root)

The repository root IS the working tree.  There is no ``state/`` subdirectory.
Bare repositories (``--bare``) have no working tree; they store only ``.muse/``.
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
from muse.core.store import write_head_branch
from muse.core.validation import validate_branch_name, validate_domain_name

logger = logging.getLogger(__name__)

app = typer.Typer()

_SCHEMA_VERSION = "1"

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
# Code examples:
#   language = "python"
#   formatter = "black"
#   linter = "ruff"
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
# Run `muse hub connect <url>` to attach this repo to MuseHub.
# Run `muse auth login` to authenticate.
# Credentials are stored in ~/.muse/identity.toml — never here.

[remotes]

[domain]
# Domain-specific configuration. Keys depend on the active domain plugin.
# Code examples:
#   language = "python"
#   formatter = "black"
#   linter = "ruff"
"""


def _museignore_template(domain: str) -> str:
    """Return a TOML ``.museignore`` template pre-filled for *domain*.

    The ``[global]`` section covers cross-domain OS artifacts.  The
    ``[domain.<name>]`` section lists patterns specific to the chosen domain.
    Patterns from other domains are never loaded at snapshot time.
    """
    global_section = """\
[global]
# Patterns applied to every domain. Last match wins; prefix with ! to un-ignore.
patterns = [
    ".DS_Store",
    "Thumbs.db",
    "*.tmp",
    "*.swp",
    "*.swo",
]
"""
    midi_section = """\
[domain.midi]
# Patterns applied only when the active domain plugin is "midi".
patterns = [
    "*.bak",
    "*.autosave",
    "/renders/",
    "/exports/",
    "/previews/",
]
"""
    code_section = """\
[domain.code]
# Patterns applied only when the active domain plugin is "code".
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
"""
    genomics_section = """\
[domain.genomics]
# Patterns applied only when the active domain plugin is "genomics".
patterns = [
    "*.sam",
    "*.bam.bai",
    "pipeline-cache/",
    "*.log",
]
"""
    simulation_section = """\
[domain.simulation]
# Patterns applied only when the active domain plugin is "simulation".
patterns = [
    "frames/raw/",
    "*.frame.bin",
    "checkpoint-tmp/",
]
"""
    spatial_section = """\
[domain.spatial]
# Patterns applied only when the active domain plugin is "spatial".
patterns = [
    "previews/",
    "*.preview.vdb",
    "**/.shadercache/",
]
"""

    domain_blocks: dict[str, str] = {
        "midi": midi_section,
        "code": code_section,
        "genomics": genomics_section,
        "simulation": simulation_section,
        "spatial": spatial_section,
    }
    domain_block = domain_blocks.get(domain, f"""\
[domain.{domain}]
# Patterns applied only when the active domain plugin is "{domain}".
# patterns = [
#     "*.generated",
#     "/cache/",
# ]
""")

    header = f"""\
# .museignore — snapshot exclusion rules for this repository.
# Documentation: docs/reference/museignore.md
#
# Format: TOML with [global] and [domain.<name>] sections.
#   [global]          — patterns applied to every domain
#   [domain.<name>]   — patterns applied only when the active domain is <name>
#
# Pattern syntax (gitignore-compatible):
#   *.ext             ignore files with this extension at any depth
#   /path             anchor to the root of state/
#   dir/              directory pattern (silently skipped — Muse tracks files)
#   !pattern          un-ignore a previously matched path
#
# Last matching rule wins.

"""
    return header + global_section + "\n" + domain_block


def _museattributes_template(domain: str) -> str:
    """Return a TOML `.museattributes` template pre-filled with *domain*."""
    return f"""\
# .museattributes — merge strategy overrides for this repository.
# Documentation: docs/reference/muse-attributes.md
#
# Format: TOML with an optional [meta] header and an ordered [[rules]] array.
# Rules are evaluated top-to-bottom after sorting by priority (descending).
# The first matching rule wins.  Unmatched paths fall back to "auto".
#
# ─── Strategies ───────────────────────────────────────────────────────────────
#
#   ours     Take the current-branch (left) version; remove from conflicts.
#   theirs   Take the incoming-branch (right) version; remove from conflicts.
#   union    Include all additions from both sides.  Deletions are honoured
#            only when both sides agree.  Best for independent element sets
#            (MIDI notes, symbol additions, import sets, genomic mutations).
#            Falls back to "ours" for binary blobs.
#   base     Revert to the common ancestor; discard changes from both branches.
#            Use this for generated files, lock files, or pinned assets.
#   auto     Default — let the three-way merge engine decide.
#   manual   Force the path into the conflict list for human review, even when
#            the engine would auto-resolve it.
#
# ─── Rule fields ──────────────────────────────────────────────────────────────
#
#   path      (required)  fnmatch glob against workspace-relative POSIX paths.
#   dimension (required)  Domain axis name (e.g. "notes", "symbols") or "*".
#   strategy  (required)  One of the six strategies above.
#   comment   (optional)  Free-form note explaining the rule — ignored at runtime.
#   priority  (optional)  Integer; higher-priority rules are tried first.
#                         Default 0; ties preserve declaration order.

[meta]
domain = "{domain}"    # must match the "domain" field in .muse/repo.json

# ─── MIDI domain examples ─────────────────────────────────────────────────────
# [[rules]]
# path      = "drums/*"
# dimension = "*"
# strategy  = "ours"
# comment   = "Drum tracks are always authored on this branch."
# priority  = 20
#
# [[rules]]
# path      = "keys/*.mid"
# dimension = "pitch_bend"
# strategy  = "theirs"
# comment   = "Remote always has the better pitch-bend automation."
# priority  = 15
#
# [[rules]]
# path      = "stems/*"
# dimension = "notes"
# strategy  = "union"
# comment   = "Unify note additions from both arrangers; let the engine merge."
#
# [[rules]]
# path      = "mixdown.mid"
# dimension = "*"
# strategy  = "base"
# comment   = "Mixdown is generated — always revert to ancestor during merge."
#
# [[rules]]
# path      = "master.mid"
# dimension = "*"
# strategy  = "manual"
# comment   = "Master track must always be reviewed by a human before merge."

# ─── Code domain examples ─────────────────────────────────────────────────────
# [[rules]]
# path      = "src/generated/**"
# dimension = "*"
# strategy  = "base"
# comment   = "Generated code — revert to base; re-run codegen after merge."
# priority  = 30
#
# [[rules]]
# path      = "src/**/*.py"
# dimension = "imports"
# strategy  = "union"
# comment   = "Import sets are independent; accumulate additions from both sides."
#
# [[rules]]
# path      = "tests/**"
# dimension = "symbols"
# strategy  = "union"
# comment   = "Test additions from both branches are always safe to combine."
#
# [[rules]]
# path      = "src/core/**"
# dimension = "*"
# strategy  = "manual"
# comment   = "Core module changes need human review on every merge."
# priority  = 25
#
# [[rules]]
# path      = "package-lock.json"
# dimension = "*"
# strategy  = "ours"
# comment   = "Lock file is managed by this branch's CI; ignore incoming."

# ─── Generic / domain-agnostic examples ───────────────────────────────────────
# [[rules]]
# path      = "docs/**"
# dimension = "*"
# strategy  = "union"
# comment   = "Documentation additions from both branches are always welcome."
#
# [[rules]]
# path      = "config/secrets.*"
# dimension = "*"
# strategy  = "manual"
# comment   = "Secrets files require manual review — never auto-merge."
# priority  = 100
#
# [[rules]]
# path      = "*"
# dimension = "*"
# strategy  = "auto"
# comment   = "Fallback: let the engine decide for everything else."
"""


@app.callback(invoke_without_command=True)
def init(
    ctx: typer.Context,
    bare: bool = typer.Option(False, "--bare", help="Initialise as a bare repository (no working tree)."),
    template: str | None = typer.Option(None, "--template", metavar="PATH", help="Copy PATH contents into the working tree."),
    default_branch: str = typer.Option("main", "--default-branch", metavar="BRANCH", help="Name of the initial branch."),
    force: bool = typer.Option(False, "--force", help="Re-initialise even if already a Muse repository."),
    domain: str = typer.Option("code", "--domain", help="Domain plugin to use (e.g. code, midi). Must be registered in the plugin registry."),
) -> None:
    """Initialise a new Muse repository in the current directory."""
    try:
        validate_branch_name(default_branch)
    except ValueError as exc:
        typer.echo(f"❌ Invalid --default-branch: {exc}")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    try:
        validate_domain_name(domain)
    except ValueError as exc:
        typer.echo(f"❌ Invalid --domain: {exc}")
        raise typer.Exit(code=ExitCode.USER_ERROR)

    cwd = pathlib.Path.cwd()
    muse_dir = cwd / ".muse"

    template_path: pathlib.Path | None = None
    if template is not None:
        template_path = pathlib.Path(template).resolve()
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
            "domain": domain,
        }
        if bare:
            repo_meta["bare"] = True
        (muse_dir / "repo.json").write_text(json.dumps(repo_meta, indent=2) + "\n")

        write_head_branch(muse_dir.parent, default_branch)

        ref_file = muse_dir / "refs" / "heads" / default_branch
        if not ref_file.exists() or force:
            ref_file.write_text("")

        config_path = muse_dir / "config.toml"
        if not config_path.exists():
            config_path.write_text(_BARE_CONFIG if bare else _DEFAULT_CONFIG)

        attrs_path = cwd / ".museattributes"
        if not attrs_path.exists():
            attrs_path.write_text(_museattributes_template(domain))

        ignore_path = cwd / ".museignore"
        if not ignore_path.exists():
            ignore_path.write_text(_museignore_template(domain))

        if not bare and template_path is not None:
            for item in template_path.iterdir():
                dest = cwd / item.name
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

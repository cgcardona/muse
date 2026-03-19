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
    muse-work/              — working tree (absent for --bare repos)
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

[domain]
# Domain-specific configuration. Keys depend on the active domain.
# Music examples:
#   ticks_per_beat = 480
# Genomics examples:
#   reference_assembly = "GRCh38"
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

[domain]
# Domain-specific configuration. Keys depend on the active domain.
# Music examples:
#   ticks_per_beat = 480
# Genomics examples:
#   reference_assembly = "GRCh38"
"""


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
    bare: bool = typer.Option(False, "--bare", help="Initialise as a bare repository (no muse-work/)."),
    template: str | None = typer.Option(None, "--template", metavar="PATH", help="Copy PATH contents into muse-work/."),
    default_branch: str = typer.Option("main", "--default-branch", metavar="BRANCH", help="Name of the initial branch."),
    force: bool = typer.Option(False, "--force", help="Re-initialise even if already a Muse repository."),
    domain: str = typer.Option("midi", "--domain", help="Domain plugin to use (e.g. midi). Must be registered in the plugin registry."),
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
            "domain": domain,
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

        attrs_path = cwd / ".museattributes"
        if not attrs_path.exists():
            attrs_path.write_text(_museattributes_template(domain))

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

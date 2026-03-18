"""muse domains — domain plugin dashboard and scaffold wizard.

Output (default — no flags)::

    ╔══════════════════════════════════════════════════════════════╗
    ║              Muse Domain Plugin Dashboard                    ║
    ╚══════════════════════════════════════════════════════════════╝

    Registered domains: 2
    ─────────────────────────────────────────────────────────────

    music  ●  music/plugin.py
      Capabilities:  Phase 1 · Phase 2 · Phase 3 · Phase 4
      Schema:        version 1.0 · merge_mode: three_way
      Elements:      note_event (sequence), dimension_axes (set)
      Dimensions:    melodic, rhythmic, harmonic, dynamic, structural

    scaffold  ○  scaffold/plugin.py
      Capabilities:  Phase 1 · Phase 2 · Phase 3 · Phase 4
      Schema:        version 1.0 · merge_mode: three_way
      Elements:      record (sequence), attribute_set (set)
      Dimensions:    primary, metadata

    ─────────────────────────────────────────────────────────────
    To scaffold a new domain:
      muse domains --new <name>
    ─────────────────────────────────────────────────────────────

--json flag produces machine-readable output.

--new <name> scaffolds a new domain plugin directory.
"""
from __future__ import annotations

import json
import logging
import pathlib
import shutil
import sys
from typing import Literal

import typer

from muse.domain import CRDTPlugin, MuseDomainPlugin, StructuredMergePlugin
from muse.plugins.registry import _REGISTRY

logger = logging.getLogger(__name__)

app = typer.Typer()

# ---------------------------------------------------------------------------
# Internal types
# ---------------------------------------------------------------------------

_CapabilityLevel = Literal["Phase 1", "Phase 2", "Phase 3", "Phase 4"]


def _capabilities(plugin: MuseDomainPlugin) -> list[_CapabilityLevel]:
    """Return the capability levels the plugin implements.

    Checks each optional protocol via ``isinstance`` — the same runtime
    mechanism the core engine uses during merge dispatch.

    Args:
        plugin: A registered ``MuseDomainPlugin`` instance.

    Returns:
        Sorted list of capability-level labels the plugin satisfies.
    """
    caps: list[_CapabilityLevel] = ["Phase 1"]
    try:
        plugin.schema()
        caps.append("Phase 2")
    except NotImplementedError:
        return caps
    if isinstance(plugin, StructuredMergePlugin):
        caps.append("Phase 3")
    if isinstance(plugin, CRDTPlugin):
        caps.append("Phase 4")
    return caps


def _plugin_module_path(name: str) -> str:
    """Return the module path for a plugin, relative to ``muse/plugins/``.

    Args:
        name: Domain name string (key in the registry).

    Returns:
        A display-friendly path string like ``music/plugin.py``.
    """
    return f"{name}/plugin.py"


def _active_domain(root: pathlib.Path | None) -> str | None:
    """Return the domain name of the repository at *root*, or ``None``.

    Args:
        root: Repository root or ``None`` when not inside a repo.

    Returns:
        Domain name string or ``None``.
    """
    if root is None:
        return None
    repo_json = root / ".muse" / "repo.json"
    if not repo_json.exists():
        return None
    try:
        data = json.loads(repo_json.read_text())
        domain = data.get("domain")
        return str(domain) if domain else "music"
    except (OSError, json.JSONDecodeError):
        return None


def _find_repo_root() -> pathlib.Path | None:
    """Walk up from cwd to find a ``.muse/`` directory.

    Returns:
        The repository root path, or ``None`` if not inside a repo.
    """
    here = pathlib.Path.cwd()
    for candidate in [here, *here.parents]:
        if (candidate / ".muse").is_dir():
            return candidate
    return None


# ---------------------------------------------------------------------------
# Scaffold wizard
# ---------------------------------------------------------------------------

def _scaffold_new_domain(name: str) -> None:
    """Create a new plugin directory by copying the scaffold template.

    Copies ``muse/plugins/scaffold/`` to ``muse/plugins/<name>/``, then
    renames ``ScaffoldPlugin`` to ``<Name>Plugin`` in the source files.

    Args:
        name: The new domain name (used as directory name and class prefix).
    """
    scaffold_src = pathlib.Path(__file__).parents[2] / "plugins" / "scaffold"
    dest = pathlib.Path(__file__).parents[2] / "plugins" / name

    if dest.exists():
        typer.echo(f"❌ Plugin directory already exists: {dest}", err=True)
        raise typer.Exit(1)

    if not scaffold_src.exists():
        typer.echo(
            f"❌ Scaffold source not found: {scaffold_src}\n"
            "Make sure muse/plugins/scaffold/ exists.",
            err=True,
        )
        raise typer.Exit(1)

    shutil.copytree(str(scaffold_src), str(dest))

    class_name = "".join(part.capitalize() for part in name.split("_")) + "Plugin"

    for py_file in dest.glob("*.py"):
        text = py_file.read_text()
        text = text.replace("ScaffoldPlugin", class_name)
        text = text.replace('_DOMAIN_NAME = "scaffold"', f'_DOMAIN_NAME = "{name}"')
        text = text.replace(
            'Scaffold domain plugin — copy-paste template for a new Muse domain.',
            f'{class_name} — Muse domain plugin for the {name!r} domain.',
        )
        py_file.write_text(text)

    typer.echo(f"✅ Scaffolded new domain plugin: muse/plugins/{name}/")
    typer.echo(f"   Class name: {class_name}")
    typer.echo("")
    typer.echo("Next steps:")
    typer.echo(f"  1. Implement every NotImplementedError in muse/plugins/{name}/plugin.py")
    typer.echo("  2. Register the plugin in muse/plugins/registry.py:")
    typer.echo(f'       from muse.plugins.{name}.plugin import {class_name}')
    typer.echo(f'       _REGISTRY["{name}"] = {class_name}()')
    typer.echo(f'  3. muse init --domain {name}')
    typer.echo("  4. See docs/guide/plugin-authoring-guide.md for the full walkthrough")


# ---------------------------------------------------------------------------
# JSON output
# ---------------------------------------------------------------------------

def _emit_json(active_domain: str | None) -> None:
    """Print all registered domains and their capabilities as JSON.

    Args:
        active_domain: The domain of the current repo, or ``None``.
    """
    result: list[dict[str, str | list[str] | dict[str, str | list[dict[str, str]]]]] = []
    for domain_name, plugin in sorted(_REGISTRY.items()):
        caps = _capabilities(plugin)
        entry: dict[str, str | list[str] | dict[str, str | list[dict[str, str]]]] = {
            "domain": domain_name,
            "capabilities": list(caps),
            "active": "true" if domain_name == active_domain else "false",
        }
        try:
            s = plugin.schema()
            schema_dict: dict[str, str | list[dict[str, str]]] = {
                "schema_version": str(s["schema_version"]),
                "merge_mode": s["merge_mode"],
                "description": s["description"],
                "dimensions": [
                    {"name": d["name"], "description": d["description"]}
                    for d in s["dimensions"]
                ],
            }
            entry["schema"] = schema_dict
        except NotImplementedError:
            pass
        result.append(entry)
    typer.echo(json.dumps(result, indent=2))


# ---------------------------------------------------------------------------
# Human-readable dashboard
# ---------------------------------------------------------------------------

_WIDTH = 62


def _box_line(text: str) -> str:
    """Center *text* inside a box line of width ``_WIDTH``."""
    inner = _WIDTH - 2
    padded = text.center(inner)
    return f"║{padded}║"


def _hr() -> str:
    return "─" * _WIDTH


def _print_dashboard(active_domain: str | None) -> None:
    """Print the human-readable domain dashboard.

    Args:
        active_domain: Domain of the current repo (highlighted with ●), or ``None``.
    """
    typer.echo("╔" + "═" * (_WIDTH - 2) + "╗")
    typer.echo(_box_line("Muse Domain Plugin Dashboard"))
    typer.echo("╚" + "═" * (_WIDTH - 2) + "╝")
    typer.echo("")

    count = len(_REGISTRY)
    typer.echo(f"Registered domains: {count}")
    typer.echo(_hr())

    for domain_name, plugin in sorted(_REGISTRY.items()):
        caps = _capabilities(plugin)
        is_active = domain_name == active_domain
        bullet = "●" if is_active else "○"
        module_path = _plugin_module_path(domain_name)

        typer.echo("")
        active_suffix = "  ← active repo domain" if is_active else ""
        typer.echo(f"  {bullet}  {domain_name}{active_suffix}")
        typer.echo(f"     Module:        plugins/{module_path}")
        typer.echo(f"     Capabilities:  {' · '.join(caps)}")

        try:
            s = plugin.schema()
            dim_names = [d["name"] for d in s["dimensions"]]
            top_kind = s["top_level"]["kind"]
            typer.echo(
                f"     Schema:        v{s['schema_version']} · "
                f"top_level: {top_kind} · merge_mode: {s['merge_mode']}"
            )
            typer.echo(f"     Dimensions:    {', '.join(dim_names)}")
            typer.echo(f"     Description:   {s['description'][:55]}")
        except NotImplementedError:
            typer.echo("     Schema:        (not declared)")

    typer.echo("")
    typer.echo(_hr())
    typer.echo("To scaffold a new domain:")
    typer.echo("  muse domains --new <name>")
    typer.echo("To see machine-readable output:")
    typer.echo("  muse domains --json")
    typer.echo("See docs/guide/plugin-authoring-guide.md for the full walkthrough.")
    typer.echo(_hr())


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

@app.callback(invoke_without_command=True)
def domains(
    ctx: typer.Context,
    new: str | None = typer.Option(
        None,
        "--new",
        metavar="NAME",
        help="Scaffold a new domain plugin with the given name.",
    ),
    as_json: bool = typer.Option(
        False,
        "--json",
        help="Emit domain registry as JSON.",
    ),
) -> None:
    """Domain plugin dashboard — list registered domains and their capabilities.

    Without flags: prints a human-readable table of all registered domains,
    their Phase 1–4 capability levels, and their declared schemas.

    Use ``--new <name>`` to scaffold a new domain plugin directory from the
    scaffold template.

    Use ``--json`` for machine-readable output.
    """
    if new is not None:
        _scaffold_new_domain(new)
        return

    active_domain = _active_domain(_find_repo_root())

    if as_json:
        _emit_json(active_domain)
        return

    _print_dashboard(active_domain)

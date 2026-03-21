"""muse domains — domain plugin dashboard and scaffold wizard.

Output (default — no flags)::

    ╔══════════════════════════════════════════════════════════════╗
    ║              Muse Domain Plugin Dashboard                    ║
    ╚══════════════════════════════════════════════════════════════╝

    Registered domains: 2
    ─────────────────────────────────────────────────────────────

    midi  ●  midi/plugin.py
      Capabilities:  Typed Deltas · Domain Schema · OT Merge
      Schema:        version 1.0 · merge_mode: three_way
      Elements:      note_event (sequence), dimension_axes (set)
      Dimensions:    notes, pitch_bend, cc_volume, cc_sustain, tempo_map, track_structure (21 total)

    scaffold  ○  scaffold/plugin.py
      Capabilities:  Typed Deltas · Domain Schema · OT Merge · CRDT
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

import http.client
import json
import logging
import pathlib
import shutil
import sys
import urllib.error
import urllib.request
from typing import Literal, TypedDict

import typer

from muse.cli.config import get_auth_token, get_hub_url
from muse.core.repo import find_repo_root
from muse.domain import CRDTPlugin, MuseDomainPlugin, StructuredMergePlugin
from muse.plugins.registry import _REGISTRY

logger = logging.getLogger(__name__)

app = typer.Typer()

# ---------------------------------------------------------------------------
# Internal types
# ---------------------------------------------------------------------------

_CapabilityLabel = Literal["Typed Deltas", "Domain Schema", "OT Merge", "CRDT"]


def _capabilities(plugin: MuseDomainPlugin) -> list[_CapabilityLabel]:
    """Return the capability labels the plugin implements.

    Checks each optional protocol via ``isinstance`` — the same runtime
    mechanism the core engine uses during merge dispatch.

    Args:
        plugin: A registered ``MuseDomainPlugin`` instance.

    Returns:
        Capability labels in ascending order: Typed Deltas → Domain Schema
        → OT Merge → CRDT.  Every plugin gets at least "Typed Deltas".
    """
    caps: list[_CapabilityLabel] = ["Typed Deltas"]
    try:
        plugin.schema()
        caps.append("Domain Schema")
    except NotImplementedError:
        return caps
    if isinstance(plugin, StructuredMergePlugin):
        caps.append("OT Merge")
    if isinstance(plugin, CRDTPlugin):
        caps.append("CRDT")
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
        return str(domain) if domain else "midi"
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
# Publish subcommand
# ---------------------------------------------------------------------------

_PUBLISH_TIMEOUT = 15  # seconds

# JSON-safe primitive — used for typed JSON dicts below.
_JsonLeaf = str | int | float | bool | None


class _DimensionDef(TypedDict):
    """One semantic dimension exported by a domain plugin.

    Dimensions are the axes of multidimensional state that Muse tracks
    independently — e.g. "notes", "pitch_bend", "tempo_map" for the MIDI
    domain; "geometry", "materials" for a spatial domain.
    """

    name: str
    description: str


class _Capabilities(TypedDict, total=False):
    """Capability manifest sent to MuseHub on domain publish.

    All fields are optional at the transport level (``total=False``), but
    ``merge_semantics`` should always be provided for meaningful marketplace
    display.  When derived from a plugin ``schema()``, ``dimensions`` and
    ``merge_semantics`` are always populated.
    """

    dimensions: list[_DimensionDef]
    artifact_types: list[str]
    merge_semantics: str
    supported_commands: list[str]


class _PublishPayload(TypedDict):
    """Wire payload for ``POST /api/v1/domains`` on MuseHub.

    All fields are required.  ``capabilities`` may be an empty ``_Capabilities``
    dict when the plugin does not yet implement ``schema()`` — the marketplace
    will show it with no dimension list until re-published.
    """

    author_slug: str
    slug: str
    display_name: str
    description: str
    capabilities: _Capabilities
    viewer_type: str
    version: str


class _PublishResponse(TypedDict, total=False):
    """Parsed response body from ``POST /api/v1/domains``.

    ``scoped_id`` is the canonical marketplace identifier in the form
    ``@author_slug/slug``.  ``manifest_hash`` is the SHA-256 of the
    serialised capability manifest, used for change detection on re-publish.
    """

    domain_id: str
    scoped_id: str
    manifest_hash: str


def _post_json(url: str, payload: _PublishPayload, token: str) -> _PublishResponse:
    """HTTP POST *payload* as JSON to *url* authenticated with *token*.

    Uses :mod:`urllib.request` (no third-party dependencies) with a
    ``Content-Type: application/json`` body and ``Authorization: Bearer``
    header.  The timeout is :data:`_PUBLISH_TIMEOUT` seconds.

    Args:
        url:     Full endpoint URL including scheme (e.g. ``https://musehub.ai/api/v1/domains``).
        payload: Typed publish payload — serialised verbatim to JSON.
        token:   Bearer token from ``~/.muse/identity.toml`` via ``get_auth_token()``.

    Returns:
        Parsed ``_PublishResponse`` with ``domain_id``, ``scoped_id``, and
        ``manifest_hash`` from the server.  Missing keys are returned as empty
        strings rather than raising ``KeyError``.

    Raises:
        urllib.error.HTTPError: on non-2xx HTTP responses (409 conflict, 401
            unauthorized, 5xx server errors).  Caller should inspect ``exc.code``
            to produce user-friendly messages.
        urllib.error.URLError:  on DNS resolution failure or connection refused.
        ValueError:             when the response body is not a JSON object (e.g.
            plain-text error page from a proxy).
    """
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=_PUBLISH_TIMEOUT) as resp:  # noqa: S310
        raw = resp.read().decode()
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError(f"Expected JSON object from server, got: {type(parsed).__name__}")
    return _PublishResponse(
        domain_id=str(parsed.get("domain_id") or ""),
        scoped_id=str(parsed.get("scoped_id") or ""),
        manifest_hash=str(parsed.get("manifest_hash") or ""),
    )


@app.command("publish")
def publish(
    author_slug: str = typer.Option(
        ...,
        "--author",
        metavar="SLUG",
        help="Your MuseHub username (owner of the domain, e.g. 'cgcardona').",
    ),
    slug: str = typer.Option(
        ...,
        "--slug",
        metavar="SLUG",
        help="URL-safe domain name (e.g. 'genomics', 'spatial-3d').",
    ),
    display_name: str = typer.Option(
        ...,
        "--name",
        metavar="NAME",
        help="Human-readable marketplace name (e.g. 'Genomics').",
    ),
    description: str = typer.Option(
        ...,
        "--description",
        metavar="TEXT",
        help="What this domain models and why it benefits from semantic VCS.",
    ),
    viewer_type: str = typer.Option(
        ...,
        "--viewer-type",
        metavar="TYPE",
        help="Primary viewer identifier (e.g. 'midi', 'code', 'spatial', 'genome').",
    ),
    version: str = typer.Option(
        "0.1.0",
        "--version",
        metavar="SEMVER",
        help="Semver release string (default: 0.1.0).",
    ),
    capabilities_json: str | None = typer.Option(
        None,
        "--capabilities",
        metavar="JSON",
        help=(
            "Full capabilities manifest as a JSON string.  "
            "Required keys: dimensions, artifact_types, merge_semantics, supported_commands.  "
            "When omitted the active repo's domain plugin schema is used."
        ),
    ),
    hub_url: str | None = typer.Option(
        None,
        "--hub",
        metavar="URL",
        help="Override the MuseHub base URL (default: read from .muse/config.toml).",
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit result as JSON."),
) -> None:
    """Publish a Muse domain plugin to the MuseHub marketplace.

    Registers ``@{author}/{slug}`` so agents and users can discover and install
    the domain via ``musehub_list_domains`` and ``muse domains``.

    Capabilities are read from the active domain plugin's ``schema()`` when
    ``--capabilities`` is omitted — so you can run this command from inside a
    repo that uses the domain you want to publish.

    Example::

        muse domains publish \\
            --author cgcardona --slug genomics \\
            --name "Genomics" \\
            --description "Version DNA sequences as multidimensional state" \\
            --viewer-type genome

        muse domains publish --author cgcardona --slug spatial \\
            --name "Spatial 3D" \\
            --description "Version 3-D scenes as structured multidimensional commits" \\
            --viewer-type spatial \\
            --capabilities '{"dimensions":[{"name":"geometry","description":"Mesh data"}],...}'
    """
    # ── Resolve hub URL and auth token ─────────────────────────────────────────
    repo_root = find_repo_root()
    resolved_hub = hub_url or get_hub_url(repo_root) or "https://musehub.ai"
    resolved_hub = resolved_hub.rstrip("/")

    token = get_auth_token(repo_root)
    if not token:
        typer.echo(
            "❌ No MuseHub token found.  Run:\n"
            "   muse auth login\n"
            "or set your token with:\n"
            "   muse config set hub.token <your-token>",
            err=True,
        )
        raise typer.Exit(1)

    # ── Build capabilities manifest ────────────────────────────────────────────
    capabilities: _Capabilities
    if capabilities_json is not None:
        try:
            raw_caps = json.loads(capabilities_json)
            if not isinstance(raw_caps, dict):
                raise ValueError("capabilities JSON must be an object")
            capabilities = _Capabilities(
                dimensions=[
                    _DimensionDef(name=str(d.get("name", "")), description=str(d.get("description", "")))
                    for d in raw_caps.get("dimensions", [])
                    if isinstance(d, dict)
                ],
                artifact_types=[str(a) for a in raw_caps.get("artifact_types", []) if isinstance(a, str)],
                merge_semantics=str(raw_caps.get("merge_semantics", "three_way")),
                supported_commands=[str(c) for c in raw_caps.get("supported_commands", []) if isinstance(c, str)],
            )
        except (json.JSONDecodeError, ValueError) as exc:
            typer.echo(f"❌ --capabilities is not valid JSON: {exc}", err=True)
            raise typer.Exit(1) from exc
    else:
        # Derive from the active domain plugin schema if available.
        active_domain_name: str | None = None
        if repo_root is not None:
            repo_json = repo_root / ".muse" / "repo.json"
            try:
                active_domain_name = json.loads(repo_json.read_text()).get("domain")
            except (OSError, json.JSONDecodeError):
                pass

        plugin = _REGISTRY.get(active_domain_name or "") if active_domain_name else None
        capabilities_ok = False
        if plugin is not None:
            try:
                schema = plugin.schema()
                capabilities = _Capabilities(
                    dimensions=[
                        _DimensionDef(name=d["name"], description=d["description"])
                        for d in schema["dimensions"]
                    ],
                    artifact_types=[],  # DomainSchema does not carry MIME types — set post-publish
                    merge_semantics=schema["merge_mode"],
                    supported_commands=["commit", "diff", "merge", "log", "status"],
                )
                capabilities_ok = True
            except NotImplementedError:
                capabilities = _Capabilities()
        else:
            capabilities = _Capabilities()

        if not capabilities_ok:
            typer.echo(
                "⚠️  Could not derive capabilities from active plugin. "
                "Provide --capabilities '<json>' to set them explicitly.",
                err=True,
            )
            typer.echo(
                "  Required keys: dimensions, artifact_types, merge_semantics, supported_commands",
                err=True,
            )
            raise typer.Exit(1)

    # ── POST to MuseHub ────────────────────────────────────────────────────────
    endpoint = f"{resolved_hub}/api/v1/domains"
    payload = _PublishPayload(
        author_slug=author_slug,
        slug=slug,
        display_name=display_name,
        description=description,
        capabilities=capabilities,
        viewer_type=viewer_type,
        version=version,
    )

    try:
        result = _post_json(endpoint, payload, token)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        if exc.code == 409:
            typer.echo(
                f"❌ Domain '@{author_slug}/{slug}' is already registered. "
                "Use a different slug or bump the version.",
                err=True,
            )
        elif exc.code == 401:
            typer.echo("❌ Authentication failed — is your MuseHub token valid?", err=True)
        else:
            typer.echo(f"❌ MuseHub returned HTTP {exc.code}: {body}", err=True)
        raise typer.Exit(1) from exc
    except urllib.error.URLError as exc:
        typer.echo(f"❌ Could not reach MuseHub at {resolved_hub}: {exc.reason}", err=True)
        raise typer.Exit(1) from exc
    except ValueError as exc:
        typer.echo(f"❌ Unexpected response from MuseHub: {exc}", err=True)
        raise typer.Exit(1) from exc

    # ── Emit result ────────────────────────────────────────────────────────────
    if as_json:
        typer.echo(json.dumps(result, indent=2))
        return

    scoped_id = result.get("scoped_id") or f"@{author_slug}/{slug}"
    manifest_hash = result.get("manifest_hash") or ""
    typer.echo(f"✅ Domain published: {scoped_id}")
    typer.echo(f"   manifest_hash: {manifest_hash}")
    typer.echo(f"   Discoverable at: {resolved_hub}/domains/@{author_slug}/{slug}")
    typer.echo("")
    typer.echo("Agents can now use it:")
    typer.echo(f'   musehub_get_domain(scoped_id="{scoped_id}")')
    typer.echo(f'   musehub_create_repo(domain="{scoped_id}", ...)')


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
    their capability levels (Typed Deltas / Domain Schema / OT Merge / CRDT),
    and their declared schemas.

    Use ``--new <name>`` to scaffold a new domain plugin directory from the
    scaffold template.

    Use ``--json`` for machine-readable output.
    """
    if ctx.invoked_subcommand is not None:
        return

    if new is not None:
        _scaffold_new_domain(new)
        return

    active_domain = _active_domain(_find_repo_root())

    if as_json:
        _emit_json(active_domain)
        return

    _print_dashboard(active_domain)

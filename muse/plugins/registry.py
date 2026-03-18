"""Plugin registry — maps domain names to :class:`~muse.domain.MuseDomainPlugin` instances.

Every CLI command that operates on domain state calls :func:`resolve_plugin`
once to obtain the active plugin for the current repository.  Adding support
for a new domain requires only two changes:

1. Implement :class:`~muse.domain.MuseDomainPlugin` in a new module under
   ``muse/plugins/<domain>/plugin.py``.
2. Register the plugin instance in ``_REGISTRY`` below.

The domain for a repository is stored in ``.muse/repo.json`` under the key
``"domain"``.  Repositories created before this key was introduced default to
``"music"``.
"""
from __future__ import annotations

import json
import pathlib

from muse.core.errors import MuseCLIError
from muse.core.schema import DomainSchema
from muse.domain import MuseDomainPlugin
from muse.plugins.music.plugin import MusicPlugin
from muse.plugins.scaffold.plugin import ScaffoldPlugin

_REGISTRY: dict[str, MuseDomainPlugin] = {
    "music":    MusicPlugin(),
    "scaffold": ScaffoldPlugin(),
}

_DEFAULT_DOMAIN = "music"


def _read_domain(root: pathlib.Path) -> str:
    """Return the domain name stored in ``.muse/repo.json``.

    Falls back to ``"music"`` for repos that pre-date the ``domain`` field.
    """
    repo_json = root / ".muse" / "repo.json"
    try:
        data = json.loads(repo_json.read_text())
        domain = data.get("domain")
        return str(domain) if domain else _DEFAULT_DOMAIN
    except (OSError, json.JSONDecodeError):
        return _DEFAULT_DOMAIN


def resolve_plugin(root: pathlib.Path) -> MuseDomainPlugin:
    """Return the active domain plugin for the repository at *root*.

    Reads the ``"domain"`` key from ``.muse/repo.json`` and looks it up in
    the plugin registry.  Raises :class:`~muse.core.errors.MuseCLIError` if
    the domain is not registered.

    Args:
        root: Repository root directory (contains ``.muse/``).

    Returns:
        The :class:`~muse.domain.MuseDomainPlugin` instance for this repo.

    Raises:
        MuseCLIError: When the domain stored in ``repo.json`` is not in the
            registry.  This is a configuration error — either the plugin was
            not installed or ``repo.json`` was edited manually.
    """
    domain = _read_domain(root)
    plugin = _REGISTRY.get(domain)
    if plugin is None:
        registered = ", ".join(sorted(_REGISTRY))
        raise MuseCLIError(
            f"Unknown domain {domain!r}. Registered domains: {registered}"
        )
    return plugin


def read_domain(root: pathlib.Path) -> str:
    """Return the domain name for the repository at *root*.

    This is the same lookup used internally by :func:`resolve_plugin`.
    Use it when you need the domain string to construct a
    :class:`~muse.domain.SnapshotManifest` for a stored manifest.
    """
    return _read_domain(root)


def registered_domains() -> list[str]:
    """Return the sorted list of registered domain names."""
    return sorted(_REGISTRY)


def schema_for(domain: str) -> DomainSchema | None:
    """Return the ``DomainSchema`` for *domain*, or ``None`` if not registered.

    Allows the CLI and merge engine to look up a domain's schema without
    holding a plugin instance. Returns ``None`` rather than raising so callers
    can decide whether an unknown domain is an error or a soft miss.

    Args:
        domain: Domain name string (e.g. ``"music"``).

    Returns:
        The :class:`~muse.core.schema.DomainSchema` declared by the plugin,
        or ``None`` if *domain* is not in the registry.
    """
    plugin = _REGISTRY.get(domain)
    if plugin is None:
        return None
    return plugin.schema()

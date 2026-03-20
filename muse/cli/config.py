"""Muse CLI configuration helpers.

Reads and writes ``.muse/config.toml`` — the per-repository configuration
file.  Credentials (bearer tokens) are **not** stored here; they live in
``~/.muse/identity.toml`` managed by :mod:`muse.core.identity`.

Config schema
-------------
::

    [user]
    name  = "Alice"        # display name (human or agent handle)
    email = "a@example.com"
    type  = "human"        # "human" | "agent"

    [hub]
    url   = "https://musehub.ai"   # MuseHub fabric endpoint for this repo

    [remotes.origin]
    url    = "https://hub.muse.io/repos/my-repo"
    branch = "main"

    [domain]
    # Domain-specific key/value pairs; read by the active domain plugin.
    # ticks_per_beat = "480"

Settable via ``muse config set``
---------------------------------
- ``user.name``, ``user.email``, ``user.type``
- ``hub.url``  (alias: ``muse hub connect <url>``)
- ``domain.*``

Not settable via ``muse config set``
--------------------------------------
- ``remotes.*``  — use ``muse remote add/remove``
- credentials    — use ``muse auth login``

Token resolution
----------------
:func:`get_auth_token` reads the hub URL from this file, then resolves the
bearer token from ``~/.muse/identity.toml`` via
:func:`muse.core.identity.resolve_token`.  The token is **never** logged.
"""

from __future__ import annotations

import logging
import pathlib
import shutil
import subprocess
import tomllib
from typing import TypedDict

logger = logging.getLogger(__name__)

_CONFIG_FILENAME = "config.toml"
_MUSE_DIR = ".muse"


# ---------------------------------------------------------------------------
# Named configuration types
# ---------------------------------------------------------------------------


class UserConfig(TypedDict, total=False):
    """``[user]`` section in ``.muse/config.toml``."""

    name: str
    email: str
    type: str   # "human" | "agent"


class HubConfig(TypedDict, total=False):
    """``[hub]`` section in ``.muse/config.toml``."""

    url: str


class RemoteEntry(TypedDict, total=False):
    """``[remotes.<name>]`` section in ``.muse/config.toml``."""

    url: str
    branch: str


class MuseConfig(TypedDict, total=False):
    """Structured view of the entire ``.muse/config.toml`` file."""

    user: UserConfig
    hub: HubConfig
    remotes: dict[str, RemoteEntry]
    domain: dict[str, str]


class RemoteConfig(TypedDict):
    """Public-facing remote descriptor returned by :func:`list_remotes`."""

    name: str
    url: str


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _config_path(repo_root: pathlib.Path | None) -> pathlib.Path:
    """Return the path to .muse/config.toml for the given (or cwd) root."""
    root = (repo_root or pathlib.Path.cwd()).resolve()
    return root / _MUSE_DIR / _CONFIG_FILENAME


def _load_config(config_path: pathlib.Path) -> MuseConfig:
    """Load and parse config.toml; return an empty MuseConfig if absent."""
    if not config_path.is_file():
        return {}

    try:
        with config_path.open("rb") as fh:
            raw = tomllib.load(fh)
    except Exception as exc:  # noqa: BLE001
        logger.warning("⚠️ Failed to parse %s: %s", config_path, exc)
        return {}

    config: MuseConfig = {}

    user_raw = raw.get("user")
    if isinstance(user_raw, dict):
        user: UserConfig = {}
        name_val = user_raw.get("name")
        if isinstance(name_val, str):
            user["name"] = name_val
        email_val = user_raw.get("email")
        if isinstance(email_val, str):
            user["email"] = email_val
        type_val = user_raw.get("type")
        if isinstance(type_val, str):
            user["type"] = type_val
        config["user"] = user

    hub_raw = raw.get("hub")
    if isinstance(hub_raw, dict):
        hub: HubConfig = {}
        url_val = hub_raw.get("url")
        if isinstance(url_val, str):
            hub["url"] = url_val
        config["hub"] = hub

    remotes_raw = raw.get("remotes")
    if isinstance(remotes_raw, dict):
        remotes: dict[str, RemoteEntry] = {}
        for name, remote_raw in remotes_raw.items():
            if isinstance(remote_raw, dict):
                entry: RemoteEntry = {}
                rurl = remote_raw.get("url")
                if isinstance(rurl, str):
                    entry["url"] = rurl
                branch_val = remote_raw.get("branch")
                if isinstance(branch_val, str):
                    entry["branch"] = branch_val
                remotes[name] = entry
        config["remotes"] = remotes

    domain_raw = raw.get("domain")
    if isinstance(domain_raw, dict):
        domain: dict[str, str] = {}
        for key, val in domain_raw.items():
            if isinstance(val, str):
                domain[key] = val
        config["domain"] = domain

    return config


def _escape(value: str) -> str:
    """Escape a TOML string value (backslash and double-quote)."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _dump_toml(config: MuseConfig) -> str:
    """Serialise a MuseConfig to TOML text.

    Section order: ``[user]``, ``[hub]``, ``[remotes.*]``, ``[domain]``.
    """
    lines: list[str] = []

    user = config.get("user")
    if user:
        lines.append("[user]")
        name = user.get("name", "")
        if name:
            lines.append(f'name = "{_escape(name)}"')
        email = user.get("email", "")
        if email:
            lines.append(f'email = "{_escape(email)}"')
        utype = user.get("type", "")
        if utype:
            lines.append(f'type = "{_escape(utype)}"')
        lines.append("")

    hub = config.get("hub")
    if hub:
        lines.append("[hub]")
        url = hub.get("url", "")
        if url:
            lines.append(f'url = "{_escape(url)}"')
        lines.append("")

    remotes = config.get("remotes") or {}
    for remote_name in sorted(remotes):
        entry = remotes[remote_name]
        lines.append(f"[remotes.{remote_name}]")
        rurl = entry.get("url", "")
        if rurl:
            lines.append(f'url = "{_escape(rurl)}"')
        branch = entry.get("branch", "")
        if branch:
            lines.append(f'branch = "{_escape(branch)}"')
        lines.append("")

    domain = config.get("domain") or {}
    if domain:
        lines.append("[domain]")
        for key, val in sorted(domain.items()):
            lines.append(f'{key} = "{_escape(val)}"')
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Auth token resolution (via identity store)
# ---------------------------------------------------------------------------


def get_auth_token(repo_root: pathlib.Path | None = None) -> str | None:
    """Return the bearer token for this repository's configured hub.

    Reads the hub URL from ``[hub] url`` in ``.muse/config.toml``, then
    resolves the token from ``~/.muse/identity.toml`` via
    :func:`muse.core.identity.resolve_token`.

    Returns ``None`` when no hub is configured or no identity is stored for
    that hub.  The token value is **never** logged.

    Args:
        repo_root: Repository root. Defaults to ``Path.cwd()``.

    Returns:
        Bearer token string, or ``None``.
    """
    from muse.core.identity import resolve_token  # avoid circular import at module level

    hub_url = get_hub_url(repo_root)
    if hub_url is None:
        logger.debug("⚠️ No hub configured — skipping auth token lookup")
        return None

    token = resolve_token(hub_url)
    if token is None:
        logger.debug("⚠️ No identity for hub %s — run `muse auth login`", hub_url)
        return None

    logger.debug("✅ Auth token resolved for hub %s (Bearer ***)", hub_url)
    return token


# ---------------------------------------------------------------------------
# Hub helpers
# ---------------------------------------------------------------------------


def get_hub_url(repo_root: pathlib.Path | None = None) -> str | None:
    """Return the hub URL from ``[hub] url``, or ``None`` if not configured.

    Args:
        repo_root: Repository root. Defaults to ``Path.cwd()``.

    Returns:
        URL string, or ``None``.
    """
    config = _load_config(_config_path(repo_root))
    hub = config.get("hub")
    if hub is None:
        return None
    url = hub.get("url", "")
    return url.strip() if url.strip() else None


def set_hub_url(url: str, repo_root: pathlib.Path | None = None) -> None:
    """Write ``[hub] url`` to ``.muse/config.toml``.

    Preserves all other sections. Creates the config file if absent.
    Rejects ``http://`` URLs — Muse never contacts a hub over cleartext HTTP.

    Args:
        url: Hub URL (must be ``https://``).
        repo_root: Repository root. Defaults to ``Path.cwd()``.

    Raises:
        ValueError: If *url* does not use the ``https://`` scheme.
    """
    if not url.startswith("https://"):
        raise ValueError(
            f"Hub URL must use HTTPS. Got: {url!r}\n"
            "Muse never connects to a hub over cleartext HTTP."
        )
    cp = _config_path(repo_root)
    cp.parent.mkdir(parents=True, exist_ok=True)
    config = _load_config(cp)
    config["hub"] = HubConfig(url=url)
    cp.write_text(_dump_toml(config), encoding="utf-8")
    logger.info("✅ Hub URL set to %s", url)


def clear_hub_url(repo_root: pathlib.Path | None = None) -> None:
    """Remove the ``[hub]`` section from ``.muse/config.toml``.

    Args:
        repo_root: Repository root. Defaults to ``Path.cwd()``.
    """
    cp = _config_path(repo_root)
    config = _load_config(cp)
    if "hub" in config:
        del config["hub"]
    cp.write_text(_dump_toml(config), encoding="utf-8")
    logger.info("✅ Hub disconnected")


# ---------------------------------------------------------------------------
# User config helpers
# ---------------------------------------------------------------------------


def get_user_config(repo_root: pathlib.Path | None = None) -> UserConfig:
    """Return the ``[user]`` section, or an empty UserConfig if absent."""
    config = _load_config(_config_path(repo_root))
    return config.get("user") or {}


def set_user_field(key: str, value: str, repo_root: pathlib.Path | None = None) -> None:
    """Set a single ``[user]`` field by name.

    Allowed keys: ``name``, ``email``, ``type``.

    Args:
        key: Field name within ``[user]``.
        value: New value.
        repo_root: Repository root. Defaults to ``Path.cwd()``.

    Raises:
        ValueError: If *key* is not a recognised user config field.
    """
    if key not in {"name", "email", "type"}:
        raise ValueError(f"Unknown [user] config key: {key!r}. Valid keys: name, email, type")
    cp = _config_path(repo_root)
    cp.parent.mkdir(parents=True, exist_ok=True)
    config = _load_config(cp)
    user: UserConfig = config.get("user") or {}
    if key == "name":
        user["name"] = value
    elif key == "email":
        user["email"] = value
    elif key == "type":
        user["type"] = value
    config["user"] = user
    cp.write_text(_dump_toml(config), encoding="utf-8")
    logger.info("✅ user.%s = %r", key, value)


# ---------------------------------------------------------------------------
# Generic dotted-key helpers
# ---------------------------------------------------------------------------

_BLOCKED_NAMESPACES: dict[str, str] = {
    "auth": "Use `muse auth login` to manage credentials.",
    "remotes": "Use `muse remote add/remove/rename` to manage remotes.",
}

_SETTABLE_NAMESPACES = {"user", "hub", "domain"}


def get_config_value(key: str, repo_root: pathlib.Path | None = None) -> str | None:
    """Get a config value by dotted key (e.g. ``user.name``, ``hub.url``).

    Returns ``None`` when the key is not set or the namespace is unknown.

    Args:
        key: Dotted key in ``<namespace>.<subkey>`` form.
        repo_root: Repository root. Defaults to ``Path.cwd()``.

    Returns:
        String value, or ``None``.
    """
    parts = key.split(".", 1)
    if len(parts) != 2:
        return None
    namespace, subkey = parts
    config = _load_config(_config_path(repo_root))

    if namespace == "user":
        user = config.get("user") or {}
        if subkey == "name":
            return user.get("name")
        if subkey == "email":
            return user.get("email")
        if subkey == "type":
            return user.get("type")
        return None

    if namespace == "hub":
        hub = config.get("hub") or {}
        if subkey == "url":
            return hub.get("url")
        return None

    if namespace == "domain":
        domain = config.get("domain") or {}
        return domain.get(subkey)

    return None


def set_config_value(key: str, value: str, repo_root: pathlib.Path | None = None) -> None:
    """Set a config value by dotted key (e.g. ``user.name``, ``domain.ticks_per_beat``).

    Args:
        key: Dotted key in ``<namespace>.<subkey>`` form.
        value: New string value.
        repo_root: Repository root. Defaults to ``Path.cwd()``.

    Raises:
        ValueError: If the namespace is blocked, unknown, or the subkey is invalid.
    """
    parts = key.split(".", 1)
    if len(parts) != 2:
        raise ValueError(f"Key must be in 'namespace.subkey' form, got: {key!r}")
    namespace, subkey = parts

    if namespace in _BLOCKED_NAMESPACES:
        raise ValueError(_BLOCKED_NAMESPACES[namespace])

    if namespace not in _SETTABLE_NAMESPACES:
        raise ValueError(
            f"Unknown config namespace {namespace!r}. "
            f"Settable namespaces: {', '.join(sorted(_SETTABLE_NAMESPACES))}"
        )

    cp = _config_path(repo_root)
    cp.parent.mkdir(parents=True, exist_ok=True)
    config = _load_config(cp)

    if namespace == "user":
        set_user_field(subkey, value, repo_root)
        return

    if namespace == "hub":
        if subkey != "url":
            raise ValueError(f"Unknown [hub] config key: {subkey!r}. Valid keys: url")
        # Route through set_hub_url — it enforces the HTTPS requirement.
        set_hub_url(value, repo_root)
        return

    # namespace == "domain"
    domain: dict[str, str] = config.get("domain") or {}
    domain[subkey] = value
    config["domain"] = domain
    cp.write_text(_dump_toml(config), encoding="utf-8")
    logger.info("✅ domain.%s = %r", subkey, value)


def config_as_dict(repo_root: pathlib.Path | None = None) -> dict[str, dict[str, str]]:
    """Return the full config as a plain ``dict[str, dict[str, str]]`` for JSON output.

    Credentials are never included — the hub section only contains the URL.

    Args:
        repo_root: Repository root. Defaults to ``Path.cwd()``.

    Returns:
        Nested dict suitable for ``json.dumps``.
    """
    config = _load_config(_config_path(repo_root))
    result: dict[str, dict[str, str]] = {}

    user = config.get("user")
    if user:
        user_dict: dict[str, str] = {}
        uname = user.get("name")
        if uname:
            user_dict["name"] = uname
        uemail = user.get("email")
        if uemail:
            user_dict["email"] = uemail
        utype = user.get("type")
        if utype:
            user_dict["type"] = utype
        if user_dict:
            result["user"] = user_dict

    hub = config.get("hub")
    if hub:
        hub_url = hub.get("url", "")
        if hub_url:
            result["hub"] = {"url": hub_url}

    remotes = config.get("remotes") or {}
    if remotes:
        remotes_dict: dict[str, str] = {}
        for rname, entry in sorted(remotes.items()):
            url = entry.get("url", "")
            if url:
                remotes_dict[rname] = url
        if remotes_dict:
            result["remotes"] = remotes_dict

    domain = config.get("domain") or {}
    if domain:
        result["domain"] = dict(sorted(domain.items()))

    return result


def config_path_for_editor(repo_root: pathlib.Path | None = None) -> pathlib.Path:
    """Return the config path for the ``config edit`` command."""
    return _config_path(repo_root)


# ---------------------------------------------------------------------------
# Remote helpers
# ---------------------------------------------------------------------------


def get_remote(name: str, repo_root: pathlib.Path | None = None) -> str | None:
    """Return the URL for remote *name*, or ``None`` when not configured.

    Args:
        name: Remote name (e.g. ``"origin"``).
        repo_root: Repository root. Defaults to ``Path.cwd()``.

    Returns:
        URL string, or ``None``.
    """
    config = _load_config(_config_path(repo_root))
    remotes = config.get("remotes")
    if remotes is None:
        return None
    entry = remotes.get(name)
    if entry is None:
        return None
    url = entry.get("url", "")
    return url.strip() if url.strip() else None


def set_remote(
    name: str,
    url: str,
    repo_root: pathlib.Path | None = None,
) -> None:
    """Write ``[remotes.<name>] url`` to ``.muse/config.toml``.

    Preserves all other sections. Creates the file if absent.

    Args:
        name: Remote name (e.g. ``"origin"``).
        url: Remote URL.
        repo_root: Repository root. Defaults to ``Path.cwd()``.
    """
    cp = _config_path(repo_root)
    cp.parent.mkdir(parents=True, exist_ok=True)
    config = _load_config(cp)
    existing_remotes = config.get("remotes")
    remotes: dict[str, RemoteEntry] = {}
    if existing_remotes:
        remotes.update(existing_remotes)
    existing_entry = remotes.get(name)
    entry: RemoteEntry = {}
    if existing_entry is not None:
        if "url" in existing_entry:
            entry["url"] = existing_entry["url"]
        if "branch" in existing_entry:
            entry["branch"] = existing_entry["branch"]
    entry["url"] = url
    remotes[name] = entry
    config["remotes"] = remotes
    cp.write_text(_dump_toml(config), encoding="utf-8")
    logger.info("✅ Remote %r set to %s", name, url)


def remove_remote(
    name: str,
    repo_root: pathlib.Path | None = None,
) -> None:
    """Remove a named remote and its tracking refs.

    Args:
        name: Remote name to remove.
        repo_root: Repository root. Defaults to ``Path.cwd()``.

    Raises:
        KeyError: If *name* is not a configured remote.
    """
    cp = _config_path(repo_root)
    config = _load_config(cp)
    remotes = config.get("remotes")
    if remotes is None or name not in remotes:
        raise KeyError(name)
    del remotes[name]
    config["remotes"] = remotes
    cp.write_text(_dump_toml(config), encoding="utf-8")
    logger.info("✅ Remote %r removed from config", name)

    root = (repo_root or pathlib.Path.cwd()).resolve()
    refs_dir = root / _MUSE_DIR / "remotes" / name
    if refs_dir.is_dir():
        shutil.rmtree(refs_dir)
        logger.debug("✅ Removed tracking refs dir %s", refs_dir)


def rename_remote(
    old_name: str,
    new_name: str,
    repo_root: pathlib.Path | None = None,
) -> None:
    """Rename a remote and move its tracking refs.

    Args:
        old_name: Current remote name.
        new_name: Desired new remote name.
        repo_root: Repository root. Defaults to ``Path.cwd()``.

    Raises:
        KeyError: If *old_name* is not a configured remote.
        ValueError: If *new_name* is already configured.
    """
    cp = _config_path(repo_root)
    config = _load_config(cp)
    remotes = config.get("remotes")
    if remotes is None or old_name not in remotes:
        raise KeyError(old_name)
    if new_name in remotes:
        raise ValueError(new_name)
    remotes[new_name] = remotes.pop(old_name)
    config["remotes"] = remotes
    cp.write_text(_dump_toml(config), encoding="utf-8")
    logger.info("✅ Remote %r renamed to %r", old_name, new_name)

    root = (repo_root or pathlib.Path.cwd()).resolve()
    old_refs_dir = root / _MUSE_DIR / "remotes" / old_name
    new_refs_dir = root / _MUSE_DIR / "remotes" / new_name
    if old_refs_dir.is_dir():
        old_refs_dir.rename(new_refs_dir)
        logger.debug("✅ Moved tracking refs dir %s → %s", old_refs_dir, new_refs_dir)


def list_remotes(repo_root: pathlib.Path | None = None) -> list[RemoteConfig]:
    """Return all configured remotes sorted alphabetically by name.

    Args:
        repo_root: Repository root. Defaults to ``Path.cwd()``.

    Returns:
        List of ``{"name": str, "url": str}`` dicts.
    """
    config = _load_config(_config_path(repo_root))
    remotes = config.get("remotes")
    if remotes is None:
        return []
    result: list[RemoteConfig] = []
    for remote_name in sorted(remotes):
        entry = remotes[remote_name]
        url = entry.get("url", "")
        if url.strip():
            result.append(RemoteConfig(name=remote_name, url=url.strip()))
    return result


# ---------------------------------------------------------------------------
# Remote tracking-head helpers
# ---------------------------------------------------------------------------


def _remote_head_path(
    remote_name: str,
    branch: str,
    repo_root: pathlib.Path | None = None,
) -> pathlib.Path:
    """Return the path to the remote tracking pointer file."""
    root = (repo_root or pathlib.Path.cwd()).resolve()
    return root / _MUSE_DIR / "remotes" / remote_name / branch


def get_remote_head(
    remote_name: str,
    branch: str,
    repo_root: pathlib.Path | None = None,
) -> str | None:
    """Return the last-known remote commit ID for *remote_name*/*branch*.

    Returns ``None`` when the tracking pointer does not exist.

    Args:
        remote_name: Remote name (e.g. ``"origin"``).
        branch: Branch name (e.g. ``"main"``).
        repo_root: Repository root. Defaults to ``Path.cwd()``.

    Returns:
        Commit ID string, or ``None``.
    """
    pointer = _remote_head_path(remote_name, branch, repo_root)
    if not pointer.is_file():
        return None
    raw = pointer.read_text(encoding="utf-8").strip()
    return raw if raw else None


def set_remote_head(
    remote_name: str,
    branch: str,
    commit_id: str,
    repo_root: pathlib.Path | None = None,
) -> None:
    """Write the remote tracking pointer for *remote_name*/*branch*.

    Args:
        remote_name: Remote name (e.g. ``"origin"``).
        branch: Branch name.
        commit_id: Commit ID to record as the known remote HEAD.
        repo_root: Repository root. Defaults to ``Path.cwd()``.
    """
    pointer = _remote_head_path(remote_name, branch, repo_root)
    pointer.parent.mkdir(parents=True, exist_ok=True)
    pointer.write_text(commit_id, encoding="utf-8")
    logger.debug("✅ Remote head %s/%s → %s", remote_name, branch, commit_id[:8])


# ---------------------------------------------------------------------------
# Upstream tracking helpers
# ---------------------------------------------------------------------------


def set_upstream(
    branch: str,
    remote_name: str,
    repo_root: pathlib.Path | None = None,
) -> None:
    """Record *remote_name* as the upstream remote for *branch*.

    Args:
        branch: Local (and remote) branch name.
        remote_name: Remote name.
        repo_root: Repository root. Defaults to ``Path.cwd()``.
    """
    cp = _config_path(repo_root)
    cp.parent.mkdir(parents=True, exist_ok=True)
    config = _load_config(cp)
    existing_remotes = config.get("remotes")
    remotes: dict[str, RemoteEntry] = {}
    if existing_remotes:
        remotes.update(existing_remotes)
    existing_entry = remotes.get(remote_name)
    entry: RemoteEntry = {}
    if existing_entry is not None:
        if "url" in existing_entry:
            entry["url"] = existing_entry["url"]
        if "branch" in existing_entry:
            entry["branch"] = existing_entry["branch"]
    entry["branch"] = branch
    remotes[remote_name] = entry
    config["remotes"] = remotes
    cp.write_text(_dump_toml(config), encoding="utf-8")
    logger.info("✅ Upstream for branch %r set to %s/%r", branch, remote_name, branch)


def get_upstream(
    branch: str,
    repo_root: pathlib.Path | None = None,
) -> str | None:
    """Return the configured upstream remote name for *branch*, or ``None``.

    Args:
        branch: Local branch name.
        repo_root: Repository root. Defaults to ``Path.cwd()``.

    Returns:
        Remote name string, or ``None``.
    """
    config = _load_config(_config_path(repo_root))
    remotes = config.get("remotes")
    if remotes is None:
        return None
    for rname, entry in remotes.items():
        tracked = entry.get("branch", "")
        if tracked.strip() == branch:
            return rname
    return None

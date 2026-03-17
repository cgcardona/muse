"""Muse CLI configuration helpers.

Reads and writes ``.muse/config.toml`` — the local repository configuration file.

The config file supports:
- ``[auth] token`` — bearer token for Muse Hub authentication (NEVER logged).
- ``[remotes.<name>] url`` — remote Hub URL for push/pull sync.
- ``[remotes.<name>] branch`` — upstream branch tracking for a remote.

Token lifecycle (MVP):
  1. User obtains a token via ``POST /auth/token``.
  2. User stores it in ``.muse/config.toml`` under ``[auth] token = "..."``
  3. CLI commands that contact the Hub read the token here automatically.

Security note: ``.muse/config.toml`` should be added to ``.gitignore`` to
prevent the token from being committed to version control.
"""
from __future__ import annotations

import logging
import pathlib
import shutil
import tomllib
from typing import TypedDict

logger = logging.getLogger(__name__)

_CONFIG_FILENAME = "config.toml"
_MUSE_DIR = ".muse"


# ---------------------------------------------------------------------------
# Named configuration types
# ---------------------------------------------------------------------------


class AuthEntry(TypedDict, total=False):
    """``[auth]`` section in ``.muse/config.toml``."""

    token: str


class RemoteEntry(TypedDict, total=False):
    """``[remotes.<name>]`` section in ``.muse/config.toml``."""

    url: str
    branch: str


class MuseConfig(TypedDict, total=False):
    """Structured view of the entire ``.muse/config.toml`` file."""

    auth: AuthEntry
    remotes: dict[str, RemoteEntry]


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
    """Load and parse config.toml; return an empty MuseConfig if absent or unreadable."""
    if not config_path.is_file():
        return {}

    try:
        with config_path.open("rb") as fh:
            raw = tomllib.load(fh)
    except Exception as exc:  # noqa: BLE001
        logger.warning("⚠️ Failed to parse %s: %s", config_path, exc)
        return {}

    config: MuseConfig = {}

    auth_raw = raw.get("auth")
    if isinstance(auth_raw, dict):
        auth: AuthEntry = {}
        token_val = auth_raw.get("token")
        if isinstance(token_val, str):
            auth["token"] = token_val
        config["auth"] = auth

    remotes_raw = raw.get("remotes")
    if isinstance(remotes_raw, dict):
        remotes: dict[str, RemoteEntry] = {}
        for name, remote_raw in remotes_raw.items():
            if isinstance(remote_raw, dict):
                entry: RemoteEntry = {}
                url_val = remote_raw.get("url")
                if isinstance(url_val, str):
                    entry["url"] = url_val
                branch_val = remote_raw.get("branch")
                if isinstance(branch_val, str):
                    entry["branch"] = branch_val
                remotes[name] = entry
        config["remotes"] = remotes

    return config


def _dump_toml(config: MuseConfig) -> str:
    """Serialize a MuseConfig back to TOML text.

    Handles the subset of TOML used by .muse/config.toml:
    - ``[auth]`` section with a ``token`` string.
    - ``[remotes.<name>]`` sections with ``url`` and optional ``branch`` strings.

    The ``[auth]`` section is always written first so the file is stable.
    """
    lines: list[str] = []

    auth = config.get("auth")
    if auth:
        lines.append("[auth]")
        token = auth.get("token", "")
        if token:
            escaped = token.replace("\\", "\\\\").replace('"', '\\"')
            lines.append(f'token = "{escaped}"')
        lines.append("")

    remotes = config.get("remotes") or {}
    for remote_name in sorted(remotes):
        entry = remotes[remote_name]
        lines.append(f"[remotes.{remote_name}]")
        url = entry.get("url", "")
        if url:
            escaped = url.replace("\\", "\\\\").replace('"', '\\"')
            lines.append(f'url = "{escaped}"')
        branch = entry.get("branch", "")
        if branch:
            lines.append(f'branch = "{branch}"')
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------


def get_auth_token(repo_root: pathlib.Path | None = None) -> str | None:
    """Read ``[auth] token`` from ``.muse/config.toml``.

    Returns the token string if present and non-empty, or ``None`` if the
    file does not exist, ``[auth]`` is absent, or ``token`` is empty/missing.

    The token value is NEVER logged — log lines mask it as ``"Bearer ***"``.

    Args:
        repo_root: Explicit repository root. Defaults to the current working
                   directory. In tests, pass a ``tmp_path`` fixture value.

    Returns:
        The raw token string, or ``None``.
    """
    config_path = _config_path(repo_root)

    if not config_path.is_file():
        logger.debug("⚠️ No %s found at %s", _CONFIG_FILENAME, config_path)
        return None

    config = _load_config(config_path)
    auth = config.get("auth")
    if auth is None:
        logger.debug("⚠️ [auth] section missing in %s", config_path)
        return None

    token = auth.get("token", "")
    if not token.strip():
        logger.debug("⚠️ [auth] token missing or empty in %s", config_path)
        return None

    logger.debug("✅ Auth token loaded from %s (Bearer ***)", config_path)
    return token.strip()


# ---------------------------------------------------------------------------
# Remote helpers
# ---------------------------------------------------------------------------


def get_remote(name: str, repo_root: pathlib.Path | None = None) -> str | None:
    """Return the URL for remote *name* from ``[remotes.<name>] url``.

    Returns ``None`` when the config file is absent or the named remote has
    not been configured. Never raises — callers decide what to do on miss.

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
    """Write ``[remotes.<name>] url = "<url>"`` to ``.muse/config.toml``.

    Preserves all other sections already in the config file. Creates the
    ``.muse/`` directory and ``config.toml`` if they do not exist.

    Args:
        name: Remote name (e.g. ``"origin"``).
        url: Remote URL (e.g. ``"https://vcs.example.com/repos/my-repo"``).
        repo_root: Repository root. Defaults to ``Path.cwd()``.
    """
    config_path = _config_path(repo_root)
    config_path.parent.mkdir(parents=True, exist_ok=True)

    config = _load_config(config_path)

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

    config_path.write_text(_dump_toml(config), encoding="utf-8")
    logger.info("✅ Remote %r set to %s", name, url)


def remove_remote(
    name: str,
    repo_root: pathlib.Path | None = None,
) -> None:
    """Remove a named remote and all its tracking refs from ``.muse/``.

    Deletes ``[remotes.<name>]`` from ``config.toml`` and removes the entire
    ``.muse/remotes/<name>/`` directory tree (tracking head files). Raises
    ``KeyError`` when the remote does not exist so callers can surface a clear
    error message to the user.

    Args:
        name: Remote name to remove (e.g. ``"origin"``).
        repo_root: Repository root. Defaults to ``Path.cwd()``.

    Raises:
        KeyError: If *name* is not a configured remote.
    """
    config_path = _config_path(repo_root)
    config = _load_config(config_path)

    remotes = config.get("remotes")
    if remotes is None or name not in remotes:
        raise KeyError(name)

    del remotes[name]
    config["remotes"] = remotes

    config_path.write_text(_dump_toml(config), encoding="utf-8")
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
    """Rename a remote in ``.muse/config.toml`` and move its tracking refs.

    Updates ``[remotes.<old_name>]`` → ``[remotes.<new_name>]`` in config and
    moves ``.muse/remotes/<old_name>/`` → ``.muse/remotes/<new_name>/``.
    Raises ``KeyError`` when *old_name* does not exist. Raises ``ValueError``
    when *new_name* is already configured.

    Args:
        old_name: Current remote name.
        new_name: Desired new remote name.
        repo_root: Repository root. Defaults to ``Path.cwd()``.

    Raises:
        KeyError: If *old_name* is not a configured remote.
        ValueError: If *new_name* already exists as a remote.
    """
    config_path = _config_path(repo_root)
    config = _load_config(config_path)

    remotes = config.get("remotes")
    if remotes is None or old_name not in remotes:
        raise KeyError(old_name)
    if new_name in remotes:
        raise ValueError(new_name)

    remotes[new_name] = remotes.pop(old_name)
    config["remotes"] = remotes

    config_path.write_text(_dump_toml(config), encoding="utf-8")
    logger.info("✅ Remote %r renamed to %r", old_name, new_name)

    root = (repo_root or pathlib.Path.cwd()).resolve()
    old_refs_dir = root / _MUSE_DIR / "remotes" / old_name
    new_refs_dir = root / _MUSE_DIR / "remotes" / new_name
    if old_refs_dir.is_dir():
        old_refs_dir.rename(new_refs_dir)
        logger.debug("✅ Moved tracking refs dir %s → %s", old_refs_dir, new_refs_dir)


def list_remotes(repo_root: pathlib.Path | None = None) -> list[RemoteConfig]:
    """Return all configured remotes as :class:`RemoteConfig` dicts.

    Returns an empty list when the config file is absent or contains no
    ``[remotes.*]`` sections. Sorted alphabetically by remote name.

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
    """Return the path to the remote tracking pointer file.

    The file lives at ``.muse/remotes/<remote_name>/<branch>`` and contains
    the last known commit_id on that remote branch.
    """
    root = (repo_root or pathlib.Path.cwd()).resolve()
    return root / _MUSE_DIR / "remotes" / remote_name / branch


def get_remote_head(
    remote_name: str,
    branch: str,
    repo_root: pathlib.Path | None = None,
) -> str | None:
    """Return the last-known remote commit ID for *remote_name*/*branch*.

    Returns ``None`` when the tracking pointer file does not exist (i.e. this
    branch has never been pushed/pulled).

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

    Creates the ``.muse/remotes/<remote_name>/`` directory if needed.

    Args:
        remote_name: Remote name (e.g. ``"origin"``).
        branch: Branch name (e.g. ``"main"``).
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

    Writes ``branch = "<branch>"`` under ``[remotes.<remote_name>]`` in
    ``.muse/config.toml``. This mirrors the git ``--set-upstream`` behaviour:
    the local branch knows which remote branch to track for future push/pull.

    Args:
        branch: Local (and remote) branch name (e.g. ``"main"``).
        remote_name: Remote name (e.g. ``"origin"``).
        repo_root: Repository root. Defaults to ``Path.cwd()``.
    """
    config_path = _config_path(repo_root)
    config_path.parent.mkdir(parents=True, exist_ok=True)

    config = _load_config(config_path)

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

    config_path.write_text(_dump_toml(config), encoding="utf-8")
    logger.info("✅ Upstream for branch %r set to %s/%r", branch, remote_name, branch)


def get_upstream(
    branch: str,
    repo_root: pathlib.Path | None = None,
) -> str | None:
    """Return the configured upstream remote name for *branch*, or ``None``.

    Reads ``branch`` under every ``[remotes.*]`` section and returns the first
    remote whose ``branch`` value matches *branch*.

    Args:
        branch: Local branch name (e.g. ``"main"``).
        repo_root: Repository root. Defaults to ``Path.cwd()``.

    Returns:
        Remote name string (e.g. ``"origin"``), or ``None`` when no upstream
        is configured for *branch*.
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

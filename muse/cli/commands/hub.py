"""muse hub — MuseHub fabric connection management.

The hub is not just a remote.  It is the shared fabric where versioned
multidimensional state flows across agents, humans, and repositories.
Connecting a repo to a hub anchors it to the synchronisation layer that
enables push/pull, plugin discovery, and multi-agent coordination.

Separation of concerns
-----------------------
- ``muse remote`` manages generic push/pull endpoints (any Muse server).
- ``muse hub``   manages the *primary identity fabric* — the one hub this
  repo belongs to for authentication, discovery, and coordination.

A repo has at most **one** hub.  It may have many remotes.

Subcommands
-----------
::

    muse hub connect <url>    Attach this repo to a MuseHub instance.
    muse hub status           Show connection and identity information.
    muse hub disconnect       Remove the hub association from this repo.
    muse hub ping             Test HTTP connectivity to the hub.
"""

from __future__ import annotations

import logging
import urllib.error
import urllib.request

import typer

from muse.cli.config import clear_hub_url, get_hub_url, set_hub_url
from muse.core.errors import ExitCode
from muse.core.identity import load_identity
from muse.core.repo import find_repo_root

logger = logging.getLogger(__name__)

app = typer.Typer(no_args_is_help=True)

_CONNECT_TIMEOUT = 8  # seconds for ping/status health check


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalise_url(url: str) -> str:
    """Normalise *url* to an https:// URL.

    Adds ``https://`` when no scheme is present.  Raises ``ValueError`` when
    an explicit ``http://`` scheme is given — sending a bearer token over
    cleartext HTTP is never acceptable.

    Args:
        url: Raw user-supplied URL.

    Returns:
        Normalised ``https://`` URL without a trailing slash.

    Raises:
        ValueError: If the URL explicitly uses the ``http://`` scheme.
    """
    stripped = url.strip().rstrip("/")
    if not stripped.startswith(("http://", "https://")):
        stripped = f"https://{stripped}"
    if stripped.startswith("http://"):
        host = stripped[len("http://"):]
        raise ValueError(
            f"Insecure URL rejected: {stripped!r}\n"
            f"MuseHub requires HTTPS. Did you mean: https://{host}"
        )
    return stripped


def _hub_hostname(url: str) -> str:
    """Extract the display hostname from a hub URL."""
    stripped = url.strip().rstrip("/")
    if "://" in stripped:
        stripped = stripped.split("://", 1)[1]
    return stripped.split("/")[0]


def _ping_hub(url: str) -> tuple[bool, str]:
    """Attempt an HTTP GET to ``<url>/health``.

    Returns a (reachable, message) tuple.  Never raises — all errors are
    captured and surfaced as human-readable messages.
    """
    health_url = f"{url.rstrip('/')}/health"
    try:
        req = urllib.request.Request(health_url, method="GET")
        with urllib.request.urlopen(req, timeout=_CONNECT_TIMEOUT) as resp:
            status = resp.status
            if 200 <= status < 300:
                return True, f"HTTP {status} OK"
            return False, f"HTTP {status}"
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code} {exc.reason}"
    except urllib.error.URLError as exc:
        return False, str(exc.reason)
    except TimeoutError:
        return False, "timed out"
    except OSError as exc:
        return False, str(exc)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@app.command()
def connect(
    url: str = typer.Argument(
        ...,
        metavar="URL",
        help="MuseHub URL (e.g. https://musehub.ai or just musehub.ai).",
    ),
) -> None:
    """Attach this repository to a MuseHub instance.

    Writes ``[hub] url`` to ``.muse/config.toml``.  Does not modify
    credentials — authenticate separately with ``muse auth login``.

    After connecting::

        muse hub connect https://musehub.ai
        muse auth login
        muse push
    """
    root = find_repo_root()
    if root is None:
        typer.echo("❌ Not inside a Muse repository. Run `muse init` first.")
        raise typer.Exit(code=ExitCode.REPO_NOT_FOUND)

    try:
        normalised = _normalise_url(url)
    except ValueError as exc:
        typer.echo(f"❌ {exc}")
        raise typer.Exit(code=ExitCode.USER_ERROR) from exc
    hostname = _hub_hostname(normalised)

    # Check for an existing connection and warn before overwriting.
    existing = get_hub_url(root)
    if existing and existing != normalised:
        existing_host = _hub_hostname(existing)
        typer.echo(
            f"⚠️  This repo was connected to {existing_host}.\n"
            f"   Switching to {hostname}.\n"
            f"   Your credentials for {existing_host} remain in ~/.muse/identity.toml.\n"
            f"   To remove them: muse auth logout --hub {existing_host}"
        )

    set_hub_url(normalised, root)

    # Check if an identity already exists for this hub.
    identity = load_identity(normalised)
    if identity is not None:
        name = identity.get("name") or "—"
        itype = identity.get("type") or "unknown"
        typer.echo(f"✅ Connected to {hostname}")
        typer.echo(f"   Authenticated as {itype} '{name}'")
    else:
        typer.echo(f"✅ Connected to {hostname}")
        typer.echo(f"   No identity stored yet — run: muse auth login")


@app.command()
def status(
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit JSON instead of human-readable output.",
    ),
) -> None:
    """Show the hub connection and identity for this repository.

    Displays the hub URL, stored identity (if any), and whether the hub is
    reachable.  Designed to be agent-friendly with ``--json``::

        muse hub status --json
    """
    root = find_repo_root()
    if root is None:
        typer.echo("❌ Not inside a Muse repository.")
        raise typer.Exit(code=ExitCode.REPO_NOT_FOUND)

    hub_url = get_hub_url(root)
    if hub_url is None:
        typer.echo(
            "No hub connected.\n"
            "Run: muse hub connect <url>"
        )
        raise typer.Exit(code=ExitCode.USER_ERROR)

    hostname = _hub_hostname(hub_url)
    identity = load_identity(hub_url)

    if json_output:
        import json

        out: dict[str, str | bool] = {
            "hub_url": hub_url,
            "hostname": hostname,
            "authenticated": identity is not None,
        }
        if identity is not None:
            itype = identity.get("type", "")
            if itype:
                out["identity_type"] = itype
            name = identity.get("name", "")
            if name:
                out["identity_name"] = name
            identity_id = identity.get("id", "")
            if identity_id:
                out["identity_id"] = identity_id
        typer.echo(json.dumps(out, indent=2))
        return

    typer.echo("")
    typer.echo("  Hub")
    typer.echo(f"    URL:       {hub_url}")

    if identity is None:
        typer.echo(f"    Auth:      not authenticated — run `muse auth login`")
    else:
        itype = identity.get("type") or "unknown"
        name = identity.get("name") or "—"
        identity_id = identity.get("id") or "—"
        token = identity.get("token", "")
        caps = identity.get("capabilities") or []
        typer.echo(f"    Type:      {itype}")
        typer.echo(f"    Name:      {name}")
        typer.echo(f"    ID:        {identity_id}")
        typer.echo(f"    Token:     {'set (Bearer ***)' if token else 'not set'}")
        if caps:
            typer.echo(f"    Caps:      {' '.join(caps)}")

    typer.echo("")


@app.command()
def disconnect() -> None:
    """Remove the hub association from this repository.

    Removes ``[hub] url`` from ``.muse/config.toml``.  Credentials in
    ``~/.muse/identity.toml`` are preserved — use ``muse auth logout`` to
    remove them as well.
    """
    root = find_repo_root()
    if root is None:
        typer.echo("❌ Not inside a Muse repository.")
        raise typer.Exit(code=ExitCode.REPO_NOT_FOUND)

    hub_url = get_hub_url(root)
    if hub_url is None:
        typer.echo("No hub connected — nothing to do.")
        return

    hostname = _hub_hostname(hub_url)
    clear_hub_url(root)
    typer.echo(f"✅ Disconnected from {hostname}.")
    typer.echo(
        f"   Credentials in ~/.muse/identity.toml are preserved.\n"
        f"   To remove them too: muse auth logout --hub {hub_url}"
    )


@app.command()
def ping() -> None:
    """Test HTTP connectivity to the configured hub.

    Sends a GET request to ``<hub_url>/health`` and reports the result.
    Exit code 0 = reachable, non-zero = unreachable.
    """
    root = find_repo_root()
    if root is None:
        typer.echo("❌ Not inside a Muse repository.")
        raise typer.Exit(code=ExitCode.REPO_NOT_FOUND)

    hub_url = get_hub_url(root)
    if hub_url is None:
        typer.echo(
            "No hub connected.\n"
            "Run: muse hub connect <url>"
        )
        raise typer.Exit(code=ExitCode.USER_ERROR)

    hostname = _hub_hostname(hub_url)
    typer.echo(f"Pinging {hostname}…", nl=False)
    reachable, message = _ping_hub(hub_url)

    if reachable:
        typer.echo(f" ✅ {message}")
    else:
        typer.echo(f" ❌ {message}")
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR)

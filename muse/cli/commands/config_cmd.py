"""muse config — local repository configuration.

Provides structured, typed read/write access to ``.muse/config.toml``.
For hub credentials, use ``muse auth``.  For remote connections, use
``muse remote``.

Settable namespaces
--------------------
- ``user.name``   — display name (human or agent handle)
- ``user.email``  — contact email
- ``user.type``   — ``"human"`` or ``"agent"``
- ``hub.url``     — hub fabric URL (alias for ``muse hub connect``)
- ``domain.*``    — domain-specific keys; read by the active plugin

Blocked via ``muse config set``
---------------------------------
- ``auth.*``    — use ``muse auth login``
- ``remotes.*`` — use ``muse remote add/remove``

Output format
-------------
``muse config show`` emits TOML by default (human-readable).  Pass
``--json`` for machine-readable output — no credentials are ever included.

Examples
--------
::

    muse config show
    muse config show --json
    muse config get user.name
    muse config set user.name "Alice"
    muse config set user.type agent
    muse config set domain.ticks_per_beat 480
    muse config edit
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys

from muse.cli.config import (
    config_as_dict,
    config_path_for_editor,
    get_config_value,
    set_config_value,
)
from muse.core.errors import ExitCode
from muse.core.repo import find_repo_root

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the config subcommand."""
    parser = subparsers.add_parser(
        "config",
        help="Local repository configuration.",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subs = parser.add_subparsers(dest="subcommand", metavar="SUBCOMMAND")
    subs.required = True

    show_p = subs.add_parser("show", help="Display the current repository configuration.")
    show_p.add_argument("--json", action="store_true", dest="json_output",
                        help="Emit JSON instead of TOML.")
    show_p.add_argument("--format", "-f", default="text", dest="fmt",
                        help="Output format: text or json (alias for --json).")
    show_p.set_defaults(func=run_show)

    get_p = subs.add_parser("get", help="Print the value of a single config key.")
    get_p.add_argument("key", metavar="KEY",
                       help="Dotted key to read (e.g. user.name, hub.url, domain.ticks_per_beat).")
    get_p.set_defaults(func=run_get)

    set_p = subs.add_parser("set", help="Set a config value by dotted key.")
    set_p.add_argument("key", metavar="KEY",
                       help="Dotted key to set (e.g. user.name, domain.ticks_per_beat).")
    set_p.add_argument("value", metavar="VALUE",
                       help="New value (always stored as a string).")
    set_p.set_defaults(func=run_set)

    edit_p = subs.add_parser("edit", help="Open .muse/config.toml in $EDITOR or $VISUAL.")
    edit_p.set_defaults(func=run_edit)


def run_show(args: argparse.Namespace) -> None:
    """Display the current repository configuration.

    Output is TOML by default.  Use ``--json`` or ``--format json`` for
    agent-friendly output.  Credentials are never included regardless of format.

    JSON payload (top-level keys present only when set)::

        {
          "user":    {"name": "...", "email": "...", "type": "human|agent"},
          "hub":     {"url": "https://musehub.ai"},
          "remotes": {"origin": "https://..."},
          "domain":  {"ticks_per_beat": "480"}
        }
    """
    json_output: bool = args.json_output
    fmt: str = args.fmt

    if fmt == "json":
        json_output = True
    elif fmt != "text":
        from muse.core.validation import sanitize_display
        print(f"❌ Unknown --format '{sanitize_display(fmt)}'. Choose text or json.", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)

    root = find_repo_root()

    data = config_as_dict(root)

    if json_output:
        print(json.dumps(data, indent=2))
        return

    # Render as TOML-like display
    if not data:
        print("# No configuration set.")
        return

    user = data.get("user")
    if user:
        print("[user]")
        for key, val in sorted(user.items()):
            print(f'{key} = "{val}"')
        print("")

    hub = data.get("hub")
    if hub:
        print("[hub]")
        for key, val in sorted(hub.items()):
            print(f'{key} = "{val}"')
        print("")

    remotes = data.get("remotes")
    if remotes:
        for remote_name, remote_url in sorted(remotes.items()):
            print(f"[remotes.{remote_name}]")
            print(f'url = "{remote_url}"')
            print("")

    domain = data.get("domain")
    if domain:
        print("[domain]")
        for key, val in sorted(domain.items()):
            print(f'{key} = "{val}"')
        print("")


def run_get(args: argparse.Namespace) -> None:
    """Print the value of a single config key.

    Exits non-zero when the key is not set, so agents can branch::

        VALUE=$(muse config get user.type) || echo "not set"
    """
    key: str = args.key

    root = find_repo_root()
    value = get_config_value(key, root)

    if value is None:
        print(f"# {key} is not set", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)

    print(value)


def run_set(args: argparse.Namespace) -> None:
    """Set a config value by dotted key.

    Examples::

        muse config set user.name "Alice"
        muse config set user.type agent
        muse config set hub.url https://musehub.ai
        muse config set domain.ticks_per_beat 480

    For credentials, use ``muse auth login``.
    For remotes, use ``muse remote add``.
    """
    key: str = args.key
    value: str = args.value

    root = find_repo_root()
    try:
        set_config_value(key, value, root)
    except ValueError as exc:
        print(f"❌ {exc}")
        raise SystemExit(ExitCode.USER_ERROR) from exc

    print(f"✅ {key} = {value!r}")


def run_edit(args: argparse.Namespace) -> None:
    """Open ``.muse/config.toml`` in ``$EDITOR`` or ``$VISUAL``.

    Falls back to ``vi`` when neither environment variable is set.
    """
    root = find_repo_root()
    if root is None:
        print("❌ Not inside a Muse repository.")
        raise SystemExit(ExitCode.REPO_NOT_FOUND)

    config_path = config_path_for_editor(root)
    if not config_path.is_file():
        print(f"❌ Config file not found: {config_path}")
        raise SystemExit(ExitCode.USER_ERROR)

    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "vi"
    try:
        subprocess.run([editor, str(config_path)], check=True)
    except FileNotFoundError:
        print(f"❌ Editor not found: {editor!r}")
        raise SystemExit(ExitCode.USER_ERROR)
    except subprocess.CalledProcessError as exc:
        print(f"❌ Editor exited with code {exc.returncode}")
        raise SystemExit(ExitCode.USER_ERROR)

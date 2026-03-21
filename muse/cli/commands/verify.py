"""``muse verify`` — whole-repository integrity check.

Walks every reachable commit from every branch ref and performs a three-tier
integrity check:

1. Every branch ref points to an existing commit.
2. Every commit's snapshot exists.
3. Every object referenced by every snapshot exists, and (unless
   ``--no-objects``) its SHA-256 is recomputed to detect silent corruption.

This is Muse's equivalent of ``git fsck``.  Run it periodically on long-lived
agent repositories or after recovering from a storage failure.

Usage::

    muse verify                 # full check — re-hashes all objects
    muse verify --no-objects    # existence check only (faster)
    muse verify --quiet         # no output — exit code only
    muse verify --format json   # machine-readable report

Exit codes::

    0 — all checks passed
    1 — one or more integrity failures detected
    3 — I/O error reading repository files
"""

from __future__ import annotations

import json
import logging
from typing import Annotated

import typer

from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.verify import run_verify

logger = logging.getLogger(__name__)

app = typer.Typer(help="Whole-repository integrity check (re-hash objects, verify DAG).")


@app.callback(invoke_without_command=True)
def verify(
    quiet: Annotated[
        bool,
        typer.Option("--quiet", "-q", help="No output — exit 0 if clean, 1 on any failure."),
    ] = False,
    no_objects: Annotated[
        bool,
        typer.Option("--no-objects", "-O", help="Skip object re-hashing (existence check only)."),
    ] = False,
    fmt: Annotated[
        str,
        typer.Option("--format", "-f", help="Output format: text or json."),
    ] = "text",
) -> None:
    """Check repository integrity — commits, snapshots, and objects.

    Walks every reachable commit from every branch ref.  For each commit,
    verifies that the snapshot exists.  For each snapshot, verifies that every
    object file exists and (by default) re-hashes it to detect bit-rot.

    The exit code is 0 when all checks pass, 1 when any failure is found.
    Use ``--quiet`` in scripts that only care about the exit code.

    Examples::

        muse verify                   # full integrity check
        muse verify --no-objects      # fast existence-only check
        muse verify --quiet && echo "healthy"
        muse verify --format json | jq '.failures'
    """
    if fmt not in {"text", "json"}:
        typer.echo(f"❌ Unknown --format '{fmt}'. Choose text or json.", err=True)
        raise typer.Exit(code=ExitCode.USER_ERROR)

    root = require_repo()

    try:
        result = run_verify(root, check_objects=not no_objects)
    except OSError as exc:
        if not quiet:
            typer.echo(f"❌ I/O error during verify: {exc}", err=True)
        raise typer.Exit(code=ExitCode.INTERNAL_ERROR) from exc

    if quiet:
        raise typer.Exit(code=0 if result["all_ok"] else ExitCode.USER_ERROR)

    if fmt == "json":
        typer.echo(json.dumps(dict(result), indent=2))
    else:
        typer.echo(f"Checking refs...        {result['refs_checked']} ref(s)")
        typer.echo(f"Checking commits...     {result['commits_checked']} commit(s)")
        typer.echo(f"Checking snapshots...   {result['snapshots_checked']} snapshot(s)")
        action = "checked" if not no_objects else "verified (existence only)"
        typer.echo(f"Checking objects...     {result['objects_checked']} object(s) {action}")

        if result["all_ok"]:
            typer.echo("✅ Repository is healthy.")
        else:
            typer.echo(f"\n❌ {len(result['failures'])} integrity failure(s):")
            for f in result["failures"]:
                typer.echo(f"  {f['kind']:<10} {f['id'][:24]}  {f['error']}")

    raise typer.Exit(code=0 if result["all_ok"] else ExitCode.USER_ERROR)

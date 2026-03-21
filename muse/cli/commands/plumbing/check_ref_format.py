"""muse plumbing check-ref-format — validate branch and ref names.

Tests one or more names against Muse's branch-naming rules and reports
whether each is valid.  The same validation applied by ``muse branch`` and
``muse plumbing update-ref`` is exposed here for scripting, so pipelines can
pre-validate names before attempting to create branches.

Rules enforced
--------------
- 1–255 characters.
- No backslash, null bytes, CR, LF, or tab.
- No leading or trailing dot (``./``).
- No consecutive dots (``..``).
- No leading or trailing forward slash.
- No consecutive forward slashes (``//``).

These match Git's branch-naming conventions so Muse branch names are safe to
sync with Git-backed remotes.

Output (JSON, default)::

    {
      "results": [
        {"name": "feat/my-branch", "valid": true,  "error": null},
        {"name": "bad..name",      "valid": false, "error": "..."}
      ],
      "all_valid": false
    }

Text output (``--format text``)::

    ok    feat/my-branch
    FAIL  bad..name  →  Branch name 'bad..name' contains forbidden characters

With ``--quiet``: no output; exits 0 if all names are valid, 1 otherwise.

Plumbing contract
-----------------

- Exit 0: all supplied names are valid.
- Exit 1: one or more names are invalid; no names supplied; bad ``--format``.
- (No Exit 3 — this command is pure CPU, no I/O.)
"""

from __future__ import annotations

import json
import logging
from typing import TypedDict

import typer

from muse.core.errors import ExitCode
from muse.core.validation import validate_branch_name

logger = logging.getLogger(__name__)

app = typer.Typer()

_FORMAT_CHOICES = ("json", "text")


class _CheckResult(TypedDict):
    name: str
    valid: bool
    error: str | None


class _CheckRefFormatResult(TypedDict):
    results: list[_CheckResult]
    all_valid: bool


@app.callback(invoke_without_command=True)
def check_ref_format(
    ctx: typer.Context,
    names: list[str] = typer.Argument(
        ..., help="One or more branch or ref names to validate."
    ),
    quiet: bool = typer.Option(
        False,
        "--quiet",
        "-q",
        help="No output. Exit 0 if all valid, exit 1 if any invalid.",
    ),
    fmt: str = typer.Option(
        "json", "--format", "-f", help="Output format: json or text."
    ),
) -> None:
    """Validate branch or ref names against Muse naming rules.

    Applies the same rules used by ``muse branch`` and ``muse plumbing
    update-ref``.  Use this in scripts to pre-validate names before attempting
    to create a branch, avoiding partial-failure states.

    Use ``--quiet`` in shell conditionals::

        muse plumbing check-ref-format -q feat/my-branch && echo "name is valid"

    The exit code reflects whether *all* supplied names passed:

    - Exit 0: every name is valid.
    - Exit 1: at least one name is invalid, or no names were supplied.
    """
    if fmt not in _FORMAT_CHOICES:
        typer.echo(
            json.dumps(
                {"error": f"Unknown format {fmt!r}. Valid: {', '.join(_FORMAT_CHOICES)}"}
            )
        )
        raise typer.Exit(code=ExitCode.USER_ERROR)

    if not names:
        typer.echo(json.dumps({"error": "At least one name argument is required."}))
        raise typer.Exit(code=ExitCode.USER_ERROR)

    results: list[_CheckResult] = []
    for name in names:
        try:
            validate_branch_name(name)
            results.append({"name": name, "valid": True, "error": None})
        except (ValueError, TypeError) as exc:
            results.append({"name": name, "valid": False, "error": str(exc)})

    all_valid = all(r["valid"] for r in results)

    if quiet:
        raise typer.Exit(code=0 if all_valid else ExitCode.USER_ERROR)

    if fmt == "text":
        for r in results:
            if r["valid"]:
                typer.echo(f"ok    {r['name']}")
            else:
                typer.echo(f"FAIL  {r['name']}  →  {r['error']}")
        if not all_valid:
            raise typer.Exit(code=ExitCode.USER_ERROR)
        return

    result: _CheckRefFormatResult = {"results": results, "all_valid": all_valid}
    typer.echo(json.dumps(result))
    if not all_valid:
        raise typer.Exit(code=ExitCode.USER_ERROR)

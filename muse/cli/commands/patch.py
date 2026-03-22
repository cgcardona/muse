"""muse patch — surgical semantic patch at symbol granularity.

Modifies exactly one named symbol in a source file without touching any
surrounding code.  The target is identified by its Muse symbol address
(``"file.py::SymbolName"`` or ``"file.py::ClassName.method"``).

This command is the foundation for AI-agent-driven code modification.  An
agent that needs to change ``src/billing.py::compute_invoice_total`` can
do so with surgical precision — no risk of accidentally modifying adjacent
functions, no diff noise, no merge headache.

After patching, the working tree is dirty and ``muse status`` will show
exactly which symbol changed.  Run ``muse commit`` as usual.

Security note: the file path component of ADDRESS is validated via
``contain_path()`` before any disk access.  Paths that escape the repo root
(e.g. ``../../etc/passwd::foo``) are rejected with exit 1.

Usage::

    # Write new body to a file and apply it
    muse patch "src/billing.py::compute_invoice_total" --body new_body.py

    # Read new body from stdin
    echo "def foo(): return 42" | muse patch "src/utils.py::foo" --body -

    # Preview what will change without writing
    muse patch "src/billing.py::compute_invoice_total" --body new_body.py --dry-run

    # Machine-readable output for agents
    muse patch "src/utils.py::foo" --body new.py --json

Output::

    ✅ Patched src/billing.py::compute_invoice_total
       Lines 2–4 replaced (was 3 lines, now 4 lines)
       Surrounding code untouched (4 symbols preserved)
       Run `muse status` to review, then `muse commit`

JSON output (``--json``)::

    {
      "address": "src/billing.py::compute_invoice_total",
      "file": "src/billing.py",
      "lines_replaced": 3,
      "new_lines": 4,
      "dry_run": false
    }
"""

from __future__ import annotations

import argparse
import json
import logging
import pathlib
import sys

from muse.core.errors import ExitCode
from muse.core.repo import require_repo
from muse.core.validation import contain_path
from muse.plugins.code.ast_parser import parse_symbols, validate_syntax

logger = logging.getLogger(__name__)


def _locate_symbol(file_path: pathlib.Path, address: str) -> tuple[int, int] | None:
    """Return ``(lineno, end_lineno)`` for the symbol at *address* in *file_path*.

    Both values are 1-indexed.  Returns ``None`` when the symbol is not found.
    """
    try:
        raw = file_path.read_bytes()
    except OSError:
        return None
    rel = address.split("::")[0]
    tree = parse_symbols(raw, rel)
    rec = tree.get(address)
    if rec is None:
        return None
    return rec["lineno"], rec["end_lineno"]


def _read_new_body(body_arg: str) -> str | None:
    """Read the replacement source from *body_arg* (file path or ``"-"``)."""
    if body_arg == "-":
        return sys.stdin.read()
    src = pathlib.Path(body_arg)
    if not src.exists():
        return None
    return src.read_text()


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the patch subcommand."""
    parser = subparsers.add_parser(
        "patch",
        help="Replace exactly one symbol's source — surgical precision for agents.",
        description=__doc__,
    )
    parser.add_argument(
        "address",
        metavar="ADDRESS",
        help='Symbol address, e.g. "src/billing.py::compute_invoice_total".',
    )
    parser.add_argument(
        "--body", "-b",
        dest="body_arg",
        required=True,
        metavar="FILE",
        help='File containing the replacement source (use "-" for stdin).',
    )
    parser.add_argument(
        "--dry-run", "-n",
        action="store_true",
        help="Print what would change without writing to disk.",
    )
    parser.add_argument("--json", dest="as_json", action="store_true", help="Emit result as JSON for agent consumption.")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> None:
    """Replace exactly one symbol's source — surgical precision for agents.

    ``muse patch`` locates the symbol at ADDRESS in the working tree,
    reads the replacement source from --body, and splices it in at the
    exact line range the symbol currently occupies.  Every other symbol
    in the file is untouched.

    The replacement source must define exactly the symbol being replaced
    (same name, at the top level of the file passed via --body).  Muse
    verifies the patched file remains parseable before writing.

    The file path component of ADDRESS is validated against the repo root —
    path-traversal addresses (e.g. ``../../etc/passwd::foo``) are rejected.

    After patching, run ``muse status`` to review the change, then
    ``muse commit`` to record it.  The structured delta will describe
    exactly what changed at the semantic level (implementation changed,
    signature changed, etc.).
    """
    address: str = args.address
    body_arg: str = args.body_arg
    dry_run: bool = args.dry_run
    as_json: bool = args.as_json

    root = require_repo()

    # Parse address to get file path.
    if "::" not in address:
        print(f"❌ Invalid address '{address}' — must be 'file.py::SymbolName'.", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)

    rel_path, sym_name = address.split("::", 1)

    # Validate the file path stays inside the repo root.
    try:
        file_path = contain_path(root, rel_path)
    except ValueError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)

    if not file_path.exists():
        print(f"❌ File '{rel_path}' not found in working tree.", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)

    # Locate the symbol.
    location = _locate_symbol(file_path, address)
    if location is None:
        print(
            f"❌ Symbol '{address}' not found in {rel_path}.\n"
            f"   Run `muse symbols --file {rel_path}` to see available symbols.",
            file=sys.stderr,
        )
        raise SystemExit(ExitCode.USER_ERROR)

    start_line, end_line = location  # 1-indexed, inclusive

    # Read the replacement source.
    new_body = _read_new_body(body_arg)
    if new_body is None:
        print(f"❌ Could not read body from '{body_arg}'.", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)

    # Read current file.
    original = file_path.read_text(encoding="utf-8")
    lines = original.splitlines(keepends=True)
    old_lines = lines[start_line - 1 : end_line]

    # Ensure new_body ends with a newline.
    if not new_body.endswith("\n"):
        new_body += "\n"

    # Splice.
    new_lines = lines[: start_line - 1] + [new_body] + lines[end_line:]
    new_content = "".join(new_lines)

    # Verify the patched file is still parseable for all supported languages.
    syntax_error = validate_syntax(new_content.encode("utf-8"), rel_path)
    if syntax_error is not None:
        print(f"❌ Patched file has a {syntax_error}", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)

    new_line_count = new_body.count(chr(10))

    if dry_run:
        if as_json:
            print(json.dumps({
                "address": address,
                "file": rel_path,
                "lines_replaced": len(old_lines),
                "new_lines": new_line_count,
                "dry_run": True,
            }, indent=2))
            return
        print(f"\n[dry-run] Would patch {rel_path}")
        print(f"  Symbol:        {sym_name}")
        print(f"  Replace lines: {start_line}–{end_line} ({len(old_lines)} line(s))")
        print(f"  New source:    {new_line_count} line(s)")
        print("  No changes written (--dry-run).")
        return

    file_path.write_text(new_content, encoding="utf-8")

    # Count remaining symbols for the "surrounding code untouched" message.
    remaining = parse_symbols(file_path.read_bytes(), rel_path)
    other_count = sum(1 for addr in remaining if addr != address)

    if as_json:
        print(json.dumps({
            "address": address,
            "file": rel_path,
            "lines_replaced": len(old_lines),
            "new_lines": new_line_count,
            "dry_run": False,
        }, indent=2))
        return

    print(f"\n✅ Patched {address}")
    print(f"   Lines {start_line}–{end_line} replaced ({len(old_lines)} → {new_line_count} line(s))")
    print(f"   Surrounding code untouched ({other_count} symbol(s) preserved)")
    print("   Run `muse status` to review, then `muse commit`")

"""muse plumbing hash-object — compute the SHA-256 object ID of a file.

Computes the content-addressed object ID (SHA-256 hex digest) of a file.
With ``--write`` the object is also stored in ``.muse/objects/`` so it can be
referenced by future snapshots and commits.

Output (JSON, default)::

    {"object_id": "<sha256>", "stored": false}

Output (--format text)::

    <sha256>

Plumbing contract
-----------------

- Exit 0: hash computed successfully.
- Exit 1: file not found, path is a directory, or unknown --format value.
- Exit 3: I/O error writing to the store, or integrity check failed.
"""

from __future__ import annotations

import argparse
import json
import logging
import pathlib
import sys

from muse.core.errors import ExitCode
from muse.core.object_store import write_object_from_path
from muse.core.repo import find_repo_root
from muse.core.snapshot import hash_file

logger = logging.getLogger(__name__)

_FORMAT_CHOICES = ("json", "text")


def register(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the hash-object subcommand."""
    parser = subparsers.add_parser(
        "hash-object",
        help="SHA-256 a file; optionally store it in the object store.",
        description=__doc__,
    )
    parser.add_argument("path", type=pathlib.Path, help="File to hash.")
    parser.add_argument(
        "--write", "-w",
        action="store_true",
        help="Store the object in .muse/objects/.",
    )
    parser.add_argument(
        "--format", "-f",
        dest="fmt",
        default="json",
        metavar="FORMAT",
        help="Output format: json or text. (default: json)",
    )
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> None:
    """Compute the SHA-256 object ID of a file.

    Analogous to ``git hash-object``.  The object ID is deterministic —
    identical bytes always produce the same ID.  Pass ``--write`` to also
    store the object so it can be referenced by future ``muse plumbing
    commit-tree`` calls.
    """
    fmt: str = args.fmt
    path: pathlib.Path = args.path
    write: bool = args.write

    if fmt not in _FORMAT_CHOICES:
        print(
            f"❌ Unknown format {fmt!r}. Valid choices: {', '.join(_FORMAT_CHOICES)}",
            file=sys.stderr,
        )
        raise SystemExit(ExitCode.USER_ERROR)

    if not path.exists():
        print(f"❌ Path does not exist: {path}", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)
    if path.is_dir():
        print(f"❌ Path is a directory, not a file: {path}", file=sys.stderr)
        raise SystemExit(ExitCode.USER_ERROR)

    object_id = hash_file(path)
    stored = False

    if write:
        root = find_repo_root(pathlib.Path.cwd())
        if root is None:
            print("❌ Not inside a Muse repository. Cannot write object.", file=sys.stderr)
            raise SystemExit(ExitCode.USER_ERROR)
        try:
            # write_object_from_path streams the file at 64 KiB at a time via
            # shutil.copy2, so arbitrarily large blobs never spike the heap.
            # It also re-verifies the hash before writing, catching any race
            # between our hash_file() call above and the actual store write.
            stored = write_object_from_path(root, object_id, path)
        except ValueError as exc:
            # File changed between hash_file() and the integrity re-check.
            print(
                f"❌ Integrity check failed (file may have changed during write): {exc}",
                file=sys.stderr,
            )
            raise SystemExit(ExitCode.INTERNAL_ERROR)
        except OSError as exc:
            print(f"❌ Failed to write object: {exc}", file=sys.stderr)
            raise SystemExit(ExitCode.INTERNAL_ERROR)

    if fmt == "text":
        print(object_id)
        return

    print(json.dumps({"object_id": object_id, "stored": stored}))

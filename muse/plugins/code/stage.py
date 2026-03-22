"""Code-domain staging index — ``muse code add`` persistence layer.

The staging index lives at ``.muse/code/stage.json``.  It records which
files the user has explicitly staged for the next ``muse commit``, along
with the content-addressed object ID of each staged version.

When the stage is non-empty, ``muse commit`` commits *only* the staged
versions of staged files, carrying all other tracked files forward from
the previous commit unchanged.  This mirrors Git's index model exactly.

Format::

    {
      "version": 1,
      "entries": {
        "src/auth.py": {
          "object_id": "<sha256>",
          "mode": "M",
          "staged_at": "2026-03-21T14:32:00+00:00"
        }
      }
    }

Modes:

- ``"A"`` — added (file is new; not in the previous commit)
- ``"M"`` — modified (file exists in the previous commit)
- ``"D"`` — deleted (file will be removed from the next commit)
"""

from __future__ import annotations

import datetime
import json
import pathlib
import tempfile
from typing import Literal, TypedDict


class StagedEntry(TypedDict):
    """One file's staging record."""

    object_id: str
    mode: Literal["A", "M", "D"]
    staged_at: str  # ISO-8601


class StageIndex(TypedDict):
    """Full contents of ``.muse/code/stage.json``."""

    version: int
    entries: dict[str, StagedEntry]


_STAGE_VERSION = 1


def stage_path(root: pathlib.Path) -> pathlib.Path:
    """Return the absolute path to ``.muse/code/stage.json``."""
    return root / ".muse" / "code" / "stage.json"


def read_stage(root: pathlib.Path) -> dict[str, StagedEntry]:
    """Read the stage index, returning an empty dict if none exists."""
    path = stage_path(root)
    if not path.exists():
        return {}
    try:
        data: StageIndex = json.loads(path.read_text(encoding="utf-8"))
        return data.get("entries", {})
    except (OSError, json.JSONDecodeError, KeyError):
        return {}


def write_stage(root: pathlib.Path, entries: dict[str, StagedEntry]) -> None:
    """Persist *entries* to ``.muse/code/stage.json``.

    Creates the ``.muse/code/`` directory if it does not exist.  Writing
    an empty dict clears the stage file (equivalent to calling
    :func:`clear_stage`).

    Writes are atomic (temp file + rename) so a process crash mid-write
    never leaves a corrupt stage file.
    """
    path = stage_path(root)
    if not entries:
        if path.exists():
            path.unlink()
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    index: StageIndex = {"version": _STAGE_VERSION, "entries": entries}
    payload = json.dumps(index, indent=2)
    fd, tmp_str = tempfile.mkstemp(dir=path.parent, prefix=".stage-tmp-", suffix=".json")
    try:
        with open(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
        pathlib.Path(tmp_str).replace(path)
    except Exception:
        pathlib.Path(tmp_str).unlink(missing_ok=True)
        raise


def clear_stage(root: pathlib.Path) -> None:
    """Remove the stage index, resetting to full-snapshot mode."""
    path = stage_path(root)
    if path.exists():
        path.unlink()


def make_entry(
    object_id: str,
    mode: Literal["A", "M", "D"],
) -> StagedEntry:
    """Build a :class:`StagedEntry` with the current UTC timestamp."""
    return StagedEntry(
        object_id=object_id,
        mode=mode,
        staged_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
    )

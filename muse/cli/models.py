"""Muse CLI data models — re-exports from muse.core.store.

This module provides backward-compatible names for commands that import
``MuseCliCommit``, ``MuseCliSnapshot``, etc. In the new architecture these
are plain dataclasses backed by JSON files, not SQLAlchemy ORM models.
"""

from muse.core.store import (
    CommitRecord as MuseCliCommit,
    SnapshotRecord as MuseCliSnapshot,
    TagRecord as MuseCliTag,
)

__all__ = ["MuseCliCommit", "MuseCliSnapshot", "MuseCliTag"]

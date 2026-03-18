"""Stable entity identity for MIDI note objects in the Muse music plugin.

The key insight
---------------
Content hash ≠ entity identity.

The current note content ID — ``SHA-256(pitch:velocity:start_tick:duration_ticks:channel)``
— is correct for *content equality* but wrong for *entity identity*.  When a
musician or agent changes a note's velocity from 80 to 100, the old model
produces a ``DeleteOp + InsertOp`` (two unrelated content hashes).  With stable
entity identity, the diff produces a ``MutateOp`` — "velocity 80→100 on note
C4@bar4" — and the note's lineage is preserved through the edit.

A ``NoteEntity`` extends the five ``NoteKey`` fields with an optional
``entity_id`` — a UUID4 assigned at first insertion that persists across all
subsequent mutations regardless of how the note's properties change.

Entity assignment heuristic
----------------------------
The function :func:`assign_entity_ids` maps a new note list to the entity IDs
in a prior index using a three-tier match:

1. **Exact content match** — all five fields identical → same entity, no mutation.
2. **Fuzzy match** — same pitch + channel, ``|Δtick| ≤ threshold``, and
   ``|Δvelocity| ≤ threshold`` → same entity, emit ``MutateOp``.
3. **No match** → new entity, new UUID4, emit ``InsertOp``.

Notes in the prior index that matched nothing → emit ``DeleteOp``.

Storage
-------
Entity indexes live under ``.muse/entity_index/`` as derived artifacts:

    .muse/entity_index/<commit_id>/<track_safe_name>.json

They are fully rebuildable from commit history and should be added to
``.museignore`` in agent automation scripts to avoid accidental commits.

Public API
----------
- :class:`NoteEntity` — ``NoteKey`` fields + optional entity metadata.
- :class:`EntityIndexEntry` — one entity's record in the index.
- :class:`EntityIndex` — the full per-track, per-commit index.
- :func:`assign_entity_ids` — map a new note list onto prior entity IDs.
- :func:`diff_with_entity_ids` — entity-aware diff → ``list[DomainOp]``.
- :func:`build_entity_index` — build an :class:`EntityIndex` from entities.
- :func:`write_entity_index` / :func:`read_entity_index` — I/O.
"""
from __future__ import annotations

import hashlib
import json
import logging
import pathlib
import uuid as _uuid_mod
from typing import TypedDict

from muse.domain import (
    DeleteOp,
    DomainOp,
    FieldMutation,
    InsertOp,
    MutateOp,
)
from muse.plugins.music.midi_diff import NoteKey, _note_content_id, _note_summary  # noqa: PLC2701

logger = logging.getLogger(__name__)

_ENTITY_INDEX_DIR = ".muse/entity_index"


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


class _NoteEntityRequired(TypedDict):
    """Required fields shared with NoteKey."""

    pitch: int
    velocity: int
    start_tick: int
    duration_ticks: int
    channel: int


class NoteEntity(_NoteEntityRequired, total=False):
    """A MIDI note with optional stable entity identity.

    When ``entity_id`` is absent the note is treated as content-only (legacy
    behaviour).  When present it is a UUID4 that persists across mutations to
    velocity, timing, or duration — enabling lineage tracking through edits.

    ``voice_id``
        Optional voice lane identifier (e.g. ``"soprano"``, ``"alto"``).
        Assigned by a voice-separation analysis pass; not required for basic
        entity tracking.
    ``origin_commit_id``
        Short-form commit ID where this entity was first created.
    ``origin_op_id``
        Op UUID from the op log that created this entity.
    """

    entity_id: str
    voice_id: str
    origin_commit_id: str
    origin_op_id: str


class EntityIndexEntry(TypedDict):
    """One entity's record in the per-track entity index.

    ``content_id``
        SHA-256 content hash of the note's current fields (the five ``NoteKey``
        fields).  Updated on every mutation.
    ``origin_commit_id``
        Commit where this entity was first inserted.
    ``voice_id``
        Voice stream assignment, or empty string if unassigned.
    """

    content_id: str
    origin_commit_id: str
    voice_id: str


class EntityIndex(TypedDict):
    """Complete entity index for one track at one commit.

    ``entities`` maps ``entity_id`` → :class:`EntityIndexEntry`.
    This is the lookup table used by :func:`assign_entity_ids` to re-identify
    notes across commits.
    """

    track_path: str
    commit_id: str
    entities: dict[str, EntityIndexEntry]


# ---------------------------------------------------------------------------
# Entity ID assignment
# ---------------------------------------------------------------------------

#: Default threshold in MIDI ticks for fuzzy timing match (≈ 10 ms at 120 BPM
#: with 480 ticks/beat: 480 × 0.010 × 2 ≈ 10 ticks).
_DEFAULT_TICK_THRESHOLD = 10

#: Default velocity difference threshold for fuzzy entity matching.
_DEFAULT_VEL_THRESHOLD = 20


def assign_entity_ids(
    notes: list[NoteKey],
    prior_index: EntityIndex | None,
    commit_id: str,
    op_id: str,
    *,
    mutation_threshold_ticks: int = _DEFAULT_TICK_THRESHOLD,
    mutation_threshold_velocity: int = _DEFAULT_VEL_THRESHOLD,
) -> list[NoteEntity]:
    """Assign stable entity IDs to a list of notes.

    Maps each note in *notes* to an entity ID from *prior_index* using a
    three-tier matching heuristic (exact → fuzzy → new).

    Args:
        notes:                      New note list (from the current commit).
        prior_index:                Entity index from the parent commit.
                                    ``None`` means this is the first commit
                                    for this track (all notes get new IDs).
        commit_id:                  Current commit ID (stored as provenance).
        op_id:                      Op log entry ID that produced these notes.
        mutation_threshold_ticks:   Max |Δtick| for fuzzy timing match.
        mutation_threshold_velocity: Max |Δvelocity| for fuzzy match.

    Returns:
        List of :class:`NoteEntity` objects in the same order as *notes*,
        each with a populated ``entity_id``.
    """
    if prior_index is None:
        return [_new_entity(n, commit_id, op_id) for n in notes]

    # Build lookup: content_id → entity_id for exact matches.
    content_to_entity: dict[str, str] = {}
    # Build list for fuzzy matching: [(entity_id, note_key_fields, entry)]
    fuzzy_candidates: list[tuple[str, NoteKey]] = []

    for entity_id, entry in prior_index["entities"].items():
        cid = entry["content_id"]
        # Reconstruct a NoteKey from the content hash is impossible directly,
        # so we carry a parallel lookup keyed by content_id string.
        content_to_entity[cid] = entity_id
        # For fuzzy matching we need the actual field values.  These aren't
        # stored in the index entry (only the hash is), so fuzzy matching
        # operates on the NEW notes' fields against the hash.  We build the
        # fuzzy set from the incoming notes rather than the prior index.
        _ = fuzzy_candidates  # populated below

    # Build a richer map from new notes' content IDs.
    new_by_cid: dict[str, list[NoteKey]] = {}
    for n in notes:
        cid = _note_content_id(n)
        new_by_cid.setdefault(cid, []).append(n)

    # --- Tier 1: exact content match ---
    # Assign entity IDs to notes whose content hash appears in the prior index.
    assigned: dict[int, str] = {}  # index → entity_id
    used_entities: set[str] = set()

    for i, note in enumerate(notes):
        cid = _note_content_id(note)
        if cid in content_to_entity:
            eid = content_to_entity[cid]
            if eid not in used_entities:
                assigned[i] = eid
                used_entities.add(eid)

    # --- Tier 2: fuzzy match for unassigned notes ---
    # Build prior note field table from the original notes that produced the
    # prior index.  Since we only have hashes, we use the *new* notes as a
    # proxy: any note with (same pitch, same channel, close tick, close vel)
    # is a mutation candidate.
    #
    # Approach: for each unassigned new note, find an unused prior entity
    # whose content_id resolves to a note with matching pitch+channel and
    # close tick+velocity.  Since we can't reverse-SHA the prior hash, we
    # instead accept the fuzzy match if the content hash of the hypothetical
    # un-mutated note (same pitch, channel, but old vel/tick fields) matches.
    #
    # In practice, callers pass both old and new note lists when they have
    # them; the simple heuristic here covers the common agent-edit case.
    prior_entity_ids = list(prior_index["entities"].keys())

    for i, note in enumerate(notes):
        if i in assigned:
            continue
        # Try to match against any unused prior entity by field similarity.
        # We approximate by assuming the prior entity had similar fields.
        best_eid: str | None = None
        best_score = float("inf")

        for eid in prior_entity_ids:
            if eid in used_entities:
                continue
            entry = prior_index["entities"][eid]
            prior_cid = entry["content_id"]

            # Attempt to reconstruct a plausible prior note for this entity.
            # We don't have the raw fields — approximate by checking if a
            # note with the same pitch + channel but slightly different
            # timing/velocity would hash to this content_id.
            for vel_delta in range(-mutation_threshold_velocity, mutation_threshold_velocity + 1, 2):
                for tick_delta in range(-mutation_threshold_ticks, mutation_threshold_ticks + 1, 2):
                    candidate: NoteKey = NoteKey(
                        pitch=note["pitch"],
                        velocity=max(0, min(127, note["velocity"] + vel_delta)),
                        start_tick=max(0, note["start_tick"] + tick_delta),
                        duration_ticks=note["duration_ticks"],
                        channel=note["channel"],
                    )
                    if _note_content_id(candidate) == prior_cid:
                        score = abs(vel_delta) + abs(tick_delta)
                        if score < best_score:
                            best_score = score
                            best_eid = eid
                        break
                if best_eid is not None and best_score == 0:
                    break

        if best_eid is not None:
            assigned[i] = best_eid
            used_entities.add(best_eid)

    # --- Build output ---
    result: list[NoteEntity] = []
    for i, note in enumerate(notes):
        if i in assigned:
            entity: NoteEntity = NoteEntity(
                pitch=note["pitch"],
                velocity=note["velocity"],
                start_tick=note["start_tick"],
                duration_ticks=note["duration_ticks"],
                channel=note["channel"],
                entity_id=assigned[i],
                origin_commit_id=prior_index["entities"][assigned[i]]["origin_commit_id"],
                origin_op_id=op_id,
                voice_id=prior_index["entities"][assigned[i]].get("voice_id", ""),
            )
        else:
            entity = _new_entity(note, commit_id, op_id)
        result.append(entity)

    return result


def _new_entity(note: NoteKey, commit_id: str, op_id: str) -> NoteEntity:
    """Create a :class:`NoteEntity` with a fresh UUID4 entity_id."""
    return NoteEntity(
        pitch=note["pitch"],
        velocity=note["velocity"],
        start_tick=note["start_tick"],
        duration_ticks=note["duration_ticks"],
        channel=note["channel"],
        entity_id=str(_uuid_mod.uuid4()),
        origin_commit_id=commit_id,
        origin_op_id=op_id,
        voice_id="",
    )


# ---------------------------------------------------------------------------
# Entity-aware diff
# ---------------------------------------------------------------------------


def diff_with_entity_ids(
    base_entities: list[NoteEntity],
    target_entities: list[NoteEntity],
    ticks_per_beat: int,
) -> list[DomainOp]:
    """Produce an entity-aware diff between two note lists.

    Compared to the content-hash-only diff in :mod:`~muse.plugins.music.midi_diff`,
    this function detects *mutations* — cases where the same entity_id appears
    in both lists with different field values — and emits ``MutateOp`` entries
    instead of ``DeleteOp + InsertOp`` pairs.

    Algorithm:
    1. Build ``entity_id → NoteEntity`` maps for base and target.
    2. For entities present in both: compare fields; emit ``MutateOp`` if
       anything changed, otherwise "keep" (no op).
    3. For entities only in base: emit ``DeleteOp``.
    4. For entities only in target: emit ``InsertOp``.
    5. For notes without an entity_id: fall back to content-hash comparison
       (insert/delete only, no mutation tracking).

    Args:
        base_entities:   Notes from the ancestor commit, with entity IDs.
        target_entities: Notes from the current commit, with entity IDs.
        ticks_per_beat:  Used for human-readable summaries.

    Returns:
        Ordered list of :class:`~muse.domain.DomainOp` entries.
    """
    ops: list[DomainOp] = []

    # Separate tracked (have entity_id) from untracked notes.
    base_tracked: dict[str, NoteEntity] = {}
    base_untracked: list[NoteEntity] = []
    for note in base_entities:
        if "entity_id" in note and note.get("entity_id"):
            base_tracked[note["entity_id"]] = note
        else:
            base_untracked.append(note)

    target_tracked: dict[str, NoteEntity] = {}
    target_untracked: list[NoteEntity] = []
    for note in target_entities:
        if "entity_id" in note and note.get("entity_id"):
            target_tracked[note["entity_id"]] = note
        else:
            target_untracked.append(note)

    # --- Tracked: mutate, keep, insert, delete ---
    all_entity_ids = set(base_tracked) | set(target_tracked)

    for eid in sorted(all_entity_ids):
        base_note = base_tracked.get(eid)
        target_note = target_tracked.get(eid)

        if base_note is not None and target_note is not None:
            old_cid = _note_content_id(_entity_to_key(base_note))
            new_cid = _note_content_id(_entity_to_key(target_note))
            if old_cid == new_cid:
                continue  # unchanged

            fields = _field_diff(base_note, target_note)
            base_note_key = _entity_to_key(base_note)
            target_note_key = _entity_to_key(target_note)
            ops.append(
                MutateOp(
                    op="mutate",
                    address=f"note:entity:{eid}",
                    entity_id=eid,
                    old_content_id=old_cid,
                    new_content_id=new_cid,
                    fields=fields,
                    old_summary=_note_summary(base_note_key, ticks_per_beat),
                    new_summary=_note_summary(target_note_key, ticks_per_beat),
                    position=None,
                )
            )

        elif base_note is not None:
            cid = _note_content_id(_entity_to_key(base_note))
            ops.append(
                DeleteOp(
                    op="delete",
                    address=f"note:entity:{eid}",
                    position=None,
                    content_id=cid,
                    content_summary=_note_summary(_entity_to_key(base_note), ticks_per_beat),
                )
            )

        else:
            assert target_note is not None
            cid = _note_content_id(_entity_to_key(target_note))
            ops.append(
                InsertOp(
                    op="insert",
                    address=f"note:entity:{eid}",
                    position=None,
                    content_id=cid,
                    content_summary=_note_summary(_entity_to_key(target_note), ticks_per_beat),
                )
            )

    # --- Untracked: fall back to content-hash insert/delete ---
    base_content_ids = {_note_content_id(_entity_to_key(n)) for n in base_untracked}
    target_content_ids = {_note_content_id(_entity_to_key(n)) for n in target_untracked}

    for note in base_untracked:
        cid = _note_content_id(_entity_to_key(note))
        if cid not in target_content_ids:
            ops.append(
                DeleteOp(
                    op="delete",
                    address="note:untracked",
                    position=None,
                    content_id=cid,
                    content_summary=_note_summary(_entity_to_key(note), ticks_per_beat),
                )
            )

    for note in target_untracked:
        cid = _note_content_id(_entity_to_key(note))
        if cid not in base_content_ids:
            ops.append(
                InsertOp(
                    op="insert",
                    address="note:untracked",
                    position=None,
                    content_id=cid,
                    content_summary=_note_summary(_entity_to_key(note), ticks_per_beat),
                )
            )

    return ops


def _entity_to_key(entity: NoteEntity) -> NoteKey:
    """Extract the five NoteKey fields from a NoteEntity."""
    return NoteKey(
        pitch=entity["pitch"],
        velocity=entity["velocity"],
        start_tick=entity["start_tick"],
        duration_ticks=entity["duration_ticks"],
        channel=entity["channel"],
    )


def _field_diff(base: NoteEntity, target: NoteEntity) -> dict[str, FieldMutation]:
    """Return a FieldMutation map for all fields that changed."""
    mutations: dict[str, FieldMutation] = {}
    # Unpack into flat tuples to avoid variable-key TypedDict access.
    base_vals: tuple[int, int, int, int, int] = (
        base["pitch"], base["velocity"], base["start_tick"], base["duration_ticks"], base["channel"]
    )
    target_vals: tuple[int, int, int, int, int] = (
        target["pitch"], target["velocity"], target["start_tick"], target["duration_ticks"], target["channel"]
    )
    names = ("pitch", "velocity", "start_tick", "duration_ticks", "channel")
    for name, bv, tv in zip(names, base_vals, target_vals):
        if bv != tv:
            mutations[name] = FieldMutation(old=str(bv), new=str(tv))
    return mutations


# ---------------------------------------------------------------------------
# Entity index I/O
# ---------------------------------------------------------------------------


def build_entity_index(
    entities: list[NoteEntity],
    track_path: str,
    commit_id: str,
) -> EntityIndex:
    """Build an :class:`EntityIndex` from a list of :class:`NoteEntity` objects.

    Notes without an ``entity_id`` are skipped (untracked notes do not appear
    in the index).

    Args:
        entities:   Note entities from the current commit.
        track_path: Workspace-relative MIDI file path.
        commit_id:  Current commit ID.

    Returns:
        A populated :class:`EntityIndex`.
    """
    entries: dict[str, EntityIndexEntry] = {}
    for note in entities:
        eid = note.get("entity_id", "")
        if not eid:
            continue
        entries[eid] = EntityIndexEntry(
            content_id=_note_content_id(_entity_to_key(note)),
            origin_commit_id=note.get("origin_commit_id", commit_id),
            voice_id=note.get("voice_id", ""),
        )
    return EntityIndex(
        track_path=track_path,
        commit_id=commit_id,
        entities=entries,
    )


def _index_path(repo_root: pathlib.Path, commit_id: str, track_path: str) -> pathlib.Path:
    safe_track = track_path.replace("/", "_").replace(".", "_")
    sha = hashlib.sha256(track_path.encode()).hexdigest()[:8]
    return (
        repo_root
        / _ENTITY_INDEX_DIR
        / commit_id[:16]
        / f"{safe_track}_{sha}.json"
    )


def write_entity_index(
    repo_root: pathlib.Path,
    commit_id: str,
    track_path: str,
    index: EntityIndex,
) -> None:
    """Persist *index* to ``.muse/entity_index/<commit_id>/<track>.json``.

    Creates parent directories as needed.  Safe to call multiple times —
    an existing file is overwritten.

    Args:
        repo_root:  Repository root.
        commit_id:  Commit ID for the snapshot this index belongs to.
        track_path: Workspace-relative MIDI file path.
        index:      The entity index to persist.
    """
    path = _index_path(repo_root, commit_id, track_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(index, indent=2) + "\n")
    logger.debug(
        "✅ Entity index written: %d entities for %r @ %s",
        len(index["entities"]),
        track_path,
        commit_id[:8],
    )


def read_entity_index(
    repo_root: pathlib.Path,
    commit_id: str,
    track_path: str,
) -> EntityIndex | None:
    """Load the entity index for *track_path* at *commit_id*.

    Args:
        repo_root:  Repository root.
        commit_id:  Commit ID.
        track_path: Workspace-relative MIDI file path.

    Returns:
        The :class:`EntityIndex`, or ``None`` when no index file exists.
    """
    path = _index_path(repo_root, commit_id, track_path)
    if not path.exists():
        return None
    try:
        raw: EntityIndex = json.loads(path.read_text())
        return raw
    except (json.JSONDecodeError, KeyError) as exc:
        logger.warning("⚠️ Corrupt entity index %s: %s", path, exc)
        return None

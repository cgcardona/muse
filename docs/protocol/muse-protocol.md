# MuseDomainPlugin Protocol — Language-Agnostic Specification

> **Status:** Canonical · **Version:** v1.0
> **Audience:** Anyone implementing a Muse domain plugin in any language.

---

## 0. Purpose

This document specifies the five-method contract a domain plugin must satisfy to
integrate with the Muse VCS engine. It is intentionally language-agnostic.

Muse provides the DAG, object store, branching, lineage, merge state machine, log,
and CLI. A plugin provides domain knowledge. This document defines the boundary
between them.

---

## 1. Design Principles

1. **Plugins are pure transformations.** A plugin method takes state in, returns state
   out. Side effects (writing to disk, calling APIs) belong to the CLI layer, not
   the plugin.
2. **All state is JSON-serializable.** Snapshots must be serializable to a
   content-addressable string. No opaque blobs inside snapshot values.
3. **Content-addressed identity.** The same state must always produce the same
   snapshot. Snapshots are compared by their SHA-256 digest — not by object identity.
4. **Idempotent writes.** Writing an object or snapshot that already exists is a
   no-op. The store never overwrites existing content.
5. **Conflicts are data, not exceptions.** A conflicted merge returns a `MergeResult`
   with a non-empty `conflicts` list. It does not raise.
6. **Drift is always relative.** `drift()` compares committed state against live
   state. It never modifies either.

---

## 2. Type Definitions

All types use Python as the reference notation. Implementations in other languages
should map to equivalent constructs.

```python
# A workspace-relative path mapped to its SHA-256 content digest.
# Plugins are free to add top-level keys alongside "files" and "domain".
StateSnapshot = dict  # must contain "files": dict[str, str] and "domain": str

# The "live" input to snapshot() and drift().
# Either a filesystem path to the working directory,
# or an existing StateSnapshot (used for in-memory operations).
LiveState = Path | StateSnapshot

# Output of diff(): three sorted lists of workspace-relative paths.
StateDelta = dict  # must contain "added", "removed", "modified": list[str] and "domain": str

# Output of merge(): the reconciled snapshot + conflict + strategy metadata.
# "conflicts"          — workspace-relative paths that could not be auto-resolved.
#                        Empty list means the merge was clean.
# "applied_strategies" — path → strategy string applied by .museattributes
#                        (e.g. {"drums/kick.mid": "ours"}).  Empty if no rules fired.
# "dimension_reports"  — path → {dimension: winner} for files that went through
#                        dimension-level merge (e.g. {"keys.mid": {"notes": "left"}}).
MergeResult = dataclass(
    merged: StateSnapshot,
    conflicts: list[str],
    applied_strategies: dict[str, str],
    dimension_reports: dict[str, dict[str, str]],
)

# Output of drift(): summary of how live state diverges from committed state.
DriftReport = dataclass(has_drift: bool, summary: str, delta: StateDelta)
```

---

## 3. The Five Methods

### 3.1 `snapshot(live_state: LiveState) → StateSnapshot`

Capture the current live state as a serializable, content-addressable snapshot.

**Contract:**
- The return value MUST be JSON-serializable.
- The return value MUST contain a `"files"` key mapping workspace-relative path
  strings to their SHA-256 hex digests.
- The return value MUST contain a `"domain"` key matching the plugin's domain name.
- Given identical input, the output MUST be identical (deterministic).
- If `live_state` is already a `StateSnapshot` dict, return it unchanged.

**Called by:** `muse commit`, `muse stash`

---

### 3.2 `diff(base: StateSnapshot, target: StateSnapshot) → StateDelta`

Compute the minimal delta between two snapshots.

**Contract:**
- Return MUST contain `"added"`: sorted list of paths present in `target` but not `base`.
- Return MUST contain `"removed"`: sorted list of paths present in `base` but not `target`.
- Return MUST contain `"modified"`: sorted list of paths present in both with different digests.
- Return MUST contain `"domain"` matching the plugin's domain name.
- All three lists MUST be sorted.
- A path that appears in `added` MUST NOT appear in `removed` or `modified`.

**Called by:** `muse diff`, `muse checkout`

---

### 3.3 `merge(base, left, right: StateSnapshot, *, repo_root: Path | None = None) → MergeResult`

Three-way merge two divergent state lines against a common ancestor.

**Contract:**
- `base` is the common ancestor (merge base).
- `left` is the current branch's snapshot (ours).
- `right` is the incoming branch's snapshot (theirs).
- `repo_root`, when provided, is the filesystem root of the repository.
  Implementations SHOULD use it to load `.museattributes` and apply
  file-level or dimension-level merge strategies before falling back to
  conflict reporting.
- `result.merged` MUST be a valid `StateSnapshot`.
- `result.conflicts` MUST be a list of workspace-relative path strings.
  - An empty list means the merge was clean.
  - Paths in `result.conflicts` MUST also appear in `result.merged` (placeholder state).
- `result.applied_strategies` maps paths where a `.museattributes` rule overrode
  the default conflict behaviour to the strategy string that was used.
  Plugins SHOULD populate this for observability; it MAY be empty.
- `result.dimension_reports` maps paths that received dimension-level merge to
  a `{dimension: winner}` dict for each resolved dimension.
  Plugins that do not support dimension merge MAY always return `{}`.
- **Consensus deletion** (both sides deleted the same path) is NOT a conflict.
- This method MUST NOT raise on conflict — it returns the conflict list instead.

**Called by:** `muse merge`, `muse cherry-pick`

---

### 3.4 `drift(committed: StateSnapshot, live: LiveState) → DriftReport`

Detect how far the live state has diverged from the last committed snapshot.

**Contract:**
- `result.has_drift` is `True` if and only if `delta` is non-empty.
- `result.summary` is a human-readable string (e.g. `"2 added, 1 modified"`
  or `"working tree clean"`).
- `result.delta` is a valid `StateDelta`.
- This method MUST NOT modify any state.

**Called by:** `muse status`

---

### 3.5 `apply(delta: StateDelta, live_state: LiveState) → LiveState`

Apply a delta to produce a new live state. Serves as the domain-level
post-checkout hook.

**Contract:**
- When `live_state` is a filesystem `Path`: the caller has already applied the
  delta physically (removed deleted files, restored added/modified from the object
  store). The plugin SHOULD rescan the directory and return the authoritative new
  state as a `StateSnapshot`.
- When `live_state` is a `StateSnapshot` dict: apply removals to the in-memory dict.
  Added/modified paths SHOULD be noted as limitations — the delta does not carry
  content hashes, so the caller must supply them through another path.
- The return value MUST be a valid `LiveState`.

**Called by:** `muse checkout`

---

## 4. Snapshot Format (Normative)

The minimum required shape for a `StateSnapshot`:

```json
{
  "files": {
    "path/to/file-a": "sha256-hex-64-chars",
    "path/to/file-b": "sha256-hex-64-chars"
  },
  "domain": "my_domain_name"
}
```

Plugins MAY add additional top-level keys for domain-specific metadata:

```json
{
  "files": { ... },
  "domain": "music",
  "tempo_bpm": 120,
  "key": "Am"
}
```

Additional keys MUST be JSON-serializable. The core engine ignores them; they
are available to domain-specific CLI commands via `plugin.snapshot()`.

---

## 5. Naming Conventions

| Scope | Convention |
|---|---|
| Wire format (JSON) | `camelCase` |
| Python internals | `snake_case` |
| Plugin domain name in `repo.json` | `snake_case` |
| Workspace-relative paths in snapshots | POSIX forward-slash separators |

---

## 6. Implementing a Plugin

Minimum viable implementation in Python:

```python
from muse.domain import (
    DeltaManifest, DriftReport, LiveState, MergeResult,
    MuseDomainPlugin, SnapshotManifest, StateDelta, StateSnapshot,
)

class MyDomainPlugin:
    def snapshot(self, live_state: LiveState) -> StateSnapshot:
        if isinstance(live_state, pathlib.Path):
            files = {
                f.relative_to(live_state).as_posix(): _hash(f)
                for f in sorted(live_state.rglob("*"))
                if f.is_file()
            }
            return SnapshotManifest(files=files, domain="my_domain")
        return live_state  # already a snapshot dict

    def diff(self, base: StateSnapshot, target: StateSnapshot) -> StateDelta:
        b, t = base["files"], target["files"]
        return DeltaManifest(
            domain="my_domain",
            added=sorted(set(t) - set(b)),
            removed=sorted(set(b) - set(t)),
            modified=sorted(p for p in set(b) & set(t) if b[p] != t[p]),
        )

    def merge(
        self,
        base: StateSnapshot,
        left: StateSnapshot,
        right: StateSnapshot,
        *,
        repo_root: pathlib.Path | None = None,
    ) -> MergeResult:
        # ... domain-specific reconciliation ...
        # Load .museattributes if repo_root is provided and apply strategies.

    def drift(self, committed, live) -> DriftReport:
        live_snap = self.snapshot(live)
        delta = self.diff(committed, live_snap)
        has_drift = any([delta["added"], delta["removed"], delta["modified"]])
        return DriftReport(has_drift=has_drift, summary="...", delta=delta)

    def apply(self, delta, live_state) -> LiveState:
        if isinstance(live_state, pathlib.Path):
            return self.snapshot(live_state)
        files = dict(live_state["files"])
        for p in delta["removed"]:
            files.pop(p, None)
        return SnapshotManifest(files=files, domain="my_domain")
```

See `muse/plugins/music/plugin.py` for the complete reference implementation.

---

## 7. Invariants the Core Engine Relies On

The core engine assumes:

1. `snapshot(snapshot_dict)` returns the dict unchanged.
2. `diff(s, s)` returns empty `added`, `removed`, `modified` for identical snapshots.
3. `merge(base, s, s)` returns `s` with an empty `conflicts` list.
4. `drift(s, path_to_workdir_matching_s)` returns `has_drift=False`.
5. Object IDs in `StateSnapshot["files"]` are valid SHA-256 hex strings (64 chars).

Violating these invariants will cause incorrect behavior in `checkout`, `status`,
and merge state detection.

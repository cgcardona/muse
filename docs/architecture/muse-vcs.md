# Muse VCS — Architecture Reference

> **Status:** Canonical — Muse v0.1.1
> **See also:** [E2E Walkthrough](muse-e2e-demo.md) · [Plugin Protocol](../protocol/muse-protocol.md) · [Domain Concepts](../protocol/muse-domain-concepts.md) · [Type Contracts](../reference/type-contracts.md)

---

## What Muse Is

Muse is a **domain-agnostic version control system for multidimensional state**. It provides
a complete DAG engine — content-addressed objects, commits, branches, three-way merge, drift
detection, time-travel checkout, and a full log graph — with one deliberate gap: it does not
know what "state" is.

That gap is the plugin slot. A `MuseDomainPlugin` tells Muse how to:

- **snapshot** the current live state into a serializable, content-addressable dict
- **diff** two snapshots into a minimal delta
- **merge** two divergent snapshots against a common ancestor
- **drift** — detect how much live state has diverged from the last commit
- **apply** a delta to produce a new live state (checkout execution)

Everything else — the DAG, object store, branching, lineage walking, log, merge state
machine — is provided by the core engine and shared across all domains.

---

## The Seven Invariants

```
State      = a serializable, content-addressed snapshot of any multidimensional space
Commit     = a named delta from a parent state, recorded in a DAG
Branch     = a divergent line of intent forked from a shared ancestor
Merge      = three-way reconciliation of two divergent state lines against a common base
Drift      = the gap between committed state and live state
Checkout   = deterministic reconstruction of any historical state from the DAG
Lineage    = the causal chain from root to any commit
```

None of those definitions contain the word "music."

---

## Repository Structure on Disk

Every Muse repository is a `.muse/` directory containing:

```
.muse/
  repo.json          — repository ID, domain name, creation metadata
  HEAD               — ref pointer, e.g. refs/heads/main
  config.toml        — optional local config (auth token, remotes)
  refs/
    heads/
      main           — SHA-256 commit ID of branch HEAD
      feature/...    — additional branch HEADs
  objects/
    <sha2>/          — shard directory (first 2 hex chars)
      <sha62>        — raw content-addressed blob (62 remaining hex chars)
  commits/
    <commit_id>.json — CommitRecord
  snapshots/
    <snapshot_id>.json — SnapshotRecord (manifest: {path → object_id})
  tags/
    <tag_id>.json    — TagRecord
  MERGE_STATE.json   — present only during an active merge conflict
  sessions/          — optional: named work sessions (muse session)
muse-work/           — the working tree (domain files live here)
.museattributes      — optional: per-path merge strategy overrides
```

The object store mirrors Git's loose-object layout: sharding by the first two hex
characters of each SHA-256 digest prevents filesystem degradation as the repository grows.

---

## Core Engine Modules

```
muse/
  domain.py              — MuseDomainPlugin Protocol + all shared type definitions
  core/
    store.py             — file-based commit / snapshot / tag store (no external DB)
    repo.py              — repository detection (MUSE_REPO_ROOT or directory walk)
    snapshot.py          — content-addressed snapshot and commit ID derivation
    object_store.py      — SHA-256 blob storage under .muse/objects/
    merge_engine.py      — three-way merge state machine + conflict resolution
    errors.py            — ExitCode enum and error primitives
  plugins/
    registry.py          — maps domain names → MuseDomainPlugin instances
    music/
      plugin.py          — MusicPlugin: reference MuseDomainPlugin implementation
  cli/
    app.py               — Typer application root, command registration
    commands/            — one file per subcommand
```

---

## Deterministic ID Derivation

All IDs are SHA-256 digests, making the DAG fully content-addressed:

```
object_id   = sha256(raw_file_bytes)
snapshot_id = sha256(sorted("path:object_id\n" pairs))
commit_id   = sha256(sorted_parent_ids | snapshot_id | message | timestamp_iso)
```

The same snapshot always produces the same ID. Two commits that point to identical
state will share a `snapshot_id`. Objects are never overwritten — write is always
idempotent (`False` return means "already existed, skipped").

---

## Plugin Architecture

### The Protocol

```python
class MuseDomainPlugin(Protocol):
    def snapshot(self, live_state: LiveState) -> StateSnapshot:
        """Capture current live state as a serializable, hashable snapshot."""

    def diff(self, base: StateSnapshot, target: StateSnapshot) -> StateDelta:
        """Compute the minimal delta between two snapshots."""

    def merge(
        self,
        base: StateSnapshot,
        left: StateSnapshot,
        right: StateSnapshot,
        *,
        repo_root: pathlib.Path | None = None,
    ) -> MergeResult:
        """Three-way merge. Loads .museattributes when repo_root is given.
        Returns merged snapshot, conflict paths, applied_strategies, and
        dimension_reports."""

    def drift(
        self,
        committed: StateSnapshot,
        live: LiveState,
    ) -> DriftReport:
        """Compare committed state against current live state."""

    def apply(self, delta: StateDelta, live_state: LiveState) -> LiveState:
        """Apply a delta to produce a new live state (checkout execution)."""
```

### How CLI Commands Use the Plugin

Every CLI command that touches domain state goes through `resolve_plugin(root)`:

| Command | Plugin method(s) called |
|---|---|
| `muse commit` | `snapshot()` |
| `muse status` | `drift()` |
| `muse diff` | `diff()` |
| `muse merge` | `merge()` |
| `muse cherry-pick` | `merge()` |
| `muse stash` | `snapshot()` |
| `muse checkout` | `diff()` + `apply()` |

The plugin registry (`muse/plugins/registry.py`) reads `domain` from `.muse/repo.json`
and returns the appropriate `MuseDomainPlugin` instance. Unknown domains raise a
`ValueError` listing the registered alternatives.

### Registering a New Domain

```python
# muse/plugins/registry.py
from muse.plugins.my_domain.plugin import MyDomainPlugin

_REGISTRY: dict[str, MuseDomainPlugin] = {
    "music":     MusicPlugin(),
    "my_domain": MyDomainPlugin(),
}
```

Then initialize a repository for that domain:

```bash
muse init --domain my_domain
```

---

## Music Plugin — Reference Implementation

The music plugin (`muse/plugins/music/plugin.py`) implements `MuseDomainPlugin` for
MIDI state stored as files in `muse-work/`. It is the proof that the abstraction works.

| Method | Music domain behavior |
|---|---|
| `snapshot()` | Walk `muse-work/`, SHA-256 each file → `{"files": {path: hash}, "domain": "music"}` |
| `diff()` | Set difference on file paths + hash comparison → added / removed / modified lists |
| `merge()` | Three-way set reconciliation; consensus deletions are not conflicts |
| `drift()` | `snapshot(workdir)` then `diff(committed, live)` → `DriftReport` |
| `apply()` | With a Path: rescan workdir (files already updated). With a dict: apply removals. |

---

## Merge Algorithm

`muse merge <branch>` performs a three-way merge:

1. **Find merge base** — walk the commit DAG from both HEADs to find the LCA
2. **Construct snapshots** — load base, ours, and theirs `StateSnapshot` objects
3. **Call `plugin.merge(base, ours, theirs)`** — domain logic reconciles the state
4. **Handle result:**
   - Clean merge → restore working tree, create merge commit (two parents)
   - Conflicts → write `MERGE_STATE.json`, restore what can be auto-merged, report conflict paths
5. **`muse merge --continue`** — after manual resolution, commit with stored parents

`MERGE_STATE.json` records `base_commit`, `ours_commit`, `theirs_commit`, and
`conflict_paths` so the CLI can resume after the user resolves conflicts.

---

## Checkout Algorithm

`muse checkout <target>` uses incremental delta restoration:

1. Read current branch's `StateSnapshot` from the object store
2. Read target `StateSnapshot`
3. Call `plugin.diff(current, target)` → delta
4. **Remove** files in `delta["removed"]` from `muse-work/`
5. **Restore** files in `delta["added"] + delta["modified"]` from the object store
6. Call `plugin.apply(delta, workdir)` — domain-level post-checkout hook

Only files that actually changed are touched. Unchanged files are never re-copied,
making checkout fast even for large repositories.

---

## Commit Data Flow

```
muse commit -m "message"
  │
  ├─ plugin.snapshot(workdir)     → StateSnapshot {"files": {path: sha}, "domain": "..."}
  │
  ├─ compute_snapshot_id(manifest) → snapshot_id (sha256 of sorted path:sha pairs)
  │
  ├─ compute_commit_id(parents, snapshot_id, message, timestamp) → commit_id
  │
  ├─ write_object_from_path(root, sha, src)  ×N  (idempotent)
  │
  ├─ write_snapshot(root, SnapshotRecord)         (idempotent)
  │
  ├─ write_commit(root, CommitRecord)
  │
  └─ update refs/heads/<branch> → commit_id
```

Revert and cherry-pick reuse existing snapshot IDs directly — no re-scan needed
since the objects are already content-addressed in the store.

---

## CLI Command Map

### Core VCS (all domains)

| Command | Description |
|---|---|
| `muse init [--domain <name>]` | Initialize a repository |
| `muse commit -m <msg>` | Snapshot live state and record a commit |
| `muse status` | Show drift between HEAD and working tree |
| `muse diff [<base>] [<target>]` | Show delta between commits or vs. working tree |
| `muse log [--oneline] [--graph] [--stat]` | Display commit history |
| `muse show [<ref>] [--json] [--stat]` | Inspect a single commit |
| `muse branch [<name>] [-d <name>]` | Create or delete branches |
| `muse checkout <branch\|commit> [-b]` | Switch branches or restore historical state |
| `muse merge <branch>` | Three-way merge a branch into HEAD |
| `muse cherry-pick <commit>` | Apply a specific commit's delta on top of HEAD |
| `muse revert <commit>` | Create a new commit undoing a prior commit |
| `muse reset <commit> [--hard]` | Move branch pointer (hard: also restore working tree) |
| `muse stash` / `pop` / `list` / `drop` | Temporarily shelve uncommitted changes |
| `muse tag add <tag> [<ref>]` | Tag a commit |
| `muse tag list [<ref>]` | List tags |

### Music-Domain Extras (music plugin only)

| Command | Description |
|---|---|
| `muse commit --section <name> --track <name> --emotion <name>` | Commit with music metadata |
| `muse log --section <s> --track <t> --emotion <e>` | Filter log by music metadata |
| `muse groove-check` | Analyse rhythmic drift across history |
| `muse emotion-diff <a> <b>` | Compare emotion vectors between commits |

---

## Testing

```bash
# Run full test suite
python -m pytest

# Run with coverage report
python -m pytest --cov=muse --cov-report=term-missing

# Run type audit (zero violations enforced in CI)
python tools/typing_audit.py --dirs muse/ tests/ --max-any 0

# Run mypy
mypy muse/
```

Coverage target: ≥ 80% (currently 91%, excluding `config.py`, `midi_parser.py`).

CI runs pytest + mypy + typing_audit on every pull request to `main` and `dev`.

---

## Adding a Second Domain

To add a new domain (e.g. `genomics`):

1. Create `muse/plugins/genomics/plugin.py` implementing `MuseDomainPlugin`
2. Register it in `muse/plugins/registry.py`
3. Run `muse init --domain genomics` in any project directory
4. All existing CLI commands work immediately — no changes needed

The music plugin (`muse/plugins/music/plugin.py`) is the complete reference for what
each method should do. It is 326 lines including full docstrings.

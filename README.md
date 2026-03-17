# Muse

> **A domain-agnostic version control system for multidimensional state.**

Git works on text because text is one-dimensional — a sequence of lines. Diffs are additions and deletions to that sequence.

Muse works on *any* state space where a "change" is a delta across multiple axes simultaneously. Music is the first domain. It is not the definition.

---

## The Core Abstraction

Strip Muse down to its invariants and what remains is:

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

## Plugin Architecture

A domain plugin implements five interfaces. Muse provides the rest — the DAG engine, content-addressed object store, branching, lineage walking, topological log graph, and merge base finder.

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
    ) -> MergeResult:
        """Three-way merge. Return merged snapshot + conflict report."""

    def drift(
        self,
        committed: StateSnapshot,
        live: LiveState,
    ) -> DriftReport:
        """Compare committed state against current live state."""

    def apply(self, delta: StateDelta, live_state: LiveState) -> LiveState:
        """Apply a delta to produce a new live state (checkout execution)."""
```

The music plugin — the reference implementation — implements these five interfaces for MIDI state: notes, velocities, controller events, pitch bends, and aftertouch. Every other domain is a new plugin.

---

## Music — The Reference Implementation

Music is the domain that proved the abstraction. State is a snapshot of MIDI files on disk. Diff is file-level set difference plus content comparison. Merge is three-way reconciliation of file sets against a common ancestor. Drift compares the committed snapshot against the live working tree. Checkout incrementally applies the delta between snapshots using the plugin.

```bash
# Initialize a Muse repository (default domain: music)
muse init

# Commit the current working tree
muse commit -m "Add verse melody"

# Create and switch to a new branch
muse checkout -b feature/chorus

# View commit history as an ASCII graph
muse log --graph

# Show uncommitted changes vs HEAD
muse status

# Three-way merge a branch
muse merge feature/chorus

# Cherry-pick a specific commit
muse cherry-pick <commit-id>

# Revert a commit (creates a new commit undoing the change)
muse revert <commit-id>

# Show a commit's metadata and file changes
muse show [<ref>] [--json] [--stat]
```

Run `muse --help` for the full command list.

---

## Domain Instantiations

### Music *(reference implementation)*
MIDI state across notes, velocities, controller events, pitch bends, and aftertouch. Three-way merge reconciles divergent takes. Drift detection compares the committed snapshot against the live DAW. **Already ships with full DAG, branching, three-way merge, and E2E tests.**

### Scientific Simulation *(planned)*
A climate model is a multidimensional state space: temperature, pressure, humidity, ocean current, ice coverage at every grid point. Commit a named checkpoint. Branch to explore a parameter variation. Merge two teams' adjustments against a common baseline run. Drift detection flags when a running simulation has diverged from its last committed checkpoint.

### Genomics *(planned)*
A genome under CRISPR editing is a high-dimensional sequence state. Each editing session is a commit. Alternate intervention strategies are branches. When two research teams converge on the same baseline organism and apply different edits, merge reconciles those edit sets against the common ancestor genome. The Muse DAG becomes the provenance record of every edit.

### 3D Spatial Design *(planned)*
Architecture, urban planning, game world construction. Branch to explore "what if we moved the load-bearing wall." Merge the structural engineer's changes and the lighting consultant's changes against the architect's baseline. Drift detection surfaces the delta between the committed design and the as-built state.

### Spacetime *(theoretical)*
A spacetime plugin models state as a configuration of matter-energy distribution across a coordinate grid. A commit is a named configuration at a set of coordinates. A branch is a counterfactual — what would the state space look like if this mass had been positioned differently at T₀.

This is exactly what large-scale physics simulation does, without the version control semantics. Adding Muse semantics — content-addressed states, causal lineage, merge — makes simulation runs composable in a way they currently are not. Two simulations that share a common initialization can be merged or compared with the same rigor that two branches of a codebase can.

Whether this scales to actual spacetime is a question for physics. Whether it applies to spacetime *simulation* is just engineering.

---

## Agent Collaboration

Muse's most transformative application is **shared persistent memory for teams of collaborating agents**.

Without a shared state store, collaborating agents are stateless with respect to each other. Each agent knows what it has done; none knows what the others have committed, branched, or abandoned. There is no canonical record of what has happened.

Muse solves this at the protocol level. Every agent in a tree sees the same DAG. An agent can:

- Read the full commit history to understand what has been tried
- Branch from any commit to explore an alternative without polluting the main line
- Commit its work with a message that becomes part of the permanent record
- Merge its branch back, with three-way reconciliation handling conflicts
- Check out any historical state to understand what the system looked like at any prior point

This is the missing primitive for agent collaboration — not a message queue, not a shared database, but a **versioned, branchable, mergeable, content-addressed state store** that every agent in the tree can read and write coherently.

A tree of musical agents with distinct cognitive identities, collaborating over a shared Muse repository:

```
Composer (root coordinator)
├── Bach agent          — commits fugue subject on branch counterpoint/main
├── Jimi Hendrix agent  — commits lead response on branch lead/main
└── Miles Davis agent   — commits harmonic reframing on branch modal/main
```

The Composer runs a three-way merge. Conflicts are real musical conflicts — two agents wrote to the same beat, the same frequency range, the same structural moment. The Composer's cognitive architecture resolves them.

This is not AI generating music from a prompt. This is structured improvisation between agents with distinct cognitive identities, mediated by a version control system.

---

## Repository Structure

```
muse/
  domain.py           — MuseDomainPlugin Protocol + shared type definitions
  core/               — domain-agnostic VCS engine
    store.py          — file-based commit/snapshot/tag store (no external DB)
    repo.py           — repository detection (directory walk or MUSE_REPO_ROOT)
    snapshot.py       — content-addressed snapshot and commit ID derivation
    object_store.py   — SHA-256 blob storage under .muse/objects/
    merge_engine.py   — three-way merge state machine
    errors.py         — exit codes and error primitives
  plugins/
    registry.py       — maps domain names → MuseDomainPlugin instances
    music/            — music domain plugin (reference implementation)
      plugin.py       — implements all five MuseDomainPlugin methods for MIDI state
  cli/
    app.py            — Typer application root
    commands/         — one file per subcommand

tests/
  test_cli_*.py       — CLI integration tests (one per command group)
  test_core_*.py      — core engine unit tests
  test_music_plugin.py
  test_plugin_registry.py

docs/
  architecture/       — architecture reference and E2E walkthrough
  protocol/           — MuseDomainPlugin protocol spec and domain concepts
  reference/          — type contracts, .museattributes format
```

---

## Installation

```bash
# From source (recommended during v0.1.x development)
git clone https://github.com/cgcardona/muse
cd muse
pip install -e ".[dev]"
```

Core dependencies:

- Python 3.11+
- Typer (CLI)
- mido (MIDI parsing, music plugin only)
- toml

No database required. Muse stores all state in the `.muse/` directory — objects, snapshots, commits, refs — exactly like Git stores state in `.git/`.

---

## Documentation

- [Architecture](docs/architecture/muse-vcs.md) — full technical design and module map
- [E2E Walkthrough](docs/architecture/muse-e2e-demo.md) — step-by-step lifecycle from `init` to merge conflict
- [Plugin Protocol](docs/protocol/muse-protocol.md) — language-agnostic `MuseDomainPlugin` specification
- [Domain Concepts](docs/protocol/muse-domain-concepts.md) — universal terms, cross-domain patterns, and music-specific vocabulary
- [Variation Spec](docs/protocol/muse-variation-spec.md) — music-domain variation UX and wire contract (Stori/Maestro)
- [Type Contracts](docs/reference/type-contracts.md) — named type definitions with Mermaid diagrams
- [`.museattributes` Reference](docs/reference/muse-attributes.md) — per-repo merge strategy overrides

---

## Origin

Muse began as the version control subsystem of [Maestro](https://github.com/tellurstori/maestro), the AI music composition backend powering the [Stori DAW](https://tellurstori.com). The music domain proved the abstraction. Muse v2 generalizes it.

*Built from the couch. March 2026.*

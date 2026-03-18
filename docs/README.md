# Muse Documentation

> **Version:** v0.1.1 · [Project README](../README.md) · [Source](../muse/)

This directory contains the complete documentation for Muse — a domain-agnostic version control system for multidimensional state.

---

## Quick Navigation

| I want to… | Start here |
|-------------|-----------|
| Understand the full architecture | [Architecture Reference](architecture/muse-vcs.md) |
| Build a new domain plugin | [Plugin Authoring Guide](guide/plugin-authoring-guide.md) |
| Learn CRDT semantics | [CRDT Reference](guide/crdt-reference.md) |
| See an end-to-end walkthrough | [E2E Demo](architecture/muse-e2e-demo.md) |
| Read the protocol spec | [Plugin Protocol](protocol/muse-protocol.md) |
| Understand domain concepts | [Domain Concepts](protocol/muse-domain-concepts.md) |
| Look up a named type | [Type Contracts](reference/type-contracts.md) |
| Configure merge strategies | [`.museattributes` Reference](reference/muse-attributes.md) |
| Exclude files from snapshots | [`.museignore` Reference](reference/museignore.md) |
| Watch the demo narration | [Tour de Force Script](demo/tour-de-force-script.md) |

---

## Directory Map

```
docs/
├── README.md                     ← you are here
│
├── architecture/
│   ├── muse-vcs.md               — full technical design: protocol stack, storage,
│   │                               diff algorithms, OT merge, CRDT semantics,
│   │                               config system, and CLI command map
│   └── muse-e2e-demo.md          — step-by-step lifecycle: init → commit → branch
│                                   → merge conflict → resolve → tag
│
├── guide/
│   ├── plugin-authoring-guide.md — complete walkthrough for writing a new domain
│   │                               plugin, from core protocol through OT merge
│   │                               and CRDT convergent merge
│   └── crdt-reference.md         — CRDT primer: lattice laws, all six primitives
│                                   (VectorClock, LWWRegister, ORSet, RGA, AWMap,
│                                   GCounter), composition patterns, when to use CRDTs
│
├── protocol/
│   ├── muse-protocol.md          — language-agnostic MuseDomainPlugin spec: six
│   │                               required methods, StructuredMergePlugin and
│   │                               CRDTPlugin optional extensions, invariants
│   ├── muse-domain-concepts.md   — universal vocabulary: what "state", "delta",
│   │                               "dimension", "drift", and "merge" mean across
│   │                               music, genomics, simulation, and beyond
│   └── muse-variation-spec.md    — variation semantics for the MIDI domain
│
├── reference/
│   ├── type-contracts.md         — single source of truth for every named type:
│   │                               TypedDicts, dataclasses, Protocols, Enums,
│   │                               and TypeAliases with Mermaid diagrams
│   ├── muse-attributes.md        — .museattributes TOML format reference:
│   │                               [meta] domain, [[rules]] syntax, all five
│   │                               strategies, multi-domain usage, examples
│   └── museignore.md             — .museignore format: glob patterns, negation,
│                                   dotfile exclusion rules
│
└── demo/
    └── tour-de-force-script.md   — narration script for the video demo covering
                                    all nine acts: core VCS through CRDT semantics
                                    and the live domain dashboard
```

---

## Architecture at a Glance

Muse separates the **VCS engine** (domain-agnostic) from **domain plugins** (domain-specific). The engine never changes when a new domain is added — only a new plugin is registered.

```
┌─────────────────────────────────────────────┐
│                  muse CLI                   │
│          (15 commands, Typer-based)         │
└──────────────────────┬──────────────────────┘
                       │
┌──────────────────────▼──────────────────────┐
│              Muse Core Engine               │
│  DAG · object store · branching · merging   │
│  content-addressed blobs · lineage walking  │
└──────┬────────────────────────────┬─────────┘
       │                            │
┌──────▼──────┐              ┌──────▼──────┐
│MuseDomainPlugin│          │MuseDomainPlugin│
│   (music)    │            │  (your domain) │
│  6 methods   │            │   6 methods    │
└──────────────┘            └───────────────┘
```

The six required methods:

| Method | What it does |
|--------|-------------|
| `snapshot()` | Capture live state → content-addressed dict |
| `diff()` | Compute typed delta between two snapshots |
| `merge()` | Three-way reconciliation against a common ancestor |
| `drift()` | Compare committed state against live working tree |
| `apply()` | Apply a delta to reconstruct historical state |
| `schema()` | Declare data structure → drives diff algorithm selection |

Two optional protocol extensions enable richer merge semantics:

| Extension | Adds | Effect |
|-----------|------|--------|
| `StructuredMergePlugin` | `merge_ops()` | Operation-level OT merge — minimal real conflicts |
| `CRDTPlugin` | `join()` + 3 helpers | Convergent merge — `join` always succeeds, no conflict state |

---

## Adding a New Domain

1. Copy `muse/plugins/scaffold/` → `muse/plugins/<your_domain>/`
2. Rename `ScaffoldPlugin` → `<YourDomain>Plugin`
3. Replace every `raise NotImplementedError(...)` with real implementation
4. Register in `muse/plugins/registry.py`
5. Run `muse init --domain <your_domain>` in a project directory

All 15 `muse` CLI commands work immediately. See the [Plugin Authoring Guide](guide/plugin-authoring-guide.md) for the full walkthrough.

---

## Config Files Generated by `muse init`

| File | Location | Purpose |
|------|----------|---------|
| `repo.json` | `.muse/repo.json` | Immutable identity: repo UUID, schema version, domain |
| `config.toml` | `.muse/config.toml` | Mutable: `[user]`, `[auth]`, `[remotes]`, `[domain]` |
| `HEAD` | `.muse/HEAD` | Current branch ref |
| `.museattributes` | repo root | TOML merge strategy overrides (`[[rules]]`) |
| `.museignore` | repo root | Glob patterns to exclude from snapshots |

---

## Testing Standards

Every public function has a unit test. Integration tests wire real components. E2E tests invoke the CLI via `typer.testing.CliRunner`.

```bash
# Run all tests
pytest tests/ -v

# Type-check
mypy muse/

# Zero-Any audit
python tools/typing_audit.py --dirs muse/ tests/ --max-any 0
```

All three gates must be green before any PR merges.

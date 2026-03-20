# Muse Documentation

> **Version:** v0.1.3 · [Project README](../README.md) · [Source](../muse/)

This directory contains the complete documentation for Muse — a domain-agnostic version control system for multidimensional state.

---

## Quick Navigation

| I want to… | Start here |
|-------------|-----------|
| Understand the full architecture | [Architecture Reference](architecture/muse-vcs.md) |
| Authenticate with MuseHub | [Auth Reference](reference/auth.md) |
| Connect a repo to a hub | [Hub Reference](reference/hub.md) |
| Understand the security model | [Security Architecture](reference/security.md) |
| Push, fetch, or clone a repo | [Remotes Reference](reference/remotes.md) |
| Browse the CLI command tiers | [CLI Tiers Reference](reference/cli-tiers.md) |
| See all MIDI semantic commands | [MIDI Domain Reference](reference/midi-domain.md) |
| See all Code semantic commands | [Code Domain Reference](reference/code-domain.md) |
| Build a new domain plugin | [Plugin Authoring Guide](guide/plugin-authoring-guide.md) |
| Learn CRDT semantics | [CRDT Reference](guide/crdt-reference.md) |
| See an end-to-end walkthrough | [E2E Demo](architecture/muse-e2e-demo.md) |
| Read the protocol spec | [Plugin Protocol](protocol/muse-protocol.md) |
| Understand domain concepts | [Domain Concepts](protocol/muse-domain-concepts.md) |
| Look up a named type | [Type Contracts](reference/type-contracts.md) |
| Configure merge strategies | [`.museattributes` Reference](reference/muse-attributes.md) |
| Exclude files from snapshots | [`.museignore` Reference](reference/museignore.md) |
| Undo accidental resets | [Reflog Reference](reference/reflog.md) |
| Remove unused blobs | [GC Reference](reference/gc.md) |
| Export a versioned archive | [Archive Reference](reference/archive.md) |
| Hunt regressions automatically | [Bisect Reference](reference/bisect.md) |
| Attribute text file lines | [Blame Reference](reference/blame.md) |
| Work on multiple branches at once | [Worktree Reference](reference/worktree.md) |
| Compose multiple Muse repos | [Workspace Reference](reference/workspace.md) |
| Watch the demo narration | [Demo Script](demo/demo-script.md) |

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
│   ├── auth.md                   — muse auth login/whoami/logout: identity lifecycle,
│   │                               ~/.muse/identity.toml format, env vars, flows
│   ├── hub.md                    — muse hub connect/status/disconnect/ping: hub fabric,
│   │                               hub vs remote distinction, HTTPS enforcement
│   ├── remotes.md                — muse push/fetch/clone/pull/remote: transport arch,
│   │                               PackBundle wire format, tracking branches
│   ├── security.md               — security architecture: validation trust model,
│   │                               path containment, ANSI injection, XML safety,
│   │                               transport hardening, identity store guarantees
│   ├── cli-tiers.md              — three-tier CLI architecture: Tier 1 plumbing
│   │                               (JSON, pipeable), Tier 2 porcelain (core VCS),
│   │                               Tier 3 semantic (midi/code/coord namespaces)
│   ├── midi-domain.md            — MIDI domain complete reference: all 31 semantic
│   │                               porcelain commands, flags, JSON schemas, types
│   ├── code-domain.md            — Code domain complete reference: all semantic
│   │                               porcelain commands, symbol identity model, types
│   ├── type-contracts.md         — single source of truth for every named type:
│   │                               TypedDicts, dataclasses, Protocols, Enums,
│   │                               and TypeAliases with Mermaid diagrams
│   ├── muse-attributes.md        — .museattributes TOML format reference:
│   │                               [meta] domain, [[rules]] syntax, all five
│   │                               strategies, multi-domain usage, examples
│   ├── museignore.md             — .museignore format: glob patterns, negation,
│   │                               dotfile exclusion rules
│   ├── reflog.md                 — muse reflog: HEAD/branch movement history,
│   │                               undo safety net, per-ref journals
│   ├── gc.md                     — muse gc: reachability walk, dry-run, stats,
│   │                               when and why to run garbage collection
│   ├── archive.md                — muse archive: tar.gz/zip export, --prefix,
│   │                               --ref, distribution without history
│   ├── bisect.md                 — muse bisect: binary regression search,
│   │                               start/bad/good/skip/run/log/reset, agent-safe
│   ├── blame.md                  — muse blame: line-level text attribution,
│   │                               porcelain JSON output, attribution algorithm
│   ├── worktree.md               — muse worktree: parallel branch checkouts,
│   │                               shared object store, agent-per-branch pattern
│   └── workspace.md              — muse workspace: multi-repo composition,
│                                   manifest, sync, coordinator/worker patterns
│
└── demo/
    ├── midi-demo.md              — MIDI domain demo walkthrough: all 31 semantic
    │                               porcelain commands with CLI output examples
    ├── demo-code.md              — Code domain demo walkthrough
    └── demo-script.md            — narration script for the video demo
```

---

## Architecture at a Glance

Muse separates the **VCS engine** (domain-agnostic) from **domain plugins** (domain-specific). The engine never changes when a new domain is added — only a new plugin is registered.

```
┌─────────────────────────────────────────────┐
│                  muse CLI                   │
│  Tier 1: plumbing  ·  Tier 2: porcelain    │
│  Tier 3: midi / code / coord domains        │
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

All Tier 2 core VCS commands work immediately for the new domain. Tier 3 semantic commands live in the new `muse/<domain> …` sub-namespace. See the [Plugin Authoring Guide](guide/plugin-authoring-guide.md) for the full walkthrough.

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

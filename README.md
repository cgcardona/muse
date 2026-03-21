# Muse

**Git tracks lines of text. Muse tracks anything.**

Version control for multidimensional state — music, code, genomics, 3D design, scientific simulation. One engine. Any domain. Agents as first-class citizens.

[![CI](https://github.com/cgcardona/muse/actions/workflows/ci.yml/badge.svg)](https://github.com/cgcardona/muse/actions)
[![Python 3.14](https://img.shields.io/badge/python-3.14-blue.svg)](https://python.org)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

---

## The idea in one paragraph

Git works because text is one-dimensional — a sequence of lines. Diffs are additions and deletions to that sequence. The moment your state has multiple independent axes — 21 MIDI dimensions, a graph of AST symbols, a climate model grid — Git becomes meaningless. Muse replaces the line-diff model with a **typed delta algebra** over any state space you define. The core engine (DAG, branching, three-way merge, content-addressed object store) never changes. Your domain is a plugin — six methods — and Muse handles the rest.

---

## Live Demos

Two domains, fully interactive:

**→ [MIDI Demo](https://cgcardona.github.io/muse/)** — version control for music. Commit DAG, DAW track view, 21-dimension heatmap, note-level merge with zero conflicts between dimensions.

**→ [Code Demo](https://cgcardona.github.io/muse/)** — version control for code graphs. Symbol-level diff, AST-aware merge, hotspot detection, rename and move tracking — across 11 languages.

---

## Install

```bash
git clone https://github.com/cgcardona/muse
cd muse
pip install -e ".[dev]"
```

No database. No daemon. All state lives in `.muse/` — same mental model as `.git/`.

---

## Quick start

```bash
muse init --domain midi        # init a repo (midi, code, or your own plugin)
muse commit -m "Add verse"    # snapshot the working tree
muse checkout -c feat/chorus  # branch
muse status                   # drift vs HEAD
muse diff                     # what changed, typed by dimension
muse merge feat/chorus        # three-way merge — conflicts are real domain conflicts
muse log --format json        # pipe to anything; every command is agent-ready
```

That's it. Everything else is flags.

---

## What makes it different

| | Git | Muse |
|---|---|---|
| Unit of state | Line of text | Snapshot of any typed state space |
| Diff | Line additions / deletions | Typed delta per domain dimension |
| Merge | Line-level | Three-way per dimension — independent axes never conflict |
| Domain | Hard-coded (files + text) | Plugin protocol — six methods |
| Agent support | None | JSON output on every command; CRDT mode for zero-conflict multi-agent writes |

**MIDI example:** Two agents edit the same MIDI file — one changes sustain pedal data, the other changes pitch bend. In Git, both touch the same file, so they conflict. In Muse, sustain and pitch bend are independent dimensions. The merge always succeeds. No human in the loop.

**Code example:** Two agents refactor the same file — one renames a function, the other adds a parameter to a different function. Muse tracks symbols by content hash, not line number. No conflict. The rename is detected and propagated automatically.

---

## The plugin contract

Six methods. That's the entire API surface for a new domain.

```python
class MuseDomainPlugin(Protocol):
    def snapshot(self, live_state: LiveState) -> StateSnapshot: ...
    def diff(self, base: StateSnapshot, target: StateSnapshot, ...) -> StateDelta: ...
    def merge(self, base, left, right, ...) -> MergeResult: ...
    def drift(self, committed: StateSnapshot, live: LiveState) -> DriftReport: ...
    def apply(self, delta: StateDelta, live: LiveState) -> LiveState: ...
    def schema(self) -> DomainSchema: ...
```

Implement those six and you get branching, three-way merge, a content-addressed object store, commit history, reflog, cherry-pick, revert, bisect, stash, tags, worktrees, garbage collection, archive export, offline bundles, and 40+ CLI commands — all domain-agnostic, all immediately working.

Two optional extensions unlock richer semantics:

- **`StructuredMergePlugin`** — sub-file auto-merge via Operational Transformation
- **`CRDTPlugin`** — convergent multi-agent writes; `muse merge` always succeeds, no conflict state ever

---

## Shipped domains

### MIDI *(reference implementation)*
State across **21 independent dimensions**: notes, pitch bend, polyphonic aftertouch, 11 CC controllers, program changes, tempo map, time signatures, key signatures, markers, track structure. Note-level Myers LCS diff. Dimension-aware three-way merge. Voice-aware RGA CRDT. MIDI query DSL. 31 semantic porcelain commands.

### Code *(second domain)*
Code as a **graph of named symbols** — not a bag of text lines. Symbol-level diff. AST-aware merge. Rename and move detection via content-addressed symbol identity. Two agents editing different functions in the same file always auto-merge. 12 commands strictly impossible in Git. Supported languages: Python, TypeScript, JavaScript, Go, Rust, Java, C, C++, C#, Ruby, Kotlin.

### Planned
Genomics · Scientific Simulation · 3D Spatial Design · Spacetime Simulation

---

## For agents

Every porcelain and plumbing command emits `--format json`. Every exit code is stable and documented. The entire CLI is synchronous — no background daemons, no event loops, no surprises.

```bash
# Machine-readable output everywhere
muse status --json
muse log --format json | jq '.[0].commit_id'
muse commit -m "agent checkpoint" --format json | jq .snapshot_id

# Full plumbing layer for agent pipelines
muse plumbing rev-parse HEAD -f text
muse plumbing commit-graph --tip main -f json
muse plumbing pack-objects HEAD | muse plumbing unpack-objects
```

[Plumbing reference →](docs/reference/plumbing.md) · [Porcelain reference →](docs/reference/porcelain.md)

---

## Documentation

| | |
|---|---|
| [Plumbing reference](docs/reference/plumbing.md) | All 22 low-level commands — flags, JSON schemas, exit codes, composability patterns |
| [Porcelain reference](docs/reference/porcelain.md) | All 30+ high-level commands — flags, JSON schemas, conflict flows |
| [Plugin authoring guide](docs/guide/plugin-authoring-guide.md) | Step-by-step: build and ship a new domain in under an hour |
| [Architecture](docs/architecture/muse-vcs.md) | Full technical design, module map, layer rules |
| [CRDT reference](docs/guide/crdt-reference.md) | VectorClock, LWWRegister, ORSet, RGA, AWMap, GCounter — API and lattice laws |
| [Plugin protocol](docs/protocol/muse-protocol.md) | Language-agnostic `MuseDomainPlugin` spec |
| [Type contracts](docs/reference/type-contracts.md) | Every named type with field tables |
| [`.museattributes`](docs/reference/muse-attributes.md) | Per-path merge strategy overrides |
| [`.museignore`](docs/reference/museignore.md) | Snapshot exclusion rules |

---

## Requirements

- Python 3.14+
- `mido` (MIDI plugin)
- `tree-sitter` + language grammars (Code plugin)

---

*v0.1.4 · Python 3.14 · Built from the couch. March 2026.*

# Muse VCS

**Muse** is a Git-style version control system for musical compositions. It gives AI agents and human composers a complete version-control workflow — commit, branch, merge, checkout, rebase, cherry-pick, bisect, and more — built specifically around the semantics of music: notes, phrases, tracks, regions, and time.

Muse was extracted from [Maestro](https://github.com/tellurstori/maestro), the AI music composition backend powering the [Stori DAW](https://tellurstori.com) for macOS.

---

## What is Muse?

Where Git tracks line-level diffs in text files, Muse tracks **note-level diffs** in MIDI compositions. A Muse commit records which notes were added, modified, or removed — and by which instrument, in which region, at which beat. The commit graph forms a DAG identical to Git's, enabling the same branching and merging model, but applied to music.

Key capabilities:

- **Commit / branch / checkout / merge** — full VCS lifecycle for musical state
- **Three-way merge** — auto-resolves non-conflicting note changes across tracks
- **Drift detection** (`muse status`) — detects uncommitted working-tree changes against HEAD
- **Rebase / cherry-pick / revert / reset** — full history rewriting and surgery
- **Bisect** — binary search over commit history for regression hunting
- **Stash** — shelve uncommitted changes temporarily
- **Rerere** — reuse recorded conflict resolutions across repeated merges
- **Musical analysis** — groove-check, emotion-diff, motif tracking, divergence scoring, contour analysis, and more
- **CLI** (`muse`) — 70+ subcommands covering every VCS primitive
- **HTTP API** — FastAPI router at `/api/v1/muse/*` for DAW integration
- **Content-addressed object store** — SHA-256 blobs under `.muse/objects/`, mirroring Git's loose-object layout

---

## Repository Structure

```
maestro/
  services/muse_*.py      — 33 service modules (merge, rebase, drift, checkout, etc.)
  muse_cli/               — CLI package: app.py + 73 command files
  api/routes/muse.py      — FastAPI HTTP router
  db/muse_models.py       — SQLAlchemy ORM models

tourdeforce/
  clients/muse.py         — HTTP client for the Muse API

tests/
  test_muse_*.py          — 30 unit/integration test files
  muse_cli/               — 54 CLI-level test files
  e2e/                    — E2E harness, golden-path, and integration tests
  test_commit_drift_safety.py

docs/
  architecture/muse-vcs.md        — canonical architecture reference
  architecture/muse-e2e-demo.md   — E2E demo walkthrough
  protocol/muse-protocol.md       — language-agnostic protocol spec
  protocol/muse-variation-spec.md — variation UX and wire contract
  reference/muse-attributes.md    — .museattributes format reference
```

The internal package paths (`maestro/services/muse_*.py`, `maestro/muse_cli/`, etc.) are preserved from the original Maestro monorepo so that all internal cross-imports remain intact.

---

## CLI Quick Reference

```bash
# Initialize a Muse repository in the current directory
muse init

# Stage and commit the current working tree
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

# Binary-search for a regression
muse bisect start --bad HEAD --good <commit-id>

# Analyse rhythmic groove drift across history
muse groove-check

# Compare emotion vectors between two commits
muse emotion-diff <commit-a> <commit-b>
```

Run `muse --help` for the full list of subcommands, or `muse <subcommand> --help` for per-command usage.

---

## HTTP API

The FastAPI router exposes five endpoints at `/api/v1/muse`:

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/muse/variations` | Persist a variation (commit a musical change) |
| `POST` | `/muse/head` | Set the HEAD pointer |
| `GET`  | `/muse/log` | Retrieve the commit DAG as a `MuseLogGraph` |
| `POST` | `/muse/checkout` | Check out a prior variation (time travel) |
| `POST` | `/muse/merge` | Three-way merge two variations |

---

## Dependencies

Muse was extracted from the Maestro monorepo. Some imports reference `maestro.*` packages (domain models, database utilities, DAW state). To run Muse standalone, you will need to either:

1. Install the Maestro core package as a dependency, or
2. Refactor the `maestro.*` imports to a standalone `muse.*` package namespace (planned for a future release).

Core runtime dependencies (inherited from Maestro):

- Python 3.11+
- FastAPI + uvicorn
- SQLAlchemy (async) + asyncpg
- Pydantic v2
- Typer (CLI)
- httpx

---

## Documentation

- [Architecture](docs/architecture/muse-vcs.md) — full technical design
- [E2E Demo](docs/architecture/muse-e2e-demo.md) — step-by-step lifecycle walkthrough
- [Protocol Spec](docs/protocol/muse-protocol.md) — language-agnostic protocol definition
- [Variation Spec](docs/protocol/muse-variation-spec.md) — variation UX and wire contract
- [`.museattributes` Reference](docs/reference/muse-attributes.md) — per-repo merge strategies

---

## Origin

Muse is part of the [Tellurstori](https://tellurstori.com) music technology stack. It was built as an embedded subsystem of [Maestro](https://github.com/tellurstori/maestro) and extracted into this standalone repository to enable independent development and reuse.

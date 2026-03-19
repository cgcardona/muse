# Muse — Agent Contract

This document defines how AI agents operate in this repository. It applies to every agent working on Muse: core VCS engine, CLI commands, domain plugins, tests, and docs.

---

## Agent Role

You are a **senior implementation agent** maintaining Muse — a domain-agnostic version control system for multidimensional state.

You:
- Implement features, fix bugs, refactor, extend the plugin architecture, add tests, update docs.
- Write production-quality, fully-typed, synchronous Python.
- Think like a staff engineer: composability over cleverness, clarity over brevity.

You do NOT:
- Redesign architecture unless explicitly requested.
- Introduce new dependencies without justification and user approval.
- Add `async`, `await`, FastAPI, SQLAlchemy, Pydantic, or httpx — these are permanently removed.
- Work directly on `dev` or `main`. Ever.

---

## No legacy. No deprecated. No exceptions.

- **Delete on sight.** When you touch a file and find dead code, a deprecated shape, a backward-compatibility shim, or a legacy fallback — delete it in the same commit. Do not defer it.
- **No fallback paths.** The current shape is the only shape. Every trace of the old way is deleted.
- **No "legacy" or "deprecated" annotations.** Code marked `# deprecated` should be deleted, not annotated.
- **No dead constants, dead regexes, dead fields.** If it can never be reached, delete it.
- **No references to prior projects.** External codebases do not exist here. Do not name or import them.

When you remove something, remove it completely: implementation, tests, docs, config.

---

## Architecture

```
muse/
  domain.py          → MuseDomainPlugin protocol (the six-method contract every domain implements)
  core/
    object_store.py  → content-addressed blob storage (.muse/objects/, SHA-256)
    snapshot.py      → manifest hashing, workdir diffing, commit-id computation
    store.py         → file-based CRUD: CommitRecord, SnapshotRecord, TagRecord (.muse/commits/ etc.)
    merge_engine.py  → three-way merge, merge-base BFS, conflict detection, merge-state I/O
    repo.py          → require_repo() — walk up from cwd to find .muse/
    errors.py        → ExitCode enum
  cli/
    app.py           → Typer root — registers all 14 core commands
    commands/        → one module per command (init, commit, log, status, diff, show,
                       branch, checkout, merge, reset, revert, cherry_pick, stash, tag)
    models.py        → re-exports store types for backward-import compatibility
    config.py        → .muse/config.toml read/write helpers
    midi_parser.py   → MIDI / MusicXML → NoteEvent (music domain utility, no external deps)
  plugins/
    music/
      plugin.py      → MusicPlugin — the reference MuseDomainPlugin implementation
tools/
  typing_audit.py    → regex + AST violation scanner; CI runs with --max-any 0
tests/
  test_core_store.py        → CommitRecord / SnapshotRecord / TagRecord CRUD
  test_core_snapshot.py     → hashing, manifest building, workdir diff
  test_core_merge_engine.py → three-way merge, base-finding, conflict detection
  test_cli_workflow.py      → end-to-end CLI: init → commit → log → branch → merge → …
  test_music_plugin.py      → MusicPlugin satisfies MuseDomainPlugin protocol
```

### Layer rules (hard constraints)

- **Commands are thin.** `cli/commands/*.py` call `muse.core.*` — no business logic lives in them.
- **Core is domain-agnostic.** `muse.core.*` never imports from `muse.plugins.*`.
- **Plugins are isolated.** `muse.plugins.music.plugin` is the only file that imports music-domain logic.
- **New domains = new plugin.** Add `muse/plugins/<domain>/plugin.py` implementing `MuseDomainPlugin`. The core engine is never modified for a new domain.
- **No async.** Every function is synchronous. No `async def`, no `await`, no `asyncio`.

---

## Branch Discipline — Absolute Rule

**`dev` and `main` are read-only. Every piece of work happens on a feature branch.**

### Full task lifecycle

1. **Start clean.** `git status` — must show `nothing to commit, working tree clean`.
2. **Branch first.** `git checkout -b fix/<description>` or `feat/<description>` is always the first command.
3. **Do the work.** Commit on the branch.
4. **Verify locally** — in this exact order:
   ```bash
   mypy muse/                                                        # zero errors, strict mode
   python tools/typing_audit.py --dirs muse/ tests/ --max-any 0     # zero typing violations
   pytest tests/ -v                                                  # all 99+ tests green
   ```
5. **Open a PR** against `dev` via `gh pr create` or the GitHub MCP tool.
6. **Merge immediately.** Feature→dev: squash. Dev→main: merge (never squash — squashing severs the commit-graph relationship and causes spurious conflicts on every subsequent dev→main merge).
7. **Clean up:** delete remote branch, delete local branch, `git pull origin dev`, `git status` clean.

### Enforcement protocol

| Checkpoint | Command | Expected |
|-----------|---------|----------|
| Before branching | `git status` | `nothing to commit, working tree clean` |
| Before opening PR | `mypy` + `typing_audit` + `pytest` | All pass locally |
| After task | Branch deleted, dev pulled | `git status` clean |

---

## GitHub Interactions — MCP First

The `user-github` MCP server is available in every session. Prefer MCP tools over `gh` CLI.

| Operation | MCP tool |
|-----------|----------|
| Read an issue | `issue_read` |
| Create / edit an issue | `issue_write` |
| Add a comment | `add_issue_comment` |
| List issues | `list_issues` |
| Search issues / PRs | `search_issues`, `search_pull_requests` |
| Read a PR | `pull_request_read` |
| Create a PR | `create_pull_request` |
| Merge a PR | `merge_pull_request` |
| Create a review | `pull_request_review_write` |
| List / create branches | `list_branches`, `create_branch` |
| Get current user | `get_me` |
| Search code | `search_code` |

Only fall back to `gh` CLI for operations not yet covered by the MCP server.

---

## Code Standards

- **Type hints everywhere — 100% coverage.** No untyped function parameters, no untyped return values.
- **Modern syntax only:** `list[X]`, `dict[K, V]`, `X | None` — never `List`, `Dict`, `Optional[X]`.
- **Synchronous I/O.** No `async`, no `await`, no `asyncio` anywhere in `muse/`.
- **`logging.getLogger(__name__)`** — never `print()`.
- **Docstrings** on public modules, classes, and functions. "Why" over "what."
- **Sparse logs.** Emoji prefixes where used: ❌ error, ⚠️ warning, ✅ success.

---

## Typing — Zero-Tolerance Rules

Strong, explicit types are the contract that makes the codebase navigable by humans and agents. These rules have no exceptions.

**Banned — no exceptions:**

| What | Why banned | Use instead |
|------|------------|-------------|
| `Any` | Collapses type safety for all downstream callers | `TypedDict`, `Protocol`, a specific union |
| `object` | Effectively `Any` — carries no structural information | The actual type or a constrained union |
| `list` (bare) | Tells nothing about contents | `list[X]` with the concrete element type |
| `dict` (bare) | Same | `dict[K, V]` with concrete key and value types |
| `dict[str, Any]` with known keys | Structured data masquerading as dynamic | `TypedDict` — if you know the keys, name them |
| `cast(T, x)` | Masks a broken return type upstream | Fix the callee to return `T` correctly |
| `# type: ignore` | A lie in the source — silences a real error | Fix the root cause |
| `Optional[X]` | Legacy syntax | `X \| None` |
| `List[X]`, `Dict[K,V]` | Legacy typing imports | `list[X]`, `dict[K, V]` |

**The known-keys rule:** `dict[K, V]` is correct when any key is valid at runtime. If you know the keys at write time, use a `TypedDict` and name them. `dict[str, Any]` with a known key structure is the highest-signal red flag — structured data treated as unstructured.

**The cast rule:** writing `cast(SomeType, value)` means the function producing `value` returns the wrong type. Do not paper over it. Go upstream, fix the return type, let the correct type flow down.

### Enforcement chain

| Layer | Command | Threshold |
|-------|---------|-----------|
| Local | `mypy muse/` | strict, 0 errors |
| Typing ceiling | `python tools/typing_audit.py --dirs muse/ tests/ --max-any 0` | 0 violations — blocks commit |
| CI | `mypy muse/` in GitHub Actions | 0 errors — blocks PR merge |

---

## Testing Standards

| Level | Scope | Required when |
|-------|-------|---------------|
| **Unit** | Single function or class, mocked dependencies | Always — every public function |
| **Integration** | Multiple real components wired together | Any time two modules interact |
| **Regression** | Reproduces a specific bug before the fix | Every bug fix, named `test_<what_broke>_<fixed_behavior>` |
| **E2E CLI** | Full CLI invocation via `typer.testing.CliRunner` | Any user-facing command |

**Test scope:** run only the test files covering changed source files. The full suite is the gate for dev→main merges.

**Agents own all broken tests — not just theirs.** If you see a failing test, fix it or block the PR. "This was already broken" is not an acceptable response.

---

## Verification Checklist

Run before opening any PR:

- [ ] On a feature branch — never on `dev` or `main`
- [ ] `mypy muse/` — zero errors, strict mode
- [ ] `python tools/typing_audit.py --dirs muse/ tests/ --max-any 0` — zero violations
- [ ] `pytest tests/ -v` — all tests green
- [ ] No `Any`, `object`, bare collections, `cast()`, `# type: ignore`, `Optional[X]`, `List`/`Dict`
- [ ] No dead code, no references to prior projects, no async/await
- [ ] Affected docs updated in the same commit
- [ ] No secrets, no `print()`, no orphaned imports

---

## Scope of Authority

### Decide yourself
- Implementation details within existing patterns.
- Bug fixes with regression tests.
- Refactoring that preserves behaviour.
- Test additions and improvements.
- Doc updates reflecting code changes.

### Ask the user first
- New plugin domains (`muse/plugins/<domain>/`).
- New dependencies in `pyproject.toml`.
- Changes to the `MuseDomainPlugin` protocol (breaks all existing plugins).
- New CLI commands (user-facing API changes).
- Architecture changes (new layers, new storage formats).

---

## Anti-Patterns (never do these)

- Working directly on `dev` or `main`.
- `Any`, `object`, bare collections, `cast()`, `# type: ignore` — absolute bans.
- `Optional[X]`, `List[X]`, `Dict[K,V]` — use modern syntax.
- `async`/`await` anywhere in `muse/`.
- Importing from `muse.plugins.*` inside `muse.core.*`.
- Adding `fastapi`, `sqlalchemy`, `pydantic`, `httpx`, `asyncpg` as dependencies.
- Referencing external prior projects — they do not exist in this codebase.
- `print()` for diagnostics.
- Merging with a known failing test.

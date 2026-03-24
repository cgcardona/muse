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
- Use `git`, `gh`, or GitHub for anything — Muse and MuseHub are the only VCS tools.
- Work directly on `main`. Ever.

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
    app.py           → Typer root — registers all commands
    commands/        → one module per command (init, commit, log, status, diff, show,
                       branch, checkout, merge, reset, revert, cherry_pick, stash, tag)
    models.py        → re-exports store types for backward-import compatibility
    config.py        → .muse/config.toml read/write helpers
    midi_parser.py   → MIDI / MusicXML → NoteEvent (MIDI domain utility, no external deps)
  plugins/
    music/
      plugin.py      → MidiPlugin — the reference MuseDomainPlugin implementation
tools/
  typing_audit.py    → regex + AST violation scanner; run with --max-any 0
tests/
  test_core_store.py        → CommitRecord / SnapshotRecord / TagRecord CRUD
  test_core_snapshot.py     → hashing, manifest building, workdir diff
  test_core_merge_engine.py → three-way merge, base-finding, conflict detection
  test_cli_workflow.py      → end-to-end CLI: init → commit → log → branch → merge → …
  test_midi_plugin.py       → MidiPlugin satisfies MuseDomainPlugin protocol
```

### Layer rules (hard constraints)

- **Commands are thin.** `cli/commands/*.py` call `muse.core.*` — no business logic lives in them.
- **Core is domain-agnostic.** `muse.core.*` never imports from `muse.plugins.*`.
- **Plugins are isolated.** `muse.plugins.music.plugin` is the only file that imports music-domain logic.
- **New domains = new plugin.** Add `muse/plugins/<domain>/plugin.py` implementing `MuseDomainPlugin`. The core engine is never modified for a new domain.
- **No async.** Every function is synchronous. No `async def`, no `await`, no `asyncio`.

---

## Version Control — Muse Only

**Git and GitHub are not used.** All branching, committing, merging, and releasing happen through Muse. Never run `git`, `gh`, or reference GitHub Actions.

### The mental model

Git tracks line changes in files. Muse tracks **named things** — functions, classes, sections, notes — across time. The file is the container; the symbol is the unit of meaning.

- `muse diff` shows `Invoice.calculate()` was modified, not that lines 42–67 changed.
- `muse merge --dry-run` identifies conflicting symbol edits before a conflict marker is written.
- `muse status` surfaces untracked symbols and dead code the moment it is orphaned.
- `muse commit` is a **typed event** — Muse proposes MAJOR/MINOR/PATCH based on structural changes.

### Starting work

```
muse status                     # where am I, what's dirty
muse branch feat/my-thing       # create branch
muse checkout feat/my-thing     # switch to it
```

### While working

```
muse status                     # constantly
muse diff                       # symbol-level diff
muse code add .                 # stage
muse commit -m "..."            # typed event
```

### Before merging

```
muse fetch origin
muse status
muse merge --dry-run main       # confirm no symbol conflicts
```

### Merging

```
muse checkout main
muse merge feat/my-thing
```

### Releasing

```
# Create a local release at HEAD (--title and --body are required by convention)
muse release add <tag> --title "<title>" --body "<description>"

# Optionally pin the channel (default inferred from semver pre-release label)
muse release add <tag> --title "<title>" --body "<description>" --channel stable

# Push to a remote
muse release push <tag> --remote local

# Full delete-and-recreate cycle (e.g. after a DB migration or data fix):
muse release delete <tag> --remote local --yes
muse release add <tag> --title "<title>" --body "<description>"
muse release push <tag> --remote local
```

### Branch discipline — absolute rule

**`main` is not for direct work. Every task lives on a branch.**

Full lifecycle:
1. `muse status` — clean before branching.
2. `muse branch feat/<desc>` then `muse checkout feat/<desc>`.
3. Do the work. Commit on the branch.
4. **Verify** before merging — in this exact order:
   ```
   mypy muse/                                                        # zero errors
   python tools/typing_audit.py --dirs muse/ tests/ --max-any 0     # zero violations
   pytest tests/ -v                                                  # all green
   ```
5. `muse merge --dry-run main` — confirm clean.
6. `muse checkout main && muse merge feat/<desc>`.
7. `muse release add <tag> --title "<title>" --body "<description>"` then `muse release push <tag> --remote local`.

### Enforcement protocol

| Checkpoint | Command | Expected |
|-----------|---------|----------|
| Before branching | `muse status` | clean working tree |
| Before merging | `mypy` + `typing_audit` + `pytest` | all pass |
| After merge | `muse status` | clean |

---

## MuseHub Interactions

MuseHub at `http://localhost:10003` is the remote repository server. Releases, issues, and browsing all happen here. The `user-github` MCP server may be used **for MuseHub issue tracking only** (not for code commits or releases — those go through Muse CLI).

| Operation | Tool |
|-----------|------|
| View releases | `http://localhost:10003/<owner>/<repo>/releases` |
| Push release | `muse release push <tag> --remote local` |
| Delete remote release | `muse release delete <tag> --remote local --yes` |
| List remote releases | `muse release list --remote local` |

---

## Frontend Separation of Concerns — Absolute Rule (MuseHub contributions)

When working on any MuseHub template or static asset, every concern belongs in exactly one layer. Violations are treated the same as a typing error — fix on sight, in the same commit.

| Layer | Where it lives | What it does |
|-------|---------------|--------------|
| **Structure** | `templates/musehub/pages/*.html`, `fragments/*.html` | Jinja2 markup only — no `<style>`, no `<script>` tags |
| **Behaviour** | `templates/musehub/static/js/*.js` | All JS / Alpine.js / HTMX logic |
| **Style** | `templates/musehub/static/scss/_*.scss` | All CSS, compiled via `app.scss` → `app.css` |

**Never put `<style>` blocks or non-dynamic inline `style="..."` attributes in a Jinja2 template.** If you find them while touching a file, extract them to the matching SCSS partial in the same commit.

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

---

## Testing Standards

| Level | Scope | Required when |
|-------|-------|---------------|
| **Unit** | Single function or class, mocked dependencies | Always — every public function |
| **Integration** | Multiple real components wired together | Any time two modules interact |
| **Regression** | Reproduces a specific bug before the fix | Every bug fix, named `test_<what_broke>_<fixed_behavior>` |
| **E2E CLI** | Full CLI invocation via `typer.testing.CliRunner` | Any user-facing command |

**Test scope:** run only the test files covering changed source files. The full suite is the gate before merging to `main`.

**Agents own all broken tests — not just theirs.** If you see a failing test, fix it or block the merge.

**Test efficiency — mandatory protocol:**
1. Run the full suite **once** to find all failures.
2. Fix every failure found.
3. Re-run **only the files that were failing** to confirm the fix.
4. Run the full suite only as the final pre-merge gate.

---

## Verification Checklist

Run before merging to `main`:

- [ ] On a feature branch — never on `main`
- [ ] `mypy muse/` — zero errors, strict mode
- [ ] `python tools/typing_audit.py --dirs muse/ tests/ --max-any 0` — zero violations
- [ ] `pytest tests/ -v` — all tests green
- [ ] No `Any`, `object`, bare collections, `cast()`, `# type: ignore`, `Optional[X]`, `List`/`Dict`
- [ ] No dead code, no async/await
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

- Using `git`, `gh`, or GitHub for anything. Muse and MuseHub only.
- Working directly on `main`.
- `Any`, `object`, bare collections, `cast()`, `# type: ignore` — absolute bans.
- `Optional[X]`, `List[X]`, `Dict[K,V]` — use modern syntax.
- `async`/`await` anywhere in `muse/`.
- Importing from `muse.plugins.*` inside `muse.core.*`.
- Adding `fastapi`, `sqlalchemy`, `pydantic`, `httpx`, `asyncpg` as dependencies.
- `print()` for diagnostics.

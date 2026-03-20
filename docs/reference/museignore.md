# `.museignore` Reference

> **Format:** TOML · **Location:** repository root (next to `.muse/`)
> **Loaded by:** every `plugin.snapshot()` call via `muse.core.ignore`

`.museignore` tells Muse which files to exclude from every snapshot.
It lives in the **repository root** (the directory that contains `.muse/` and
`state/`) and uses TOML syntax for consistency with `.muse/config.toml`
and `.museattributes`.

---

## Why it matters

`muse commit` snapshots everything in `state/`. Without `.museignore`,
OS artifacts (`.DS_Store`), DAW temp files (`*.bak`, `*.tmp`), rendered
previews, and build outputs enter the content-addressed object store and
contribute to diff noise on every commit.

`.museignore` lets you declare — once, in a machine-readable file — exactly
what belongs in version history and what does not.

---

## File location

```
my-project/
├── .muse/               ← VCS metadata
├── state/           ← tracked workspace (content here is snapshotted)
├── .museignore          ← ignore rules (lives here, next to state/)
└── .museattributes      ← merge strategies
```

---

## File structure

`.museignore` is a TOML file with two kinds of sections:

```toml
# .museignore
# Ignore rules for this repository.
# Docs: docs/reference/museignore.md

[global]
# Patterns applied to every domain.
# Gitignore-compatible glob syntax. Last match wins.
# Prefix a pattern with ! to un-ignore a previously matched path.
patterns = [
    ".DS_Store",
    "Thumbs.db",
    "*.tmp",
    "*.log",
]

[domain.midi]
# Patterns applied only when the active domain plugin is "midi".
patterns = [
    "*.bak",
    "*.autosave",
    "/renders/",
    "/exports/",
]

[domain.code]
# Patterns applied only when the active domain plugin is "code".
patterns = [
    "__pycache__/",
    "*.pyc",
    "node_modules/",
    "dist/",
    "build/",
    ".venv/",
]
```

---

## Sections

### `[global]` (optional)

Patterns in `[global]` are loaded first and applied to **every domain**.
This is the right place for OS artifacts and truly cross-cutting rules.

### `[domain.<name>]` (optional, repeatable)

Patterns in `[domain.<name>]` are applied **only when the active domain
plugin matches `<name>`**. Use the same string your plugin reports as its
domain tag (e.g. `"midi"`, `"code"`, `"genomics"`).

Patterns from all other `[domain.*]` sections are never loaded.

---

## Evaluation order

When `muse` runs any command that reads the workspace:

1. `[global]` patterns are loaded in array order.
2. The active domain's `[domain.<name>]` patterns are appended in array order.
3. Each file path is tested against the combined list — **last matching rule wins**.
4. A negation rule (`!pattern`) can un-ignore a path matched by an earlier rule.

This means a `[domain.midi]` negation rule can override a `[global]` ignore,
and vice versa — just put the rule you want to win later in the list.

---

## Pattern syntax

Each string in a `patterns` array uses gitignore-compatible glob syntax:

| Syntax | Meaning |
|--------|---------|
| `*.ext` | Ignore all files with this extension, at any depth |
| `name` | Ignore any file named exactly `name`, at any depth |
| `dir/*.ext` | Ignore matching files inside `dir/` at that exact depth |
| `**/name` | Ignore `name` inside any subdirectory at any depth |
| `name/` | Directory pattern — silently skipped (Muse tracks files, not directories) |
| `/pattern` | Anchor to root — only matches at the top level of `state/` |
| `!pattern` | Negate — un-ignore a previously matched path |

### Patterns without a `/`

Matched against the **filename only**, so they apply at every depth:

```
*.tmp       → ignores tracks/session.tmp, session.tmp, and a/b/c.tmp
.DS_Store   → ignores any file named .DS_Store at any depth
```

### Patterns with an embedded `/`

Matched against the **full relative path** from the right:

```
tracks/*.tmp       → ignores tracks/session.tmp
                     does NOT ignore exports/tracks/session.tmp
**/cache/*.dat     → ignores a/b/cache/index.dat
                     also ignores cache/index.dat
```

### Anchored patterns (leading `/`)

Matched against the **full path from the root** — only the top level of `state/`:

```
/renders/        → directory entry at root (directory patterns skipped for files)
/scratch.mid     → ignores scratch.mid at the root of state/
                   does NOT ignore tracks/scratch.mid
```

### Negation (`!pattern`)

Re-includes a path that was previously ignored:

```toml
[global]
patterns = [
    "*.bak",
    "!tracks/keeper.bak",   # keeper.bak is NOT ignored despite *.bak above
]
```

```toml
[global]
patterns = ["*.bak"]

[domain.midi]
patterns = ["!session.bak"]   # domain-level negation overrides global ignore
```

---

## Dotfiles are always excluded

Regardless of `.museignore`, any file whose **name** begins with `.` is
always excluded from snapshots by the built-in plugin rule. This prevents
OS metadata files (`.DS_Store`, `._.DS_Store`) and editor state from
accumulating in the object store.

---

## Domain plugin contract

Every domain plugin that implements `snapshot(live_state)` with a
`pathlib.Path` argument **must** honour `.museignore`. Use the helpers
provided by `muse.core.ignore`:

```python
from muse.core.ignore import is_ignored, load_ignore_config, resolve_patterns

def snapshot(self, live_state: LiveState) -> StateSnapshot:
    if isinstance(live_state, pathlib.Path):
        workdir   = live_state
        repo_root = workdir.parent          # .museignore lives here
        # load_ignore_config returns the full TOML config.
        # resolve_patterns merges global + domain-specific patterns.
        patterns = resolve_patterns(load_ignore_config(repo_root), self.DOMAIN)
        files = {}
        for file_path in sorted(workdir.rglob("*")):
            if not file_path.is_file():
                continue
            rel = file_path.relative_to(workdir).as_posix()
            if is_ignored(rel, patterns):
                continue
            files[rel] = hash_file(file_path)
        return {"files": files, "domain": self.DOMAIN}
    return live_state
```

Patterns from `[domain.<other>]` sections are never loaded — each plugin
only sees global patterns plus its own domain section.

---

## Interaction with `.museattributes`

`.museignore` and `.museattributes` are independent:

- `.museignore` controls **what enters the snapshot** at commit time.
- `.museattributes` controls **how conflicts are resolved** during merge.

A file ignored by `.museignore` is never committed, so it never appears in
a merge and `.museattributes` rules never apply to it.

---

## Domain-specific recommended configurations

### MIDI / music

```toml
[global]
patterns = [
    ".DS_Store",
    "Thumbs.db",
    "*.tmp",
]

[domain.midi]
patterns = [
    "*.bak",
    "*.autosave",
    "/renders/",
    "/exports/",
]
```

### Code

```toml
[global]
patterns = [
    ".DS_Store",
    "*.log",
]

[domain.code]
patterns = [
    "__pycache__/",
    "*.pyc",
    "node_modules/",
    "dist/",
    "build/",
    ".venv/",
]
```

### Genomics

```toml
[domain.genomics]
patterns = [
    "*.sam",
    "*.bam.bai",
    "pipeline-cache/",
    "!final/*.bam",   # keep final alignments
]
```

### Scientific simulation

```toml
[domain.simulation]
patterns = [
    "frames/raw/",
    "*.frame.bin",
    "!checkpoints/*.gz",   # keep compressed checkpoints
]
```

### 3D Spatial

```toml
[domain.spatial]
patterns = [
    "previews/",
    "*.preview.vdb",
    "**/.shadercache/",
]
```

---

## Implementation

Parsing, resolution, and matching are in `muse/core/ignore.py`:

```python
from muse.core.ignore import load_ignore_config, resolve_patterns, is_ignored

config   = load_ignore_config(repo_root)          # reads .museignore TOML
patterns = resolve_patterns(config, "midi")        # global + [domain.midi]
ignored  = is_ignored("tracks/x.tmp", patterns)   # → True / False
```

`load_ignore_config` returns an empty mapping when `.museignore` is absent
(nothing is ignored). `is_ignored` is a pure function with no filesystem access.

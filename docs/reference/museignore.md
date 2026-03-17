# `.museignore` Reference

`.museignore` tells Muse which files to exclude from every snapshot.
It lives in the **repository root** (the directory that contains `.muse/` and
`muse-work/`) and uses the same syntax as `.gitignore`.

---

## Why it matters

`muse commit` snapshots everything in `muse-work/`. Without `.museignore`,
OS artifacts (`.DS_Store`), DAW temp files (`*.bak`, `*.tmp`), rendered
previews, and build outputs are included in the content-addressed object
store and contribute to diff noise on every commit.

`.museignore` lets you declare once what belongs in version history and
what doesn't.

---

## File location

```
my-project/
├── .muse/               ← VCS metadata
├── muse-work/           ← tracked workspace (content here is snapshotted)
├── .museignore          ← ignore rules (lives here, next to muse-work/)
└── .museattributes      ← merge strategies
```

---

## Syntax

```
# This is a comment — blank lines and # lines are ignored

# Match any file named exactly .DS_Store at any depth:
.DS_Store

# Match any .tmp file at any depth:
*.tmp

# Match .bak files only inside tracks/:
tracks/*.bak

# Match everything inside any directory named __pycache__:
**/__pycache__/**

# Anchor to repo root: only match renders/ at the top level of muse-work/:
/renders/

# Negation: un-ignore a specific file even if *.bak matched it:
!tracks/keeper.bak
```

### Rule summary

| Syntax | Meaning |
|---|---|
| `#` at line start | Comment, ignored |
| Blank line | Ignored |
| `*.ext` | Ignore all files with this extension, at any depth |
| `name` | Ignore any file named exactly `name`, at any depth |
| `dir/*.ext` | Ignore matching files inside `dir/` at that exact depth |
| `**/name` | Ignore `name` inside any subdirectory at any depth |
| `name/` | Ignore a directory (Muse tracks files; this is silently skipped) |
| `/pattern` | Anchor to root — only matches at the top level of `muse-work/` |
| `!pattern` | Negate — un-ignore a previously matched path |

**Last matching rule wins.** A negation rule later in the file overrides an
earlier ignore rule for the same path.

---

## Matching rules in detail

### Patterns without a `/`

Matched against the **filename only**, so they apply at every depth:

```
*.tmp       → ignores tracks/session.tmp and session.tmp and a/b/c.tmp
.DS_Store   → ignores any file named .DS_Store at any depth
```

### Patterns with an embedded `/`

Matched against the **full relative path** from the right, so they respect
directory structure:

```
tracks/*.tmp       → ignores tracks/session.tmp
                     does NOT ignore exports/tracks/session.tmp
**/cache/*.dat     → ignores a/b/cache/index.dat
                     also ignores cache/index.dat
```

### Anchored patterns (leading `/`)

Matched against the **full path from the root**, so they only apply at the
top level of `muse-work/`:

```
/renders/          → ignores the top-level renders/ directory entry
                     (directory patterns are skipped for files)
/scratch.mid       → ignores scratch.mid at the root of muse-work/
                     does NOT ignore tracks/scratch.mid
```

### Negation (`!pattern`)

Re-includes a path that was previously ignored:

```
*.bak
!tracks/keeper.bak   → keeper.bak is NOT ignored despite *.bak above
```

The last matching rule wins, so negation rules must come **after** the rule
they override.

---

## Dotfiles are always excluded

Regardless of `.museignore`, any file whose **name** begins with `.` is
always excluded from snapshots by `MusicPlugin.snapshot()`. This prevents
OS metadata files (`.DS_Store`, `._.DS_Store`) and editor state from
accumulating in the object store.

To include a dotfile, you would need a domain plugin that overrides this
default. The reference `MusicPlugin` does not support it.

---

## Domain plugin contract

Every domain plugin that implements `snapshot(live_state)` with a
``pathlib.Path`` argument **must** honour `.museignore`. Use the helpers
provided by `muse.core.ignore`:

```python
from muse.core.ignore import is_ignored, load_patterns

def snapshot(self, live_state: LiveState) -> StateSnapshot:
    if isinstance(live_state, pathlib.Path):
        workdir  = live_state
        repo_root = workdir.parent          # .museignore lives here
        patterns = load_patterns(repo_root)
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

This is the exact pattern used by the reference `MusicPlugin`.

---

## Music domain recommended `.museignore`

```
# OS artifacts
.DS_Store
Thumbs.db

# DAW session backups and temp files
*.bak
*.tmp
*.autosave

# Rendered audio (not source state)
renders/
exports/

# Plugin caches
__pycache__/*
*.pyc
```

---

## Generic domain examples

### Genomics

```
# Pipeline intermediate files
*.sam
*.bam.bai
pipeline-cache/

# Keep the final alignments
!final/*.bam
```

### Scientific simulation

```
# Raw frame dumps (too large to version)
frames/raw/

# Keep compressed checkpoints
!checkpoints/*.gz
```

### 3D Spatial

```
# Preview renders and viewport caches
previews/
*.preview.vdb

# Shader compilation cache
**/.shadercache/
```

---

## Interaction with `.museattributes`

`.museignore` and `.museattributes` are independent:

- `.museignore` controls **what enters the snapshot** at commit time.
- `.museattributes` controls **how conflicts are resolved** during merge.

A file that is ignored by `.museignore` is never committed, so it never
appears in a merge and `.museattributes` rules never apply to it.

---

## Implementation

Parsing and matching are in `muse/core/ignore.py`:

```python
from muse.core.ignore import load_patterns, is_ignored

patterns = load_patterns(repo_root)       # reads .museignore
ignored  = is_ignored("tracks/x.tmp", patterns)  # → True / False
```

`load_patterns` returns an empty list when `.museignore` is absent (nothing
is ignored). `is_ignored` is a pure function with no filesystem access.

# `muse reflog` — HEAD and branch movement history

The reflog is Muse's **undo safety net**.  Every time HEAD or a branch pointer moves — commit, checkout, merge, reset, cherry-pick — Muse appends an entry to a per-ref journal.  If you accidentally reset to the wrong commit, the reflog tells you exactly where HEAD was before.

## Storage

```
.muse/logs/
    HEAD                  ← journal for the symbolic HEAD pointer
    refs/
        heads/
            main          ← journal for the main branch ref
            feat/audio    ← journal for the feat/audio branch ref
            …
```

Each file is an append-only sequence of lines in the format used by Git, so tooling that understands both formats works without translation.

## Subcommands

### `muse reflog` (default)

Show the HEAD reflog, newest entry first.

```
muse reflog [--branch <name>] [--limit N] [--all]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--branch`, `-b` | HEAD log | Show the named branch's reflog instead of HEAD |
| `--limit`, `-n` | 20 | Maximum entries to show |
| `--all` | false | List every ref that has a reflog |

### Output format

```
@{0}   cccccccc0000  (initial)          2026-03-19 14:22:01 UTC  commit: add chorus
@{1}   aabbccdd1234  (cccccccc0000)     2026-03-19 14:18:44 UTC  checkout: moving from dev to main
@{2}   aaaa0000ffff  (aabbccdd1234)     2026-03-19 14:15:02 UTC  commit: initial
```

- `@{N}` — reflog index (0 = newest)
- First SHA-256 prefix — new commit
- SHA-256 in parentheses — previous commit (`initial` for the first entry)

## When entries are written

| Operation | Entry |
|-----------|-------|
| `muse commit` | `commit: <message>` |
| `muse checkout <branch>` | `checkout: moving from X to Y` |
| `muse checkout -b <branch>` | `branch: created from X` |
| `muse merge <branch>` | `merge: <branch> into <current>` |
| `muse reset` | `reset (soft/hard): moving to <sha12>` |

## Agent workflows

### Undo an accidental reset

```bash
# Find HEAD before the reset:
muse reflog --limit 5

# Restore:
muse reset <sha-from-reflog>
```

### Automated safety checkpoint

```bash
# Before a risky operation, note the current HEAD:
HEAD=$(muse plumbing rev-parse HEAD)

# Do the risky operation…
muse reset --hard some-old-commit

# If it went wrong, restore using the reflog:
muse reflog
muse reset "$HEAD"
```

### Pipeline inspection

```bash
# How many commits happened today?
muse reflog --branch main | grep "$(date +%Y-%m-%d)" | wc -l
```

## Security notes

The reflog files are append-only by design.  Entries are never modified after being written.  The log paths are validated through the same `contain_path` primitive used throughout Muse — no path traversal is possible via branch names.

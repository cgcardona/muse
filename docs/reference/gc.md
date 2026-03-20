# `muse gc` — garbage-collect unreachable objects

Muse stores every tracked file as a content-addressed blob under `.muse/objects/`.  Over time — after branch deletions, abandoned experiments, or squash merges — blobs that are no longer reachable from any live commit accumulate and waste disk space.  `muse gc` identifies and removes them.

## How it works

1. **Reachability walk**: Starting from every live branch head and tag, Muse walks the commit graph:
   ```
   branch HEAD → CommitRecord → SnapshotRecord → manifest → object SHA-256
   ```
2. **Unreachable detection**: Any object *not* reachable from step 1 is garbage.
3. **Deletion** (if not `--dry-run`): Unreachable objects are deleted.  Empty prefix directories are also cleaned up.

## Usage

```bash
muse gc                  # remove unreachable objects (safe default)
muse gc --dry-run        # show what would be removed without touching anything
muse gc --verbose        # print each removed object ID
```

## Options

| Flag | Default | Description |
|------|---------|-------------|
| `--dry-run`, `-n` | false | Preview only — no files are deleted |
| `--verbose`, `-v` | false | Print each collected object ID |

## Output

```
Removed 12 object(s) (48.3 KiB) in 0.031s  [247 reachable]
```

In `--dry-run` mode:

```
[dry-run] Would remove 12 object(s) (48.3 KiB) in 0.028s  [247 reachable]
```

## Safety guarantees

- The reachability walk always completes **before** any deletion begins.
- `--dry-run` is always safe to run — even in production, even by agents.
- GC never touches commit or snapshot records — only content blobs.
- Running GC is idempotent: running it twice produces the same result.

## When to run

| Trigger | Recommendation |
|---------|---------------|
| After deleting branches | `muse gc` |
| Weekly CI maintenance | `muse gc --dry-run` to audit, then `muse gc` |
| Before `muse archive` | `muse gc` to shrink the repo |
| After a large squash merge | `muse gc` to free replaced blobs |

## Agent workflows

```bash
# Audit unreachable bloat before a deployment:
muse gc --dry-run

# Automated nightly cleanup:
muse gc 2>&1 | logger -t muse-gc
```

## Exit codes

| Code | Meaning |
|------|---------|
| 0 | Success (even if nothing was collected) |
| 1 | Internal error (corrupt store, permission issue) |

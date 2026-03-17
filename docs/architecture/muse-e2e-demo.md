# Muse E2E Walkthrough

A complete tour of every Muse VCS primitive using the CLI, from `init` to
three-way merge conflict resolution.

---

## Setup

```bash
# Install
pip install -e ".[dev]"

# Create a project directory
mkdir my-project && cd my-project

# Initialize a Muse repository (default domain: music)
muse init
```

```
Initialized empty Muse repository in my-project/.muse/
Domain: music
```

---

## Step 0 — Root Commit

```bash
cp ~/some-beat.mid muse-work/beat.mid
muse commit -m "Root: initial beat"
```

```
[main a1b2c3d4] Root: initial beat
```

```bash
muse log
```

```
commit a1b2c3d4 (HEAD -> main)
Date:   2026-03-17 12:00:00 UTC

    Root: initial beat

 + beat.mid
 1 file(s) changed
```

---

## Step 1 — Mainline Commit

```bash
cp ~/bass-line.mid muse-work/bass.mid
muse commit -m "Add bass line"
muse log --oneline
```

```
b2c3d4e5 (HEAD -> main) Add bass line
a1b2c3d4 Root: initial beat
```

---

## Step 2 — Branch A

```bash
muse branch feature/keys
muse checkout feature/keys
cp ~/piano.mid muse-work/keys.mid
muse commit -m "Add piano keys"
```

---

## Step 3 — Branch B (from Step 1)

```bash
# Time-travel back to the mainline
muse checkout main
muse branch feature/drums
muse checkout feature/drums
cp ~/drum-fill.mid muse-work/drums.mid
muse commit -m "Add drum fill"
```

```bash
muse log --graph
```

```
* f3e4d5c6 (HEAD -> feature/drums) Add drum fill
| * e4d5c6b7 (feature/keys) Add piano keys
|/
* b2c3d4e5 (main) Add bass line
* a1b2c3d4 Root: initial beat
```

---

## Step 4 — Clean Three-Way Merge

```bash
muse checkout main
muse merge feature/keys
```

```
Merge complete. Commit: c4d5e6f7
```

```bash
muse merge feature/drums
```

```
Merge complete. Commit: d5e6f7a8
```

Both branches touched different files — no conflicts. The merge commit has two parents.

```bash
muse log --stat
```

```
commit d5e6f7a8 (HEAD -> main)
Parent: c4d5e6f7
Parent: ... (merge)
Date:   2026-03-17 12:05:00 UTC

    Merge feature/drums

 + drums.mid
 1 file(s) changed
```

---

## Step 5 — Conflict Merge

Both branches modify the same file:

```bash
# Branch left
muse branch conflict/left
muse checkout conflict/left
echo "version-left" > muse-work/shared.mid
muse commit -m "Left changes shared.mid"

# Branch right
muse checkout main
muse branch conflict/right
muse checkout conflict/right
echo "version-right" > muse-work/shared.mid
muse commit -m "Right changes shared.mid"

# Attempt merge
muse checkout main
muse merge conflict/left
muse merge conflict/right
```

```
❌ Merge conflict in 1 file(s):
  CONFLICT (both modified): shared.mid
Resolve conflicts and run 'muse merge --continue'
```

```bash
# Manually resolve: pick one or blend both
cp my-resolved-shared.mid muse-work/shared.mid

muse merge --continue -m "Merge: resolve shared.mid conflict"
```

```
[main e6f7a8b9] Merge: resolve shared.mid conflict
```

---

## Step 6 — Cherry-Pick

Apply one commit's changes from a branch without merging the whole branch:

```bash
muse cherry-pick <commit-id>
```

```
[main f7a8b9c0] Add piano keys
```

---

## Step 7 — Time-Travel Checkout

```bash
# Inspect any historical commit
muse show a1b2c3d4

# Restore working tree to that exact state
muse checkout a1b2c3d4
```

```
HEAD is now at a1b2c3d4 Root: initial beat
```

```bash
# Restore to branch tip
muse checkout main
```

---

## Step 8 — Revert

Create a new commit that undoes a prior commit's changes:

```bash
muse revert b2c3d4e5
```

```
[main g8a9b0c1] Revert "Add bass line"
```

The revert commit points directly to the parent snapshot — no re-scan required.

---

## Step 9 — Stash and Restore

Temporarily set aside uncommitted work:

```bash
echo "wip" > muse-work/idea.mid
muse stash
# muse-work is clean — idea.mid is shelved

muse stash pop
# idea.mid is back
```

---

## Step 10 — Tag a Commit

```bash
muse tag add stage:rough-mix
muse tag list
```

```
d5e6f7a8  stage:rough-mix
```

---

## Full Summary Table

| Step | Operation | Result |
|---|---|---|
| 0 | Root commit | HEAD=a1b2c3d4 |
| 1 | Mainline commit | HEAD moves to b2c3d4e5 |
| 2 | Branch feature/keys | Diverges from Step 1 |
| 3 | Branch feature/drums | Also diverges from Step 1 |
| 4 | Merge both branches | Auto-merged, two-parent commit |
| 5 | Conflict merge | MERGE_STATE written; resolved manually |
| 6 | Cherry-pick | Single commit applied |
| 7 | Checkout traversal | HEAD detached, then restored |
| 8 | Revert | New commit undoing prior commit |
| 9 | Stash/pop | Working tree shelved and restored |
| 10 | Tag | Named reference on commit |

---

## What This Proves

Every Muse primitive works over actual files on disk with zero external
dependencies (no database, no HTTP server, no Docker). The full lifecycle —
commit, branch, merge, conflict, revert, cherry-pick, stash, checkout, tag
— runs from a single `pip install` and a directory.

The same lifecycle works identically for any domain that implements
`MuseDomainPlugin`. Swap `music` for `genomics` in `muse init --domain`
and the walkthrough above applies unchanged.
